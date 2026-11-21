"""Verify endpoint migrations preserve the existing Home Assistant identity."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.unifi_air_quality.api import (
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectProtocolError,
    ProtectTlsError,
)
from custom_components.unifi_air_quality.const import CONF_VERIFY_SSL, DOMAIN

from .ha_test_helpers import MockConfigEntry
from .test_config_flow import USER_INPUT, _snapshot

RECONFIGURE = {
    CONF_HOST: "new-protect.local",
    CONF_PORT: 8443,
    CONF_VERIFY_SSL: True,
}


@pytest.mark.parametrize("verify_ssl", [False, True])
async def test_reconfigure_preserves_identity_and_options(hass, verify_ssl):
    """Validate the same console before updating endpoint data and reloading."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="console-id",
        title="My console",
        data={**USER_INPUT, CONF_VERIFY_SSL: verify_ssl},
        options={"saved_led_brightness": {"device-id": 75}},
    )
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "device-id")}
    )
    devices.async_update_device(device.id, name_by_user="Office")
    entities = er.async_get(hass)
    entity = entities.async_get_or_create(
        "sensor", DOMAIN, "device-id_co2", config_entry=entry, device_id=device.id
    )
    entities.async_update_entity(entity.entity_id, name="Office carbon dioxide")
    with (
        patch(
            "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
        ) as clients,
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
    ):
        clients.return_value.async_get_snapshot = AsyncMock(return_value=_snapshot())
        clients.return_value.async_close = AsyncMock()
        form = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
        defaults = {key.schema: key.default() for key in form["data_schema"].schema}
        assert defaults[CONF_VERIFY_SSL] is verify_ssl
        assert set(defaults) == set(RECONFIGURE)
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], RECONFIGURE
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {**USER_INPUT, **RECONFIGURE}
    assert entry.unique_id == "console-id"
    assert entry.title == "My console"
    assert entry.options == {"saved_led_brightness": {"device-id": 75}}
    assert entities.async_get(entity.entity_id).unique_id == "device-id_co2"
    assert entities.async_get(entity.entity_id).name == "Office carbon dioxide"
    assert devices.async_get(device.id).name_by_user == "Office"
    assert entities.async_get(entity.entity_id).config_entry_id == entry.entry_id
    reload_entry.assert_awaited_once_with(entry.entry_id)
    clients.return_value.async_close.assert_awaited_once()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (ProtectTlsError(), "tls_error"),
        (ProtectInvalidAuth(), "invalid_auth"),
        (ProtectCannotConnect(), "cannot_connect"),
        (ProtectProtocolError(), "invalid_response"),
        (RuntimeError(), "unknown"),
        (_snapshot("different-console"), "different_console"),
    ],
)
async def test_reconfigure_failure_keeps_saved_connection(hass, response, expected):
    """Failed validation never replaces credentials, endpoint, options or identity."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="console-id", data=USER_INPUT.copy(), options={"x": 1}
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
        ) as clients,
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
    ):
        clients.return_value.async_get_snapshot = (
            AsyncMock(side_effect=response)
            if isinstance(response, Exception)
            else AsyncMock(return_value=response)
        )
        clients.return_value.async_close = AsyncMock()
        form = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], RECONFIGURE
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert entry.data == USER_INPUT
    assert entry.unique_id == "console-id"
    assert entry.options == {"x": 1}
    reload_entry.assert_not_awaited()
    defaults = {key.schema: key.default() for key in result["data_schema"].schema}
    assert defaults == RECONFIGURE
    clients.return_value.async_close.assert_awaited_once()


@pytest.mark.parametrize("port", [443.5, True])
async def test_reconfigure_rejects_non_integer_port(hass, port):
    """An endpoint port cannot be silently rounded or treated as a boolean."""
    from custom_components.unifi_air_quality.config_flow import (
        UnifiAirQualityConfigFlow,
    )

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="console-id", data=USER_INPUT.copy()
    )
    entry.add_to_hass(hass)
    flow = UnifiAirQualityConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    with patch.object(flow, "_validate", AsyncMock()) as validate:
        result = await flow.async_step_reconfigure({**RECONFIGURE, CONF_PORT: port})
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data == USER_INPUT
    validate.assert_not_awaited()


@pytest.mark.parametrize(
    "port", [443.5, True, 0, 65536, float("nan"), float("inf"), "443", None]
)
def test_port_validation_rejects_lossy_values(port):
    """UI and direct flow input cannot truncate, coerce or overflow port values."""
    from custom_components.unifi_air_quality.config_flow import _validated_port

    with pytest.raises(ProtectCannotConnect):
        _validated_port(port)


@pytest.mark.parametrize("port", [1, 443.0, 65535.0])
def test_port_validation_accepts_integer_selector_values(port):
    """The selector's normal float-valued integers are stored as integer ports."""
    from custom_components.unifi_air_quality.config_flow import _validated_port

    result = _validated_port(port)
    assert result == port and type(result) is int
