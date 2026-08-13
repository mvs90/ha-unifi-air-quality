"""Tests for writable UP-AirQuality configuration entities."""

from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.unifi_air_quality.entity import nested_update, nested_value
from custom_components.unifi_air_quality.models import AirQualityDevice, ProtectSnapshot
from custom_components.unifi_air_quality.number import (
    NUMBER_DESCRIPTIONS,
    AirQualityNumber,
)
from custom_components.unifi_air_quality.number import (
    async_setup_entry as async_setup_numbers,
)
from custom_components.unifi_air_quality.select import (
    RingLedMetricSelect,
)
from custom_components.unifi_air_quality.select import (
    async_setup_entry as async_setup_selects,
)
from custom_components.unifi_air_quality.switch import (
    SWITCH_DESCRIPTIONS,
    AirQualitySwitch,
    RingLedSwitch,
)
from custom_components.unifi_air_quality.switch import (
    async_setup_entry as async_setup_switches,
)
from custom_components.unifi_air_quality.time import (
    TIME_DESCRIPTIONS,
    AirQualityTime,
)
from custom_components.unifi_air_quality.time import (
    async_setup_entry as async_setup_times,
)


def _device(*, connected: bool = True) -> AirQualityDevice:
    return AirQualityDevice(
        "device-id",
        "Office air",
        "UP-AirQuality",
        "1.0.16",
        connected,
        {
            "ledSettings": {"isEnabled": True, "activityFeedback": False},
            "airQualitySettings": {
                "ringLedBrightness": 80,
                "ringLedMetric": 1,
                "nightModeEnabled": True,
                "nightModeBrightness": 10,
                "nightModeStartTime": "22:30",
                "nightModeEndTime": "06:15",
                "readingInterval": 15,
                "aqiSettings": {
                    "isEnabled": True,
                    "lowThreshold": None,
                    "highThreshold": None,
                },
                "co2Settings": {
                    "isEnabled": True,
                    "lowThreshold": 400,
                    "highThreshold": 1200,
                },
                "vapeSensitivitySettings": {
                    "isEnabled": True,
                    "sensitivity": 50,
                },
            },
        },
    )


def _coordinator(device: AirQualityDevice):
    coordinator = MagicMock()
    coordinator.data = ProtectSnapshot("console", "Protect", "6.2", "1", (device,))
    coordinator.last_update_success = True
    coordinator.client.async_update_device = AsyncMock()
    coordinator.config_entry = SimpleNamespace(options={})
    return coordinator


def _description(descriptions, key):
    return next(item for item in descriptions if item.key == key)


async def test_platforms_add_all_controls(hass) -> None:
    coordinator = _coordinator(_device())
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))

    numbers = []
    switches = []
    selects = []
    times = []
    await async_setup_numbers(hass, entry, lambda entities: numbers.extend(entities))
    await async_setup_switches(hass, entry, lambda entities: switches.extend(entities))
    await async_setup_selects(hass, entry, lambda entities: selects.extend(entities))
    await async_setup_times(hass, entry, lambda entities: times.extend(entities))

    assert len(numbers) == len(NUMBER_DESCRIPTIONS) == 25
    assert len(switches) == len(SWITCH_DESCRIPTIONS) + 1 == 16
    assert len(selects) == 1
    assert len(times) == len(TIME_DESCRIPTIONS) == 2


async def test_switch_reads_and_writes() -> None:
    coordinator = _coordinator(_device())
    entity = AirQualitySwitch(
        coordinator, "device-id", _description(SWITCH_DESCRIPTIONS, "status_light")
    )
    assert entity.is_on is True
    await entity.async_turn_off()
    await entity.async_turn_on()
    assert coordinator.client.async_update_device.await_args_list[0].args[1] == {
        "ledSettings": {"isEnabled": False}
    }
    assert coordinator.client.async_update_device.await_args_list[1].args[1] == {
        "ledSettings": {"isEnabled": True}
    }


async def test_alarm_state_follows_thresholds() -> None:
    coordinator = _coordinator(_device())
    inactive = AirQualitySwitch(
        coordinator, "device-id", _description(SWITCH_DESCRIPTIONS, "aqi_alerts")
    )
    active = AirQualitySwitch(
        coordinator, "device-id", _description(SWITCH_DESCRIPTIONS, "co2_alerts")
    )

    assert inactive.is_on is False
    assert active.is_on is True
    with pytest.raises(HomeAssistantError):
        await inactive.async_turn_on()

    await active.async_turn_off()
    coordinator.client.async_update_device.assert_awaited_once_with(
        "device-id",
        {
            "airQualitySettings": {
                "co2Settings": {
                    "isEnabled": False,
                    "lowThreshold": None,
                    "highThreshold": None,
                }
            }
        },
    )


async def test_alarm_with_threshold_can_be_enabled() -> None:
    coordinator = _coordinator(_device())
    coordinator.data.devices[0].raw["airQualitySettings"]["co2Settings"][
        "isEnabled"
    ] = False
    entity = AirQualitySwitch(
        coordinator, "device-id", _description(SWITCH_DESCRIPTIONS, "co2_alerts")
    )
    assert entity.is_on is False
    await entity.async_turn_on()
    coordinator.client.async_update_device.assert_awaited_once_with(
        "device-id",
        {"airQualitySettings": {"co2Settings": {"isEnabled": True}}},
    )


async def test_ring_led_switch_restores_brightness() -> None:
    coordinator = _coordinator(_device())
    entity = RingLedSwitch(coordinator, "device-id")
    assert entity.is_on is True

    await entity.async_turn_off()
    coordinator.data.devices[0].raw["airQualitySettings"]["ringLedBrightness"] = 0
    assert entity.is_on is False
    await entity.async_turn_on()

    assert coordinator.client.async_update_device.await_args_list[0].args[1] == {
        "airQualitySettings": {"ringLedBrightness": 0}
    }
    assert coordinator.client.async_update_device.await_args_list[1].args[1] == {
        "airQualitySettings": {"ringLedBrightness": 80}
    }
    coordinator.hass.config_entries.async_update_entry.assert_called_once()


async def test_ring_led_switch_restores_persisted_brightness_after_reload() -> None:
    device = _device()
    device.raw["airQualitySettings"]["ringLedBrightness"] = 0
    coordinator = _coordinator(device)
    coordinator.config_entry.options = {"ring_led_previous_brightness_device-id": 35}
    entity = RingLedSwitch(coordinator, "device-id")

    assert entity.is_on is False
    await entity.async_turn_on()
    coordinator.client.async_update_device.assert_awaited_once_with(
        "device-id", {"airQualitySettings": {"ringLedBrightness": 35}}
    )


async def test_number_reads_and_writes_integer_and_float() -> None:
    coordinator = _coordinator(_device())
    entity = AirQualityNumber(
        coordinator,
        "device-id",
        _description(NUMBER_DESCRIPTIONS, "co2_high_threshold"),
    )
    assert entity.native_value == 1200
    await entity.async_set_native_value(1000.0)
    await entity.async_set_native_value(1000.5)
    assert coordinator.client.async_update_device.await_args_list[0].args[1] == {
        "airQualitySettings": {
            "co2Settings": {"isEnabled": True, "highThreshold": 1000}
        }
    }
    assert coordinator.client.async_update_device.await_args_list[1].args[1] == {
        "airQualitySettings": {
            "co2Settings": {"isEnabled": True, "highThreshold": 1000.5}
        }
    }

    missing = AirQualityNumber(
        coordinator,
        "device-id",
        _description(NUMBER_DESCRIPTIONS, "aqi_low_threshold"),
    )
    assert missing.native_value is None


async def test_select_reads_and_writes() -> None:
    coordinator = _coordinator(_device())
    entity = RingLedMetricSelect(coordinator, "device-id")
    assert entity.current_option == "air_quality"
    await entity.async_select_option("carbon_dioxide")
    coordinator.client.async_update_device.assert_awaited_once_with(
        "device-id", {"airQualitySettings": {"ringLedMetric": 0}}
    )

    coordinator.data.devices[0].raw["airQualitySettings"]["ringLedMetric"] = 9
    assert entity.current_option is None


async def test_time_reads_writes_and_rejects_invalid_wire_value() -> None:
    coordinator = _coordinator(_device())
    entity = AirQualityTime(
        coordinator,
        "device-id",
        _description(TIME_DESCRIPTIONS, "night_mode_start"),
    )
    assert entity.native_value == time(22, 30)
    await entity.async_set_value(time(21, 5))
    coordinator.client.async_update_device.assert_awaited_once_with(
        "device-id", {"airQualitySettings": {"nightModeStartTime": "21:05"}}
    )

    coordinator.data.devices[0].raw["airQualitySettings"]["nightModeStartTime"] = (
        "invalid"
    )
    assert entity.native_value is None


def test_nested_helpers_and_availability() -> None:
    raw = {"one": {"two": 2}}
    assert nested_value(raw, ("one", "two")) == 2
    assert nested_value(raw, ("one", "missing")) is None
    assert nested_value(raw, ("one", "two", "three")) is None
    assert nested_update(("one", "two"), 3) == {"one": {"two": 3}}

    coordinator = _coordinator(_device(connected=False))
    entity = RingLedMetricSelect(coordinator, "device-id")
    assert not entity.available
    coordinator.data = ProtectSnapshot("console", "Protect", "6.2", "2", ())
    assert not entity.available
    assert entity.raw_value(("airQualitySettings",)) is None
