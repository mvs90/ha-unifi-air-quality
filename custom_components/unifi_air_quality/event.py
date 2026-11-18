"""Alarm transition events for UniFi Protect Air Quality."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAirQualityConfigEntry
from .binary_sensor import ALARM_DESCRIPTIONS
from .coordinator import UnifiAirQualityCoordinator
from .entity import UnifiAirQualityEntity, async_setup_dynamic_entities
from .models import ProtectAlarmEvent

ALARM_EVENT_TYPES = tuple(
    f"{description.payload_key}_alarm_{transition}"
    for description in ALARM_DESCRIPTIONS
    for transition in ("started", "ended")
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one event stream entity for every discovered device."""
    coordinator = entry.runtime_data.coordinator
    async_setup_dynamic_entities(
        entry,
        async_add_entities,
        lambda device_id: (UnifiAirQualityAlarmEvent(coordinator, device_id),),
    )


class UnifiAirQualityAlarmEvent(UnifiAirQualityEntity, EventEntity):
    """Expose momentary Protect alarm transitions to automations."""

    _attr_translation_key = "air_quality_alarm"
    _attr_event_types = ALARM_EVENT_TYPES

    def __init__(self, coordinator: UnifiAirQualityCoordinator, device_id: str) -> None:
        """Initialize this component with its entry-scoped dependencies."""
        # Keep all mutable state scoped to this instance and its config entry.
        super().__init__(coordinator, device_id, "air_quality_alarm_event")

    async def async_added_to_hass(self) -> None:
        """Subscribe only while the entity belongs to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.client.subscribe_alarm_events(self._handle_alarm_event)
        )

    @callback
    def _handle_alarm_event(self, event: ProtectAlarmEvent) -> None:
        """Expose supported normalized alarm transitions for this device."""
        # Filter by device and public event type before triggering HA automations.
        if event.device_id != self._device_id:
            return
        event_type = f"{event.metric}_alarm_{event.transition}"
        if event_type not in self.event_types:
            return
        data: dict[str, Any] = {"metric": event.metric}
        if event.status is not None:
            data["status"] = event.status
        if event.value is not None:
            data["value"] = event.value
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
