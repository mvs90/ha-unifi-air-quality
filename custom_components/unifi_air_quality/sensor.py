"""Measurement entities with independent freshness and Protect classifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDensity,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .const import MEASUREMENT_ALIASES
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity, async_setup_dynamic_entities


@dataclass(frozen=True, kw_only=True)
class UnifiAirQualitySensorEntityDescription(SensorEntityDescription):
    """Describe a Protect Air Quality measurement."""

    payload_key: str


SENSOR_DESCRIPTIONS: tuple[UnifiAirQualitySensorEntityDescription, ...] = (
    UnifiAirQualitySensorEntityDescription(
        key="aqi",
        translation_key="aqi",
        payload_key="aqi",
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="co2",
        translation_key="co2",
        payload_key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        payload_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        payload_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="pm1",
        translation_key="pm1",
        payload_key="pm1p0",
        device_class=SensorDeviceClass.PM1,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="pm25",
        translation_key="pm25",
        payload_key="pm2p5",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="pm4",
        translation_key="pm4",
        payload_key="pm4p0",
        device_class=SensorDeviceClass.PM4,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="pm10",
        translation_key="pm10",
        payload_key="pm10p0",
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="tvoc",
        translation_key="tvoc",
        payload_key="tvoc",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="voc_index",
        translation_key="voc_index",
        payload_key="voc",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="nox_index",
        translation_key="nox_index",
        payload_key="nox",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    UnifiAirQualitySensorEntityDescription(
        key="vape_index",
        translation_key="vape_index",
        payload_key="vape",
        state_class=SensorStateClass.MEASUREMENT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every discovered UP-AirQuality device."""
    coordinator = entry.runtime_data.coordinator
    async_setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda device_id: (
            UnifiAirQualitySensor(coordinator, device_id, description)
            for description in SENSOR_DESCRIPTIONS
        ),
    )


class UnifiAirQualitySensor(UnifiAirQualityEntity, SensorEntity):
    """Represent one measurement from a UP-AirQuality device."""

    entity_description: UnifiAirQualitySensorEntityDescription

    def __init__(
        self,
        coordinator: UnifiAirQualityCoordinator,
        device_id: str,
        description: UnifiAirQualitySensorEntityDescription,
    ) -> None:
        """Bind a measurement description to this device's stable sensor ID."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Require a recent value for this metric, not just activity on the device."""
        return super().available and self.coordinator.client.is_measurement_fresh(
            self._device_id, self.entity_description.payload_key
        )

    @property
    def native_value(self) -> int | float | None:
        """Return the cached numeric reading, excluding booleans and absent values."""
        # Check wire types before converting to the Home Assistant representation.
        measurement = self._measurement
        value = measurement.get("value") if measurement else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose the measurement's Protect status string when supplied."""
        measurement = self._measurement
        status = measurement.get("status") if measurement else None
        return {"protect_status": status} if isinstance(status, str) else None

    @property
    def _measurement(self) -> dict[str, Any] | None:
        """Read the metric from the scalar or structured protocol variant."""
        # Keep scalar support while retaining classifications on structured readings.
        device = self.device
        air_quality = device.raw.get("airQuality") if device else None
        if not isinstance(air_quality, dict):
            return None
        metric = self.entity_description.payload_key
        measurement = air_quality.get(metric)
        if metric not in air_quality and metric in MEASUREMENT_ALIASES:
            measurement = air_quality.get(MEASUREMENT_ALIASES[metric])
        if isinstance(measurement, (int, float)) and not isinstance(measurement, bool):
            # The original wire variant sends scalar readings without a status.
            return {"value": measurement}
        return measurement if isinstance(measurement, dict) else None
