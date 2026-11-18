"""Regression coverage for confirmed alarms, permissions, TLS and recovery."""

import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import ClientSSLError

from custom_components.unifi_air_quality.api import (
    PrivateProtectClient,
    ProtectPermissionDenied,
    ProtectTlsError,
)
from custom_components.unifi_air_quality.config_flow import _schema
from custom_components.unifi_air_quality.const import CONF_VERIFY_SSL

from .test_client import FakeResponse, FakeSession, _client, _frame


def push(
    client, data, *, model="sensor", operation="update", id="anonymous-air-quality-id"
):
    """Provide push for the isolated regression scenario."""
    client._process_websocket_payload(
        _frame({"action": operation, "modelKey": model, "id": id}) + _frame(data)
    )


async def test_bootstrap_reconciles_missed_end_and_preserves_active(load_fixture):
    """Verify bootstrap reconciles missed end and preserves active."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    recovered = deepcopy(payload)
    recovered["sensors"][0]["airQuality"] = {
        "co2": {"status": "neutral", "value": 600},
        "nox": {"status": "high", "value": 150},
    }
    client = _client(
        FakeSession(
            FakeResponse(),
            [FakeResponse(payload=payload), FakeResponse(payload=recovered)],
        )
    )
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    push(
        client,
        {
            "airQuality": {
                "co2": {"status": "high", "value": 1400},
                "nox": {"status": "high", "value": 151},
            }
        },
    )
    assert callback.call_count == 2
    snapshot = await client.async_get_snapshot()
    assert [a.metric for a in snapshot.devices[0].active_alarms] == ["nox"]
    assert callback.call_count == 3
    assert callback.call_args.args[0].transition == "ended"


async def test_incomplete_resync_expires_orphan_without_false_end(load_fixture):
    """Verify incomplete resync expires orphan without false end."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    push(client, {"airQuality": {"co2": {"status": "high", "value": 1400}}})
    snapshot = await client.async_get_snapshot()
    assert not snapshot.devices[0].active_alarms
    assert snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
    assert callback.call_count == 1
    push(client, {"airQuality": {"co2": {"status": "neutral", "value": 600}}})
    assert not client.snapshot.devices[0].uncertain_alarm_metrics
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"


@pytest.mark.parametrize("event_count", [1, 2])
async def test_partial_alarm_ends_after_sensor_bootstrap(event_count, load_fixture):
    """Keep enough event identity to confirm an end on scalar firmware."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    started, ended = events["extreme_started"], events["extreme_ended"]
    ids = [f"event-{index}" for index in range(event_count)]
    for event_id in ids:
        client._process_websocket_payload(
            _frame(started["action"] | {"id": event_id}) + _frame(started["data"])
        )
    assert callback.call_count == 1
    assert (await client.async_get_snapshot()).devices[0].uncertain_alarm_metrics == (
        "co2",
    )
    for event_id in ids:
        remaining = event_id != ids[-1]
        frame = _frame(ended["action"] | {"id": event_id}) + _frame(ended["data"])
        client._process_websocket_payload(frame)
        client._process_websocket_payload(frame)
        assert bool(client.snapshot.devices[0].uncertain_alarm_metrics) is remaining
        assert callback.call_count == (1 if remaining else 2)
    assert not client.snapshot.devices[0].active_alarms
    assert callback.call_args.args[0].transition == "ended"
    assert client.is_alarm_fresh("anonymous-air-quality-id", "co2")
    await client.async_get_snapshot()
    assert client.is_alarm_fresh("anonymous-air-quality-id", "co2")


@pytest.mark.parametrize("resolution", ["older_end", "authoritative_normal"])
async def test_retired_alarm_prevents_false_clear_after_new_event(
    resolution, load_fixture
):
    """An unknown old event cannot be cleared by a later source ending."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    started, ended = events["extreme_started"], events["extreme_ended"]

    def send(event, event_id):
        """Deliver an event with a known, independent source identifier."""
        client._process_websocket_payload(
            _frame(event["action"] | {"id": event_id}) + _frame(event["data"])
        )

    send(started, "older")
    assert callback.call_count == 1
    await client.async_get_snapshot()
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)

    send(started, "newer")
    count_before_end = callback.call_count
    send(ended, "newer")
    assert callback.call_count == count_before_end
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
    assert not client.snapshot.devices[0].active_alarms

    if resolution == "older_end":
        send(ended, "older")
    else:
        push(client, {"airQuality": {"co2": {"status": "neutral", "value": 600}}})
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ()
    assert callback.call_count == count_before_end + 1
    assert callback.call_args.args[0].transition == "ended"


async def test_retired_alarm_preserves_newest_live_source_attributes(load_fixture):
    """A surviving live source supplies status after another live source ends."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)

    def send(event_id, operation, status):
        """Send an event start or a partial end with a source identifier."""
        client._process_websocket_payload(
            _frame({"modelKey": "event", "id": event_id, "action": operation})
            + _frame(
                {"end": 123}
                if operation == "update"
                else {
                    "type": "sensorExtremeValues",
                    "device": "anonymous-air-quality-id",
                    "metadata": {"sensorType": "co2", "status": status},
                }
            )
        )

    send("old", "add", "high")
    await client.async_get_snapshot()
    send("high", "add", "high")
    send("critical", "add", "critical")
    before = callback.call_count
    send("high", "update", None)
    device = client.snapshot.devices[0]
    assert device.active_alarms[0].status == "critical"
    assert callback.call_count == before
    send("critical", "update", None)
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
    assert callback.call_count == before
    send("old", "update", None)
    assert callback.call_count == before + 1
    assert callback.call_args.args[0].transition == "ended"


async def test_pruned_old_alarm_never_fabricates_clear(load_fixture):
    """Pruning an unresolved event ID cannot make a newer end definitive."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    started, ended = events["extreme_started"], events["extreme_ended"]

    def send(event, event_id):
        """Send a confirmed event frame with the selected source ID."""
        client._process_websocket_payload(
            _frame(event["action"] | {"id": event_id}) + _frame(event["data"])
        )

    send(started, "pruned")
    send(started, "retained")
    with patch("custom_components.unifi_air_quality.api._RETIRED_ALARM_EVENT_LIMIT", 1):
        await client.async_get_snapshot()
    assert ("anonymous-air-quality-id", "co2") in client._unresolvable_alarm_metrics
    send(started, "newer")
    count_before_end = callback.call_count
    send(ended, "newer")
    assert callback.call_count == count_before_end
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
    push(client, {"airQuality": {"co2": {"status": "normal", "value": 600}}})
    assert callback.call_count == count_before_end + 1
    assert callback.call_args.args[0].transition == "ended"


async def test_expired_event_metadata_remains_bounded(load_fixture):
    """Prune old identities while keeping recent partial end frames decodable."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    started = events["extreme_started"]
    with patch("custom_components.unifi_air_quality.api._RETIRED_ALARM_EVENT_LIMIT", 2):
        for event_id in ("first", "second", "third"):
            client._process_websocket_payload(
                _frame(started["action"] | {"id": event_id}) + _frame(started["data"])
            )
        await client.async_get_snapshot()
    assert set(client._raw_alarm_events) == {"second", "third"}
    assert set(client._alarm_event_devices) == {"second", "third"}
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)


async def test_forbidden_write_keeps_cookie_reads_and_push(load_fixture):
    """Verify forbidden write keeps cookie reads and push."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(
        FakeResponse(headers={"Set-Cookie": "SESSION=synthetic"}),
        FakeResponse(payload=payload),
        patch=FakeResponse(status=403),
    )
    client = _client(session)
    await client.async_get_snapshot()
    with pytest.raises(ProtectPermissionDenied, match="write permission"):
        await client.async_update_device(
            "anonymous-air-quality-id", {"ledSettings": {"isEnabled": False}}
        )
    assert not client._auth_failed
    await client.async_get_snapshot()
    push(client, {"airQuality": {"co2": 750}})
    assert client.snapshot.devices[0].raw["airQuality"]["co2"] == 750
    assert len(session.post_calls) == 1
    assert len(session.patch_calls) == 1


async def test_unexpected_stream_failure_recovers_with_bounded_backoff(caplog):
    """Verify unexpected stream failure recovers with bounded backoff."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    delays = []
    calls = 0

    async def once():
        """Provide once for the isolated regression scenario."""
        nonlocal calls
        calls += 1
        if calls <= 8:
            raise RuntimeError("secret-payload-cookie")
        client._closing = True

    async def sleep(delay):
        """Provide sleep for the isolated regression scenario."""
        delays.append(delay)

    client._websocket_once = once
    with patch(
        "custom_components.unifi_air_quality.api.asyncio.sleep", side_effect=sleep
    ):
        await client._websocket_loop()
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert calls == 9
    assert "secret-payload-cookie" not in caplog.text


@pytest.mark.parametrize("verify", [True, False])
async def test_explicit_tls_applies_to_http_and_wss(verify, load_fixture):
    """Verify explicit tls applies to http and wss."""
    from .test_client import FakeWebsocket

    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(
        FakeResponse(), FakeResponse(payload=payload), FakeWebsocket([])
    )
    client = PrivateProtectClient(
        session,
        host="protect.local",
        port=443,
        username="synthetic",
        password="synthetic",  # pragma: allowlist secret -- synthetic TLS fixture.
        verify_ssl=verify,
    )
    await client.async_get_snapshot()
    await client._websocket_once()
    for calls in (session.post_calls, session.get_calls, session.ws_calls):
        assert calls[0][1]["ssl"] is (None if verify else False)


async def test_tls_error_is_actionable_and_safe():
    """Verify tls error is actionable and safe."""
    client = _client(
        FakeSession(
            FakeResponse(error=ClientSSLError(MagicMock(), OSError("private"))),
            FakeResponse(),
        )
    )
    with pytest.raises(ProtectTlsError, match="Trust the console"):
        await client.async_get_snapshot()


def test_new_tls_default_and_explicit_legacy_value():
    """Verify new tls default and explicit legacy value."""
    assert (
        _schema()({"host": "x", "port": 443, "username": "x", "password": "x"})[
            CONF_VERIFY_SSL
        ]
        is True
    )
    assert (
        _schema({CONF_VERIFY_SSL: False})(
            {"host": "x", "port": 443, "username": "x", "password": "x"}
        )[CONF_VERIFY_SSL]
        is False
    )


@pytest.mark.parametrize("normal_before_event_end", [False, True])
async def test_retired_sensor_source_requires_normal_after_independent_event(
    normal_before_event_end, load_fixture
):
    """A newer event cannot end an unresolved sensor-only alarm source."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    push(client, {"airQuality": {"co2": {"status": "high", "value": 1400}}})
    await client.async_get_snapshot()
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
    assert callback.call_count == 1

    started, ended = events["extreme_started"], events["extreme_ended"]
    client._process_websocket_payload(
        _frame(started["action"] | {"id": "independent"}) + _frame(started["data"])
    )
    assert callback.call_count == 1
    assert client.snapshot.devices[0].active_alarms
    normal = {"airQuality": {"co2": {"status": "normal", "value": 600}}}
    if normal_before_event_end:
        push(client, normal)
        assert client.snapshot.devices[0].active_alarms
        assert callback.call_count == 1

    client._process_websocket_payload(
        _frame(ended["action"] | {"id": "independent"}) + _frame(ended["data"])
    )
    assert not client.snapshot.devices[0].active_alarms
    if not normal_before_event_end:
        assert callback.call_count == 1
        assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
        push(client, normal)
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ()
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"
    push(client, normal)
    assert callback.call_count == 2


async def test_reconfirmed_sensor_alarm_does_not_duplicate_aggregate_start(
    load_fixture,
):
    """Unknown state does not end the aggregate's existing notification cycle."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    push(client, {"airQuality": {"co2": {"status": "high", "value": 1400}}})
    await client.async_get_snapshot()
    push(client, {"airQuality": {"co2": {"status": "critical", "value": 2000}}})
    assert callback.call_count == 1
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ()
    assert client.snapshot.devices[0].active_alarms[0].status == "critical"
    push(client, {"airQuality": {"co2": {"status": "normal", "value": 600}}})
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"


async def test_older_normal_bootstrap_does_not_clear_new_alarm_source(load_fixture):
    """A concurrent event start takes precedence over an older normal response."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    normal_payload = deepcopy(payload)
    normal_payload["sensors"][0]["airQuality"]["co2"] = {
        "status": "normal", "value": 600
    }
    response = FakeResponse(payload=normal_payload)
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(
        FakeSession(
            FakeResponse(),
            [FakeResponse(payload=payload), FakeResponse(payload=payload), response],
        )
    )
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    push(client, {"airQuality": {"co2": {"status": "high", "value": 1400}}})
    await client.async_get_snapshot()

    async def read_normal_with_new_event():
        """Deliver a newer stream event before the pending HTTP body completes."""
        started = events["extreme_started"]
        client._process_websocket_payload(
            _frame(started["action"] | {"id": "concurrent"}) + _frame(started["data"])
        )
        return normal_payload

    with patch.object(response, "json", side_effect=read_normal_with_new_event):
        snapshot = await client.async_get_snapshot()
    assert snapshot.devices[0].active_alarms
    assert callback.call_count == 1
    ended = events["extreme_ended"]
    client._process_websocket_payload(
        _frame(ended["action"] | {"id": "concurrent"}) + _frame(ended["data"])
    )
    assert callback.call_count == 1
    assert client.snapshot.devices[0].uncertain_alarm_metrics == ("co2",)
    push(client, {"airQuality": {"co2": {"status": "normal", "value": 600}}})
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"
