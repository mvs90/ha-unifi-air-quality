"""Shared entities for UniFi Protect Air Quality."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import UnifiAirQualityCoordinator
from .models import AirQualityDevice


def nested_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Read a value from a nested private-API payload."""
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
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
        device = self.device
        return super().available and device is not None and device.is_connected

    def raw_value(self, path: tuple[str, ...]) -> Any:
        """Read a raw setting from the current device."""
        device = self.device
        return nested_value(device.raw, path) if device else None

    async def async_write_value(self, path: tuple[str, ...], value: Any) -> None:
        """Write one setting through the private API adapter."""
        await self.coordinator.client.async_update_device(
            self._device_id, nested_update(path, value)
        )
