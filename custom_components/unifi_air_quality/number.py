"""Configuration numbers for UniFi Protect Air Quality."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDensity,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity


@dataclass(frozen=True, kw_only=True)
class AirQualityNumberDescription(NumberEntityDescription):
    """Describe a writable numeric setting."""

    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ThresholdMetric:
    """Metadata for one pair of alarm thresholds."""

    key: str
    settings_key: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None


THRESHOLD_METRICS = (
    ThresholdMetric("aqi", "aqiSettings", 0, 500, 1),
    ThresholdMetric("co2", "co2Settings", 0, 40000, 10, UnitOfRatio.PARTS_PER_MILLION),
    ThresholdMetric("humidity", "humiditySettings", 0, 100, 1, PERCENTAGE),
    ThresholdMetric(
        "pm1", "pm1p0Settings", 0, 1000, 1, UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
    ),
    ThresholdMetric(
        "pm25", "pm2p5Settings", 0, 1000, 1, UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
    ),
    ThresholdMetric(
        "pm4", "pm4p0Settings", 0, 1000, 1, UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
    ),
    ThresholdMetric(
        "pm10", "pm10p0Settings", 0, 1000, 1, UnitOfDensity.MICROGRAMS_PER_CUBIC_METER
    ),
    ThresholdMetric(
        "temperature", "temperatureSettings", -20, 60, 1, UnitOfTemperature.CELSIUS
    ),
    ThresholdMetric("tvoc", "tvocSettings", 0, 1000, 1),
    ThresholdMetric("voc", "vocSettings", 0, 500, 1),
    ThresholdMetric("vape", "vapeSettings", 0, 100, 1),
)

NUMBER_DESCRIPTIONS: tuple[AirQualityNumberDescription, ...] = (
    AirQualityNumberDescription(
        key="ring_led_brightness",
        translation_key="ring_led_brightness",
        icon="mdi:brightness-6",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        path=("airQualitySettings", "ringLedBrightness"),
    ),
    AirQualityNumberDescription(
        key="night_mode_brightness",
        translation_key="night_mode_brightness",
        icon="mdi:brightness-3",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        path=("airQualitySettings", "nightModeBrightness"),
    ),
    AirQualityNumberDescription(
        key="vape_sensitivity",
        translation_key="vape_sensitivity",
        icon="mdi:tune-variant",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        path=("airQualitySettings", "vapeSensitivitySettings", "sensitivity"),
    ),
    *(
        AirQualityNumberDescription(
            key=f"{metric.key}_{bound}_threshold",
            translation_key=f"{metric.key}_{bound}_threshold",
            icon="mdi:tune-variant",
            entity_category=EntityCategory.CONFIG,
            native_min_value=metric.minimum,
            native_max_value=metric.maximum,
            native_step=metric.step,
            native_unit_of_measurement=metric.unit,
            mode=NumberMode.BOX,
            path=(
                "airQualitySettings",
                metric.settings_key,
                f"{bound}Threshold",
            ),
        )
        for metric in THRESHOLD_METRICS
        for bound in ("low", "high")
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up writable numeric settings."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        AirQualityNumber(coordinator, device.id, description)
        for device in coordinator.data.devices
        for description in NUMBER_DESCRIPTIONS
    )


class AirQualityNumber(UnifiAirQualityEntity, NumberEntity):
    """Represent one writable Protect numeric setting."""

    entity_description: AirQualityNumberDescription

    def __init__(
        self,
        coordinator: UnifiAirQualityCoordinator,
        device_id: str,
        description: AirQualityNumberDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = self.raw_value(self.entity_description.path)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        normalized: int | float = int(value) if value.is_integer() else value
        await self.async_write_value(self.entity_description.path, normalized)
