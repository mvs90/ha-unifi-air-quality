"""Re-export MockConfigEntry from the installed Home Assistant test helpers.

Prefer the newer pytest_homeassistant package when available; otherwise use
pytest_homeassistant_custom_component. The container runner selects a compatible
helper requirement before pytest loads this module.
"""

from importlib.util import find_spec

if find_spec("pytest_homeassistant") is not None:
    from pytest_homeassistant.common import MockConfigEntry
else:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

__all__ = ["MockConfigEntry"]
