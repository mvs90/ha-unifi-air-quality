"""Shared test fixtures."""

from pathlib import Path

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in Home Assistant tests."""


@pytest.fixture
def load_fixture():
    """Load a fixture as text."""

    def _load(name: str) -> str:
        return (Path(__file__).parent / "fixtures" / name).read_text()

    return _load
