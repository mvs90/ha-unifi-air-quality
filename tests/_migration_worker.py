"""Disposable HA worker used by run_migration_smoke.py across package versions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _test_config_flow import USER_INPUT
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers import restore_state as rs
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.plugins import mock_storage

from custom_components.unifi_air_quality.api import snapshot_from_bootstrap
from custom_components.unifi_air_quality.const import DOMAIN

pytest_plugins = ["pytest_homeassistant_custom_component"]
STATE = Path(os.environ["MIGRATION_STATE"])
PHASE = os.environ["MIGRATION_PHASE"]


@pytest.fixture
def hass_storage():
    """Reload the previous subprocess's serialized HA registries and config entry."""
    data = json.loads(STATE.read_text())["storage"] if STATE.exists() else {}
    with mock_storage(data) as storage:
        yield storage


@pytest.fixture
def load_registries():
    """Use real serialized registries instead of the helper's empty-store shortcut."""
    return False


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow the actual package copied into this disposable configuration."""


def identities(hass, entry):
    """Capture persistent IDs and explicit customizations, excluding runtime state."""
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return {
        "entry_id": entry.entry_id,
        "unique_id": entry.unique_id,
        "title": entry.title,
        "data": dict(entry.data),
        "options": dict(entry.options),
        "entities": sorted(
            [
                [
                    entity.entity_id,
                    entity.unique_id,
                    entity.name,
                    entity.disabled_by,
                    dict(entity.options),
                ]
                for entity in entities
            ]
        ),
        "devices": sorted(
            [
                [device.id, sorted(device.identifiers), device.name_by_user]
                for device in devices
            ]
        ),
    }


async def test_package_install_upgrade_rollback(hass, hass_storage):
    """Run one real seven-platform HA setup and retain identity across versions."""
    previous = json.loads(STATE.read_text()) if STATE.exists() else None
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    for registry in (ar, cr, dr, er, fr, ir, lr, rs):
        await registry.async_load(hass)
    payload = json.loads(Path("fixture.json").read_text())
    snapshot = snapshot_from_bootstrap(payload)
    manifest = json.loads(
        Path("custom_components/unifi_air_quality/manifest.json").read_text()
    )
    assert manifest["version"] == ("0.5.4" if PHASE.startswith("upstream") else "0.6.0")
    import custom_components.unifi_air_quality.api as loaded_api

    assert Path(loaded_api.__file__).is_relative_to(Path.cwd())
    entries = hass.config_entries.async_entries(DOMAIN)
    if entries:
        entry = entries[0]
    else:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=snapshot.console_id,
            title="Migration console",
            data=USER_INPUT.copy(),
            options={"ring_led_previous_brightness_anonymous-air-quality-id": 75},
        )
        entry.add_to_hass(hass)
    with patch("custom_components.unifi_air_quality.PrivateProtectClient") as factory:
        client = factory.return_value
        client.async_get_snapshot = AsyncMock(return_value=snapshot)
        client.async_close = AsyncMock()
        client.subscribe_alarm_events.return_value = MagicMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert len(entities) == 71
        assert len({entity.entity_id for entity in entities}) == 71
        if previous is None:
            hass.config_entries.async_update_entry(entry, title="Named Protect console")
            co2 = next(item for item in entities if item.unique_id.endswith("_co2"))
            registry.async_update_entity(co2.entity_id, name="Office CO2")
            registry.async_update_entity_options(
                co2.entity_id, "sensor", {"display_precision": 0}
            )
            nox = next(
                item for item in entities if item.unique_id.endswith("_nox_index")
            )
            registry.async_update_entity(
                nox.entity_id, disabled_by=er.RegistryEntryDisabler.USER
            )
            device = dr.async_entries_for_config_entry(
                dr.async_get(hass), entry.entry_id
            )[0]
            dr.async_get(hass).async_update_device(device.id, name_by_user="Office")
        current = identities(hass, entry)
        # JSON normalization matches registry serialization between subprocesses.
        current = json.loads(json.dumps(current))
        if previous is not None:
            assert current == previous["identities"]
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    await hass.async_stop(force=True)
    await hass.async_block_till_done()
    assert "core.config_entries" in hass_storage
    assert "core.entity_registry" in hass_storage
    assert "core.device_registry" in hass_storage
    STATE.write_text(json.dumps({"storage": hass_storage, "identities": current}))
    print(f"{PHASE}: 71 entities, one entry, options and custom names preserved")
