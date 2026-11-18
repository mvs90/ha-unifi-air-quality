"""Adversarial regression coverage for transport, auth, inventory and aliases."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import WSServerHandshakeError

from custom_components.unifi_air_quality.api import (
    MAX_BOOTSTRAP_BYTES,
    PrivateProtectClient,
    ProtectProtocolError,
    ProtectStreamPermissionDenied,
    _contains_update,
    _host_for_url,
    snapshot_from_bootstrap,
)
from custom_components.unifi_air_quality.event import ALARM_EVENT_TYPES

from .test_client import FakeResponse, FakeSession, _client
from .test_recovery import push


@pytest.mark.parametrize(
    ("host", "canonical"),
    [
        ("Protect.LOCAL.", "protect.local"),
        ("127.0.0.1", "127.0.0.1"),
        ("::1", "[::1]"),
        ("[2001:0db8::1]", "[2001:db8::1]"),
        ("münchen.local", "xn--mnchen-3ya.local"),
    ],
)
def test_origin_accepts_only_canonical_host_parts(host, canonical):
    """Accept legitimate DNS names and both IP families."""
    assert _host_for_url(host) == canonical


@pytest.mark.parametrize(
    "host",
    [
        "example.com/evil",
        "example.com?next=evil",
        "example.com#fragment",
        "example.com@evil",
        "example.com%2fattack",
        "[::1]:443",
        "bad..domain",
        "foo bar",
        "evil\\another",
        "https://evil",
        "999.999.999.999",
        "invalid_host",
    ],
)
def test_origin_rejects_untrusted_host_syntax(host):
    """Reject scheme, authority, path, URL-encoding and host spoofing."""
    with pytest.raises(ProtectProtocolError):
        _host_for_url(host)


@pytest.mark.parametrize("port", [-1, 0, 65536, True, "443"])
def test_origin_rejects_noncanonical_port(port):
    """Never construct authenticated URLs with unvalidated ports."""
    with pytest.raises(ProtectProtocolError):
        PrivateProtectClient(
            FakeSession(FakeResponse(), FakeResponse()),
            host="protect.local",
            port=port,
            username="user",
            password="synthetic",
            verify_ssl=True,
        )


@pytest.mark.parametrize(
    ("confirmed", "requested", "expected"),
    [
        ({"enabled": True}, {"enabled": 1}, False),
        ({"enabled": 1}, {"enabled": True}, False),
        ({"volume": 1}, {"volume": 1.0}, True),
        ({"volume": True}, {"volume": 1}, False),
        ({"volume": None}, {"volume": None}, True),
        ({}, {"volume": None}, False),
        ({"nested": {}}, {"nested": {"key": None}}, False),
        ({"nested": {"key": None}}, {"nested": {"key": None}}, True),
        ({"name": "1"}, {"name": 1}, False),
    ],
)
def test_readback_verification_respects_wire_types(confirmed, requested, expected):
    """Boolean, numeric and intentionally absent settings are not conflated."""
    assert _contains_update(confirmed, requested) is expected


@pytest.mark.parametrize(
    "inventory",
    [
        [None],
        [123],
        [{"id": "same", "model": "UP-AirQuality"}, {"id": "same", "type": "UP-Sense"}],
        [{"id": "", "type": "UP-Sense"}],
        [{"id": "valid", "type": "UP-Sense"}, []],
    ],
)
def test_inventory_fails_closed_on_structural_ambiguity(inventory):
    """A damaged Protect inventory cannot report all devices removed."""
    with pytest.raises(ProtectProtocolError):
        snapshot_from_bootstrap({"nvr": {"id": "console"}, "sensors": inventory})


def test_empty_and_mixed_inventories_remain_supported():
    """An empty console and an unknown but valid sensor type are permitted."""
    assert snapshot_from_bootstrap(
        {"nvr": {"id": "console"}, "sensors": []}
    ).devices == ()
    payload = {
        "nvr": {"id": "console"},
        "sensors": [
            {"id": "future", "type": "future-model"},
            {"id": "air", "type": "UP-AirQuality"},
        ],
    }
    assert [device.id for device in snapshot_from_bootstrap(payload).devices] == ["air"]


@pytest.mark.parametrize("phase", ["login", "bootstrap", "write"])
async def test_authenticated_requests_reject_redirects_without_following(
    phase, load_fixture
):
    """Do not forward credentials, cookies or settings to a redirected origin."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(
        FakeResponse(status=302 if phase == "login" else 200),
        FakeResponse(status=302 if phase == "bootstrap" else 200, payload=payload),
        patch=FakeResponse(status=307),
    )
    client = _client(session)
    if phase == "write":
        await client.async_get_snapshot()
    with pytest.raises(ProtectProtocolError):
        if phase == "write":
            await client.async_update_device(
                "anonymous-air-quality-id", {"ledSettings": {"isEnabled": True}}
            )
        else:
            await client.async_get_snapshot()
    calls = {
        "login": session.post_calls,
        "bootstrap": session.get_calls,
        "write": session.patch_calls,
    }[phase]
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["timeout"].total < 120


async def test_write_sensor_id_rejects_path_escape(load_fixture):
    """Reject encoded slash and path traversal before issuing a PATCH."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()
    for identifier in ("../admin", "abc%2Fdef", "a?b", "a#b"):
        client._raw_devices[identifier] = {"id": identifier}
        with pytest.raises(ProtectProtocolError):
            await client.async_update_device(
                identifier, {"ledSettings": {"isEnabled": True}}
            )
    assert not session.patch_calls


async def test_bootstrap_fails_closed_on_header_and_chunked_overflow(load_fixture):
    """Bound decoded response size even without a Content-Length header."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    oversized_header = FakeResponse(
        payload=payload, headers={"Content-Length": str(MAX_BOOTSTRAP_BYTES + 1)}
    )
    client = _client(
        FakeSession(
            FakeResponse(),
            [oversized_header, FakeResponse(payload=payload)],
        )
    )
    with pytest.raises(ProtectProtocolError, match="byte limit"):
        await client.async_get_snapshot()
    assert (await client.async_get_snapshot()).devices

    client._session.get_response = FakeResponse(payload=payload)
    with patch(
        "custom_components.unifi_air_quality.api.MAX_BOOTSTRAP_BYTES", 100
    ):
        with pytest.raises(ProtectProtocolError, match="byte limit"):
            await client.async_get_snapshot()
    assert (await client.async_get_snapshot()).devices


async def test_legacy_alarm_metrics_share_canonical_entity_keys(load_fixture):
    """Normalize old wire metric names for events, health and aggregate alarms."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    observed = []
    client.subscribe_alarm_events(observed.append)
    push(
        client,
        {
            "type": "sensorExtremeValues",
            "device": "anonymous-air-quality-id",
            "metadata": {"sensorType": "pm2_5", "status": "high"},
        },
        model="event",
        operation="add",
        id="pm-legacy",
    )
    assert len(observed) == 1
    assert observed[0].metric == "pm2p5"
    assert "pm2p5_alarm_started" in ALARM_EVENT_TYPES
    assert client.is_alarm_fresh("anonymous-air-quality-id", "pm2p5")
    assert client.snapshot.devices[0].active_alarms[0].metric == "pm2p5"
    push(
        client, {"airQuality": {"pm2_5": {"status": "high", "value": 15.2}}}
    )
    assert len(observed) == 1
    assert client.is_measurement_fresh("anonymous-air-quality-id", "pm2p5")


async def test_scalar_and_canonical_status_aliases_are_not_double_counted(load_fixture):
    """When both names appear, the canonical name wins and events remain unique."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    events = []
    client.subscribe_alarm_events(events.append)
    push(
        client,
        {
            "airQuality": {
                "pm2_5": {"status": "high", "value": 55},
                "pm2p5": {"status": "neutral", "value": 5},
            }
        },
    )
    assert not events
    assert client.is_alarm_fresh("anonymous-air-quality-id", "pm2p5")
    push(
        client, {"airQuality": {"vapeIndex": {"status": "detected", "value": 1}}}
    )
    assert events[0].metric == "vape"
    assert client.is_alarm_fresh("anonymous-air-quality-id", "vape")


async def test_old_malformed_response_cannot_disable_newer_valid_refresh(load_fixture):
    """A late protocol error must not override newer verified health."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    started, release = asyncio.Event(), asyncio.Event()

    class SlowInvalid(FakeResponse):
        """Yield broken JSON only after the second request has succeeded."""

        async def json(self, **kwargs):
            """Control completion order of an old synthetic read."""
            started.set()
            await release.wait()
            raise ValueError("synthetic malformed payload")

    session = FakeSession(
        FakeResponse(),
        [SlowInvalid(), FakeResponse(payload=payload)],
    )
    client = _client(session)
    old_task = asyncio.create_task(client.async_get_snapshot())
    await asyncio.wait_for(started.wait(), 1)
    fresh = await client.async_get_snapshot()
    release.set()
    assert await old_task is fresh
    assert client.snapshot is fresh
    assert not client.protocol_error


async def test_old_denial_cannot_renew_after_newer_verified_refresh(load_fixture):
    """A superseded read must leave the current cookie and permissions intact."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    started, release = asyncio.Event(), asyncio.Event()

    class SlowDenied(FakeResponse):
        """Delay a 401 until a newer successful response is installed."""

        async def __aenter__(self):
            """Hold the request after the HTTP response is selected."""
            started.set()
            await release.wait()
            return await super().__aenter__()

    session = FakeSession(
        FakeResponse(headers={"Set-Cookie": "SESSION=synthetic"}),
        [SlowDenied(status=401), FakeResponse(payload=payload)],
    )
    client = _client(session)
    old_task = asyncio.create_task(client.async_get_snapshot())
    await asyncio.wait_for(started.wait(), 1)
    good = await client.async_get_snapshot()
    release.set()
    assert await old_task is good
    assert client.snapshot is good
    assert not client.authentication_failed
    assert len(session.post_calls) == 1


@pytest.mark.parametrize("status", [401, 403])
async def test_stream_denial_preserves_authorized_rest_monitoring(status, load_fixture):
    """Denied update channel is degraded, not terminal credential failure."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    error = WSServerHandshakeError(MagicMock(), (), status=status)
    session = FakeSession(
        FakeResponse(headers={"Set-Cookie": "SESSION=synthetic"}),
        FakeResponse(payload=payload),
        websocket=[error, error],
    )
    client = _client(session)
    await client.async_get_snapshot()
    with pytest.raises(ProtectStreamPermissionDenied):
        await client._websocket_once()
    assert not client.authentication_failed
    assert (await client.async_get_snapshot()).devices
    for _url, args in session.ws_calls:
        assert args["allow_redirects"] is False


async def test_websocket_handshake_deadline_is_enforced():
    """A stalled opening handshake cannot occupy the coordinator indefinitely."""
    class HangingSession(FakeSession):
        """Model a server that never completes a WebSocket handshake."""

        async def ws_connect(self, url, **kwargs):
            """Wait forever unless the client's handshake deadline cancels us."""
            await asyncio.Event().wait()

    client = _client(HangingSession(FakeResponse(), FakeResponse()))
    with patch(
        "custom_components.unifi_air_quality.api._WS_OPEN_TIMEOUT_SECONDS", 0.01
    ):
        with pytest.raises(TimeoutError):
            await client._websocket_once()
    assert not client.websocket_connected
    assert not client.authentication_failed


async def test_cancelled_setting_write_releases_lock(load_fixture):
    """Cancelling a stalled PATCH does not retain the settings write lock."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    started = asyncio.Event()

    class HangingWrite(FakeResponse):
        """Wait at the response entrance until cancellation."""

        async def __aenter__(self):
            """Suspend the synthetic PATCH request without a response."""
            started.set()
            await asyncio.Event().wait()

    session = FakeSession(
        FakeResponse(), FakeResponse(payload=payload), patch=HangingWrite()
    )
    client = _client(session)
    await client.async_get_snapshot()
    operation = asyncio.create_task(
        client.async_update_device(
            "anonymous-air-quality-id", {"ledSettings": {"isEnabled": False}}
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert not client._write_lock.locked()
