"""Configuration switches for UniFi Protect Air Quality."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity, async_setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class AirQualitySwitchDescription(SwitchEntityDescription):
    """Describe a writable boolean setting."""

    path: tuple[str, ...]
    is_alarm: bool = False


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
    "nox_alerts": "noxSettings",
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
            entity_registry_enabled_default=key != "nox_alerts",
            icon="mdi:alert-outline",
            entity_category=EntityCategory.CONFIG,
            path=("airQualitySettings", settings_key, "isEnabled"),
            is_alarm=True,
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
    async_setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda device_id: [
            *(
                AirQualitySwitch(coordinator, device_id, description)
                for description in SWITCH_DESCRIPTIONS
            ),
            RingLedSwitch(coordinator, device_id),
        ],
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
        """Initialize this component with its entry-scoped dependencies."""
        # Keep all mutable state scoped to this instance and its config entry.
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed boolean/alarm state, or unknown if unavailable."""
        # Use current confirmed state so rejected settings never appear successful.
        value = self.raw_value(self.entity_description.path)
        if not isinstance(value, bool):
            return None
        if not self.entity_description.is_alarm:
            return value
        settings_path = self.entity_description.path[:-1]
        low = self.raw_value((*settings_path, "lowThreshold"))
        high = self.raw_value((*settings_path, "highThreshold"))
        return value and (low is not None or high is not None)

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable this setting using the last confirmed device configuration."""
        # Only submit a Protect write after validating the control's prerequisites.
        if self.entity_description.is_alarm:
            settings_path = self.entity_description.path[:-1]
            low = self.raw_value((*settings_path, "lowThreshold"))
            high = self.raw_value((*settings_path, "highThreshold"))
            if low is None and high is None:
                raise HomeAssistantError(
                    "Set at least one threshold before enabling this alarm"
                )
        await self.async_write_value(self.entity_description.path, True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable this setting while retaining the required device semantics."""
        # Preserve firmware-specific thresholds or remembered brightness where required.
        if self.entity_description.is_alarm:
            settings_path = self.entity_description.path[:-1]
            if settings_path[-1] == "vapeSettings":
                await self.async_write_value(self.entity_description.path, False)
                return
            await self.async_write_update(
                {
                    "airQualitySettings": {
                        settings_path[-1]: {
                            "isEnabled": False,
                            "lowThreshold": None,
                            "highThreshold": None,
                        }
                    }
                },
            )
            return
        await self.async_write_value(self.entity_description.path, False)


class RingLedSwitch(UnifiAirQualityEntity, SwitchEntity):
    """Turn the LED ring off without removing its brightness control."""

    _attr_translation_key = "ring_led"
    _attr_icon = "mdi:circle-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: UnifiAirQualityCoordinator, device_id: str) -> None:
        """Initialize this component with its entry-scoped dependencies."""
        # Keep all mutable state scoped to this instance and its config entry.
        super().__init__(coordinator, device_id, "ring_led")
        self._brightness_option_key = f"ring_led_previous_brightness_{device_id}"
        brightness = self.raw_value(("airQualitySettings", "ringLedBrightness"))
        saved_brightness = coordinator.config_entry.options.get(
            self._brightness_option_key
        )
        self._last_brightness = (
            int(brightness)
            if isinstance(brightness, (int, float)) and brightness > 0
            else int(saved_brightness)
            if isinstance(saved_brightness, (int, float))
            and 0 < saved_brightness <= 100
            else 100
        )

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed boolean/alarm state, or unknown if unavailable."""
        # Use current confirmed state so rejected settings never appear successful.
        brightness = self.raw_value(("airQualitySettings", "ringLedBrightness"))
        if isinstance(brightness, bool) or not isinstance(brightness, (int, float)):
            return None
        if brightness > 0:
            self._last_brightness = int(brightness)
            return True
        return False

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable this setting using the last confirmed device configuration."""
        # Only submit a Protect write after validating the control's prerequisites.
        await self.async_write_value(
            ("airQualitySettings", "ringLedBrightness"), self._last_brightness
        )

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable this setting while retaining the required device semantics."""
        # Preserve firmware-specific thresholds or remembered brightness where required.
        brightness = self.raw_value(("airQualitySettings", "ringLedBrightness"))
        if isinstance(brightness, (int, float)) and not isinstance(brightness, bool):
            if brightness > 0:
                self._last_brightness = int(brightness)
                options = {
                    **self.coordinator.config_entry.options,
                    self._brightness_option_key: self._last_brightness,
                }
                self.coordinator.hass.config_entries.async_update_entry(
                    self.coordinator.config_entry, options=options
                )
        await self.async_write_value(("airQualitySettings", "ringLedBrightness"), 0)
