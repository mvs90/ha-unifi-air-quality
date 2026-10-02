"""Alarm state entities for UniFi Protect Air Quality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity

_NON_ALARM_STATUSES = frozenset({"good", "neutral", "normal", "safe"})


@dataclass(frozen=True, kw_only=True)
class AlarmBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a metric alarm."""

    payload_key: str


ALARM_DESCRIPTIONS: tuple[AlarmBinarySensorDescription, ...] = tuple(
    AlarmBinarySensorDescription(
        key=f"{key}_alarm",
        translation_key=f"{key}_alarm",
        payload_key=payload_key,
        device_class=BinarySensorDeviceClass.PROBLEM,
    )
    for key, payload_key in (
        ("aqi", "aqi"),
        ("co2", "co2"),
        ("humidity", "humidity"),
        ("temperature", "temperature"),
        ("pm1", "pm1p0"),
        ("pm25", "pm2p5"),
        ("pm4", "pm4p0"),
        ("pm10", "pm10p0"),
        ("tvoc", "tvoc"),
        ("voc", "voc"),
        ("nox", "nox"),
        ("vape", "vape"),
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up alarm state entities for every discovered device."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        UnifiAirQualityAlarmBinarySensor(coordinator, device.id, description)
        for device in coordinator.data.devices
        for description in ALARM_DESCRIPTIONS
    )


class UnifiAirQualityAlarmBinarySensor(UnifiAirQualityEntity, BinarySensorEntity):
    """Represent the current alarm state for one metric."""

    entity_description: AlarmBinarySensorDescription

    def __init__(
        self,
        coordinator: UnifiAirQualityCoordinator,
        device_id: str,
        description: AlarmBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if device is None:
            return None
        if any(
            alarm.metric == self.entity_description.payload_key
            for alarm in device.active_alarms
        ):
            return True
        status = self._measurement_status
        if status is None:
            return False
        return status.casefold() not in _NON_ALARM_STATUSES

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        if device is not None:
            for alarm in device.active_alarms:
                if alarm.metric == self.entity_description.payload_key:
                    result: dict[str, Any] = {"protect_status": alarm.status}
                    if alarm.value is not None:
                        result["alarm_value"] = alarm.value
                    return result
        status = self._measurement_status
        return {"protect_status": status} if status is not None else None

    @property
    def _measurement_status(self) -> str | None:
        device = self.device
        air_quality = device.raw.get("airQuality") if device else None
        if not isinstance(air_quality, dict):
            return None
        measurement = air_quality.get(self.entity_description.payload_key)
        if not isinstance(measurement, dict):
            return None
        status = measurement.get("status")
        return status if isinstance(status, str) and status else None
