"""Night-mode time controls for UniFi Protect Air Quality."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity, async_setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class AirQualityTimeDescription(TimeEntityDescription):
    """Describe a writable HH:MM setting."""

    path: tuple[str, ...]


TIME_DESCRIPTIONS = (
    AirQualityTimeDescription(
        key="night_mode_start",
        translation_key="night_mode_start",
        icon="mdi:weather-night-partly-cloudy",
        entity_category=EntityCategory.CONFIG,
        path=("airQualitySettings", "nightModeStartTime"),
    ),
    AirQualityTimeDescription(
        key="night_mode_end",
        translation_key="night_mode_end",
        icon="mdi:weather-sunset-up",
        entity_category=EntityCategory.CONFIG,
        path=("airQualitySettings", "nightModeEndTime"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up night-mode time controls."""
    coordinator = entry.runtime_data.coordinator
    async_setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda device_id: (
            AirQualityTime(coordinator, device_id, description)
            for description in TIME_DESCRIPTIONS
        ),
    )


class AirQualityTime(UnifiAirQualityEntity, TimeEntity):
    """Represent one writable Protect night-mode time."""

    entity_description: AirQualityTimeDescription

    def __init__(
        self,
        coordinator: UnifiAirQualityCoordinator,
        device_id: str,
        description: AirQualityTimeDescription,
    ) -> None:
        """Bind the night-mode start or end setting to its stable entity ID."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> time | None:
        """Parse the stored ISO time string, returning None for invalid data."""
        value = self.raw_value(self.entity_description.path)
        if not isinstance(value, str):
            return None
        try:
            return time.fromisoformat(value)
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        """Write a night-mode time using Protect's HH:MM representation."""
        # Drop seconds and timezone information; this is a device schedule clock time.
        await self.async_write_value(
            self.entity_description.path, value.strftime("%H:%M")
        )
