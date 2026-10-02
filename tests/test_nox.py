"""Regression coverage for the documented NOx index and its alarm controls."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_air_quality.api import snapshot_from_bootstrap
from custom_components.unifi_air_quality.const import CONF_VERIFY_SSL, DOMAIN
from custom_components.unifi_air_quality.number import (
    NUMBER_DESCRIPTIONS,
    AirQualityNumber,
)
from custom_components.unifi_air_quality.sensor import (
    SENSOR_DESCRIPTIONS,
    UnifiAirQualitySensor,
)
from custom_components.unifi_air_quality.switch import (
    SWITCH_DESCRIPTIONS,
    AirQualitySwitch,
)


def _coordinator(load_fixture):
    coordinator = MagicMock()
    coordinator.data = snapshot_from_bootstrap(
        json.loads(load_fixture("bootstrap_nox.json"))
    )
    coordinator.last_update_success = True
    coordinator.client.async_update_device = AsyncMock()
    return coordinator


def test_nox_is_an_unscaled_index_with_status(load_fixture):
    coordinator = _coordinator(load_fixture)
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == "nox_index")
    entity = UnifiAirQualitySensor(coordinator, "anonymous-air-quality-id", description)

    assert entity.native_value == 42
    assert entity.extra_state_attributes == {"protect_status": "neutral"}
    assert entity.native_unit_of_measurement is None
    assert entity.device_class is None


@pytest.mark.parametrize("measurement", [None, {}, {"value": None}, {"value": True}])
def test_missing_or_invalid_nox_is_not_reported_as_zero(load_fixture, measurement):
    coordinator = _coordinator(load_fixture)
    coordinator.data.devices[0].raw["airQuality"]["nox"] = measurement
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == "nox_index")
    entity = UnifiAirQualitySensor(coordinator, "anonymous-air-quality-id", description)
    assert entity.native_value is None


async def test_nox_thresholds_and_switch_use_correct_setting_path(load_fixture):
    coordinator = _coordinator(load_fixture)
    for bound, value in (("low", 10.0), ("high", 120.0)):
        description = next(
            item for item in NUMBER_DESCRIPTIONS if item.key == f"nox_{bound}_threshold"
        )
        number = AirQualityNumber(coordinator, "anonymous-air-quality-id", description)
        assert number.native_min_value == 0
        assert number.native_max_value == 500
        assert number.native_value == (0 if bound == "low" else 100)
        await number.async_set_native_value(value)
        coordinator.client.async_update_device.assert_awaited_with(
            "anonymous-air-quality-id",
            {
                "airQualitySettings": {
                    "noxSettings": {"isEnabled": True, f"{bound}Threshold": int(value)}
                }
            },
        )

    description = next(item for item in SWITCH_DESCRIPTIONS if item.key == "nox_alerts")
    switch = AirQualitySwitch(coordinator, "anonymous-air-quality-id", description)
    assert switch.is_on
    await switch.async_turn_off()
    coordinator.client.async_update_device.assert_awaited_with(
        "anonymous-air-quality-id",
        {
            "airQualitySettings": {
                "noxSettings": {
                    "isEnabled": False,
                    "lowThreshold": None,
                    "highThreshold": None,
                }
            }
        },
    )


async def test_full_ha_setup_exposes_nox_entities_and_event_types(hass, load_fixture):
    snapshot = snapshot_from_bootstrap(json.loads(load_fixture("bootstrap_nox.json")))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "protect.local",
            CONF_PORT: 443,
            CONF_USERNAME: "synthetic-user",
            CONF_PASSWORD: "synthetic-password",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.unifi_air_quality.PrivateProtectClient") as factory:
        client = factory.return_value
        client.async_get_snapshot = AsyncMock(return_value=snapshot)
        client.async_close = AsyncMock()
        client.subscribe_alarm_events.return_value = MagicMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        suffixes = (
            "nox_index",
            "nox_alarm",
            "nox_low_threshold",
            "nox_high_threshold",
            "nox_alerts",
            "air_quality_alarm_event",
        )
        states = {}
        for suffix in suffixes:
            registered = next(
                item
                for item in registry.entities.values()
                if item.unique_id == f"anonymous-air-quality-id_{suffix}"
            )
            states[suffix] = hass.states.get(registered.entity_id)
            assert states[suffix] is not None
        assert states["nox_index"].state == "42"
        assert states["nox_alarm"].state == "off"
        assert states["nox_low_threshold"].state == "0.0"
        assert states["nox_high_threshold"].state == "100.0"
        assert states["nox_alerts"].state == "on"
        event_types = states["air_quality_alarm_event"].attributes["event_types"]
        assert "nox_alarm_started" in event_types
        assert "nox_alarm_ended" in event_types

        assert await hass.config_entries.async_unload(entry.entry_id)
        client.async_close.assert_awaited_once()
