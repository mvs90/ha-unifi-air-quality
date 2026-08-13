"""Tests for config-entry setup and unloading."""

from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_air_quality import async_setup_entry, async_unload_entry
from custom_components.unifi_air_quality.const import CONF_VERIFY_SSL, DOMAIN
from custom_components.unifi_air_quality.models import AirQualityDevice, ProtectSnapshot


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
            "custom_components.unifi_air_quality.async_get_clientsession"
        ) as get_session,
        patch("custom_components.unifi_air_quality.PrivateProtectClient") as client_cls,
        patch(
            "custom_components.unifi_air_quality.UnifiAirQualityCoordinator"
        ) as coordinator_cls,
    ):
        coordinator = coordinator_cls.return_value
        coordinator.data = snapshot
        coordinator.async_config_entry_first_refresh = AsyncMock()
        client = client_cls.return_value
        client.async_close = AsyncMock()

        assert await async_setup_entry(hass, entry)
        get_session.assert_called_once_with(hass)
        client.start_websocket.assert_called_once()
        assert entry.runtime_data.coordinator is coordinator

        registered = device_registry.async_get_device({(DOMAIN, "sensor-id")})
        assert registered is not None
        assert registered.model == "UP-AirQuality"

        assert await async_unload_entry(hass, entry)
        client.async_close.assert_awaited_once()
