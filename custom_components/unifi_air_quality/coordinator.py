"""Update coordinator for UniFi Protect Air Quality."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PrivateProtectClient,
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectProtocolError,
)
from .const import BOOTSTRAP_REFRESH_INTERVAL, DOMAIN
from .models import ProtectSnapshot

_LOGGER = logging.getLogger(__name__)


class UnifiAirQualityCoordinator(DataUpdateCoordinator[ProtectSnapshot]):
    """Combine WebSocket pushes with an infrequent bootstrap safety refresh."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PrivateProtectClient,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=BOOTSTRAP_REFRESH_INTERVAL,
            always_update=False,
        )
        self.client = client
        client.set_update_callback(self._handle_push_update)

    async def _async_update_data(self) -> ProtectSnapshot:
        try:
            return await self.client.async_get_snapshot()
        except ProtectInvalidAuth as err:
            raise ConfigEntryAuthFailed("Protect authentication failed") from err
        except (ProtectCannotConnect, ProtectProtocolError) as err:
            raise UpdateFailed("Unable to update Protect Air Quality data") from err

    @callback
    def _handle_push_update(self, snapshot: ProtectSnapshot) -> None:
        self.async_set_updated_data(snapshot)
