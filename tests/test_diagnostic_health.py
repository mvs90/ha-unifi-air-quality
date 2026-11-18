"""Health exports distinguish current readings from safe, stale cached values."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.unifi_air_quality import UnifiAirQualityRuntimeData
from custom_components.unifi_air_quality.const import DOMAIN
from custom_components.unifi_air_quality.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .ha_test_helpers import MockConfigEntry
from .test_client import FakeResponse, FakeSession, _client
from .test_config_flow import USER_INPUT
from .test_recovery import push


async def _diagnostic_entry(load_fixture):
    """Create an entry whose synthetic private snapshot was verified at time100."""
    payload = json.loads(load_fixture("bootstrap_nox.json"))
    payload["sensors"][0]["name"] = "Private room"
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=100):
        snapshot = await client.async_get_snapshot()
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT.copy())
    entry.runtime_data = UnifiAirQualityRuntimeData(
        client, SimpleNamespace(data=snapshot)
    )
    return entry, client


@pytest.mark.parametrize(
    ("now", "websocket", "protocol", "auth", "expected", "fresh"),
    [
        (105, True, False, False, "healthy", True),
        (105, False, False, False, "polling", True),
        (221, True, False, False, "stale", False),
        (105, True, True, False, "protocol_error", False),
        (105, True, False, True, "authentication_failed", False),
    ],
)
async def test_health_categories_and_relative_ages(
    hass, load_fixture, now, websocket, protocol, auth, expected, fresh
):
    """An open stream cannot disguise stale, invalid-protocol, or rejected data."""
    entry, client = await _diagnostic_entry(load_fixture)
    client.websocket_connected = websocket
    client.protocol_error = protocol
    client._auth_failed = auth
    with (
        patch("custom_components.unifi_air_quality.api.monotonic", return_value=now),
        patch(
            "custom_components.unifi_air_quality.diagnostics.monotonic",
            return_value=now,
        ),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    transport = result["transport"]
    assert transport["health"] == expected
    assert transport["bootstrap_age_seconds"] == now - 100
    assert transport["authentication_failed"] is auth
    assert transport["protocol_error"] is protocol
    device = result["devices"][0]
    assert device["freshness"]["age_seconds"] == now - 100
    assert device["freshness"]["fresh"] is fresh
    assert device["freshness"]["measurements"]["nox"] == {
        "age_seconds": now - 100,
        "fresh": fresh,
    }
    assert device["freshness"]["alarms"]["nox"] == {
        "age_seconds": now - 100,
        "fresh": fresh,
    }
    serialized = json.dumps(result)
    assert entry.data["host"] not in serialized
    assert entry.data["username"] not in serialized
    assert entry.data["password"] not in serialized
    assert client.snapshot.console_id not in serialized
    assert client.snapshot.devices[0].id not in serialized
    assert client.snapshot.devices[0].name not in serialized


async def test_individual_metric_ages_and_export_aliases(hass, load_fixture):
    """A fresh CO2 push does not make another cached metric look fresh."""
    entry, client = await _diagnostic_entry(load_fixture)
    client.websocket_connected = True
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=221):
        push(client, {"airQuality": {"co2": 700}})
        entry.runtime_data.coordinator.data = client.snapshot
        with patch(
            "custom_components.unifi_air_quality.diagnostics.monotonic",
            return_value=221,
        ):
            first = await async_get_config_entry_diagnostics(hass, entry)
            second = await async_get_config_entry_diagnostics(hass, entry)
    assert first["transport"]["health"] == "degraded"
    freshness = first["devices"][0]["freshness"]
    assert freshness["fresh"]
    assert freshness["age_seconds"] == 0
    assert freshness["measurements"]["co2"] == {"age_seconds": 0, "fresh": True}
    assert freshness["measurements"]["nox"] == {"age_seconds": 121, "fresh": False}
    assert freshness["alarms"]["nox"] == {"age_seconds": 121, "fresh": False}
    assert first["devices"][0]["id"] != second["devices"][0]["id"]
    unknown = client.diagnostic_freshness("missing")
    assert unknown["age_seconds"] is None
    assert not unknown["fresh"]
    assert unknown["measurements"]["co2"] == {"age_seconds": None, "fresh": False}


@pytest.mark.parametrize(
    ("last_refresh", "expected"),
    [(None, "uninitialized"), (100, "empty_inventory"), (-100, "stale")],
)
async def test_empty_inventory_health(hass, load_fixture, last_refresh, expected):
    """An empty but recently verified console is distinct from absent inventory."""
    entry, client = await _diagnostic_entry(load_fixture)
    from dataclasses import replace

    entry.runtime_data.coordinator.data = replace(client.snapshot, devices=())
    client.last_successful_refresh = last_refresh
    with patch(
        "custom_components.unifi_air_quality.diagnostics.monotonic", return_value=105
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["transport"]["health"] == expected
    assert result["devices"] == []


@pytest.mark.parametrize("offline", [False, True])
async def test_mixed_device_health_is_degraded(hass, load_fixture, offline):
    """A current device cannot hide a second stale or offline device in exports."""
    from dataclasses import replace

    entry, client = await _diagnostic_entry(load_fixture)
    original = client.snapshot.devices[0]
    other = replace(
        original,
        id="second",
        is_connected=not offline,
        raw=original.raw | {"id": "second", "isConnected": not offline},
    )
    entry.runtime_data.coordinator.data = replace(
        client.snapshot, devices=(original, other)
    )
    if offline:
        client._verified_at["second"] = 100
    with (
        patch("custom_components.unifi_air_quality.api.monotonic", return_value=105),
        patch(
            "custom_components.unifi_air_quality.diagnostics.monotonic",
            return_value=105,
        ),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["transport"]["health"] == "degraded"
