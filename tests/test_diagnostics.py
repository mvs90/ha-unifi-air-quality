"""Tests proving that diagnostics do not leak identifying strings."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.unifi_air_quality import UnifiAirQualityRuntimeData
from custom_components.unifi_air_quality.const import DOMAIN
from custom_components.unifi_air_quality.diagnostics import (
    async_get_config_entry_diagnostics,
    sanitize_raw,
)
from custom_components.unifi_air_quality.models import (
    AirQualityDevice,
    ProtectAlarm,
    ProtectSnapshot,
)

from .ha_test_helpers import MockConfigEntry


def test_sanitize_raw_preserves_shape_and_measurements(load_fixture) -> None:
    """Verify sanitize raw preserves shape and measurements."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    sanitized = sanitize_raw(payload["sensors"][0])

    assert sanitized["airQuality"]["co2"] == 612
    assert sanitized["model"] == "UP-AirQuality"
    assert sanitized["id"].startswith("<export:")
    assert "mac" not in sanitized
    assert "name" not in sanitized
    serialized = json.dumps(sanitized)
    assert "Anonymous Room" not in serialized
    assert "00:00:00:00:00:00" not in serialized


def test_sanitize_raw_redacts_unknown_strings_and_secrets() -> None:
    """Verify sanitize raw redacts unknown strings and secrets."""
    sanitized = sanitize_raw(
        {"newFirmwareField": "possibly personal", "token": "secret"}
    )
    assert sanitized == {}


def test_sanitize_raw_lists_and_unknown_types() -> None:
    """Replace unsupported top-level collections and objects with a redaction marker."""
    assert sanitize_raw([1, "private"]) == "**REDACTED**"
    assert sanitize_raw(object()) == "**REDACTED**"


async def test_config_entry_diagnostics(hass) -> None:
    """Export health summaries with aliased device IDs and redacted settings."""
    device = AirQualityDevice(
        "sensor",
        "Private room",
        "UP-AirQuality",
        "1.0",
        True,
        {"co2": 600},
        (ProtectAlarm("co2", "high", 1200),),
    )
    snapshot = ProtectSnapshot("console", "Private", "6.2", "update", (device,))
    client = MagicMock(
        websocket_connected=True,
        protocol_error=False,
        authentication_failed=False,
        last_successful_refresh=None,
    )
    client.diagnostic_freshness.return_value = {"fresh": True}
    runtime = UnifiAirQualityRuntimeData(client, SimpleNamespace(data=snapshot))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.1",
            CONF_USERNAME: "private-user",
            CONF_PASSWORD: "private-password",
        },
    )
    entry.runtime_data = runtime

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["transport"]["air_quality_device_count"] == 1
    assert diagnostics["transport"]["active_alarm_count"] == 1
    assert len(diagnostics["devices"]) == 1
    assert diagnostics["devices"][0]["id"].startswith("<export:")
    assert diagnostics["devices"][0]["freshness"] == {"fresh": True}
    assert diagnostics["config_entry"][CONF_PASSWORD] == "**REDACTED**"


def test_diagnostic_aliases_use_the_effective_canonical_reading() -> None:
    """Old duplicate aliases cannot appear fresh beside a newer canonical value."""
    raw = {"airQuality": {"pm2p5": 3, "pm2_5": 99, "vapeIndex": 4}}
    sanitized = sanitize_raw(raw)
    assert sanitized == {"airQuality": {"pm2p5": 3, "vape": 4}}
    assert raw["airQuality"]["pm2_5"] == 99
