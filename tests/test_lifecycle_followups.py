"""Regression coverage for explicit device cleanup and entry-scoped migrations."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.unifi_air_quality import (
    UnifiAirQualityRuntimeData,
    async_remove_config_entry_device,
)
from custom_components.unifi_air_quality.api import (
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectProtocolError,
)
from custom_components.unifi_air_quality.const import DOMAIN
from custom_components.unifi_air_quality.coordinator import UnifiAirQualityCoordinator
from custom_components.unifi_air_quality.number import (
    async_setup_entry as setup_numbers,
)

from .ha_test_helpers import MockConfigEntry
from .test_client import FakeResponse, FakeSession, _client
from .test_config_flow import USER_INPUT
from .test_controls import _coordinator, _device


async def _runtime(hass, load_fixture):
    """Create a validated isolated client and its current HA registry entry."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    snapshot = await client.async_get_snapshot()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=snapshot.console_id)
    entry.add_to_hass(hass)
    coordinator = UnifiAirQualityCoordinator(hass, entry, client)
    coordinator.async_set_updated_data(snapshot)
    entry.runtime_data = UnifiAirQualityRuntimeData(client, coordinator)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, snapshot.devices[0].id)},
    )
    return entry, device, session, payload


@pytest.mark.parametrize("connected", [True, False])
async def test_active_or_offline_paired_device_cannot_be_removed(
    hass, load_fixture, connected
):
    """An offline sensor still reported by Protect keeps its registry metadata."""
    entry, device, session, payload = await _runtime(hass, load_fixture)
    payload["sensors"][0]["isConnected"] = connected
    session.get_response = FakeResponse(payload=payload)
    assert not await async_remove_config_entry_device(hass, entry, device)
    assert dr.async_get(hass).async_get(device.id) is device


async def test_device_cleanup_refuses_wrong_entry_or_unidentified_device(
    hass, load_fixture
):
    """Neither another console's device nor an unknown identity authorizes a GET."""
    entry, device, _, _ = await _runtime(hass, load_fixture)
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    registry = dr.async_get(hass)
    unknown = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "foreign")}
    )
    with patch.object(entry.runtime_data.client, "async_get_snapshot") as fetch:
        assert not await async_remove_config_entry_device(hass, other, device)
        assert not await async_remove_config_entry_device(hass, entry, unknown)
        fetch.assert_not_called()
    own_unloaded = registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, "unloaded")}
    )
    assert not await async_remove_config_entry_device(hass, other, own_unloaded)


@pytest.mark.parametrize(
    "failure", [ProtectCannotConnect, ProtectInvalidAuth, ProtectProtocolError]
)
async def test_unknown_inventory_never_authorizes_cleanup(hass, load_fixture, failure):
    """Network, auth, and schema failures retain the last device and its name."""
    entry, device, _, _ = await _runtime(hass, load_fixture)
    registry = dr.async_get(hass)
    registry.async_update_device(device.id, name_by_user="Keep this room")
    with patch.object(
        entry.runtime_data.client, "async_get_snapshot", side_effect=failure
    ):
        assert not await async_remove_config_entry_device(hass, entry, device)
    assert registry.async_get(device.id).name_by_user == "Keep this room"
    assert entry.runtime_data.coordinator.data.devices


async def test_different_console_cannot_authorize_absent_device(hass, load_fixture):
    """A replacement console at the same host must not authorize registry deletion."""
    entry, device, session, payload = await _runtime(hass, load_fixture)
    changed = deepcopy(payload)
    changed["nvr"]["id"] = "replacement-console"
    changed["sensors"] = []
    session.get_response = FakeResponse(payload=changed)
    assert not await async_remove_config_entry_device(hass, entry, device)
    assert entry.runtime_data.coordinator.data.console_id == entry.unique_id


async def test_deliberately_removed_device_is_rediscovered_on_all_platforms(
    hass, load_fixture
):
    """A confirmed unpair plus explicit UI cleanup can be followed by re-adoption."""
    payload = json.loads(load_fixture("bootstrap_nox.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=payload["nvr"]["id"], data=USER_INPUT.copy()
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.unifi_air_quality.PrivateProtectClient",
            return_value=client,
        ),
        patch.object(client, "start_websocket"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    device = dr.async_entries_for_config_entry(devices, entry.entry_id)[0]
    original = er.async_entries_for_config_entry(entities, entry.entry_id)
    original_ids = {entity.unique_id for entity in original}
    assert len(original_ids) == 71

    # Unrelated registry removals must not forget this platform's active entities.
    other = MockConfigEntry(domain=DOMAIN)
    other.add_to_hass(hass)
    unrelated = devices.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, "other")}
    )
    devices.async_remove_device(unrelated.id)
    await hass.async_block_till_done()
    client._notify_update()
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(entities, entry.entry_id)) == 71

    session.get_response = FakeResponse(payload=payload | {"sensors": []})
    assert await async_remove_config_entry_device(hass, entry, device)
    # HA performs this registry removal only after the hook returned True.
    devices.async_remove_device(device.id)
    await hass.async_block_till_done()
    assert not er.async_entries_for_config_entry(entities, entry.entry_id)
    assert not dr.async_entries_for_config_entry(devices, entry.entry_id)

    session.get_response = FakeResponse(payload=payload)
    await client.async_get_snapshot()
    client._notify_update()
    await hass.async_block_till_done()
    restored = er.async_entries_for_config_entry(entities, entry.entry_id)
    assert {entity.unique_id for entity in restored} == original_ids
    assert {entity.domain for entity in restored} == {
        "sensor",
        "binary_sensor",
        "event",
        "number",
        "select",
        "switch",
        "time",
    }
    assert len(dr.async_entries_for_config_entry(devices, entry.entry_id)) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_number_cleanup_is_exact_and_scoped_to_one_entry(hass):
    """Only known obsolete numbers for the entry lose registry metadata."""
    first = MockConfigEntry(domain=DOMAIN)
    second = MockConfigEntry(domain=DOMAIN)
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    first.runtime_data = SimpleNamespace(coordinator=_coordinator(_device()))
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    devices.async_get_or_create(
        config_entry_id=first.entry_id, identifiers={(DOMAIN, "previously-unpaired")}
    )
    remove = [
        registry.async_get_or_create("number", DOMAIN, unique_id, config_entry=first)
        for unique_id in (
            "device-id_reading_interval",
            "device-id_vape_low_threshold",
            "previously-unpaired_reading_interval",
        )
    ]
    retained = [
        registry.async_get_or_create(
            "number", DOMAIN, "other_vape_low_threshold", config_entry=second
        ),
        registry.async_get_or_create(
            "number", DOMAIN, "unknown_vape_low_threshold", config_entry=first
        ),
        registry.async_get_or_create(
            "sensor", DOMAIN, "device-id_reading_interval", config_entry=first
        ),
        registry.async_get_or_create(
            "number", "other", "device-id_reading_interval", config_entry=first
        ),
    ]
    customized = retained[0]
    registry.async_update_entity(
        customized.entity_id,
        name="Other room",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    registry.async_update_entity_options(
        customized.entity_id, "number", {"mode": "box"}
    )
    before = registry.async_get(customized.entity_id)
    await setup_numbers(hass, first, lambda entities: None)
    assert all(registry.async_get(entity.entity_id) is None for entity in remove)
    assert all(registry.async_get(entity.entity_id) is not None for entity in retained)
    assert registry.async_get(customized.entity_id) is before
    assert before.options == {"number": {"mode": "box"}}
    assert before.name == "Other room"
    assert before.disabled_by is er.RegistryEntryDisabler.USER
