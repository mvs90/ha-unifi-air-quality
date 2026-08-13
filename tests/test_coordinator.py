"""Tests for coordinator error mapping and push updates."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_air_quality.api import (
    ProtectCannotConnect,
    ProtectInvalidAuth,
)
from custom_components.unifi_air_quality.coordinator import UnifiAirQualityCoordinator
from custom_components.unifi_air_quality.models import ProtectSnapshot


def _snapshot() -> ProtectSnapshot:
    return ProtectSnapshot("console", "Protect", "6.2", "update", ())


async def test_coordinator_update_and_push(hass) -> None:
    client = MagicMock()
    client.async_get_snapshot = AsyncMock(return_value=_snapshot())
    coordinator = UnifiAirQualityCoordinator(hass, MockConfigEntry(), client)

    assert await coordinator._async_update_data() == _snapshot()
    coordinator._handle_push_update(_snapshot())
    assert coordinator.data == _snapshot()
    client.set_update_callback.assert_called_once()


async def test_coordinator_maps_errors(hass) -> None:
    client = MagicMock()
    coordinator = UnifiAirQualityCoordinator(hass, MockConfigEntry(), client)
    client.async_get_snapshot = AsyncMock(side_effect=ProtectInvalidAuth())
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    client.async_get_snapshot = AsyncMock(side_effect=ProtectCannotConnect())
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
