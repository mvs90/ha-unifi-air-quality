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


def test_host_for_url_rejects_url() -> None:
    with pytest.raises(ProtectProtocolError):
        _host_for_url("https://protect.local")
