"""Regression tests for alarm pictures in actual Home Assistant states."""

import json
from base64 import b64decode
from contextlib import asynccontextmanager
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import pytest
from homeassistant.const import (
    ATTR_ENTITY_PICTURE,
    ATTR_ICON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.unifi_air_quality.binary_sensor import ALARM_DESCRIPTIONS
from custom_components.unifi_air_quality.const import DOMAIN

from .test_client import FakeResponse, FakeSession, _client, _frame
from .test_entity_names import _entry
from .test_recovery import push

_DEVICE_ID = "anonymous-air-quality-id"


@asynccontextmanager
async def _installed_alarms(hass, payload):
    """Load real entities and the real client against isolated fixture responses."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    # Enable NOx explicitly so every alarm description participates in the check.
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{_DEVICE_ID}_nox_alarm",
        config_entry=entry,
        disabled_by=None,
    )
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    with (
        patch(
            "custom_components.unifi_air_quality.async_create_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.unifi_air_quality.PrivateProtectClient",
            return_value=client,
        ),
        patch.object(client, "start_websocket"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        try:
            yield entry.runtime_data.coordinator, client, session
        finally:
            assert await hass.config_entries.async_unload(entry.entry_id)


def _normal_payload(load_fixture):
    """Give each existing metric a confirmed normal status from Protect."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    payload["sensors"][0]["airQuality"] = {
        description.payload_key: {"status": "neutral", "value": 0}
        for description in ALARM_DESCRIPTIONS
    }
    return payload


def _alarm_state(hass, key="co2_alarm"):
    """Resolve a registered alarm by its stable unique ID and return its HA state."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{_DEVICE_ID}_{key}"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_normal_alarm_pictures_preserve_boolean_states_and_identity(
    hass, load_fixture
):
    """Switch self-contained green/red pictures without changing alarm semantics."""
    async with _installed_alarms(hass, _normal_payload(load_fixture)) as (_, client, _):
        initial_states = {
            description.key: _alarm_state(hass, description.key)
            for description in ALARM_DESCRIPTIONS
        }
        for state in initial_states.values():
            assert state.state == STATE_OFF
            assert ATTR_ICON not in state.attributes
            assert state.attributes["device_class"] == "problem"
            assert state.attributes[ATTR_ENTITY_PICTURE].startswith(
                "data:image/svg+xml;base64,"
            )

        # Parse the actual published asset once; no scripts or remote images exist.
        picture = initial_states["co2_alarm"].attributes[ATTR_ENTITY_PICTURE]
        svg = fromstring(  # noqa: S314 - fixed integration asset, not external input
            b64decode(picture.split(",", 1)[1], validate=True)
        )
        assert svg.tag == "{http://www.w3.org/2000/svg}svg"
        assert svg.attrib["viewBox"]
        shapes = list(svg)
        assert {shape.tag.rsplit("}", 1)[-1] for shape in shapes} == {"circle", "path"}
        assert any(shape.attrib.get("fill") == "#2e7d32" for shape in shapes)
        assert all(
            not key.endswith("href") and not value.startswith(("url(", "http"))
            for element in svg.iter()
            for key, value in element.attrib.items()
        )

        registry = er.async_get(hass)
        registered = registry.async_get(initial_states["co2_alarm"].entity_id)
        assert registered.unique_id == f"{_DEVICE_ID}_co2_alarm"
        push(client, {"airQuality": {"co2": {"status": "high", "value": 1400}}})
        await hass.async_block_till_done()
        alarm = _alarm_state(hass)
        assert alarm.state == STATE_ON
        assert ATTR_ICON not in alarm.attributes
        red_picture = alarm.attributes[ATTR_ENTITY_PICTURE]
        assert red_picture.startswith("data:image/svg+xml;base64,")
        assert red_picture != picture
        red_svg = fromstring(  # noqa: S314 - fixed integration asset, not external input
            b64decode(red_picture.split(",", 1)[1], validate=True)
        )
        assert red_svg.tag == svg.tag
        assert red_svg.attrib["viewBox"] == svg.attrib["viewBox"]
        red_shapes = list(red_svg)
        assert {shape.tag.rsplit("}", 1)[-1] for shape in red_shapes} == {
            "circle",
            "path",
        }
        assert any(shape.attrib.get("fill") == "#c62828" for shape in red_shapes)
        assert any(shape.attrib.get("stroke") == "#fff" for shape in red_shapes)
        assert all(
            not key.endswith("href") and not value.startswith(("url(", "http"))
            for element in red_svg.iter()
            for key, value in element.attrib.items()
        )

        push(client, {"airQuality": {"co2": {"status": "normal", "value": 600}}})
        await hass.async_block_till_done()
        recovered = _alarm_state(hass)
        assert recovered.state == STATE_OFF
        assert ATTR_ICON not in recovered.attributes
        assert recovered.attributes[ATTR_ENTITY_PICTURE] == picture
        assert alarm.entity_id == recovered.entity_id == registered.entity_id
        assert registry.async_get(recovered.entity_id).unique_id == registered.unique_id


@pytest.mark.parametrize(
    "failure", ["offline", "stale_device", "stale_alarm", "coordinator_failure"]
)
async def test_alarm_picture_is_removed_during_unavailability_and_restored(
    hass, load_fixture, failure
):
    """Never retain a state picture when device or metric health becomes invalid."""
    with patch(
        "custom_components.unifi_air_quality.api.monotonic", return_value=100
    ) as clock:
        async with _installed_alarms(hass, _normal_payload(load_fixture)) as (
            coordinator,
            client,
            _,
        ):
            picture = _alarm_state(hass).attributes[ATTR_ENTITY_PICTURE]
            if failure == "offline":
                # Losing connectivity must also remove a confirmed red alarm image.
                push(client, {"airQuality": {"co2": {"status": "high", "value": 1400}}})
                await hass.async_block_till_done()
                active = _alarm_state(hass)
                assert active.state == STATE_ON
                assert active.attributes[ATTR_ENTITY_PICTURE] != picture
                push(client, {"isConnected": False})
            elif failure == "coordinator_failure":
                coordinator.async_set_update_error(UpdateFailed("Synthetic failure"))
            else:
                clock.return_value = 221
                if failure == "stale_alarm":
                    # A different fresh metric must not keep an old CO2 OK green.
                    push(
                        client,
                        {
                            "airQuality": {
                                "temperature": {"status": "normal", "value": 21}
                            }
                        },
                    )
                    assert client.is_device_fresh(_DEVICE_ID)
                coordinator.async_update_listeners()

            await hass.async_block_till_done()
            unavailable = _alarm_state(hass)
            assert unavailable.state == STATE_UNAVAILABLE
            assert ATTR_ICON not in unavailable.attributes
            assert ATTR_ENTITY_PICTURE not in unavailable.attributes

            clock.return_value = 222
            push(
                client,
                {
                    "isConnected": True,
                    "airQuality": {"co2": {"status": "neutral", "value": 600}},
                },
            )
            await hass.async_block_till_done()
            recovered = _alarm_state(hass)
            assert recovered.state == STATE_OFF
            assert ATTR_ICON not in recovered.attributes
            assert recovered.entity_id == unavailable.entity_id
            assert recovered.attributes[ATTR_ENTITY_PICTURE] == picture


async def test_unconfirmed_scalar_bootstrap_never_publishes_a_green_ok(
    hass, load_fixture
):
    """A numeric reading alone cannot confirm that an alarm is currently clear."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    async with _installed_alarms(hass, payload) as (_, client, _):
        unconfirmed = _alarm_state(hass)
        assert client.is_device_fresh(_DEVICE_ID)
        assert not client.is_alarm_fresh(_DEVICE_ID, "co2")
        assert unconfirmed.state == STATE_UNAVAILABLE
        assert ATTR_ICON not in unconfirmed.attributes
        assert ATTR_ENTITY_PICTURE not in unconfirmed.attributes

        push(client, {"airQuality": {"co2": {"status": "safe", "value": 612}}})
        await hass.async_block_till_done()
        confirmed = _alarm_state(hass)
        assert confirmed.state == STATE_OFF
        assert ATTR_ICON not in confirmed.attributes
        assert confirmed.entity_id == unconfirmed.entity_id
        assert ATTR_ENTITY_PICTURE in confirmed.attributes


async def test_uncertain_alarm_resolution_never_publishes_a_green_ok(
    hass, load_fixture
):
    """Remove alarm images during uncertainty until every end is confirmed."""
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    async with _installed_alarms(hass, _normal_payload(load_fixture)) as (
        coordinator,
        client,
        session,
    ):
        picture = _alarm_state(hass).attributes[ATTR_ENTITY_PICTURE]
        started, ended = events["extreme_started"], events["extreme_ended"]
        for event_id in ("first", "second"):
            client._process_websocket_payload(
                _frame(started["action"] | {"id": event_id}) + _frame(started["data"])
            )
        await hass.async_block_till_done()
        assert _alarm_state(hass).state == STATE_ON
        assert ATTR_ICON not in _alarm_state(hass).attributes
        red_picture = _alarm_state(hass).attributes[ATTR_ENTITY_PICTURE]
        assert red_picture.startswith("data:image/svg+xml;base64,")
        assert red_picture != picture

        session.get_response = FakeResponse(
            payload=json.loads(load_fixture("bootstrap_air_quality.json"))
        )
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
        assert _alarm_state(hass).state == STATE_UNAVAILABLE
        assert ATTR_ICON not in _alarm_state(hass).attributes
        assert ATTR_ENTITY_PICTURE not in _alarm_state(hass).attributes

        # The first end verifies liveness, but the second alarm remains unresolved.
        client._process_websocket_payload(
            _frame(ended["action"] | {"id": "first"}) + _frame(ended["data"])
        )
        await hass.async_block_till_done()
        assert client.is_alarm_fresh(_DEVICE_ID, "co2")
        assert _alarm_state(hass).state == STATE_UNKNOWN
        assert _alarm_state(hass).attributes[ATTR_ICON] == "mdi:help-circle"
        assert ATTR_ENTITY_PICTURE not in _alarm_state(hass).attributes

        client._process_websocket_payload(
            _frame(ended["action"] | {"id": "second"}) + _frame(ended["data"])
        )
        await hass.async_block_till_done()
        assert _alarm_state(hass).state == STATE_OFF
        assert ATTR_ICON not in _alarm_state(hass).attributes
        assert _alarm_state(hass).attributes[ATTR_ENTITY_PICTURE] == picture
