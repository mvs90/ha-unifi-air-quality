"""Configure a console, revalidate endpoint changes, and replace expired passwords."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from aiohttp import ClientSession, DummyCookieJar
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
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
    ProtectTlsError,
    reject_protect_redirects,
)
from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DEFAULT_VERIFY_SSL, DOMAIN


def _validated_port(value: Any) -> int:
    """Return an integer port in 1..65535 or raise ProtectCannotConnect.

    Home Assistant's number selector may supply an integer-valued float.
    Fractional values, booleans, strings, and non-finite values are rejected.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 1 <= value <= 65535
        or int(value) != value
    ):
        raise ProtectCannotConnect("Invalid HTTPS port")
    return int(value)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the connection form, preserving any explicit saved TLS choice."""
    # Only new connections receive the secure certificate-verification default.
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=values.get(CONF_HOST)): TextSelector(),
            vol.Required(
                CONF_PORT, default=values.get(CONF_PORT, DEFAULT_PORT)
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, step=1, mode="box")
            ),
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


def _reconfigure_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Expose only endpoint and TLS changes, not unnecessary credential edits."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults[CONF_HOST]): TextSelector(),
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, step=1, mode="box")
            ),
            vol.Required(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): BooleanSelector(),
        }
    )


class UnifiAirQualityConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle configuration of a Protect console."""

    VERSION = 1

    async def _validate(self, data: dict[str, Any]) -> tuple[str, str]:
        """Return console ID/name using an isolated, always-released HTTP session."""
        # Detach the attempt's session even if construction or cancellation fails.
        session = async_create_clientsession(
            self.hass,
            cookie_jar=DummyCookieJar(),
            middlewares=(reject_protect_redirects,),
        )
        try:
            return await self._validate_session(data, session)
        finally:
            session.detach()

    async def _validate_session(
        self, data: dict[str, Any], session: ClientSession
    ) -> tuple[str, str]:
        """Fetch a valid inventory and return its console ID/name, closing the client.

        Read access and schema compatibility are checked even for an empty
        sensor inventory. Adapter exceptions retain their flow-specific meaning.
        """
        # The client owns this attempt's cookies and is closed on every exit path.
        client = PrivateProtectClient(
            session,
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
        """Show connection settings or create a uniquely identified console entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input = {
                    **user_input,
                    CONF_PORT: _validated_port(user_input[CONF_PORT]),
                }
                unique_id, title = await self._validate(user_input)
            except ProtectInvalidAuth:
                errors["base"] = "invalid_auth"
            except ProtectTlsError:
                errors["base"] = "tls_error"
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate host, port, and TLS changes against the saved console identity.

        Keep credentials, entry identity, options, and registry mappings. Failed
        validation leaves the saved entry untouched and shows the error in place.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            changes = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
            }
            try:
                changes[CONF_PORT] = _validated_port(changes[CONF_PORT])
                proposed = {**entry.data, **changes}
                console_id, _ = await self._validate(proposed)
            except ProtectInvalidAuth:
                errors["base"] = "invalid_auth"
            except ProtectTlsError:
                errors["base"] = "tls_error"
            except ProtectCannotConnect:
                errors["base"] = "cannot_connect"
            except ProtectProtocolError:
                errors["base"] = "invalid_response"
            except Exception:
                errors["base"] = "unknown"
            else:
                if console_id != entry.unique_id:
                    errors["base"] = "different_console"
                else:
                    # Keep entry ID, options, credentials and registry mappings.
                    return self.async_update_reload_and_abort(
                        entry, data_updates=changes, reason="reconfigure_successful"
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema({**entry.data, **(user_input or {})}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Find the rejected entry and open its password replacement form."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate a new password for the same console, then update and reload.

        Host, port, username, and the explicit TLS choice stay in the saved entry.
        Connection or protocol failures keep the password form open.
        """
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
