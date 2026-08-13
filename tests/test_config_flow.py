"""Tests for the Home Assistant config flow."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_air_quality.api import (
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectProtocolError,
)
from custom_components.unifi_air_quality.const import CONF_VERIFY_SSL, DOMAIN
from custom_components.unifi_air_quality.models import ProtectSnapshot

USER_INPUT = {
    CONF_HOST: "protect.local",
    CONF_PORT: 443,
    CONF_USERNAME: "homeassistant",
    CONF_PASSWORD: "not-a-real-password",
    CONF_VERIFY_SSL: False,
}


def _snapshot(console_id: str = "console-id") -> ProtectSnapshot:
    return ProtectSnapshot(
        console_id=console_id,
        console_name="Protect",
        protect_version="6.2.0",
        last_update_id="update-id",
        devices=(),
    )


async def test_user_form(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_success(hass) -> None:
    with patch(
        "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
    ) as client_cls:
        client_cls.return_value.async_get_snapshot = AsyncMock(return_value=_snapshot())
        client_cls.return_value.async_close = AsyncMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT.copy()
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Protect"
    assert result["data"] == USER_INPUT
    client_cls.return_value.async_close.assert_awaited_once()


async def test_user_duplicate(hass) -> None:
    with patch(
        "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
    ) as client_cls:
        client_cls.return_value.async_get_snapshot = AsyncMock(return_value=_snapshot())
        client_cls.return_value.async_close = AsyncMock()
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT.copy()
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT.copy()
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_errors(hass) -> None:
    cases = (
        (ProtectInvalidAuth(), "invalid_auth"),
        (ProtectCannotConnect(), "cannot_connect"),
        (ProtectProtocolError(), "invalid_response"),
        (RuntimeError(), "unknown"),
    )
    for error, expected in cases:
        with patch(
            "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
        ) as client_cls:
            client_cls.return_value.async_get_snapshot = AsyncMock(side_effect=error)
            client_cls.return_value.async_close = AsyncMock()
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT.copy()
            )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": expected}


async def test_reauth_success(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="console-id")
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
        ) as client_cls,
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        client_cls.return_value.async_get_snapshot = AsyncMock(return_value=_snapshot())
        client_cls.return_value.async_close = AsyncMock()
        start = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            start["flow_id"], {CONF_PASSWORD: "replacement-password"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "replacement-password"


async def test_reauth_errors(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="console-id")
    entry.add_to_hass(hass)
    for error, expected in (
        (ProtectInvalidAuth(), "invalid_auth"),
        (ProtectCannotConnect(), "cannot_connect"),
    ):
        with patch(
            "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
        ) as client_cls:
            client_cls.return_value.async_get_snapshot = AsyncMock(side_effect=error)
            client_cls.return_value.async_close = AsyncMock()
            start = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
                data=entry.data,
            )
            result = await hass.config_entries.flow.async_configure(
                start["flow_id"], {CONF_PASSWORD: "bad-password"}
            )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": expected}
