"""Tests for HTTP and push behavior of the private Protect client."""

import asyncio
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientConnectionError, WSMsgType, WSServerHandshakeError
from multidict import CIMultiDict

from custom_components.unifi_air_quality.api import (
    PrivateProtectClient,
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectPermissionDenied,
    ProtectProtocolError,
    ProtectStreamPermissionDenied,
)
from custom_components.unifi_air_quality.models import ProtectAlarm, ProtectAlarmEvent

HEADER = struct.Struct("!bbbbi")


class FakeResponseStream:
    """Expose the same incremental byte semantics as aiohttp's response stream."""

    def __init__(self, response):
        """Keep the response's synthetic payload and cursor in one test stream."""
        self._response = response
        self._data = None
        self._offset = 0

    async def read(self, size):
        """Read at most size bytes and trigger any injected response race once."""
        if self._data is None:
            self._data = json.dumps(await self._response.json()).encode()
        data = self._data[self._offset : self._offset + size]
        self._offset += len(data)
        return data


class FakeResponse:
    """Model an async HTTP response with injected status, JSON and failure behavior."""

    def __init__(self, *, status=200, payload=None, headers=None, error=None):
        """Store the synthetic status, payload, headers and optional exception."""
        self.status = status
        self._payload = payload
        self.headers = CIMultiDict(headers or {})
        self._error = error
        self.content = FakeResponseStream(self)

    async def __aenter__(self):
        """Reset a new response body's stream for each synthetic HTTP call."""
        self.content = FakeResponseStream(self)
        return self

    async def __aexit__(self, *args):
        """Leave the synthetic response context without suppressing exceptions."""
        return None

    def raise_for_status(self):
        """Raise the configured exception without real HTTP status handling."""
        if self._error:
            raise self._error

    async def json(self, **kwargs):
        """Return the configured JSON value, or raise an injected decoding exception."""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Record HTTP/WS calls and serve configured responses without opening sockets."""

    def detach(self):
        """Mark the fake session closed without owning a real network connector."""
        self.closed = True

    def __init__(self, post, get, websocket=None, patch=None):
        """Configure response objects or response queues and initialize call records."""
        self.post_response = post
        self.get_response = get
        self.websocket = websocket
        self.patch_response = patch or FakeResponse()
        self.post_calls = []
        self.get_calls = []
        self.patch_calls = []
        self.ws_calls = []

    def post(self, url, **kwargs):
        """Record the login request and return its next configured response."""
        self.post_calls.append((url, kwargs))
        return self._next(self.post_response)

    def get(self, url, **kwargs):
        """Record the bootstrap request and return its next configured response."""
        self.get_calls.append((url, kwargs))
        return self._next(self.get_response)

    def patch(self, url, **kwargs):
        """Record the settings request and return its next configured response."""
        self.patch_calls.append((url, kwargs))
        return self._next(self.patch_response)

    async def ws_connect(self, url, **kwargs):
        """Record the upgrade request and return or raise its configured outcome."""
        self.ws_calls.append((url, kwargs))
        websocket = self._next(self.websocket)
        if isinstance(websocket, Exception):
            raise websocket
        return websocket

    @staticmethod
    def _next(response):
        """Consume a queued response, or reuse a single configured response object."""
        return response.pop(0) if isinstance(response, list) else response


class FakeMessage:
    """Represent the payload and aiohttp message type delivered by a fake stream."""

    def __init__(self, data, message_type=WSMsgType.BINARY):
        """Store message bytes and type, defaulting to a binary WebSocket message."""
        self.data = data
        self.type = message_type


class FakeWebsocket:
    """Provide a finite async stream and context manager for synthetic WS messages."""

    def __init__(self, messages):
        """Retain the mutable message queue consumed by asynchronous iteration."""
        self.messages = messages

    async def __aenter__(self):
        """Return this synthetic WebSocket when entering its async context."""
        return self

    async def __aexit__(self, *args):
        """Leave the synthetic WebSocket context without suppressing exceptions."""
        return None

    def __aiter__(self):
        """Use this object as the asynchronous message iterator."""
        return self

    async def __anext__(self):
        """Consume the next message, or signal a clean end when the queue is empty."""
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def _frame(data: dict) -> bytes:
    """Encode one uncompressed JSON frame with the Protect binary header."""
    payload = json.dumps(data).encode()
    return HEADER.pack(1, 1, 0, 0, len(payload)) + payload


def _client(session) -> PrivateProtectClient:
    """Build a client using only the injected fake session and synthetic credentials."""
    return PrivateProtectClient(
        session,
        host="protect.local",
        port=443,
        username="user",
        password="not-real",
        verify_ssl=False,
    )


async def test_get_snapshot_authenticates_and_keeps_cookie(load_fixture) -> None:
    """Verify get snapshot authenticates and keeps cookie."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(
        FakeResponse(
            headers=[
                ("Set-Cookie", "TOKEN=anonymous; Path=/; HttpOnly"),
                ("x-csrf-token", "anonymous-csrf"),
            ]
        ),
        FakeResponse(payload=payload),
    )
    client = _client(session)
    snapshot = await client.async_get_snapshot()

    assert snapshot.devices[0].raw["airQuality"]["co2"] == 612
    assert session.get_calls[0][1]["headers"]["Cookie"] == "TOKEN=anonymous"
    assert session.post_calls[0][1]["json"]["rememberMe"] is False
    assert client.snapshot is snapshot


async def test_update_device_patches_and_updates_snapshot(load_fixture) -> None:
    """Verify update device patches and updates snapshot."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    confirmed = json.loads(json.dumps(payload))
    confirmed["sensors"][0]["airQualitySettings"]["ringLedBrightness"] = 42
    session = FakeSession(
        FakeResponse(), [FakeResponse(payload=payload), FakeResponse(payload=confirmed)]
    )
    client = _client(session)
    await client.async_get_snapshot()
    callback = MagicMock()
    client.set_update_callback(callback)

    await client.async_update_device(
        "anonymous-air-quality-id",
        {"airQualitySettings": {"ringLedBrightness": 42}},
    )

    url, kwargs = session.patch_calls[0]
    assert url.endswith("/api/sensors/anonymous-air-quality-id")
    assert kwargs["json"] == {"airQualitySettings": {"ringLedBrightness": 42}}
    assert (
        client.snapshot.devices[0].raw["airQualitySettings"]["ringLedBrightness"] == 42
    )
    callback.assert_called_once()


async def test_update_device_validates_and_maps_errors(load_fixture) -> None:
    """Verify update device validates and maps errors."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()

    with pytest.raises(ProtectProtocolError):
        await client.async_update_device("missing", {"value": 1})
    with pytest.raises(ProtectProtocolError):
        await client.async_update_device("anonymous-air-quality-id", {})

    unauthorized = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(payload=payload),
            patch=FakeResponse(status=403),
        )
    )
    await unauthorized.async_get_snapshot()
    with pytest.raises(ProtectPermissionDenied):
        await unauthorized.async_update_device(
            "anonymous-air-quality-id", {"ledSettings": {"isEnabled": False}}
        )
    assert not unauthorized._auth_failed

    offline = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(payload=payload),
            patch=FakeResponse(error=ClientConnectionError("offline")),
        )
    )
    await offline.async_get_snapshot()
    with pytest.raises(ProtectCannotConnect):
        await offline.async_update_device(
            "anonymous-air-quality-id", {"ledSettings": {"isEnabled": False}}
        )


@pytest.mark.parametrize("status", [401, 403])
async def test_login_rejects_invalid_auth(status: int) -> None:
    """Verify login rejects invalid auth."""
    client = _client(FakeSession(FakeResponse(status=status), FakeResponse()))
    with pytest.raises(ProtectInvalidAuth):
        await client.async_get_snapshot()


async def test_login_connection_error() -> None:
    """Verify login connection error."""
    client = _client(
        FakeSession(
            FakeResponse(error=ClientConnectionError("offline")), FakeResponse()
        )
    )
    with pytest.raises(ProtectCannotConnect):
        await client.async_get_snapshot()


async def test_bootstrap_auth_and_protocol_errors() -> None:
    """Verify bootstrap auth and protocol errors."""
    client = _client(FakeSession(FakeResponse(), FakeResponse(status=401)))
    with pytest.raises(ProtectInvalidAuth):
        await client.async_get_snapshot()
    assert client._headers == {}

    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=ValueError())))
    with pytest.raises(ProtectProtocolError):
        await client.async_get_snapshot()

    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(error=ClientConnectionError("offline")),
        )
    )
    client._headers["Cookie"] = "existing"
    with pytest.raises(ProtectCannotConnect):
        await client.async_get_snapshot()

    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=[])))
    with pytest.raises(ProtectProtocolError):
        await client.async_get_snapshot()


async def test_websocket_push_updates_snapshot(load_fixture) -> None:
    """Verify websocket push updates snapshot."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    action = {
        "action": "update",
        "modelKey": "sensor",
        "id": "anonymous-air-quality-id",
        "newUpdateId": "new-update",
    }
    message = _frame(action) + _frame({"airQuality": {"co2": 800}})
    websocket = FakeWebsocket(
        [FakeMessage(message), FakeMessage(b"", WSMsgType.CLOSED)]
    )
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload), websocket)
    client = _client(session)
    await client.async_get_snapshot()
    callback = MagicMock()
    client.set_update_callback(callback)

    await client._websocket_once()

    assert client.snapshot.devices[0].raw["airQuality"]["co2"] == 800
    assert client.snapshot.devices[0].raw["airQuality"]["aqi"] == 17
    assert callback.call_count == 2


async def test_sensor_status_updates_emit_deduplicated_alarm_transitions(
    load_fixture,
) -> None:
    """Verify sensor status updates emit deduplicated alarm transitions."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)

    def status_frame(status, value=1200):
        """Encode a complete sensor update carrying one CO2 status and value."""
        return _frame(
            {
                "action": "update",
                "modelKey": "sensor",
                "id": "anonymous-air-quality-id",
            }
        ) + _frame({"airQuality": {"co2": {"status": status, "value": value}}})

    client._process_websocket_payload(status_frame("high"))
    client._process_websocket_payload(status_frame("high", 1250))
    assert callback.call_count == 1
    assert callback.call_args.args[0] == ProtectAlarmEvent(
        "anonymous-air-quality-id", "co2", "started", "high", 1200
    )
    assert client.snapshot.devices[0].active_alarms == (
        ProtectAlarm("co2", "high", 1250),
    )

    client._process_websocket_payload(status_frame("neutral", 800))
    client._process_websocket_payload(status_frame("neutral", 790))
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"
    assert client.snapshot.devices[0].active_alarms == ()

    client._process_websocket_payload(
        _frame(
            {
                "action": "update",
                "modelKey": "sensor",
                "id": "anonymous-air-quality-id",
            }
        )
        + _frame(
            {
                "airQuality": {
                    "invalid": "not-a-reading",
                    "missing_status": {"value": 1},
                    "empty_status": {"status": ""},
                    4: {"status": "high"},
                    "boolean_value": {"status": "high", "value": True},
                }
            }
        )
    )
    # Optional unknown metrics never create alarm transitions.
    assert callback.call_count == 2


async def test_websocket_add_remove_and_ignored_frames(load_fixture) -> None:
    """Verify websocket add remove and ignored frames."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()

    with pytest.raises(ProtectProtocolError):
        client._process_websocket_payload(b"bad")
    await client.async_get_snapshot()
    client._process_websocket_payload(
        _frame({"action": "update", "modelKey": "camera", "id": "x"}) + _frame({})
    )
    with pytest.raises(ProtectProtocolError):
        client._process_websocket_payload(
            _frame({"action": "update", "modelKey": "sensor"}) + _frame({"id": 42})
        )
    with pytest.raises(ProtectProtocolError):
        client._process_websocket_payload(
            _frame({"action": "unknown", "modelKey": "sensor", "id": "x"}) + _frame({})
        )
    client._process_websocket_payload(
        _frame({"action": "add", "modelKey": "sensor", "id": "new"})
        + _frame(
            {
                "type": "UP-AirQuality",
                "airQuality": {"co2": {"status": "high", "value": 1200}},
            }
        )
    )
    assert len(client.snapshot.devices) == 2
    assert client.snapshot.devices[1].active_alarms == (
        ProtectAlarm("co2", "high", 1200),
    )
    client._process_websocket_payload(
        _frame({"action": "remove", "modelKey": "sensor", "id": "new"}) + _frame({})
    )
    assert len(client.snapshot.devices) == 1
    client._process_websocket_payload(
        _frame({"action": "add", "modelKey": "sensor", "id": "new"})
        + _frame({"type": "UP-AirQuality", "airQuality": {"co2": 500}})
    )
    assert client.snapshot.devices[1].active_alarms == ()


async def test_websocket_alarm_start_end_and_unsubscribe(load_fixture) -> None:
    """Verify websocket alarm start end and unsubscribe."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    alarm_callback = MagicMock()
    update_callback = MagicMock()
    unsubscribe = client.subscribe_alarm_events(alarm_callback)
    client.set_update_callback(update_callback)

    started = events["extreme_started"]
    client._process_websocket_payload(
        _frame(started["action"]) + _frame(started["data"])
    )

    alarm = client.snapshot.devices[0].active_alarms[0]
    assert (alarm.metric, alarm.status, alarm.value) == ("co2", "high", 1200)
    normalized = alarm_callback.call_args.args[0]
    assert (
        normalized.device_id,
        normalized.metric,
        normalized.transition,
        normalized.status,
        normalized.value,
    ) == ("anonymous-air-quality-id", "co2", "started", "high", 1200)

    ended = events["extreme_ended"]
    in_progress = ended["action"] | {"action": "update"}
    client._process_websocket_payload(
        _frame(in_progress) + _frame({"metadata": {"sensorValue": {"text": 1250}}})
    )
    assert alarm_callback.call_count == 1

    client._process_websocket_payload(_frame(ended["action"]) + _frame(ended["data"]))
    assert client.snapshot.devices[0].active_alarms == ()
    assert alarm_callback.call_args.args[0].transition == "ended"
    assert update_callback.call_count == 2

    unsubscribe()
    client._process_websocket_payload(
        _frame(started["action"] | {"id": "second-event"})
        + _frame(started["data"] | {"id": "second-event"})
    )
    assert alarm_callback.call_count == 2


async def test_alarm_sources_are_aggregated_before_emitting_end(load_fixture) -> None:
    """Verify alarm sources are aggregated before emitting end."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)

    started = events["extreme_started"]
    ended = events["extreme_ended"]
    client._process_websocket_payload(
        _frame(started["action"]) + _frame(started["data"])
    )
    client._process_websocket_payload(
        _frame(
            {
                "action": "update",
                "modelKey": "sensor",
                "id": "anonymous-air-quality-id",
            }
        )
        + _frame({"airQuality": {"co2": {"status": "high", "value": 1250}}})
    )
    client._process_websocket_payload(_frame(ended["action"]) + _frame(ended["data"]))

    assert callback.call_count == 1
    assert client.snapshot.devices[0].active_alarms == (
        ProtectAlarm("co2", "high", 1250),
    )

    client._process_websocket_payload(
        _frame(
            {
                "action": "update",
                "modelKey": "sensor",
                "id": "anonymous-air-quality-id",
            }
        )
        + _frame({"airQuality": {"co2": {"status": "neutral", "value": 800}}})
    )
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"
    assert client.snapshot.devices[0].active_alarms == ()


async def test_multiple_event_ids_keep_metric_active_until_all_end(
    load_fixture,
) -> None:
    """Verify multiple event ids keep metric active until all end."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)

    started = events["extreme_started"]
    ended = events["extreme_ended"]
    for event_id in ("first-event", "second-event"):
        client._process_websocket_payload(
            _frame(started["action"] | {"id": event_id})
            + _frame(started["data"] | {"id": event_id})
        )
    assert callback.call_count == 1

    client._process_websocket_payload(
        _frame(ended["action"] | {"id": "first-event"})
        + _frame(ended["data"] | {"id": "first-event"})
    )
    assert callback.call_count == 1
    assert client.snapshot.devices[0].active_alarms

    client._process_websocket_payload(
        _frame(ended["action"] | {"id": "second-event"})
        + _frame(ended["data"] | {"id": "second-event"})
    )
    assert callback.call_count == 2
    assert client.snapshot.devices[0].active_alarms == ()


def test_removed_device_alarm_bookkeeping_is_isolated() -> None:
    """Verify removed device alarm bookkeeping is isolated."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    removed_key = ("removed-device", "co2")
    retained_key = ("retained-device", "vape")
    client._sensor_alarm_states = {
        removed_key: ProtectAlarm("co2", "high", 1200),
        retained_key: ProtectAlarm("vape", "detected"),
    }
    client._alarm_states = client._sensor_alarm_states.copy()
    client._alarm_event_devices = {
        "removed-event": "removed-device",
        "retained-event": "retained-device",
    }
    client._active_alarm_events = {
        "removed-event": ProtectAlarm("co2", "high", 1200),
        "retained-event": ProtectAlarm("vape", "detected"),
    }
    client._raw_alarm_events = {
        "removed-event": {"type": "sensorExtremeValues"},
        "retained-event": {"type": "sensorVape"},
    }

    client._clear_device_alarm_states("removed-device")

    assert client._sensor_alarm_states == {
        retained_key: ProtectAlarm("vape", "detected")
    }
    assert client._alarm_states == {retained_key: ProtectAlarm("vape", "detected")}
    assert client._alarm_event_devices == {"retained-event": "retained-device"}
    assert client._active_alarm_events == {
        "retained-event": ProtectAlarm("vape", "detected")
    }
    assert client._raw_alarm_events == {"retained-event": {"type": "sensorVape"}}


async def test_websocket_vape_and_alarm_edge_cases(load_fixture) -> None:
    """Verify websocket vape and alarm edge cases."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    events = json.loads(load_fixture("websocket_alarm_events.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)

    vape = events["vape_started"]
    client._process_websocket_payload(_frame(vape["action"]) + _frame(vape["data"]))
    event = callback.call_args.args[0]
    assert (event.metric, event.status, event.value) == ("vape", "detected", None)

    ignored = (
        ({"action": "update", "modelKey": "event"}, {}),
        (
            {"action": "noop", "modelKey": "event", "id": "x"},
            {"type": "sensorExtremeValues"},
        ),
        (
            {"action": "add", "modelKey": "event", "id": "x"},
            {"type": "motion", "device": "anonymous-air-quality-id"},
        ),
        (
            {"action": "add", "modelKey": "event", "id": "x"},
            {"type": "sensorExtremeValues", "device": 42},
        ),
        (
            {"action": "add", "modelKey": "event", "id": "x"},
            {
                "type": "sensorExtremeValues",
                "device": "anonymous-air-quality-id",
                "metadata": {"sensorType": {"text": 42}},
            },
        ),
    )
    for action, data in ignored:
        if data.get("type") == "sensorExtremeValues":
            with pytest.raises(ProtectProtocolError):
                client._process_websocket_payload(_frame(action) + _frame(data))
        else:
            client._process_websocket_payload(_frame(action) + _frame(data))
    assert callback.call_count == 1


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("12.5", 12.5), ("invalid", None), (True, None), (None, None)],
)
def test_alarm_details_value_normalization(raw_value, expected) -> None:
    """Verify alarm details value normalization."""
    assert PrivateProtectClient._alarm_details(
        {
            "type": "sensorExtremeValues",
            "metadata": {
                "sensorType": "co2",
                "status": "high",
                "sensorValue": raw_value,
            },
        }
    ) == ("co2", "high", expected)


def test_alarm_details_rejects_missing_metadata() -> None:
    """Verify alarm details rejects missing metadata."""
    assert PrivateProtectClient._alarm_details({"type": "sensorExtremeValues"}) == (
        None,
        None,
        None,
    )


async def test_start_and_close_background_task() -> None:
    """Verify start and close background task."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    blocker = asyncio.Event()
    client._websocket_loop = AsyncMock(side_effect=blocker.wait)
    client.start_websocket()
    assert client._ws_task is not None
    await client.async_close()
    assert client._ws_task is None
    await client.async_close()


async def test_websocket_loop_recovers_from_connection_error() -> None:
    """Verify websocket loop recovers from connection error."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._websocket_once = AsyncMock(side_effect=ClientConnectionError())

    async def stop_after_backoff(_delay):
        """End the reconnect loop at its first backoff without actually sleeping."""
        client._closing = True

    with patch(
        "custom_components.unifi_air_quality.api.asyncio.sleep",
        side_effect=stop_after_backoff,
    ):
        await client._websocket_loop()
    assert client.websocket_connected is False


async def test_websocket_loop_resets_backoff_after_clean_disconnect() -> None:
    """Verify websocket loop resets backoff after clean disconnect."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._websocket_once = AsyncMock()

    async def stop_after_backoff(delay):
        """Check the reset one-second delay, then stop the loop without sleeping."""
        assert delay == 1
        client._closing = True

    with patch(
        "custom_components.unifi_air_quality.api.asyncio.sleep",
        side_effect=stop_after_backoff,
    ):
        await client._websocket_loop()
    client._websocket_once.assert_awaited_once()


async def test_websocket_loop_stops_after_auth_rejection() -> None:
    """Verify websocket loop stops after auth rejection."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._headers["Cookie"] = "expired"
    client._websocket_once = AsyncMock(side_effect=ProtectInvalidAuth())
    client._async_authenticate = AsyncMock(side_effect=ProtectCannotConnect())

    async def stop_after_backoff(_delay):
        """Stop the loop defensively if its forbidden post-auth backoff is reached."""
        client._closing = True

    with patch(
        "custom_components.unifi_air_quality.api.asyncio.sleep",
        side_effect=stop_after_backoff,
    ) as sleep:
        await client._websocket_loop()
    client._async_authenticate.assert_not_awaited()
    client._websocket_once.assert_awaited_once()
    sleep.assert_not_awaited()
    assert not client.websocket_connected


async def test_stream_auth_failure_notifies_owner_once_and_blocks_later_logins():
    """Verify stream auth failure notifies owner once and blocks later logins."""
    session = FakeSession(FakeResponse(status=401), FakeResponse())
    client = _client(session)
    callback = MagicMock()
    client.set_auth_failure_callback(callback)
    with patch("custom_components.unifi_air_quality.api.asyncio.sleep") as sleep:
        await client._websocket_loop()
    callback.assert_called_once_with()
    sleep.assert_not_called()
    for _ in range(3):
        with pytest.raises(ProtectInvalidAuth):
            await client.async_get_snapshot()
    assert len(session.post_calls) == 1
    await client.async_close()
    assert session.closed


async def test_websocket_authenticates_and_uses_last_update_id(load_fixture) -> None:
    """Verify websocket authenticates and uses last update id."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    websocket = FakeWebsocket([])
    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "TOKEN=anonymous")]),
        FakeResponse(payload=payload),
        websocket,
    )
    client = _client(session)
    await client.async_get_snapshot()
    client._headers.clear()

    await client._websocket_once()

    assert session.ws_calls[0][0].endswith("?lastUpdateId=anonymous-update-id")
    assert client.websocket_connected is True


@pytest.mark.parametrize("status", [401, 403])
async def test_websocket_maps_auth_handshake_error(status) -> None:
    """Verify websocket maps auth handshake error."""
    error = WSServerHandshakeError(
        MagicMock(real_url="https://protect.local"),
        (),
        status=status,
        message="unauthorized",
    )
    session = FakeSession(FakeResponse(), FakeResponse(status=401), error)
    client = _client(session)
    client._headers["Cookie"] = "expired"

    expected = ProtectInvalidAuth if status == 401 else ProtectStreamPermissionDenied
    with pytest.raises(expected):
        await client._websocket_once()


async def test_websocket_preserves_non_auth_handshake_error() -> None:
    """Verify websocket preserves non auth handshake error."""
    error = WSServerHandshakeError(
        MagicMock(real_url="https://protect.local"),
        (),
        status=500,
        message="failure",
    )
    session = FakeSession(FakeResponse(), FakeResponse(), error)
    client = _client(session)
    client._headers["Cookie"] = "valid"

    with pytest.raises(WSServerHandshakeError):
        await client._websocket_once()


async def test_websocket_loop_propagates_cancellation() -> None:
    """Verify websocket loop propagates cancellation."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._websocket_once = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await client._websocket_loop()


def test_rebuild_before_initial_snapshot_is_safe() -> None:
    """Verify rebuild before initial snapshot is safe."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._rebuild_snapshot("ignored")
    assert client.snapshot is None


@pytest.mark.parametrize("status", [401, 403])
async def test_expired_bootstrap_session_renews_without_reauth(status, load_fixture):
    """Verify expired bootstrap session renews without reauth."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "SESSION=synthetic-renewed")]),
        [FakeResponse(status=status), FakeResponse(payload=payload)],
    )
    client = _client(session)
    client._headers = {"Cookie": "SESSION=synthetic-expired"}

    snapshot = await client.async_get_snapshot()

    assert snapshot.devices
    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 2
    assert (
        session.get_calls[0][1]["headers"]["Cookie"]
        != (session.get_calls[1][1]["headers"]["Cookie"])
    )


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_renewed_bootstrap_session_stops_after_one_retry(status):
    """Verify rejected renewed bootstrap session stops after one retry."""
    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "SESSION=synthetic-renewed")]),
        FakeResponse(status=status),
    )
    client = _client(session)
    client._headers["Cookie"] = "synthetic-expired"

    with pytest.raises(ProtectInvalidAuth):
        await client.async_get_snapshot()

    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 2
    assert client._headers == {}


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(status=401), ProtectInvalidAuth),
        (FakeResponse(status=403), ProtectInvalidAuth),
        (FakeResponse(error=ClientConnectionError()), ProtectCannotConnect),
        (FakeResponse(error=TimeoutError()), ProtectCannotConnect),
    ],
)
async def test_failed_session_renewal_keeps_correct_error(response, error):
    """Verify failed session renewal keeps correct error."""
    session = FakeSession(response, FakeResponse(status=401))
    client = _client(session)
    client._headers["Cookie"] = "synthetic-expired"

    with pytest.raises(error):
        await client.async_get_snapshot()

    assert len(session.post_calls) == 1
    assert len(session.get_calls) == 1
    assert client._headers == {}


@pytest.mark.parametrize("status", [401])
async def test_expired_patch_session_renews_and_applies_update_once(
    status, load_fixture
):
    """Verify expired patch session renews and applies update once."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    confirmed = json.loads(json.dumps(payload))
    confirmed["sensors"][0]["airQualitySettings"]["ringLedBrightness"] = 42
    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "SESSION=synthetic-renewed")]),
        [FakeResponse(payload=payload), FakeResponse(payload=confirmed)],
        patch=[FakeResponse(status=status), FakeResponse()],
    )
    client = _client(session)
    await client.async_get_snapshot()
    client._headers["Cookie"] = "synthetic-expired"
    callback = MagicMock()
    client.set_update_callback(callback)
    update = {"airQualitySettings": {"ringLedBrightness": 42}}

    await client.async_update_device("anonymous-air-quality-id", update)

    assert len(session.patch_calls) == 2
    assert [call[1]["json"] for call in session.patch_calls] == [update, update]
    callback.assert_called_once()
    assert (
        client.snapshot.devices[0].raw["airQualitySettings"]["ringLedBrightness"] == 42
    )


async def test_patch_retry_failure_does_not_change_local_state(load_fixture):
    """Verify patch retry failure does not change local state."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "SESSION=synthetic-renewed")]),
        FakeResponse(payload=payload),
        patch=FakeResponse(status=403),
    )
    client = _client(session)
    await client.async_get_snapshot()
    previous = client.snapshot
    callback = MagicMock()
    client.set_update_callback(callback)

    with pytest.raises(ProtectPermissionDenied):
        await client.async_update_device(
            "anonymous-air-quality-id",
            {"airQualitySettings": {"ringLedBrightness": 42}},
        )

    assert len(session.patch_calls) == 1
    assert client.snapshot is previous
    callback.assert_not_called()


@pytest.mark.parametrize("status", [401])
async def test_expired_websocket_session_renews_and_reconnects(status):
    """Verify expired websocket session renews and reconnects."""
    rejected = WSServerHandshakeError(MagicMock(), (), status=status)
    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "SESSION=synthetic-renewed")]),
        FakeResponse(),
        websocket=[rejected, FakeWebsocket([])],
    )
    client = _client(session)
    client._headers["Cookie"] = "synthetic-expired"

    await client._websocket_once()

    assert len(session.post_calls) == 1
    assert len(session.ws_calls) == 2
    assert client.websocket_connected


async def test_concurrent_expired_requests_share_one_session_renewal(load_fixture):
    """Verify concurrent expired requests share one session renewal."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    both_rejected = asyncio.Event()
    rejected_count = 0

    class ConcurrentRejection(FakeResponse):
        """Hold both rejected responses until their contexts have been entered."""

        async def __aenter__(self):
            """Release both 401 responses together to exercise shared renewal."""
            nonlocal rejected_count
            rejected_count += 1
            if rejected_count == 2:
                both_rejected.set()
            await both_rejected.wait()
            return self

    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "SESSION=synthetic-renewed")]),
        [
            ConcurrentRejection(status=401),
            ConcurrentRejection(status=401),
            FakeResponse(payload=payload),
            FakeResponse(payload=payload),
        ],
    )
    client = _client(session)
    client._headers["Cookie"] = "synthetic-expired"

    snapshots = await asyncio.wait_for(
        asyncio.gather(client.async_get_snapshot(), client.async_get_snapshot()), 2
    )

    assert all(snapshot.devices for snapshot in snapshots)
    assert len(session.post_calls) == 1
    # A late rejected request reuses the newer confirmed result without retrying.
    assert len(session.get_calls) == 3


async def test_stale_request_cannot_clear_a_newer_session():
    """Verify stale request cannot clear a newer session."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._headers["Cookie"] = "synthetic-current"
    client._auth_generation = 2
    client._invalidate_session(1)
    assert client._headers


@pytest.mark.parametrize("error", [ProtectCannotConnect(), TimeoutError()])
async def test_websocket_loop_survives_failed_login_and_retries(error):
    """Verify websocket loop survives failed login and retries."""
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._websocket_once = AsyncMock(side_effect=[error, None])

    async def backoff(delay):
        """Stop after the second stream attempt while omitting the real retry delay."""
        if client._websocket_once.await_count == 2:
            client._closing = True

    with patch(
        "custom_components.unifi_air_quality.api.asyncio.sleep", side_effect=backoff
    ):
        await client._websocket_loop()

    assert client._websocket_once.await_count == 2


async def test_coordinator_refresh_recovers_expiry_while_push_still_works(
    hass, load_fixture
):
    """Verify coordinator refresh recovers expiry while push still works."""
    from custom_components.unifi_air_quality.coordinator import (
        UnifiAirQualityCoordinator,
    )

    from .ha_test_helpers import MockConfigEntry

    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(
        FakeResponse(headers=[("Set-Cookie", "SESSION=synthetic-renewed")]),
        [FakeResponse(status=401), FakeResponse(payload=payload)],
    )
    client = _client(session)
    client._headers["Cookie"] = "synthetic-expired"
    entry = MockConfigEntry()
    entry.add_to_hass(hass)
    coordinator = UnifiAirQualityCoordinator(hass, entry, client)

    await coordinator.async_refresh()
    client._process_websocket_payload(
        _frame(
            {"action": "update", "modelKey": "sensor", "id": "anonymous-air-quality-id"}
        )
        + _frame({"isConnected": False})
    )
    client._process_websocket_payload(
        _frame(
            {"action": "update", "modelKey": "sensor", "id": "anonymous-air-quality-id"}
        )
        + _frame({"isConnected": True, "airQuality": {"co2": 700}})
    )

    assert coordinator.last_update_success
    assert coordinator.data.devices[0].is_connected
    assert coordinator.data.devices[0].raw["airQuality"]["co2"] == 700
    assert not hass.config_entries.flow.async_progress()


async def test_nox_push_and_event_frames_merge_and_end_without_duplicates(load_fixture):
    """Verify nox push and event frames merge and end without duplicates."""
    payload = json.loads(load_fixture("bootstrap_nox.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)

    sensor_action = {
        "action": "update",
        "modelKey": "sensor",
        "id": "anonymous-air-quality-id",
    }
    event_action = {"action": "add", "modelKey": "event", "id": "anonymous-event-id"}
    client._process_websocket_payload(
        _frame(sensor_action)
        + _frame({"airQuality": {"nox": {"status": "high", "value": 120}}})
    )
    client._process_websocket_payload(
        _frame(event_action)
        + _frame(
            {
                "type": "sensorExtremeValues",
                "device": "anonymous-air-quality-id",
                "metadata": {"sensorType": "nox", "status": "high", "sensorValue": 120},
            }
        )
    )
    assert callback.call_count == 1
    assert callback.call_args.args[0] == ProtectAlarmEvent(
        "anonymous-air-quality-id", "nox", "started", "high", 120
    )
    client._process_websocket_payload(
        _frame(sensor_action)
        + _frame({"airQuality": {"nox": {"status": "neutral", "value": 42}}})
    )
    assert client.snapshot.devices[0].active_alarms
    client._process_websocket_payload(
        _frame(event_action | {"action": "remove"}) + _frame({})
    )
    assert callback.call_count == 2
    assert callback.call_args.args[0].transition == "ended"
    assert client.snapshot.devices[0].active_alarms == ()
    assert client.snapshot.devices[0].raw["airQuality"]["nox"]["value"] == 42
