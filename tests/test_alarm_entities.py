"""Tests for persistent and momentary alarm entities."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.unifi_air_quality.binary_sensor import (
    ALARM_DESCRIPTIONS,
    UnifiAirQualityAlarmBinarySensor,
)
from custom_components.unifi_air_quality.binary_sensor import (
    async_setup_entry as async_setup_binary_sensors,
)
from custom_components.unifi_air_quality.event import (
    ALARM_EVENT_TYPES,
    UnifiAirQualityAlarmEvent,
)
from custom_components.unifi_air_quality.event import (
    async_setup_entry as async_setup_events,
)
from custom_components.unifi_air_quality.models import (
    AirQualityDevice,
    ProtectAlarm,
    ProtectAlarmEvent,
    ProtectSnapshot,
)


def _device(
    *,
    status: object = "neutral",
    active_alarms: tuple[ProtectAlarm, ...] = (),
) -> AirQualityDevice:
    return AirQualityDevice(
        "device-id",
        "Office air",
        "UP-AirQuality",
        "1.0.16",
        True,
        {"airQuality": {"co2": {"status": status, "value": 900}}},
        active_alarms,
    )


def _coordinator(device: AirQualityDevice):
    coordinator = MagicMock()
    coordinator.data = ProtectSnapshot("console", "Protect", "6.2", "1", (device,))
    coordinator.last_update_success = True
    coordinator.client.subscribe_alarm_events = MagicMock(return_value=MagicMock())
    return coordinator


def _description(key: str):
    return next(item for item in ALARM_DESCRIPTIONS if item.key == key)


async def test_alarm_platforms_add_all_entities(hass) -> None:
    coordinator = _coordinator(_device())
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    binary_entities = []
    event_entities = []

    await async_setup_binary_sensors(
        hass, entry, lambda entities: binary_entities.extend(entities)
    )
    await async_setup_events(
        hass, entry, lambda entities: event_entities.extend(entities)
    )

    assert len(binary_entities) == len(ALARM_DESCRIPTIONS) == 11
    assert len(event_entities) == 1
    assert len(ALARM_EVENT_TYPES) == 22


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("neutral", False),
        ("safe", False),
        ("GOOD", False),
        ("normal", False),
        ("high", True),
        ("low", True),
        ("warning", True),
        (None, False),
        (42, False),
    ],
)
def test_binary_alarm_interprets_measurement_status(status, expected) -> None:
    entity = UnifiAirQualityAlarmBinarySensor(
        _coordinator(_device(status=status)), "device-id", _description("co2_alarm")
    )
    assert entity.is_on is expected


def test_binary_alarm_prefers_active_event_and_exposes_value() -> None:
    alarm = ProtectAlarm("co2", "high", 1200)
    entity = UnifiAirQualityAlarmBinarySensor(
        _coordinator(_device(active_alarms=(alarm,))),
        "device-id",
        _description("co2_alarm"),
    )
    assert entity.is_on
    assert entity.extra_state_attributes == {
        "protect_status": "high",
        "alarm_value": 1200,
    }

    alarm_without_value = ProtectAlarm("co2", None)
    coordinator = _coordinator(
        _device(active_alarms=(ProtectAlarm("aqi"), alarm_without_value))
    )
    entity = UnifiAirQualityAlarmBinarySensor(
        coordinator, "device-id", _description("co2_alarm")
    )
    assert entity.extra_state_attributes == {"protect_status": None}


def test_binary_alarm_missing_device_or_reading() -> None:
    coordinator = _coordinator(_device())
    entity = UnifiAirQualityAlarmBinarySensor(
        coordinator, "device-id", _description("pm10_alarm")
    )
    assert entity.is_on is False
    assert entity.extra_state_attributes is None

    coordinator.data.devices[0].raw.clear()
    assert entity.extra_state_attributes is None

    coordinator.data = ProtectSnapshot("console", "Protect", "6.2", "2", ())
    assert entity.is_on is None


async def test_event_entity_subscribes_filters_and_emits(hass) -> None:
    coordinator = _coordinator(_device())
    entity = UnifiAirQualityAlarmEvent(coordinator, "device-id")
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    entity._trigger_event = MagicMock()

    await entity.async_added_to_hass()
    coordinator.client.subscribe_alarm_events.assert_called_once()
    callback = coordinator.client.subscribe_alarm_events.call_args.args[0]

    callback(ProtectAlarmEvent("other", "co2", "started", "high", 1300))
    callback(ProtectAlarmEvent("device-id", "unknown", "started"))
    entity._trigger_event.assert_not_called()

    callback(ProtectAlarmEvent("device-id", "co2", "started", "high", 1300))
    entity._trigger_event.assert_called_once_with(
        "co2_alarm_started",
        {"metric": "co2", "status": "high", "value": 1300},
    )
    entity.async_write_ha_state.assert_called_once()

    callback(ProtectAlarmEvent("device-id", "vape", "ended"))
    entity._trigger_event.assert_called_with("vape_alarm_ended", {"metric": "vape"})
