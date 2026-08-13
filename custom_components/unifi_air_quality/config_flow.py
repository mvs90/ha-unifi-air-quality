"""Config flow for UniFi Protect Air Quality."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    PrivateProtectClient,
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectProtocolError,
)
from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DEFAULT_VERIFY_SSL, DOMAIN


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=values.get(CONF_HOST)): TextSelector(),
            vol.Required(
                CONF_PORT, default=values.get(CONF_PORT, DEFAULT_PORT)
            ): NumberSelector(NumberSelectorConfig(min=1, max=65535, mode="box")),
            vol.Required(
                CONF_USERNAME, default=values.get(CONF_USERNAME)
            ): TextSelector(),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_VERIFY_SSL,
                default=values.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): BooleanSelector(),
        }
    )


class UnifiAirQualityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle configuration of a Protect console."""

    VERSION = 1

    async def _validate(self, data: dict[str, Any]) -> tuple[str, str]:
        client = PrivateProtectClient(
            async_get_clientsession(self.hass),
            host=data[CONF_HOST],
            port=int(data[CONF_PORT]),
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            verify_ssl=data[CONF_VERIFY_SSL],
        )
        try:
            snapshot = await client.async_get_snapshot()
        finally:
            await client.async_close()
        return snapshot.console_id, snapshot.console_name

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            try:
                unique_id, title = await self._validate(user_input)
            except ProtectInvalidAuth:
                errors["base"] = "invalid_auth"
            except ProtectCannotConnect:
                errors["base"] = "cannot_connect"
            except ProtectProtocolError:
                errors["base"] = "invalid_response"
            # Home Assistant convention: keep unknown failures visible.
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials."""
        entry = self._reauth_entry
        if user_input is not None:
            data = {**entry.data, **user_input}
            try:
                unique_id, _title = await self._validate(data)
            except ProtectInvalidAuth:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_PASSWORD): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.PASSWORD)
                            )
                        }
                    ),
                    errors={"base": "invalid_auth"},
                )
            except (ProtectCannotConnect, ProtectProtocolError):
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_PASSWORD): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.PASSWORD)
                            )
                        }
                    ),
                    errors={"base": "cannot_connect"},
                )
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_mismatch()
            self.hass.config_entries.async_update_entry(entry, data=data)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
        )
