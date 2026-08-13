"""UniFi Protect Air Quality integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PrivateProtectClient
from .const import CONF_VERIFY_SSL, DOMAIN, MANUFACTURER
from .coordinator import UnifiAirQualityCoordinator

PLATFORMS = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


@dataclass(slots=True)
class UnifiAirQualityRuntimeData:
    """Objects belonging to one config entry."""

    client: PrivateProtectClient
    coordinator: UnifiAirQualityCoordinator


type UnifiAirQualityConfigEntry = ConfigEntry[UnifiAirQualityRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: UnifiAirQualityConfigEntry
) -> bool:
    """Set up UniFi Protect Air Quality from a config entry."""
    client = PrivateProtectClient(
        async_get_clientsession(hass),
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=entry.data[CONF_VERIFY_SSL],
    )
    coordinator = UnifiAirQualityCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = UnifiAirQualityRuntimeData(client, coordinator)

    registry = dr.async_get(hass)
    for device in coordinator.data.devices:
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.id)},
            manufacturer=MANUFACTURER,
            model=device.model,
            name=device.name,
            sw_version=device.firmware_version,
        )

    client.start_websocket()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: UnifiAirQualityConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.client.async_close()
    return True
