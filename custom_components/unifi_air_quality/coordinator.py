"""Publish Protect snapshots and enforce availability and refresh deadlines."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
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
    """Combine push updates with periodic full inventories and freshness checks."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PrivateProtectClient,
    ) -> None:
        """Bind one entry's client callbacks to Home Assistant update handling."""
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=BOOTSTRAP_REFRESH_INTERVAL,
            always_update=False,
        )
        self.client = client
        self._reauth_requested = False
        self._last_refresh_attempt = monotonic()
        self._health_refresh_task: asyncio.Task | None = None
        client.set_update_callback(self._handle_push_update)
        client.set_auth_failure_callback(self._handle_auth_failure)

    @callback
    def async_start_health_monitor(self) -> None:
        """Check freshness every five seconds until this entry is unloaded."""
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass, self._check_freshness, timedelta(seconds=5)
            )
        )

    @callback
    def _check_freshness(self, _now) -> None:
        """Notify entities of elapsed deadlines and schedule an overdue resync."""
        self.async_update_listeners()
        # Pushes reset HA's ordinary polling timer. Keep the safety resync bounded.
        last_success = self.client.last_successful_refresh
        last_attempt = max(self._last_refresh_attempt, last_success or 0)
        if (
            not self.client.authentication_failed
            and monotonic() - last_attempt >= BOOTSTRAP_REFRESH_INTERVAL.total_seconds()
            and (self._health_refresh_task is None or self._health_refresh_task.done())
        ):
            self._health_refresh_task = self.config_entry.async_create_background_task(
                self.hass, self.async_refresh(), "Protect safety resync"
            )

    @callback
    def _handle_auth_failure(self) -> None:
        """Report terminal authentication failure and request credentials once."""
        self.async_set_update_error(
            ConfigEntryAuthFailed("Protect authentication failed")
        )
        if not self._reauth_requested:
            self._reauth_requested = True
            self.config_entry.async_start_reauth(self.hass)

    async def _async_update_data(self) -> ProtectSnapshot:
        """Refresh state, retaining cached data only during brief network failures.

        Authentication rejection requests reauthentication; incompatible data
        raises UpdateFailed immediately. Returning a cached snapshot does not
        renew individual measurement or alarm freshness timestamps.
        """
        # Brief network outages retain fresh data; protocol/auth errors fail closed.
        self._last_refresh_attempt = monotonic()
        try:
            return await self.client.async_get_snapshot()
        except ProtectInvalidAuth as err:
            raise ConfigEntryAuthFailed("Protect authentication failed") from err
        except ProtectProtocolError as err:
            self.client.protocol_error = True
            raise UpdateFailed(
                "Unsupported Protect response. Review diagnostics and firmware "
                "versions before upgrading or reporting a compatibility issue."
            ) from err
        except ProtectCannotConnect as err:
            # Brief failures retain verified readings only until their own deadline.
            if self.data is not None and any(
                self.client.is_device_fresh(device.id) for device in self.data.devices
            ):
                return self.data
            raise UpdateFailed("Unable to update Protect Air Quality data") from err

    @callback
    def _handle_push_update(self, snapshot: ProtectSnapshot) -> None:
        """Publish a valid push or expose an actionable protocol failure."""
        # An unsupported schema must never restore stale data to an available state.
        if self.client.authentication_failed:
            self._handle_auth_failure()
            return
        if self.client.protocol_error:
            self.async_set_update_error(
                UpdateFailed(
                    "Unsupported Protect protocol; review diagnostics "
                    "and firmware versions"
                )
            )
            return
        self.async_set_updated_data(snapshot)
