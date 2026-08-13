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
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    snapshot = snapshot_from_bootstrap(payload)

    assert snapshot.console_id == "anonymous-console-id"
    assert snapshot.protect_version == "6.2.0"
    assert len(snapshot.devices) == 1
    assert snapshot.devices[0].model == "UP-AirQuality"
    assert snapshot.devices[0].raw["airQuality"]["co2"] == 612


def test_snapshot_recognizes_air_quality_payload_without_model() -> None:
    snapshot = snapshot_from_bootstrap(
        {
            "nvr": {"id": "console"},
            "sensors": [{"id": "sensor", "airQuality": {"co2": 500}}],
        }
    )
    assert snapshot.devices[0].id == "sensor"


def test_snapshot_rejects_invalid_bootstrap() -> None:
    with pytest.raises(ProtectProtocolError):
        snapshot_from_bootstrap({"nvr": {}, "sensors": []})
    with pytest.raises(ProtectProtocolError):
        snapshot_from_bootstrap({"nvr": {"id": "console"}, "sensors": {}})


def test_snapshot_skips_malformed_devices_and_uses_fallbacks() -> None:
    snapshot = snapshot_from_bootstrap(
        {
            "nvr": {"mac": "console-mac", "name": 42, "version": 6},
            "lastUpdateId": 5,
            "sensors": [
                None,
                {"id": "regular", "type": "UP-Sense"},
                {"id": None, "type": "UP-AirQuality"},
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
    target = {"airQuality": {"co2": 500, "aqi": 10}}
    _deep_merge(target, {"airQuality": {"co2": 700}})
    assert target == {"airQuality": {"co2": 700, "aqi": 10}}


@pytest.mark.parametrize(
    ("host", "expected"),
    [("protect.local", "protect.local"), ("2001:db8::1", "[2001:db8::1]")],
)
def test_host_for_url(host: str, expected: str) -> None:
    assert _host_for_url(host) == expected


@pytest.mark.parametrize("host", ["https://protect.local", "", "protect/local"])
def test_host_for_url_rejects_url(host) -> None:
    with pytest.raises(ProtectProtocolError):
        _host_for_url(host)
