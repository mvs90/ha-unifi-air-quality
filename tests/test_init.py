"""Tests for config-entry setup and unloading."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import device_registry as dr
from yarl import URL

from custom_components.unifi_air_quality import async_setup_entry, async_unload_entry
from custom_components.unifi_air_quality.const import CONF_VERIFY_SSL, DOMAIN
from custom_components.unifi_air_quality.models import AirQualityDevice, ProtectSnapshot

from .ha_test_helpers import MockConfigEntry


async def test_setup_and_unload_entry(hass, device_registry) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "protect.local",
            CONF_PORT: 443,
            CONF_USERNAME: "user",
            CONF_PASSWORD: "not-real",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)
    device = AirQualityDevice(
        "sensor-id",
        "Air Quality",
        "UP-AirQuality",
        "1.0.0",
        True,
        {"id": "sensor-id"},
    )
    snapshot = ProtectSnapshot("console", "Protect", "6.2", "update", (device,))

    with (
        patch(
            "custom_components.unifi_air_quality.async_create_clientsession"
        ) as get_session,
        patch("custom_components.unifi_air_quality.PrivateProtectClient") as client_cls,
        patch(
            "custom_components.unifi_air_quality.UnifiAirQualityCoordinator"
        ) as coordinator_cls,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ) as forward_setups,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ) as unload_platforms,
    ):
        coordinator = coordinator_cls.return_value
        coordinator.data = snapshot
        coordinator.async_config_entry_first_refresh = AsyncMock()
        client = client_cls.return_value
        client.async_close = AsyncMock()

        assert await async_setup_entry(hass, entry)
        get_session.assert_called_once()
        assert get_session.call_args.args == (hass,)
        jar = get_session.call_args.kwargs["cookie_jar"]
        jar.update_cookies({"synthetic": "cookie"}, URL("https://127.0.0.1"))
        assert jar.filter_cookies(URL("https://127.0.0.1"))
        client.start_websocket.assert_called_once()
        forward_setups.assert_awaited_once()
        assert entry.runtime_data.coordinator is coordinator

        registered = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        assert len(registered) == 1
        assert registered[0].identifiers == {(DOMAIN, "sensor-id")}
        assert registered[0].model == "UP-AirQuality"

        assert await async_unload_entry(hass, entry)
        unload_platforms.assert_awaited_once()
        client.async_close.assert_awaited_once()


async def test_unload_stops_when_platform_unload_fails(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    client = AsyncMock()
    entry.runtime_data = type("RuntimeData", (), {"client": client})()

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        assert not await async_unload_entry(hass, entry)

    client.async_close.assert_not_awaited()


@pytest.mark.parametrize("failure", [RuntimeError(), asyncio.CancelledError()])
async def test_setup_failure_closes_dedicated_session(hass, failure):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "protect.local",
            CONF_PORT: 443,
            CONF_USERNAME: "synthetic",
            CONF_PASSWORD: "synthetic",
            CONF_VERIFY_SSL: False,
        },
    )
    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    session = async_create_clientsession(hass)
    with (
        patch(
            "custom_components.unifi_air_quality.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.unifi_air_quality.PrivateProtectClient",
            side_effect=failure,
        ),
    ):
        with pytest.raises(type(failure)):
            await async_setup_entry(hass, entry)
    assert session.closed


@pytest.mark.parametrize("failure", [RuntimeError(), asyncio.CancelledError()])
async def test_refresh_failure_closes_session_before_setup_retry(hass, failure):
    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    from .test_config_flow import USER_INPUT

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT.copy())
    session = async_create_clientsession(hass)
    with (
        patch(
            "custom_components.unifi_air_quality.async_create_clientsession",
            return_value=session,
        ),
        patch("custom_components.unifi_air_quality.PrivateProtectClient") as clients,
        patch(
            "custom_components.unifi_air_quality.UnifiAirQualityCoordinator"
        ) as coordinators,
    ):
        coordinators.return_value.async_config_entry_first_refresh = AsyncMock(
            side_effect=failure
        )
        with pytest.raises(type(failure)):
            await async_setup_entry(hass, entry)
        clients.return_value.start_websocket.assert_not_called()
    assert session.closed
