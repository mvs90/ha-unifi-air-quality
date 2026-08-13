"""Tests for HTTP and push behavior of the private Protect client."""

import asyncio
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientConnectionError, WSMsgType
from multidict import CIMultiDict

from custom_components.unifi_air_quality.api import (
    PrivateProtectClient,
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectProtocolError,
)

HEADER = struct.Struct("!bbbbi")


class FakeResponse:
    def __init__(self, *, status=200, payload=None, headers=None, error=None):
        self.status = status
        self._payload = payload
        self.headers = CIMultiDict(headers or {})
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        if self._error:
            raise self._error

    async def json(self, **kwargs):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, post, get, websocket=None, patch=None):
        self.post_response = post
        self.get_response = get
        self.websocket = websocket
        self.patch_response = patch or FakeResponse()
        self.post_calls = []
        self.get_calls = []
        self.patch_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_response

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_response

    def patch(self, url, **kwargs):
        self.patch_calls.append((url, kwargs))
        return self.patch_response

    async def ws_connect(self, url, **kwargs):
        return self.websocket


class FakeMessage:
    def __init__(self, data, message_type=WSMsgType.BINARY):
        self.data = data
        self.type = message_type


class FakeWebsocket:
    def __init__(self, messages):
        self.messages = messages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def _frame(data: dict) -> bytes:
    payload = json.dumps(data).encode()
    return HEADER.pack(1, 1, 0, 0, len(payload)) + payload


def _client(session) -> PrivateProtectClient:
    return PrivateProtectClient(
        session,
        host="protect.local",
        port=443,
        username="user",
        password="not-real",
        verify_ssl=False,
    )


async def test_get_snapshot_authenticates_and_keeps_cookie(load_fixture) -> None:
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
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
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
    with pytest.raises(ProtectInvalidAuth):
        await unauthorized.async_update_device(
            "anonymous-air-quality-id", {"ledSettings": {"isEnabled": False}}
        )
    assert unauthorized._headers == {}

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
    client = _client(FakeSession(FakeResponse(status=status), FakeResponse()))
    with pytest.raises(ProtectInvalidAuth):
        await client.async_get_snapshot()


async def test_login_connection_error() -> None:
    client = _client(
        FakeSession(
            FakeResponse(error=ClientConnectionError("offline")), FakeResponse()
        )
    )
    with pytest.raises(ProtectCannotConnect):
        await client.async_get_snapshot()


async def test_bootstrap_auth_and_protocol_errors() -> None:
    client = _client(FakeSession(FakeResponse(), FakeResponse(status=401)))
    with pytest.raises(ProtectInvalidAuth):
        await client.async_get_snapshot()
    assert client._headers == {}

    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=ValueError())))
    with pytest.raises(ProtectProtocolError):
        await client.async_get_snapshot()

    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=[])))
    with pytest.raises(ProtectProtocolError):
        await client.async_get_snapshot()


async def test_websocket_push_updates_snapshot(load_fixture) -> None:
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
    callback.assert_called_once()


async def test_websocket_add_remove_and_ignored_frames(load_fixture) -> None:
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()

    client._process_websocket_payload(b"bad")
    client._process_websocket_payload(
        _frame({"action": "update", "modelKey": "camera", "id": "x"}) + _frame({})
    )
    client._process_websocket_payload(
        _frame({"action": "add", "modelKey": "sensor", "id": "new"})
        + _frame({"type": "UP-AirQuality", "airQuality": {"co2": 500}})
    )
    assert len(client.snapshot.devices) == 2
    client._process_websocket_payload(
        _frame({"action": "remove", "modelKey": "sensor", "id": "new"}) + _frame({})
    )
    assert len(client.snapshot.devices) == 1


async def test_start_and_close_background_task() -> None:
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    blocker = asyncio.Event()
    client._websocket_loop = AsyncMock(side_effect=blocker.wait)
    client.start_websocket()
    assert client._ws_task is not None
    await client.async_close()
    assert client._ws_task is None


async def test_websocket_loop_recovers_from_connection_error() -> None:
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._websocket_once = AsyncMock(side_effect=ClientConnectionError())

    async def stop_after_backoff(_delay):
        client._closing = True

    with patch(
        "custom_components.unifi_air_quality.api.asyncio.sleep",
        side_effect=stop_after_backoff,
    ):
        await client._websocket_loop()
    assert client.websocket_connected is False


async def test_websocket_loop_reauthenticates() -> None:
    client = _client(FakeSession(FakeResponse(), FakeResponse()))
    client._headers["Cookie"] = "expired"
    client._websocket_once = AsyncMock(side_effect=ProtectInvalidAuth())
    client._async_authenticate = AsyncMock(side_effect=ProtectCannotConnect())

    async def stop_after_backoff(_delay):
        client._closing = True

    with patch(
        "custom_components.unifi_air_quality.api.asyncio.sleep",
        side_effect=stop_after_backoff,
    ):
        await client._websocket_loop()
    assert client._headers == {}
