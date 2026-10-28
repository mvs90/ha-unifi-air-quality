"""Exercise real translation loading on fresh and existing installations."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.config import entity_registry as registry_api
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from custom_components.unifi_air_quality.api import snapshot_from_bootstrap
from custom_components.unifi_air_quality.const import CONF_VERIFY_SSL, DOMAIN

from .ha_test_helpers import MockConfigEntry

INTEGRATION_PATH = Path(__file__).parents[1] / "custom_components" / DOMAIN
LANGUAGES = sorted(
    path.stem for path in (INTEGRATION_PATH / "translations").glob("*.json")
)


def test_english_catalog_covers_the_translation_source():
    assert json.loads((INTEGRATION_PATH / "translations/en.json").read_text()) == (
        json.loads((INTEGRATION_PATH / "strings.json").read_text())
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_translation_catalog_is_complete(language):
    def flatten(value, prefix=""):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                result.update(flatten(child, f"{prefix}.{key}"))
            return result
        assert isinstance(value, str) and value.strip()
        return {prefix: value}

    source = flatten(json.loads((INTEGRATION_PATH / "strings.json").read_text()))
    translated = flatten(
        json.loads((INTEGRATION_PATH / "translations" / f"{language}.json").read_text())
    )
    assert translated.keys() == source.keys()


def _entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "protect.local",
            CONF_PORT: 443,
            CONF_USERNAME: "synthetic-user",
            CONF_PASSWORD: "synthetic-password",
            CONF_VERIFY_SSL: False,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _mock_client(factory, load_fixture):
    client = factory.return_value
    client.async_get_snapshot = AsyncMock(
        return_value=snapshot_from_bootstrap(
            json.loads(load_fixture("bootstrap_nox.json"))
        )
    )
    client.async_close = AsyncMock()
    client.subscribe_alarm_events.return_value = MagicMock()
    return client


@pytest.mark.parametrize("language", [*LANGUAGES, "eo"])
async def test_fresh_install_names_all_entities_and_loads_config_translations(
    hass, load_fixture, language
):
    hass.config.language = language
    entry = _entry(hass)
    translated_file = INTEGRATION_PATH / "translations" / f"{language}.json"
    expected_file = (
        translated_file
        if translated_file.exists()
        else INTEGRATION_PATH / "strings.json"
    )
    expected = json.loads(expected_file.read_text())

    with patch("custom_components.unifi_air_quality.PrivateProtectClient") as factory:
        _mock_client(factory, load_fixture)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert len(entries) == 71
        assert len({(item.domain, item.original_name) for item in entries}) == 71
        for registered in entries:
            key = registered.unique_id.removeprefix("anonymous-air-quality-id_")
            if key == "air_quality_alarm_event":
                key = "air_quality_alarm"
            name = expected["entity"][registered.domain][key]["name"]
            assert registered.original_name == name, registered.entity_id
            if state := hass.states.get(registered.entity_id):
                assert state.attributes["friendly_name"].endswith(name)

        translations = await async_get_translations(hass, language, "config", {DOMAIN})
        assert (
            translations[f"component.{DOMAIN}.config.step.user.title"]
            == (expected["config"]["step"]["user"]["title"])
        )
        entity_translations = await async_get_translations(
            hass, language, "entity", {DOMAIN}
        )
        for option, name in expected["entity"]["select"]["ring_led_metric"][
            "state"
        ].items():
            assert (
                entity_translations[
                    f"component.{DOMAIN}.entity.select.ring_led_metric.state.{option}"
                ]
                == name
            )
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_existing_names_are_repaired_without_changing_ids_or_custom_names(
    hass, load_fixture, hass_ws_client
):
    hass.config.language = "en"
    entry = _entry(hass)
    registry = er.async_get(hass)
    generic = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "anonymous-air-quality-id_tvoc",
        config_entry=entry,
        suggested_object_id="synthetic_air_quality_2",
        original_name=None,
    )
    renamed = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "anonymous-air-quality-id_voc_index",
        config_entry=entry,
        suggested_object_id="synthetic_air_quality_3",
        original_name=None,
    )
    registry.async_update_entity(renamed.entity_id, name="My custom VOC")

    with patch("custom_components.unifi_air_quality.PrivateProtectClient") as factory:
        _mock_client(factory, load_fixture)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get(generic.entity_id).original_name == (
            "Total volatile organic compounds"
        )
        assert registry.async_get(renamed.entity_id).original_name == "VOC index"
        assert registry.async_get(renamed.entity_id).name == "My custom VOC"
        # HA 2026.11 includes the device name even for user-named entities.
        # Accept exactly the two supported core renderings, not duplicates.
        assert hass.states.get(renamed.entity_id).attributes["friendly_name"] in {
            "My custom VOC",
            "UP-AirQuality My custom VOC",
        }

        websocket = await hass_ws_client(hass)
        assert registry_api.async_setup(hass)
        await websocket.send_json(
            {
                "id": 1,
                "type": "config/entity_registry/update",
                "entity_id": generic.entity_id,
                "name": "My custom TVOC",
            }
        )
        response = await websocket.receive_json()
        assert response["success"]
        await hass.async_block_till_done()
        assert hass.states.get(generic.entity_id).attributes["friendly_name"] in {
            "My custom TVOC",
            "UP-AirQuality My custom TVOC",
        }
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get(generic.entity_id).name == "My custom TVOC"
        assert registry.async_get(renamed.entity_id).name == "My custom VOC"
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_nox_is_opt_in_and_can_be_enabled_in_home_assistant(hass, load_fixture):
    entry = _entry(hass)
    registry = er.async_get(hass)

    with patch("custom_components.unifi_air_quality.PrivateProtectClient") as factory:
        _mock_client(factory, load_fixture)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        disabled = [item for item in entries if item.disabled_by is not None]
        assert len(entries) == 71
        assert {
            item.unique_id.removeprefix("anonymous-air-quality-id_")
            for item in disabled
        } == {
            "nox_index",
            "nox_alarm",
            "nox_low_threshold",
            "nox_high_threshold",
            "nox_alerts",
        }
        assert all(
            item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            for item in disabled
        )
        assert all(hass.states.get(item.entity_id) is None for item in disabled)
        assert (
            sum(hass.states.get(item.entity_id) is not None for item in entries) == 66
        )

        for item in disabled:
            registry.async_update_entity(item.entity_id, disabled_by=None)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert all(
            registry.async_get(item.entity_id).disabled_by is None for item in disabled
        )
        assert all(hass.states.get(item.entity_id) is not None for item in disabled)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_user_disabled_nox_stays_disabled_after_reload(hass, load_fixture):
    entry = _entry(hass)
    registry = er.async_get(hass)
    nox = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "anonymous-air-quality-id_nox_index",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    with patch("custom_components.unifi_air_quality.PrivateProtectClient") as factory:
        _mock_client(factory, load_fixture)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert (
            registry.async_get(nox.entity_id).disabled_by
            is er.RegistryEntryDisabler.USER
        )
        assert hass.states.get(nox.entity_id) is None
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert (
            registry.async_get(nox.entity_id).disabled_by
            is er.RegistryEntryDisabler.USER
        )
        assert await hass.config_entries.async_unload(entry.entry_id)
