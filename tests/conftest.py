"""Shared fixtures for synthetic data and isolated Home Assistant test storage."""

from importlib.util import find_spec
from pathlib import Path

import pytest

NEW_TEST_HELPERS = find_spec("pytest_homeassistant") is not None
pytest_plugins = () if NEW_TEST_HELPERS else "pytest_homeassistant_custom_component"


if NEW_TEST_HELPERS:

    @pytest.fixture
    def hass_config_dir(hass_tmp_config_dir):
        """Keep the preview's writable configuration outside the checkout."""
        return hass_tmp_config_dir


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in Home Assistant tests."""


@pytest.fixture
def load_fixture():
    """Return a loader for UTF-8 JSON fixture text from tests/fixtures."""

    def _load(name: str) -> str:
        """Read the named fixture without parsing or modifying its contents."""
        return (Path(__file__).parent / "fixtures" / name).read_text()

    return _load
