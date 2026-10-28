"""Verify Home Assistant serves bundled icons without contacting the CDN."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.brands import _BrandsBaseView
from homeassistant.setup import async_setup_component

from custom_components.unifi_air_quality.const import DOMAIN

BRAND_PATH = Path(__file__).parents[1] / "custom_components" / DOMAIN / "brand"


@pytest.mark.parametrize(
    ("requested", "filename"),
    [
        ("icon.png", "icon.png"),
        ("icon@2x.png", "icon@2x.png"),
        ("logo.png", "icon.png"),
        ("dark_icon@2x.png", "icon@2x.png"),
    ],
)
async def test_bundled_brand_images_are_served_locally(
    hass, hass_client, requested, filename
):
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    with patch.object(
        _BrandsBaseView,
        "_fetch_and_cache",
        new=AsyncMock(
            side_effect=AssertionError("Local branding must not use the CDN")
        ),
    ) as fetch:
        response = await client.get(f"/api/brands/integration/{DOMAIN}/{requested}")
        assert response.status == 200
        assert response.content_type == "image/png"
        assert await response.read() == (BRAND_PATH / filename).read_bytes()
        fetch.assert_not_awaited()
