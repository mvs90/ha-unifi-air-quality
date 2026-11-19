"""Alarm state entities for UniFi Protect Air Quality."""

from __future__ import annotations

from base64 import b64encode
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
from .const import MEASUREMENT_ALIASES
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity, async_setup_dynamic_entities

_NON_ALARM_STATUSES = frozenset({"good", "neutral", "normal", "safe"})

# The device view does not enable state colors for binary-sensor icons. Native
# entity pictures keep confirmed states colored in that view too.
# The transparent 40px canvas preserves the size of a normal icon in a HA badge.
_NORMAL_ALARM_PICTURE = "data:image/svg+xml;base64," + b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    b'<circle cx="20" cy="20" r="10" fill="#2e7d32"/>'
    b'<path d="M14.5 20 18.3 23.8 25.5 16.6" fill="none" stroke="#fff" '
    b'stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/>'
    b"</svg>"
).decode("ascii")
_ACTIVE_ALARM_PICTURE = "data:image/svg+xml;base64," + b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    b'<circle cx="20" cy="20" r="10" fill="#c62828"/>'
    b'<path d="M20 14.5v6" fill="none" stroke="#fff" '
    b'stroke-width="2.3" stroke-linecap="round"/>'
    b'<circle cx="20" cy="24.5" r="1.25" fill="#fff"/>'
    b"</svg>"
).decode("ascii")


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
    def icon(self) -> str | None:
        """Show a question mark only for an available but unknown alarm state."""
        # The default problem icon is a check even for unknown; do not imply OK.
        # Leave unavailable and confirmed states to their existing presentation.
        if self.available and self.is_on is None:
            return "mdi:help-circle"
        return None

    @property
    def entity_picture(self) -> str | None:
        """Show green OK or red alarm pictures only for confirmed, fresh states."""
        # Unknown or unavailable data must not retain a confirmed-state picture.
        if not self.available:
            return None
        state = self.is_on
        if state is False:
            return _NORMAL_ALARM_PICTURE
        if state is True:
            return _ACTIVE_ALARM_PICTURE
        return None

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
        key = self.entity_description.payload_key
        measurement = air_quality.get(key)
        if key not in air_quality:
            measurement = air_quality.get(MEASUREMENT_ALIASES.get(key))
        if not isinstance(measurement, dict):
            return None
        status = measurement.get("status")
        return status if isinstance(status, str) and status else None
