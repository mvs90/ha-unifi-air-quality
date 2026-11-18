"""Verify localized security guidance through Home Assistant's config flow."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations

from custom_components.unifi_air_quality.api import ProtectTlsError
from custom_components.unifi_air_quality.const import DOMAIN

from .test_config_flow import USER_INPUT

INTEGRATION_PATH = Path(__file__).parents[1] / "custom_components" / DOMAIN
CATALOGS = sorted((INTEGRATION_PATH / "translations").glob("*.json"))


@pytest.mark.parametrize("catalog", CATALOGS, ids=[path.stem for path in CATALOGS])
async def test_tls_error_flow_resolves_localized_security_guidance(hass, catalog):
    """Show translated TLS guidance for a rejected certificate in every locale."""
    language = catalog.stem
    hass.config.language = language
    expected = json.loads(catalog.read_text())["config"]
    english = json.loads((INTEGRATION_PATH / "strings.json").read_text())["config"]

    with patch(
        "custom_components.unifi_air_quality.config_flow.UnifiAirQualityConfigFlow._validate",
        AsyncMock(side_effect=ProtectTlsError()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT.copy()
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "tls_error"}
    translations = await async_get_translations(hass, language, "config", {DOMAIN})
    prefix = f"component.{DOMAIN}.config"
    # These are the same translation keys used by the frontend form and error.
    warning = translations[f"{prefix}.step.user.data_description.verify_ssl"]
    error = translations[f"{prefix}.error.{result['errors']['base']}"]
    assert warning == expected["step"]["user"]["data_description"]["verify_ssl"]
    assert error == expected["error"]["tls_error"]
    assert (
        translations[f"{prefix}.step.reconfigure.data_description.verify_ssl"]
        == warning
    )
    if language != "en":
        assert warning != english["step"]["user"]["data_description"]["verify_ssl"]
        assert error != english["error"]["tls_error"]
        for key in ("title", "description"):
            assert translations[f"{prefix}.step.reconfigure.{key}"] != (
                english["step"]["reconfigure"][key]
            )
        assert translations[f"{prefix}.error.different_console"] != (
            english["error"]["different_console"]
        )
        assert translations[f"{prefix}.abort.reconfigure_successful"] != (
            english["abort"]["reconfigure_successful"]
        )
