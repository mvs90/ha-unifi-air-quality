"""Configuration switches for UniFi Protect Air Quality."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity


@dataclass(frozen=True, kw_only=True)
class AirQualitySwitchDescription(SwitchEntityDescription):
    """Describe a writable boolean setting."""

    path: tuple[str, ...]


_ALERT_SETTINGS = {
    "aqi_alerts": "aqiSettings",
    "co2_alerts": "co2Settings",
    "humidity_alerts": "humiditySettings",
    "pm1_alerts": "pm1p0Settings",
    "pm25_alerts": "pm2p5Settings",
    "pm4_alerts": "pm4p0Settings",
    "pm10_alerts": "pm10p0Settings",
    "temperature_alerts": "temperatureSettings",
    "tvoc_alerts": "tvocSettings",
    "voc_alerts": "vocSettings",
    "vape_alerts": "vapeSettings",
}

SWITCH_DESCRIPTIONS: tuple[AirQualitySwitchDescription, ...] = (
    AirQualitySwitchDescription(
        key="status_light",
        translation_key="status_light",
        icon="mdi:led-on",
        entity_category=EntityCategory.CONFIG,
        path=("ledSettings", "isEnabled"),
    ),
    AirQualitySwitchDescription(
        key="activity_feedback",
        translation_key="activity_feedback",
        icon="mdi:led-outline",
        entity_category=EntityCategory.CONFIG,
        path=("ledSettings", "activityFeedback"),
    ),
    AirQualitySwitchDescription(
        key="night_mode",
        translation_key="night_mode",
        icon="mdi:weather-night",
        entity_category=EntityCategory.CONFIG,
        path=("airQualitySettings", "nightModeEnabled"),
    ),
    AirQualitySwitchDescription(
        key="vape_detection",
        translation_key="vape_detection",
        icon="mdi:smoke-detector-variant",
        entity_category=EntityCategory.CONFIG,
        path=("airQualitySettings", "vapeSensitivitySettings", "isEnabled"),
    ),
    *(
        AirQualitySwitchDescription(
            key=key,
            translation_key=key,
            icon="mdi:alert-outline",
            entity_category=EntityCategory.CONFIG,
            path=("airQualitySettings", settings_key, "isEnabled"),
        )
        for key, settings_key in _ALERT_SETTINGS.items()
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable switches."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        AirQualitySwitch(coordinator, device.id, description)
        for device in coordinator.data.devices
        for description in SWITCH_DESCRIPTIONS
    )


class AirQualitySwitch(UnifiAirQualityEntity, SwitchEntity):
    """Represent one writable Protect boolean setting."""

    entity_description: AirQualitySwitchDescription

    def __init__(
        self,
        coordinator: UnifiAirQualityCoordinator,
        device_id: str,
        description: AirQualitySwitchDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        value = self.raw_value(self.entity_description.path)
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: object) -> None:
        await self.async_write_value(self.entity_description.path, True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self.async_write_value(self.entity_description.path, False)
