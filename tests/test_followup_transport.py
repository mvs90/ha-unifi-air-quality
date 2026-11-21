"""Adversarial regression coverage for transport, auth, inventory and aliases."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import WSServerHandshakeError

from custom_components.unifi_air_quality.api import (
    MAX_BOOTSTRAP_BYTES,
    PrivateProtectClient,
    ProtectCannotConnect,
    ProtectProtocolError,
    ProtectStreamPermissionDenied,
    _contains_update,
    _host_for_url,
    _normalize_sensor_update,
    snapshot_from_bootstrap,
)
from custom_components.unifi_air_quality.binary_sensor import (
    ALARM_DESCRIPTIONS,
    UnifiAirQualityAlarmBinarySensor,
)
from custom_components.unifi_air_quality.const import MEASUREMENT_ALIASES
from custom_components.unifi_air_quality.event import ALARM_EVENT_TYPES
from custom_components.unifi_air_quality.sensor import (
    SENSOR_DESCRIPTIONS,
    UnifiAirQualitySensor,
)

from .test_client import FakeResponse, FakeSession, _client
from .test_controls import _coordinator
from .test_recovery import push


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({}, {}),
        ({"airQuality": None}, {"airQuality": None}),
        ({"airQuality": {"co2": 1}}, {"airQuality": {"co2": 1}}),
        (
            {"airQuality": {"pm2_5": 8}},
            {"airQuality": {"pm2p5": 8}},
        ),
        (
            {"airQuality": {"pm2_5": 8, "pm2p5": 9}},
            {"airQuality": {"pm2p5": 9}},
        ),
        (
            {"airQuality": {"vapeIndex": 1, "vape": 2}},
            {"airQuality": {"vape": 2}},
        ),
    ],
)
def test_canonical_update_copy_isolated_and_deterministic(update, expected):
    """Do not mutate wire frames; canonical values win in mixed frames."""
    old = json.loads(json.dumps(update))
    assert _normalize_sensor_update(update) == expected
    assert update == old


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
    assert (
        snapshot_from_bootstrap({"nvr": {"id": "console"}, "sensors": []}).devices == ()
    )
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
    with patch("custom_components.unifi_air_quality.api.MAX_BOOTSTRAP_BYTES", 100):
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
    push(client, {"airQuality": {"pm2_5": {"status": "high", "value": 15.2}}})
    assert len(observed) == 1
    assert client.is_measurement_fresh("anonymous-air-quality-id", "pm2p5")


async def test_alias_push_updates_canonical_reading_with_failed_rest(load_fixture):
    """Do not keep an old canonical display alive with newer alias timestamps."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    payload["sensors"][0]["airQuality"]["pm2p5"] = 3
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()
    for value in (4, 8, 15):
        push(client, {"airQuality": {"pm2_5": value}})
        raw = client.snapshot.devices[0].raw["airQuality"]
        assert raw["pm2p5"] == value
        assert client.is_measurement_fresh("anonymous-air-quality-id", "pm2p5")
    from aiohttp import ClientConnectionError

    session.get_response = FakeResponse(error=ClientConnectionError())
    with pytest.raises(ProtectCannotConnect):
        await client.async_get_snapshot()
    assert client.snapshot.devices[0].raw["airQuality"]["pm2p5"] == 15


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
    push(client, {"airQuality": {"vapeIndex": {"status": "detected", "value": 1}}})
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
    assert len(session.ws_calls) == (1 if status == 403 else 2)


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


@pytest.mark.parametrize(("canonical", "legacy"), list(MEASUREMENT_ALIASES.items()))
async def test_all_metric_aliases_share_value_alarm_and_expiry(
    load_fixture, canonical, legacy
):
    """Keep legacy particle/vape values, alarms and entity freshness synchronized."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    payload["sensors"][0]["airQuality"] = {canonical: {"value": 1, "status": "neutral"}}
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    observed = []
    client.subscribe_alarm_events(observed.append)
    with patch(
        "custom_components.unifi_air_quality.api.monotonic", return_value=10
    ) as now:
        await client.async_get_snapshot()
        now.return_value = 131
        push(client, {"airQuality": {legacy: {"value": 42, "status": "high"}}})
        coordinator = _coordinator(client.snapshot.devices[0])
        coordinator.client = client
        sensor_description = next(
            item for item in SENSOR_DESCRIPTIONS if item.payload_key == canonical
        )
        alarm_description = next(
            item for item in ALARM_DESCRIPTIONS if item.payload_key == canonical
        )
        sensor = UnifiAirQualitySensor(
            coordinator, "anonymous-air-quality-id", sensor_description
        )
        alarm = UnifiAirQualityAlarmBinarySensor(
            coordinator, "anonymous-air-quality-id", alarm_description
        )
        assert sensor.native_value == 42
        assert sensor.available and alarm.available and alarm.is_on
        unique_ids = (sensor.unique_id, alarm.unique_id)
        assert len(observed) == 1 and observed[0].metric == canonical
        now.return_value = 252
        assert not sensor.available and not alarm.available
        push(client, {"airQuality": {legacy: {"value": 5, "status": "neutral"}}})
        coordinator.data = client.snapshot
        assert sensor.native_value == 5 and sensor.available
        assert alarm.is_on is False and alarm.available
        assert observed[-1].metric == canonical and observed[-1].transition == "ended"
        assert (sensor.unique_id, alarm.unique_id) == unique_ids


@pytest.mark.parametrize(("canonical", "legacy"), list(MEASUREMENT_ALIASES.items()))
async def test_partial_alias_update_preserves_legacy_cached_siblings(
    load_fixture, canonical, legacy
):
    """A status-only frame preserves a legacy value without renewing its age."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    payload["sensors"][0]["airQuality"] = {legacy: {"value": 3, "status": "normal"}}
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    with patch(
        "custom_components.unifi_air_quality.api.monotonic", return_value=10
    ) as now:
        await client.async_get_snapshot()
        now.return_value = 100
        push(client, {"airQuality": {legacy: {"status": "high"}}})
        raw = client.snapshot.devices[0].raw["airQuality"]
        assert raw[canonical] == {"value": 3, "status": "high"}
        assert legacy not in raw
        assert client._metric_verified_at[("anonymous-air-quality-id", canonical)] == 10
        now.return_value = 131
        assert not client.is_measurement_fresh("anonymous-air-quality-id", canonical)
        assert client.is_alarm_fresh("anonymous-air-quality-id", canonical)
        push(client, {"airQuality": {legacy: {"value": 9}}})
        assert client.snapshot.devices[0].raw["airQuality"][canonical] == {
            "value": 9,
            "status": "high",
        }
        assert client.is_measurement_fresh("anonymous-air-quality-id", canonical)
        assert client.is_alarm_fresh("anonymous-air-quality-id", canonical)


@pytest.mark.parametrize(("canonical", "legacy"), list(MEASUREMENT_ALIASES.items()))
@pytest.mark.parametrize("cached_legacy", [True, False])
@pytest.mark.parametrize("bootstrap_legacy", [True, False])
@pytest.mark.parametrize("pushed_field", ["status", "value"])
async def test_partial_alias_push_overlays_only_its_field_during_bootstrap(
    load_fixture, canonical, legacy, cached_legacy, bootstrap_legacy, pushed_field
):
    """Keep newly read REST siblings when an alias push arrives during the read."""
    cached_key = legacy if cached_legacy else canonical
    bootstrap_key = legacy if bootstrap_legacy else canonical
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    payload["sensors"][0]["airQuality"] = {
        cached_key: {"value": 3, "status": "normal"},
        "temperature": {"value": 20, "status": "normal"},
    }
    refreshed = json.loads(json.dumps(payload))
    refreshed["sensors"][0]["airQuality"] = {
        bootstrap_key: {"value": 50, "status": "critical"},
        "temperature": {"value": 25, "status": "normal"},
    }
    refreshed["sensors"][0]["name"] = "Updated from REST"
    refreshed["sensors"][0]["ledSettings"] = {"isEnabled": False, "level": 77}
    started, release = asyncio.Event(), asyncio.Event()

    class SlowBootstrap(FakeResponse):
        """Hold the HTTP body while one independent metric field is pushed."""

        async def json(self, **kwargs):
            """Publish the deterministic in-flight point before releasing REST."""
            started.set()
            await release.wait()
            return refreshed

    client = _client(
        FakeSession(
            FakeResponse(), [FakeResponse(payload=payload), SlowBootstrap()]
        )
    )
    device_id = "anonymous-air-quality-id"
    with patch(
        "custom_components.unifi_air_quality.api.monotonic", return_value=10
    ) as now:
        await client.async_get_snapshot()
        pending = asyncio.create_task(client.async_get_snapshot())
        try:
            await asyncio.wait_for(started.wait(), 1)
            now.return_value = 100
            pushed_value = "high" if pushed_field == "status" else 9
            push(client, {"airQuality": {legacy: {pushed_field: pushed_value}}})
            # A status update alone must not make the old cached number fresh.
            assert client._metric_verified_at[(device_id, canonical)] == (
                10 if pushed_field == "status" else 100
            )
            now.return_value = 110
            release.set()
            snapshot = await pending
        finally:
            release.set()
            await pending

        device = snapshot.devices[0]
        expected = {"value": 50, "status": "critical"} | {
            pushed_field: pushed_value
        }
        assert device.raw["airQuality"][canonical] == expected
        assert legacy not in device.raw["airQuality"]
        assert device.raw["airQuality"]["temperature"] == {
            "value": 25, "status": "normal"
        }
        assert device.name == "Updated from REST"
        assert device.raw["ledSettings"] == {"isEnabled": False, "level": 77}
        assert client.is_measurement_fresh(device_id, canonical)
        assert client.is_alarm_fresh(device_id, canonical)
        description = next(
            item for item in SENSOR_DESCRIPTIONS if item.payload_key == canonical
        )
        coordinator = _coordinator(device)
        coordinator.client = client
        sensor = UnifiAirQualitySensor(coordinator, device_id, description)
        assert sensor.native_value == expected["value"]
        assert sensor.extra_state_attributes == {"protect_status": expected["status"]}
