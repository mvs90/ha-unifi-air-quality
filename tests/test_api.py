"""Tests for the private Protect adapter model boundary."""

import json

import pytest

from custom_components.unifi_air_quality.api import (
    ProtectProtocolError,
    _deep_merge,
    _host_for_url,
    snapshot_from_bootstrap,
)


def test_snapshot_discovers_only_air_quality(load_fixture) -> None:
    """Verify snapshot discovers only air quality."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    snapshot = snapshot_from_bootstrap(payload)

    assert snapshot.console_id == "anonymous-console-id"
    assert snapshot.protect_version == "6.2.0"
    assert len(snapshot.devices) == 1
    assert snapshot.devices[0].model == "UP-AirQuality"
    assert snapshot.devices[0].raw["airQuality"]["co2"] == 612


def test_snapshot_recognizes_air_quality_payload_without_model() -> None:
    """Verify snapshot recognizes air quality payload without model."""
    snapshot = snapshot_from_bootstrap(
        {
            "nvr": {"id": "console"},
            "sensors": [{"id": "sensor", "airQuality": {"co2": 500}}],
        }
    )
    assert snapshot.devices[0].id == "sensor"


@pytest.mark.parametrize(
    ("model_data", "readings", "supported"),
    [
        ({"type": "UP-Sense"}, {}, False),
        ({"sku": "USL-Entry"}, {"co2": 600}, False),
        ({"productModel": "USL-Entry-R"}, {"co2": 600}, False),
        ({"model": "USL-Smoke"}, {"co2": 600}, False),
        ({"model": "USL-Motion"}, {"co2": 600}, False),
        ({"type": "USL-Environmental"}, {"pm2p5": 8}, False),
        ({"type": "USL-GlassBreak"}, {}, False),
        ({"type": "future-model"}, {"co2": 600}, False),
        ({"type": "UP-AirQuality", "sku": "USL-Smoke"}, {"co2": 600}, False),
        ({"type": "sensor", "sku": "UP-AirQuality"}, {}, True),
        ({"model": "UP-AirQuality"}, {"co2": None}, True),
        ({"type": "sensor"}, {"temperature": 22, "humidity": 49}, False),
        ({}, {}, False),
        ({}, {"other": 1}, False),
        ({}, {"co2": {"status": "neutral"}}, False),
        ({}, {"co2": {"value": True}}, False),
        ({}, {"co2": float("inf")}, False),
        ({}, {"pm2_5": {"value": 5}}, True),
        ({"type": "unknown"}, {"co2": 612}, True),
    ],
)
def test_discovery_requires_model_or_distinctive_data(model_data, readings, supported):
    """An empty, incidental or ambiguous data block is not a model identifier."""
    sensor = {"id": "synthetic-sensor", "airQuality": readings, **model_data}
    snapshot = snapshot_from_bootstrap(
        {"nvr": {"id": "synthetic-console"}, "sensors": [sensor]}
    )
    assert bool(snapshot.devices) is supported


def test_snapshot_rejects_invalid_bootstrap() -> None:
    """Verify snapshot rejects invalid bootstrap."""
    with pytest.raises(ProtectProtocolError):
        snapshot_from_bootstrap({"nvr": {}, "sensors": []})
    with pytest.raises(ProtectProtocolError):
        snapshot_from_bootstrap({"nvr": {"id": "console"}, "sensors": {}})


def test_snapshot_skips_malformed_devices_and_uses_fallbacks() -> None:
    """Verify unknown valid device models do not affect valid sensor data."""
    snapshot = snapshot_from_bootstrap(
        {
            "nvr": {"mac": "console-mac", "name": 42, "version": 6},
            "lastUpdateId": 5,
            "sensors": [
                {"id": "regular", "type": "UP-Sense"},
                {
                    "id": "air",
                    "productModel": "UP Air-Quality",
                    "name": "",
                    "firmwareVersion": 4,
                    "isConnected": False,
                },
            ],
        }
    )
    assert snapshot.console_id == "console-mac"
    assert snapshot.console_name == "Protect"
    assert snapshot.protect_version is None
    assert snapshot.last_update_id is None
    assert snapshot.devices[0].name == "UP-AirQuality"
    assert snapshot.devices[0].firmware_version is None
    assert not snapshot.devices[0].is_connected


def test_deep_merge_preserves_other_readings() -> None:
    """Verify deep merge preserves other readings."""
    target = {"airQuality": {"co2": 500, "aqi": 10}}
    _deep_merge(target, {"airQuality": {"co2": 700}})
    assert target == {"airQuality": {"co2": 700, "aqi": 10}}


@pytest.mark.parametrize(
    ("host", "expected"),
    [("protect.local", "protect.local"), ("2001:db8::1", "[2001:db8::1]")],
)
def test_host_for_url(host: str, expected: str) -> None:
    """Leave DNS hosts unchanged and bracket an unbracketed IPv6 literal."""
    assert _host_for_url(host) == expected


@pytest.mark.parametrize("host", ["https://protect.local", "", "protect/local"])
def test_host_for_url_rejects_url(host) -> None:
    """Reject empty hosts and inputs containing a scheme or path."""
    with pytest.raises(ProtectProtocolError):
        _host_for_url(host)
