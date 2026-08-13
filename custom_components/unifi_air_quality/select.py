"""Configuration selects for UniFi Protect Air Quality."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity

LED_METRIC_VALUES = {"carbon_dioxide": 0, "air_quality": 1}

LED_METRIC_DESCRIPTION = SelectEntityDescription(
    key="ring_led_metric",
    translation_key="ring_led_metric",
    translation_placeholders={},
    icon="mdi:led-on",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ring LED metric selects."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        RingLedMetricSelect(coordinator, device.id)
        for device in coordinator.data.devices
    )


class RingLedMetricSelect(UnifiAirQualityEntity, SelectEntity):
    """Choose which metric the colored LED ring displays."""

    entity_description = LED_METRIC_DESCRIPTION
    _attr_options = list(LED_METRIC_VALUES)

    def __init__(self, coordinator: UnifiAirQualityCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, LED_METRIC_DESCRIPTION.key)

    @property
    def current_option(self) -> str | None:
        value = self.raw_value(("airQualitySettings", "ringLedMetric"))
        return next(
            (option for option, raw in LED_METRIC_VALUES.items() if raw == value), None
        )

    async def async_select_option(self, option: str) -> None:
        await self.async_write_value(
            ("airQualitySettings", "ringLedMetric"), LED_METRIC_VALUES[option]
        )
