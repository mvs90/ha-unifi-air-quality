"""Shared entities for UniFi Protect Air Quality."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import (
    EVENT_DEVICE_REGISTRY_UPDATED,
    DeviceInfo,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ProtectApiError
from .const import DOMAIN, MANUFACTURER
from .coordinator import UnifiAirQualityCoordinator
from .models import AirQualityDevice

if TYPE_CHECKING:
    from . import UnifiAirQualityConfigEntry


@callback
def async_setup_dynamic_entities(
    entry: UnifiAirQualityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Reconcile each platform using its existing coordinator subscription.

    Keep entities on removal so registry names, IDs and history survive re-pairing.
    Explicit UI removal forgets the submitted identity so re-adoption can create
    entities again. Ordinary Protect disappearance keeps registry metadata.
    """
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def discover() -> None:
        """Submit only newly observed devices, including after an empty bootstrap."""
        new_ids = {device.id for device in coordinator.data.devices} - known
        # Reserve IDs before adding: entity callbacks may trigger another update.
        known.update(new_ids)
        if new_ids:
            async_add_entities(
                [
                    entity
                    for device_id in sorted(new_ids)
                    for entity in factory(device_id)
                ]
            )

    @callback
    def forget_removed(event: Event) -> None:
        """Forget only devices explicitly removed from this entry's HA registry."""
        if event.data["action"] != "remove":
            return
        removed = event.data["device"]
        if removed["config_entry_id"] != entry.entry_id:
            return
        known.difference_update(
            identifier
            for domain, identifier in removed["identifiers"]
            if domain == DOMAIN
        )

    entry.async_on_unload(
        coordinator.hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, forget_removed)
    )
    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()


def nested_value(
    data: dict[str, Any], path: tuple[str, ...], *, default: Any = None
) -> Any:
    """Read a nested value, distinguishing absent fields when a default is given."""
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def nested_update(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """Build a minimal nested private-API PATCH body."""
    update: Any = value
    for key in reversed(path):
        update = {key: update}
    return update


class UnifiAirQualityEntity(CoordinatorEntity[UnifiAirQualityCoordinator]):
    """Base entity tied to one UP-AirQuality device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: UnifiAirQualityCoordinator,
        device_id: str,
        entity_key: str,
    ) -> None:
        """Initialize this component with its entry-scoped dependencies."""
        # Keep all mutable state scoped to this instance and its config entry.
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{entity_key}"

        device = self.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=device.model if device else "UP-AirQuality",
            name=device.name if device else "UP Air Quality",
            sw_version=device.firmware_version if device else None,
        )

    @property
    def device(self) -> AirQualityDevice | None:
        """Return the current device snapshot."""
        return next(
            (
                device
                for device in self.coordinator.data.devices
                if device.id == self._device_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        """Require coordinator health, device connectivity and verified freshness."""
        # A cached isConnected flag alone cannot prove readings are still current.
        device = self.device
        return (
            super().available
            and device is not None
            and device.is_connected
            and self.coordinator.client.is_device_fresh(self._device_id)
        )

    def raw_value(self, path: tuple[str, ...], *, default: Any = None) -> Any:
        """Read a setting while preserving an explicit default for absent data."""
        device = self.device
        return nested_value(device.raw, path, default=default) if device else default

    async def async_write_value(self, path: tuple[str, ...], value: Any) -> None:
        """Write one setting through the private API adapter."""
        await self.async_write_update(nested_update(path, value))

    async def async_write_update(self, update: dict[str, Any]) -> None:
        """Expose actionable, sanitized control errors to Home Assistant."""
        try:
            await self.coordinator.client.async_update_device(self._device_id, update)
        except ProtectApiError as err:
            raise HomeAssistantError(str(err)) from err
