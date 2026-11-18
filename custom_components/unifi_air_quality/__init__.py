"""UniFi Protect Air Quality integration."""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import ClientSession, DummyCookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import PrivateProtectClient, ProtectApiError, reject_protect_redirects
from .const import CONF_VERIFY_SSL, DOMAIN, MANUFACTURER
from .coordinator import UnifiAirQualityCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
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
    # Only generation-checked adapter headers own authentication cookies; an old
    # response must not overwrite a renewed session through automatic storage.
    session = async_create_clientsession(
        hass,
        cookie_jar=DummyCookieJar(),
        middlewares=(reject_protect_redirects,),
    )
    try:
        return await _async_setup_entry(hass, entry, session)
    except BaseException:
        session.detach()
        raise


async def _async_setup_entry(
    hass: HomeAssistant, entry: UnifiAirQualityConfigEntry, session: ClientSession
) -> bool:
    """Set up one entry, transferring ownership of its isolated session."""
    client = PrivateProtectClient(
        session,
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

    @callback
    def register_devices() -> None:
        """Refresh device metadata without deleting entries for removed sensors."""
        for device in coordinator.data.devices:
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, device.id)},
                manufacturer=MANUFACTURER,
                model=device.model,
                name=device.name,
                sw_version=device.firmware_version,
            )

    entry.async_on_unload(coordinator.async_add_listener(register_devices))
    register_devices()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_health_monitor()
    client.start_websocket()
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: UnifiAirQualityConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.client.async_close()
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: UnifiAirQualityConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow explicit cleanup only after Protect confirms this sensor is unpaired.

    The coordinator may retain a recent snapshot during network errors. Fetch
    directly so an outage, an unsupported inventory, or cached stream removal
    can never authorize deleting the user's registry metadata.
    """
    if device_entry.config_entry_id != entry.entry_id:
        return False
    device_ids = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    }
    runtime = getattr(entry, "runtime_data", None)
    if not device_ids or runtime is None:
        return False
    try:
        snapshot = await runtime.client.async_get_snapshot()
    except ProtectApiError:
        return False
    if snapshot.console_id != entry.unique_id:
        return False
    runtime.coordinator.async_set_updated_data(snapshot)
    return device_ids.isdisjoint(device.id for device in snapshot.devices)
