"""Exercise real aiohttp sockets for redirects, decoding and operation budgets."""

import asyncio
import gzip
import json
import zlib
from unittest.mock import patch

import pytest
from aiohttp import ClientTimeout, DummyCookieJar, web
from aiohttp.test_utils import TestServer
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from custom_components.unifi_air_quality.api import (
    ProtectCannotConnect,
    ProtectProtocolError,
    ProtectWriteUnconfirmed,
    _deep_merge,
    reject_protect_redirects,
)
from custom_components.unifi_air_quality.select import RingLedMetricSelect

from .test_client import _client
from .test_controls import _coordinator, _device

DEVICE_ID = "anonymous-air-quality-id"


@pytest.fixture
async def live_protect(hass, socket_enabled, load_fixture):
    """Serve disposable loopback Protect responses without contacting a console."""
    state = {
        "payload": json.loads(load_fixture("bootstrap_air_quality.json")),
        "redirect_phase": None,
        "redirect_target": None,
        "stall_phase": None,
        "release": asyncio.Event(),
        "writes": 0,
        "captures": 0,
        "body": None,
        "encoding": None,
        "ws_authenticated": False,
    }

    async def respond(request):
        """Inject one selected failure into an otherwise valid Protect endpoint."""
        if request.path == "/capture":
            state["captures"] += 1
            return web.json_response({})
        if request.path == "/api/auth/login":
            phase = "login"
        elif request.path == "/proxy/protect/api/bootstrap":
            phase = "bootstrap"
        elif request.path == "/proxy/protect/ws/updates":
            phase = "websocket"
        else:
            phase = "write"
        if state["redirect_phase"] == phase:
            return web.Response(
                status=307, headers={"Location": state["redirect_target"]}
            )
        if state["stall_phase"] == phase or (
            state["stall_phase"] == "verification"
            and phase == "bootstrap"
            and state["writes"]
        ):
            await state["release"].wait()
        if phase == "login":
            await request.json()
            response = web.json_response({}, headers={"x-csrf-token": "fixture-csrf"})
            response.set_cookie("SESSION", "fixture-session")
            return response
        if phase == "bootstrap":
            if state["body"] is not None:
                headers = {"Content-Type": "application/json"}
                if state["encoding"]:
                    headers["Content-Encoding"] = state["encoding"]
                response = web.StreamResponse(headers=headers)
                await response.prepare(request)
                await response.write(state["body"])
                await response.write_eof()
                return response
            return web.json_response(state["payload"])
        if phase == "write":
            state["writes"] += 1
            _deep_merge(state["payload"]["sensors"][0], await request.json())
            return web.json_response({})
        state["ws_authenticated"] = (
            request.cookies.get("SESSION") == "fixture-session"
            and request.headers.get("x-csrf-token") == "fixture-csrf"
        )
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        await websocket.close()
        return websocket

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", respond)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    session = async_create_clientsession(
        hass, cookie_jar=DummyCookieJar(), middlewares=(reject_protect_redirects,)
    )
    client = _client(session)
    # Test-only loopback URLs replace the production HTTPS/WSS origin.
    client._base_url = str(server.make_url("")).rstrip("/")
    client._ws_url = str(server.make_url("/proxy/protect/ws/updates")).replace(
        "http://", "ws://", 1
    )
    try:
        yield client, state, server
    finally:
        state["release"].set()
        await client.async_close()
        await server.close()


async def test_real_websocket_uses_supported_aiohttp_api(live_protect):
    """A real authenticated upgrade succeeds without unsupported kwargs."""
    client, state, _server = live_protect
    await client.async_get_snapshot()
    await client._websocket_once()
    assert state["ws_authenticated"]
    assert client.is_device_fresh(DEVICE_ID)


@pytest.mark.parametrize("phase", ["login", "bootstrap", "write", "websocket"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_real_redirect_never_forwards_session(live_protect, phase, foreign):
    """Refuse both same-origin and foreign redirects before any follow-up request."""
    client, state, server = live_protect
    captures = []

    async def capture(request):
        """Record any forbidden credential-bearing request to the second origin."""
        captures.append(request.method)
        return web.json_response({})

    app = web.Application()
    app.router.add_route("*", "/capture", capture)
    async with TestServer(app, host="127.0.0.1") as target:
        if phase in ("write", "websocket"):
            await client.async_get_snapshot()
        state["redirect_phase"] = phase
        state["redirect_target"] = str(
            (target if foreign else server).make_url("/capture")
        )
        with pytest.raises(ProtectProtocolError, match="redirect"):
            if phase == "write":
                await client.async_update_device(
                    DEVICE_ID, {"ledSettings": {"isEnabled": False}}
                )
            elif phase == "websocket":
                await client._websocket_once()
            else:
                await client.async_get_snapshot()
    assert not captures
    assert state["captures"] == 0


@pytest.mark.parametrize("phase", ["login", "bootstrap", "write", "verification"])
async def test_real_stalled_http_deadline_and_recovery(live_protect, phase):
    """Every HTTP phase times out, releases state and permits a later refresh/write."""
    client, state, _server = live_protect
    if phase in ("write", "verification"):
        await client.async_get_snapshot()
    state["stall_phase"] = phase
    error = ProtectWriteUnconfirmed if phase == "verification" else ProtectCannotConnect
    with patch(
        "custom_components.unifi_air_quality.api._HTTP_TIMEOUT",
        ClientTimeout(total=0.05, connect=0.05, sock_connect=0.05, sock_read=0.05),
    ):
        with pytest.raises(error):
            if phase in ("write", "verification"):
                await client.async_update_device(
                    DEVICE_ID, {"ledSettings": {"isEnabled": False}}
                )
            else:
                await client.async_get_snapshot()
    assert not client._pending_bootstraps
    assert not client._write_lock.locked()
    assert not client._auth_lock.locked()
    assert not client.authentication_failed
    state["stall_phase"] = None
    state["release"].set()
    assert (await client.async_get_snapshot()).devices
    await client.async_update_device(DEVICE_ID, {"ledSettings": {"isEnabled": False}})
    assert client.snapshot.devices[0].raw["ledSettings"]["isEnabled"] is False


@pytest.mark.parametrize("compressed", [False, True])
async def test_real_chunked_body_is_bounded_after_decompression(
    live_protect, compressed
):
    """Count decoded bytes even with unknown length and a small gzip wire body."""
    client, state, _server = live_protect
    body = json.dumps({"padding": "x" * 8192}).encode()
    state["body"] = gzip.compress(body) if compressed else body
    state["encoding"] = "gzip" if compressed else None
    with patch("custom_components.unifi_air_quality.api.MAX_BOOTSTRAP_BYTES", 1024):
        with pytest.raises(ProtectProtocolError, match="byte limit"):
            await client.async_get_snapshot()
    assert client.protocol_error
    state["body"] = None
    assert (await client.async_get_snapshot()).devices
    assert not client.protocol_error


@pytest.mark.parametrize("body", [b'{"broken":', b"\xff\xfe\x00", b"[]"])
async def test_real_malformed_bootstrap_fails_closed(live_protect, body):
    """Malformed streamed JSON produces a controlled protocol error and recovers."""
    client, state, _server = live_protect
    state["body"] = body
    with pytest.raises(ProtectProtocolError):
        await client.async_get_snapshot()
    assert client.protocol_error
    state["body"] = None
    assert (await client.async_get_snapshot()).devices
    assert not client.protocol_error


@pytest.mark.parametrize("wire", [True, False, None, "1", float("nan"), float("inf")])
def test_select_rejects_boolean_and_non_numeric_enums(wire):
    """Python bool/int equality must never turn malformed settings into an option."""
    device = _device()
    device.raw["airQualitySettings"]["ringLedMetric"] = wire
    assert RingLedMetricSelect(_coordinator(device), "device-id").current_option is None


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
async def test_valid_compressed_inventory_keeps_json_shape(live_protect, encoding):
    """Support negotiated encodings with a bounded incremental decoder."""
    client, state, _server = live_protect
    body = json.dumps(state["payload"]).encode()
    state["body"] = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
    state["encoding"] = encoding
    assert (await client.async_get_snapshot()).devices


@pytest.mark.parametrize(
    ("encoding", "body"),
    [
        ("br", b"unsupported"),
        ("gzip", b"invalid"),
        ("gzip", gzip.compress(b"{}")[:-3]),
        ("gzip", gzip.compress(b"{}") + b"trailing"),
    ],
)
async def test_invalid_compressed_inventory_is_controlled(live_protect, encoding, body):
    """Reject unsupported, broken, truncated and ambiguous compressed bodies."""
    client, state, _server = live_protect
    state["body"], state["encoding"] = body, encoding
    with pytest.raises(ProtectProtocolError):
        await client.async_get_snapshot()
    assert client.protocol_error
