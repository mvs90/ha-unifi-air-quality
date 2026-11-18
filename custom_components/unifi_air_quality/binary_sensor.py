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
from .entity import UnifiAirQualityEntity, async_setup_dynamic_entities

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
        entity_registry_enabled_default=payload_key != "nox",
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
    async_setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda device_id: (
            UnifiAirQualityAlarmBinarySensor(coordinator, device_id, description)
            for description in ALARM_DESCRIPTIONS
        ),
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
        """Initialize this component with its entry-scoped dependencies."""
        # Keep all mutable state scoped to this instance and its config entry.
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Do not present an old alarm as current because another metric updates."""
        return super().available and self.coordinator.client.is_alarm_fresh(
            self._device_id, self.entity_description.payload_key
        )

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed boolean/alarm state, or unknown if unavailable."""
        # Use current confirmed state so rejected settings never appear successful.
        device = self.device
        if device is None:
            return None
        if any(
            alarm.metric == self.entity_description.payload_key
            for alarm in device.active_alarms
        ):
            return True
        if self.entity_description.payload_key in device.uncertain_alarm_metrics:
            return None
        status = self._measurement_status
        if status is None:
            return False
        return status.casefold() not in _NON_ALARM_STATUSES

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the current Protect classification and optional alarm value."""
        # Return only attributes relevant to the latest confirmed measurement.
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
        """Read a nonempty status from the current metric payload."""
        # Missing or scalar measurements do not provide a confirmed alarm status.
        device = self.device
        air_quality = device.raw.get("airQuality") if device else None
        if not isinstance(air_quality, dict):
            return None
        measurement = air_quality.get(self.entity_description.payload_key)
        if not isinstance(measurement, dict):
            return None
        status = measurement.get("status")
        return status if isinstance(status, str) and status else None
