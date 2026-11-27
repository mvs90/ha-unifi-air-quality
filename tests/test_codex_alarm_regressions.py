"""Regression tests for Codex review findings on upstream pull request #13.

Exercise mixed Protect device models and alarm reconciliation races against the
real client adapter, using the existing deterministic HTTP/WebSocket fakes.
"""

import json
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from custom_components.unifi_air_quality.api import ProtectProtocolError

from .test_client import FakeResponse, FakeSession, _client, _frame


def send(client, action, data):
    """Deliver a synthetic paired Protect event or sensor update."""
    client._process_websocket_payload(_frame(action) + _frame(data))


@pytest.mark.parametrize("cache_unrelated", [False, True])
async def test_unrelated_protect_sensor_alarm_is_ignored(cache_unrelated, load_fixture):
    """A UP-Sense light alarm must not poison monitoring of UP-AirQuality."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    sensor_id = "anonymous-legacy-sensor-id"

    if cache_unrelated:
        # Unrelated model updates can be cached after the bootstrap.
        send(
            client,
            {"modelKey": "sensor", "action": "update", "id": sensor_id},
            {"type": "UP-Sense", "isConnected": True},
        )
    send(
        client,
        {"modelKey": "event", "action": "add", "id": "light-event"},
        {
            "type": "sensorExtremeValues",
            "device": sensor_id,
            "metadata": {"sensorType": "light", "status": "high"},
        },
    )
    assert not client.protocol_error
    assert not client._raw_alarm_events
    assert len(client.snapshot.devices) == 1
    assert client.snapshot.devices[0].active_alarms == ()
    callback.assert_not_called()


@pytest.mark.parametrize("operation", ["add", "update"])
async def test_foreign_model_survives_generic_push_and_refresh(load_fixture, operation):
    """A known non-target sensor never publishes or accepts a settings write."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    sensor = {"id": "legacy", "type": "USL-Entry", "airQuality": {}}
    payload["sensors"].append(sensor)
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    send(
        client,
        {"modelKey": "sensor", "action": operation, "id": "legacy"},
        {"type": "sensor", "airQuality": {"co2": {"status": "high", "value": 900}}},
    )
    assert len(client.snapshot.devices) == 1
    assert not client.is_measurement_fresh("legacy", "co2")
    assert not client.is_alarm_fresh("legacy", "co2")
    callback.assert_not_called()
    with pytest.raises(ProtectProtocolError, match="not available"):
        await client.async_update_device("legacy", {"ledSettings": {}})
    assert not session.patch_calls
    assert len((await client.async_get_snapshot()).devices) == 1


async def test_late_http_model_excludes_model_less_concurrent_push(load_fixture):
    """A push cannot erase the foreign model evidence in its matching inventory."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    payload["sensors"].append({"id": "legacy", "sku": "USL-Smoke"})
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    session.get_response = FakeResponse(
        payload=payload | {"sensors": payload["sensors"][:-1]}
    )
    await client.async_get_snapshot()
    snapshots = []
    client._update_callback = snapshots.append
    response = FakeResponse(payload=payload)

    async def receive_inventory(**kwargs):
        """Deliver an untagged add before the HTTP model information is read."""
        send(
            client,
            {"modelKey": "sensor", "action": "add", "id": "legacy"},
            {"airQuality": {"co2": 900}},
        )
        return payload

    response.json = receive_inventory
    session.get_response = response
    assert len((await client.async_get_snapshot()).devices) == 1
    assert all(len(snapshot.devices) == 1 for snapshot in snapshots)
    assert not client.is_measurement_fresh("legacy", "co2")


@pytest.mark.parametrize("old_type", ["sensor", "UP-AirQuality"])
@pytest.mark.parametrize("pushed_type", [None, "UP-AirQuality"])
async def test_fresh_foreign_identity_wins_over_racing_socket_add(
    old_type, pushed_type
):
    """No generic, inherited or conflicting pushed label can authorize a write."""
    sensor = {"id": "test-device", "type": old_type, "airQuality": {"co2": 600}}
    payload = {"nvr": {"id": "test-console"}, "sensors": [sensor]}
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    assert len((await client.async_get_snapshot()).devices) == 1
    fresh = deepcopy(payload)
    fresh["sensors"][0]["type"] = "USL-Entry"
    response = FakeResponse(payload=fresh)

    async def receive_inventory(**kwargs):
        """Interleave a partial add with the response carrying foreign identity."""
        readings = {"airQuality": {"co2": 650}}
        if pushed_type is not None:
            readings["type"] = pushed_type
        send(
            client,
            {"modelKey": "sensor", "action": "add", "id": sensor["id"]},
            readings,
        )
        return fresh

    response.json = receive_inventory
    session.get_response = response
    assert not (await client.async_get_snapshot()).devices
    # A later conflicting socket update must not undo that decision either.
    send(
        client,
        {"modelKey": "sensor", "action": "update", "id": sensor["id"]},
        {"type": "UP-AirQuality", "airQuality": {"co2": 680}},
    )
    assert not client.snapshot.devices
    with pytest.raises(ProtectProtocolError, match="not available"):
        await client.async_update_device(
            sensor["id"], {"ledSettings": {"isEnabled": True}}
        )
    assert not session.patch_calls


@pytest.mark.parametrize("revocation", ["remove", "foreign-model", "absent-http"])
async def test_model_less_device_survives_null_but_not_identity_revocation(revocation):
    """Maintain the device across null samples and require new proof after loss."""
    sensor = {"id": "test-device", "type": "sensor", "airQuality": {"co2": 600}}
    payload = {"nvr": {"id": "test-console"}, "sensors": [sensor]}
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    assert len((await client.async_get_snapshot()).devices) == 1
    action = {"modelKey": "sensor", "action": "update", "id": sensor["id"]}
    send(client, action, {"airQuality": {"co2": None}})
    assert client.snapshot.devices[0].raw["airQuality"]["co2"] is None
    sensor["airQuality"]["co2"] = None
    assert len((await client.async_get_snapshot()).devices) == 1
    send(client, action, {"airQuality": {"co2": 650}})
    assert client.snapshot.devices[0].raw["airQuality"]["co2"] == 650
    if revocation == "foreign-model":
        send(client, action, {"sku": "USL-Motion"})
    elif revocation == "remove":
        send(client, action | {"action": "remove"}, {})
    else:
        session.get_response = FakeResponse(payload=payload | {"sensors": []})
        assert not (await client.async_get_snapshot()).devices
    send(client, action, {"airQuality": {"co2": 700}})
    assert not client.snapshot.devices
    with pytest.raises(ProtectProtocolError, match="not available"):
        await client.async_update_device(
            sensor["id"], {"ledSettings": {"isEnabled": True}}
        )
    assert not session.patch_calls


async def test_real_air_quality_invalid_event_still_fails_closed(load_fixture):
    """Unrecognized metrics on a supported device still fail validation."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    with pytest.raises(ProtectProtocolError):
        send(
            client,
            {"modelKey": "event", "action": "add", "id": "bad"},
            {
                "type": "sensorExtremeValues",
                "device": "anonymous-air-quality-id",
                "metadata": {"sensorType": "light", "status": "high"},
            },
        )
    assert client.protocol_error


async def test_concurrent_alarm_end_does_not_replay_old_cached_high(load_fixture):
    """A fresh normal bootstrap must replace a pre-request cached high status."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    send(
        client,
        {
            "modelKey": "sensor",
            "action": "update",
            "id": "anonymous-air-quality-id",
        },
        {"airQuality": {"co2": {"status": "high", "value": 1400}}},
    )
    send(client, events["extreme_started"]["action"], events["extreme_started"]["data"])
    normal = deepcopy(payload)
    normal["sensors"][0]["airQuality"]["co2"] = {"status": "normal", "value": 600}

    class NormalAfterEnd(FakeResponse):
        """Deliver the matching end while an HTTP inventory is in flight."""

        async def json(self, **kwargs):
            """Return new normal evidence after a matching event end."""
            send(
                client,
                events["extreme_ended"]["action"],
                events["extreme_ended"]["data"],
            )
            return normal

    session.get_response = NormalAfterEnd()
    snapshot = await client.async_get_snapshot()
    assert snapshot.devices[0].raw["airQuality"]["co2"]["status"] == "normal"
    assert snapshot.devices[0].active_alarms == ()
    assert snapshot.devices[0].uncertain_alarm_metrics == ()
    assert [call.args[0].transition for call in callback.call_args_list] == [
        "started",
        "ended",
    ]


async def test_sensor_status_push_wins_over_concurrent_event_end(load_fixture):
    """A later confirmed sensor status is not discarded with the ending event."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()
    send(client, events["extreme_started"]["action"], events["extreme_started"]["data"])
    old = deepcopy(payload)
    old["sensors"][0]["airQuality"]["co2"] = {"status": "normal", "value": 600}

    class ReconfirmedHigh(FakeResponse):
        """Interleave an end and newer high sensor confirmation."""

        async def json(self, **kwargs):
            """Keep an explicitly pushed high over the older normal inventory."""
            send(
                client,
                events["extreme_ended"]["action"],
                events["extreme_ended"]["data"],
            )
            send(
                client,
                {
                    "modelKey": "sensor",
                    "action": "update",
                    "id": "anonymous-air-quality-id",
                },
                {"airQuality": {"co2": {"status": "high", "value": 1500}}},
            )
            return old

    session.get_response = ReconfirmedHigh()
    snapshot = await client.async_get_snapshot()
    assert snapshot.devices[0].raw["airQuality"]["co2"]["status"] == "high"
    assert snapshot.devices[0].active_alarms[0].metric == "co2"
    assert client.is_alarm_fresh("anonymous-air-quality-id", "co2")


async def test_retired_event_update_restores_alarm_without_duplicate_start(
    load_fixture,
):
    """Live continuation of a retired event restores availability and its source."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    started = events["extreme_started"]
    send(client, started["action"], started["data"])
    assert callback.call_count == 1
    await client.async_get_snapshot()
    assert not client.snapshot.devices[0].active_alarms
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)

    continuation = started["action"] | {"action": "update"}
    send(client, continuation, {"end": None})
    assert [alarm.metric for alarm in client.snapshot.devices[0].active_alarms] == [
        "co2"
    ]
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ()
    assert client.is_alarm_fresh("anonymous-air-quality-id", "co2")
    assert callback.call_count == 1

    send(client, continuation, {"end": None})
    assert callback.call_count == 1
    send(client, events["extreme_ended"]["action"], events["extreme_ended"]["data"])
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"


async def test_unknown_nonending_update_does_not_create_alarm(load_fixture):
    """An unobserved event ID is not sufficient evidence of an alarm start."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    send(
        client,
        {"modelKey": "event", "action": "update", "id": "not-started"},
        {
            "type": "sensorExtremeValues",
            "device": "anonymous-air-quality-id",
            "end": None,
            "metadata": {"sensorType": "co2", "status": "high"},
        },
    )
    assert not client._raw_alarm_events
    assert not client._active_alarm_events
    assert client.snapshot.devices[0].active_alarms == ()
