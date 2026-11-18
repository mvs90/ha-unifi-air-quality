"""Integration and adversarial regressions for issues 1–10."""

import asyncio
import json
import struct
import zlib
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientConnectionError, ClientSSLError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.unifi_air_quality.api import (
    ProtectCannotConnect,
    ProtectInvalidAuth,
    ProtectProtocolError,
    ProtectWriteUnconfirmed,
    snapshot_from_bootstrap,
)
from custom_components.unifi_air_quality.binary_sensor import (
    ALARM_DESCRIPTIONS,
    UnifiAirQualityAlarmBinarySensor,
)
from custom_components.unifi_air_quality.coordinator import UnifiAirQualityCoordinator
from custom_components.unifi_air_quality.diagnostics import safe_version, sanitize_raw
from custom_components.unifi_air_quality.entity import UnifiAirQualityEntity
from custom_components.unifi_air_quality.websocket import (
    MAX_DECOMPRESSED_BYTES,
    MAX_FRAME_BYTES,
    MAX_MESSAGE_BYTES,
    WebsocketDecodeError,
    decode_message,
)

from .ha_test_helpers import MockConfigEntry
from .test_client import FakeResponse, FakeSession, _client, _frame
from .test_recovery import push


@pytest.mark.parametrize(
    "change",
    [
        {"id": None},
        {"isConnected": "false"},
        {"airQuality": []},
        {"airQualitySettings": False},
        {"ledSettings": "changed"},
        {"airQuality": {"co2": "600"}},
        {"airQuality": {"co2": True}},
        {"airQuality": {"co2": float("nan")}},
        {"airQuality": {"co2": {"status": "new-status", "value": 500}}},
        {"airQuality": {"co2": {"status": [], "value": 500}}},
    ],
)
def test_incompatible_required_fields_fail_closed(change):
    """Changed required types/statuses cannot become current measurements/alarms."""
    with pytest.raises(ProtectProtocolError):
        snapshot_from_bootstrap(
            {
                "nvr": {"id": "nvr"},
                "sensors": [{"id": "device", "type": "UP-AirQuality", **change}],
            }
        )


def test_optional_fields_and_null_measurements_are_compatible():
    """Missing optional data and unknown future metrics do not invent readings."""
    snapshot = snapshot_from_bootstrap(
        {
            "nvr": {"id": "nvr"},
            "sensors": [
                {
                    "id": "device",
                    "type": "UP-AirQuality",
                    "future": {"personal": 123},
                    "airQuality": {
                        "co2": None,
                        "nox": {"status": "neutral"},
                        "futureMetric": {"newShape": []},
                    },
                }
            ],
        }
    )
    assert snapshot.devices[0].active_alarms == ()


@pytest.mark.parametrize("compressed", [False, True])
def test_decoder_accepts_near_limit(compressed):
    """Legitimate messages at the exact wire/expansion bound remain supported."""
    size = MAX_DECOMPRESSED_BYTES if compressed else MAX_FRAME_BYTES
    raw = b'{"v":"' + b"x" * (size - 8) + b'"}'
    wire = zlib.compress(raw) if compressed else raw
    frame = struct.pack("!bbbbi", 1, 1, int(compressed), 0, len(wire)) + wire
    assert len(decode_message(frame + _frame({}))[0]["v"]) == size - 8


@pytest.mark.parametrize(
    "wire",
    [
        b"x" * (MAX_MESSAGE_BYTES + 1),
        struct.pack("!bbbbi", 1, 1, 0, 0, MAX_FRAME_BYTES + 1),
        struct.pack("!bbbbi", 1, 1, 0, 0, -1),
        zlib.compress(b"x" * (MAX_DECOMPRESSED_BYTES + 1)),
        zlib.compress(b"{}")[:-1],
        zlib.compress(b"{}") + b"extra",
    ],
)
def test_decoder_rejects_oversize_truncated_or_bomb(wire):
    """Reject excessive expansion and malformed streams before JSON allocation."""
    if wire.startswith(b"x\x9c"):
        wire = struct.pack("!bbbbi", 1, 1, 1, 0, len(wire)) + wire
    with pytest.raises(WebsocketDecodeError):
        decode_message(wire + _frame({}))


async def test_freshness_boundaries_recovery_and_per_device_isolation(load_fixture):
    """One active sensor cannot keep another sensor's old readings available."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    payload["sensors"].append({**deepcopy(payload["sensors"][0]), "id": "second"})
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=100):
        await client.async_get_snapshot()
    for now, expected in [(219.999, True), (220, False)]:
        with patch(
            "custom_components.unifi_air_quality.api.monotonic", return_value=now
        ):
            assert client.is_device_fresh("second") is expected
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=230):
        push(client, {"airQuality": {"co2": 700}})
        assert client.is_device_fresh("anonymous-air-quality-id")
        assert not client.is_device_fresh("second")
        assert not client.is_device_fresh("missing")
        await client.async_get_snapshot()
        assert client.is_device_fresh("second")


async def test_coordinator_brief_outage_protocol_error_and_recovery(hass, load_fixture):
    """Grace applies to network loss; schema incompatibility fails immediately."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    entry = MockConfigEntry()
    coordinator = UnifiAirQualityCoordinator(hass, entry, client)
    coordinator.async_set_updated_data(await client.async_get_snapshot())
    with patch.object(client, "async_get_snapshot", side_effect=ProtectCannotConnect()):
        assert await coordinator._async_update_data() is coordinator.data
        client._verified_at.clear()
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
    with patch.object(client, "async_get_snapshot", side_effect=ProtectProtocolError()):
        with pytest.raises(UpdateFailed, match="firmware"):
            await coordinator._async_update_data()
    assert client.protocol_error
    coordinator._handle_push_update(coordinator.data)
    assert not coordinator.last_update_success
    snapshot = await client.async_get_snapshot()
    coordinator._handle_push_update(snapshot)
    assert coordinator.last_update_success
    listener = MagicMock()
    unsub = coordinator.async_add_listener(listener)
    coordinator._check_freshness(None)
    listener.assert_called_once()
    unsub()
    await coordinator.async_shutdown()


async def test_verified_write_normalization_failure_and_delayed_application(
    load_fixture,
):
    """Controls display confirmed normalized state and never an optimistic request."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    normalized = deepcopy(payload)
    normalized["sensors"][0]["airQualitySettings"]["ringLedBrightness"] = 40
    session = FakeSession(
        FakeResponse(),
        [FakeResponse(payload=payload), FakeResponse(payload=normalized)],
    )
    client = _client(session)
    await client.async_get_snapshot()
    old = client.snapshot
    with pytest.raises(ProtectWriteUnconfirmed, match="normalized"):
        await client.async_update_device(
            "anonymous-air-quality-id",
            {"airQualitySettings": {"ringLedBrightness": 42}},
        )
    assert (
        client.snapshot.devices[0].raw["airQualitySettings"]["ringLedBrightness"] == 40
    )
    assert "ringLedBrightness" not in old.devices[0].raw["airQualitySettings"]
    session.get_response = FakeResponse(error=ClientConnectionError())
    before = client.snapshot
    with pytest.raises(ProtectWriteUnconfirmed, match="verification failed"):
        await client.async_update_device(
            "anonymous-air-quality-id",
            {"airQualitySettings": {"ringLedBrightness": 60}},
        )
    assert client.snapshot is before
    # A delayed application is learned by the next ordinary verified refresh.
    normalized["sensors"][0]["airQualitySettings"]["ringLedBrightness"] = 60
    session.get_response = FakeResponse(payload=normalized)
    await client.async_get_snapshot()
    assert (
        client.snapshot.devices[0].raw["airQualitySettings"]["ringLedBrightness"] == 60
    )


@pytest.mark.parametrize("model", ["sensor", "event"])
async def test_push_during_bootstrap_never_rolls_back_confirmed_state(
    model, load_fixture
):
    """A delayed old bootstrap cannot erase a new sensor/event alarm transition."""
    payload = json.loads(load_fixture("bootstrap_nox.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()

    class ConcurrentResponse(FakeResponse):
        async def json(self, **kwargs):
            """Inject a newer push while the HTTP response is in flight."""
            if model == "sensor":
                push(client, {"airQuality": {"nox": {"status": "high", "value": 150}}})
            else:
                push(
                    client,
                    {
                        "type": "sensorExtremeValues",
                        "device": "anonymous-air-quality-id",
                        "metadata": {"sensorType": "nox", "status": "high"},
                    },
                    model="event",
                    operation="add",
                    id="event",
                )
            return payload

    session.get_response = ConcurrentResponse()
    with pytest.raises(ProtectCannotConnect, match="changed during refresh"):
        await client.async_get_snapshot()
    assert client.snapshot.devices[0].active_alarms[0].metric == "nox"


async def test_out_of_order_http_responses_keep_newer_snapshot(load_fixture):
    """An older GET response cannot replace a newer completed HTTP response."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    await client.async_get_snapshot()
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowResponse(FakeResponse):
        async def json(self, **kwargs):
            """Return old data after a newer request has already completed."""
            started.set()
            await release.wait()
            return payload

    newer = deepcopy(payload)
    newer["sensors"][0]["airQuality"]["co2"] = 900
    session.get_response = [SlowResponse(), FakeResponse(payload=newer)]
    first = asyncio.create_task(client.async_get_snapshot())
    await started.wait()
    second = await client.async_get_snapshot()
    release.set()
    assert await first is second
    assert client.snapshot.devices[0].raw["airQuality"]["co2"] == 900


async def test_changed_event_semantics_fail_without_false_alarm(load_fixture):
    """An unfamiliar alarm status cannot silently start an automation."""
    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    await client.async_get_snapshot()
    previous = client.snapshot
    with pytest.raises(ProtectProtocolError):
        push(
            client,
            {
                "type": "sensorExtremeValues",
                "device": "anonymous-air-quality-id",
                "metadata": {"sensorType": "co2", "status": "changed"},
            },
            model="event",
            operation="add",
            id="event",
        )
    assert client.protocol_error
    assert client.snapshot is previous
    assert not client._active_alarm_events


async def test_subscriber_exceptions_are_isolated_and_sanitized(load_fixture, caplog):
    """One faulty callback cannot interrupt other subscribers or future updates."""
    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(
                payload=json.loads(load_fixture("bootstrap_air_quality.json"))
            ),
        )
    )
    await client.async_get_snapshot()
    bad = MagicMock(side_effect=RuntimeError("private-payload"))
    good = MagicMock()
    client.set_update_callback(bad)
    client.subscribe_alarm_events(bad)
    client.subscribe_alarm_events(good)
    push(client, {"airQuality": {"co2": {"status": "high", "value": 1500}}})
    good.assert_called_once()
    assert "private-payload" not in caplog.text
    assert client.snapshot.devices[0].active_alarms


def test_diagnostics_drop_nested_unknowns_and_randomize_identifiers():
    """Unseen fields, identifiers and credentials are excluded at every level."""
    raw = {
        "id": "serial-or-mac",
        "model": "UP-AirQuality",
        "firmwareVersion": "1.0.16",
        "new": [{"token": "sensitive", "number": 42}],
        "airQuality": {
            "co2": {"value": 612, "status": "neutral", "token": 123},
            "newMetric": {"value": 192168001001},
        },
        "airQualitySettings": {
            "co2Settings": {
                "isEnabled": True,
                "highThreshold": 1200,
                "newSecret": [1, {"mac": "private"}],
            }
        },
        "isConnected": True,
    }
    first, second = sanitize_raw(raw), sanitize_raw(raw)
    assert first["id"] != second["id"]
    assert first["airQuality"] == {"co2": {"value": 612, "status": "neutral"}}
    assert first["airQualitySettings"]["co2Settings"] == {
        "isEnabled": True,
        "highThreshold": 1200,
    }
    serialized = json.dumps(first)
    for secret in ("serial-or-mac", "sensitive", "newSecret", "newMetric", "private"):
        assert secret not in serialized
    aliases = {}
    assert (
        sanitize_raw(raw, aliases=aliases, prefix="export")["id"]
        == sanitize_raw(raw, aliases=aliases, prefix="export")["id"]
    )
    assert safe_version("private.home") is None
    assert safe_version("1.0-admin") is None
    assert sanitize_raw(
        {"model": "private", "firmwareVersion": "private", "isConnected": "private"}
    ) == {
        "model": "**REDACTED**",
        "firmwareVersion": None,
        "isConnected": "**REDACTED**",
    }


async def test_entity_control_errors_and_unknown_alarm_metric():
    """HA receives actionable errors; incomplete resync reports an unknown alarm."""
    device = snapshot_from_bootstrap(
        {"nvr": {"id": "nvr"}, "sensors": [{"id": "device", "type": "UP-AirQuality"}]}
    ).devices[0]
    device = replace(device, uncertain_alarm_metrics=("co2",))
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(devices=(device,))
    coordinator.client.async_update_device = AsyncMock(
        side_effect=ProtectWriteUnconfirmed("retry after refresh")
    )
    entity = UnifiAirQualityEntity(coordinator, "device", "control")
    with pytest.raises(HomeAssistantError, match="retry after refresh"):
        await entity.async_write_value(("ledSettings", "isEnabled"), True)
    alarm = UnifiAirQualityAlarmBinarySensor(
        coordinator,
        "device",
        next(d for d in ALARM_DESCRIPTIONS if d.payload_key == "co2"),
    )
    assert alarm.is_on is None


async def test_bootstrap_tls_failure_is_reported(load_fixture):
    """TLS errors during refresh use the same actionable classification as login."""
    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(error=ClientSSLError(MagicMock(), OSError("private"))),
        )
    )
    from custom_components.unifi_air_quality.api import ProtectTlsError

    with pytest.raises(ProtectTlsError):
        await client.async_get_snapshot()


async def test_patch_rejected_expiry_stops_at_one_renewal(load_fixture):
    """Genuine repeated 401s stop at one retry without changing confirmed state."""
    session = FakeSession(
        FakeResponse(headers={"Set-Cookie": "SESSION=synthetic"}),
        FakeResponse(payload=json.loads(load_fixture("bootstrap_air_quality.json"))),
        patch=FakeResponse(status=401),
    )
    client = _client(session)
    await client.async_get_snapshot()
    before = client.snapshot
    with pytest.raises(ProtectInvalidAuth):
        await client.async_update_device(
            "anonymous-air-quality-id", {"ledSettings": {"isEnabled": False}}
        )
    assert client._auth_failed
    assert len(session.patch_calls) == 2
    assert len(session.post_calls) == 2
    assert client.snapshot is before


async def test_empty_unknown_and_partial_updates_do_not_refresh_old_values(
    load_fixture,
):
    """A heartbeat or another metric cannot reset the freshness of an old value."""
    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(
                payload=json.loads(load_fixture("bootstrap_air_quality.json"))
            ),
        )
    )
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=100):
        await client.async_get_snapshot()
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=219):
        push(client, {"airQuality": {}})
        push(client, {"airQuality": {"future": {"value": 1}}})
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=220):
        assert not client.is_device_fresh("anonymous-air-quality-id")
        push(client, {"airQuality": {"nox": {"value": 1, "status": "neutral"}}})
        assert client.is_device_fresh("anonymous-air-quality-id")
        assert client.is_measurement_fresh("anonymous-air-quality-id", "nox")
        assert not client.is_measurement_fresh("anonymous-air-quality-id", "co2")
        assert not client.is_measurement_fresh("anonymous-air-quality-id", "missing")


async def test_dynamic_devices_all_platforms_preserve_names_and_unload(
    hass, load_fixture
):
    """New devices appear once on all platforms and survive removal/reappearance."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    from custom_components.unifi_air_quality.const import DOMAIN

    from .test_config_flow import USER_INPUT

    payload = json.loads(load_fixture("bootstrap_nox.json"))
    session = FakeSession(FakeResponse(), FakeResponse(payload=payload))
    client = _client(session)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT.copy())
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
    registry = er.async_get(hass)
    original = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(original) == 71
    coordinator = entry.runtime_data.coordinator
    added = deepcopy(payload)
    added["sensors"].append(
        {**deepcopy(added["sensors"][0]), "id": "second", "name": "Second"}
    )
    session.get_response = FakeResponse(payload=added)
    await client.async_get_snapshot()
    client._notify_update()
    # Repeated callbacks and setup ordering must not submit duplicate entities.
    client._notify_update()
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(entities) == 142
    assert {e.domain for e in entities if e.unique_id.startswith("second_")} == {
        "sensor",
        "binary_sensor",
        "event",
        "number",
        "select",
        "switch",
        "time",
    }
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 2
    )
    target = next(e for e in entities if e.unique_id == "second_nox_index")
    registry.async_update_entity(target.entity_id, name="My custom sensor")
    session.get_response = FakeResponse(payload=payload)
    await client.async_get_snapshot()
    client._notify_update()
    await hass.async_block_till_done()
    assert registry.async_get(target.entity_id).name == "My custom sensor"
    session.get_response = FakeResponse(payload=added)
    await client.async_get_snapshot()
    client._notify_update()
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 142
    assert registry.async_get(target.entity_id).name == "My custom sensor"
    # A live add/remove follows the same discovery path as the bootstrap refresh.
    push(
        client,
        {"type": "UP-AirQuality", "airQuality": {"co2": 600}},
        operation="add",
        id="third",
    )
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 213
    push(client, {}, operation="remove", id="third")
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 213
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert client._ws_task is None
    assert not client._alarm_callbacks
    assert not coordinator._listeners


async def test_config_flow_tls_error_is_actionable(hass):
    """Untrusted certificates receive their own setup error rather than auth failure."""
    from homeassistant.config_entries import SOURCE_USER

    from custom_components.unifi_air_quality.api import ProtectTlsError
    from custom_components.unifi_air_quality.const import DOMAIN

    from .test_config_flow import USER_INPUT

    with patch(
        "custom_components.unifi_air_quality.config_flow.PrivateProtectClient"
    ) as factory:
        factory.return_value.async_get_snapshot = AsyncMock(
            side_effect=ProtectTlsError()
        )
        factory.return_value.async_close = AsyncMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT.copy()
        )
    assert result["errors"] == {"base": "tls_error"}


async def test_known_scalar_fixture_exposes_values_and_legacy_metric_aliases(
    load_fixture,
):
    """The original scalar protocol retains CO2, particle and vape measurements."""
    from custom_components.unifi_air_quality.sensor import (
        SENSOR_DESCRIPTIONS,
        UnifiAirQualitySensor,
    )

    payload = json.loads(load_fixture("bootstrap_air_quality.json"))
    client = _client(FakeSession(FakeResponse(), FakeResponse(payload=payload)))
    snapshot = await client.async_get_snapshot()
    coordinator = MagicMock(client=client, data=snapshot, last_update_success=True)
    expected = {
        "co2": 612,
        "pm1p0": 2.1,
        "pm2p5": 3.4,
        "pm4p0": 4.1,
        "pm10p0": 5.2,
        "vape": 0,
    }
    for description in SENSOR_DESCRIPTIONS:
        if description.payload_key not in expected:
            continue
        entity = UnifiAirQualitySensor(
            coordinator, "anonymous-air-quality-id", description
        )
        assert entity.native_value == expected[description.payload_key]
        assert entity.available


@pytest.mark.parametrize("end", [False, {}, [], "123", -1, float("inf")])
async def test_malformed_end_cannot_emit_false_ended(end, load_fixture):
    """Only a finite timestamp or explicit removal can confirm an alarm end."""
    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(
                payload=json.loads(load_fixture("bootstrap_air_quality.json"))
            ),
        )
    )
    await client.async_get_snapshot()
    callback = MagicMock()
    client.subscribe_alarm_events(callback)
    event = {
        "type": "sensorExtremeValues",
        "device": "anonymous-air-quality-id",
        "metadata": {"sensorType": "co2", "status": "high", "sensorValue": 1400},
    }
    push(client, event, model="event", operation="add", id="event")
    with pytest.raises(ProtectProtocolError):
        push(client, {"end": end}, model="event", id="event")
    assert callback.call_count == 1
    assert client.snapshot.devices[0].active_alarms


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "nan", "inf"])
def test_event_values_reject_nonfinite_numbers(value):
    """NaN/Infinity cannot escape through event normalization into HA state."""
    from custom_components.unifi_air_quality.api import PrivateProtectClient

    with pytest.raises(ProtectProtocolError):
        PrivateProtectClient._alarm_details({"metadata": {"sensorValue": value}})


async def test_alarm_freshness_is_per_metric_and_includes_event_only_vape(load_fixture):
    """Other readings cannot refresh an old alarm, while a new event can."""
    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(
                payload=json.loads(load_fixture("bootstrap_air_quality.json"))
            ),
        )
    )
    coordinator = MagicMock(client=client, last_update_success=True)
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=100):
        await client.async_get_snapshot()
        push(client, {"airQuality": {"co2": {"status": "high", "value": 1400}}})
    coordinator.data = client.snapshot
    entity = UnifiAirQualityAlarmBinarySensor(
        coordinator,
        "anonymous-air-quality-id",
        next(d for d in ALARM_DESCRIPTIONS if d.payload_key == "co2"),
    )
    with patch("custom_components.unifi_air_quality.api.monotonic", return_value=220):
        push(client, {"airQuality": {"nox": {"value": 42, "status": "neutral"}}})
        assert not entity.available
        assert not client.is_alarm_fresh("anonymous-air-quality-id", "missing")
        push(
            client,
            {"type": "sensorVape", "device": "anonymous-air-quality-id"},
            model="event",
            operation="add",
            id="vape",
        )
        assert client.is_alarm_fresh("anonymous-air-quality-id", "vape")


async def test_auth_failure_cannot_be_cleared_by_transport_notification(
    hass, load_fixture
):
    """A fresh snapshot must not revive availability after terminal auth rejection."""
    from homeassistant.exceptions import ConfigEntryAuthFailed

    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(
                payload=json.loads(load_fixture("bootstrap_air_quality.json"))
            ),
        )
    )
    entry = MockConfigEntry()
    coordinator = UnifiAirQualityCoordinator(hass, entry, client)
    coordinator.async_set_updated_data(await client.async_get_snapshot())
    client._websocket_once = AsyncMock(side_effect=ProtectInvalidAuth())
    with patch.object(entry, "async_start_reauth") as reauth:
        await client._websocket_loop()
        assert not coordinator.last_update_success
        assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)
        assert not client.is_device_fresh("anonymous-air-quality-id")
        client._notify_update()
        reauth.assert_called_once()


async def test_direct_bootstrap_protocol_errors_fail_closed(hass, load_fixture):
    """Write/reconnect callers receive the same protocol-health failure as polls."""
    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(
                payload=json.loads(load_fixture("bootstrap_air_quality.json"))
            ),
        )
    )
    entry = MockConfigEntry()
    coordinator = UnifiAirQualityCoordinator(hass, entry, client)
    coordinator.async_set_updated_data(await client.async_get_snapshot())
    client._session.get_response = FakeResponse(payload={"nvr": {}, "sensors": []})
    with pytest.raises(ProtectWriteUnconfirmed):
        await client.async_update_device(
            "anonymous-air-quality-id", {"ledSettings": {"isEnabled": True}}
        )
    assert client.protocol_error
    assert not coordinator.last_update_success
    assert not client.is_device_fresh("anonymous-air-quality-id")


async def test_pushes_cannot_postpone_safety_refresh_and_unload_cancels_it(
    hass, load_fixture
):
    """Continuous push traffic still gets a bounded, entry-owned bootstrap resync."""
    client = _client(
        FakeSession(
            FakeResponse(),
            FakeResponse(
                payload=json.loads(load_fixture("bootstrap_air_quality.json"))
            ),
        )
    )
    entry = MockConfigEntry()
    coordinator = UnifiAirQualityCoordinator(hass, entry, client)
    coordinator.async_set_updated_data(await client.async_get_snapshot())
    client.last_successful_refresh = 100
    coordinator._last_refresh_attempt = 100
    blocker = asyncio.Event()
    with (
        patch.object(
            coordinator, "async_refresh", AsyncMock(side_effect=blocker.wait)
        ) as refresh,
        patch(
            "custom_components.unifi_air_quality.coordinator.monotonic",
            return_value=160,
        ),
    ):
        coordinator._check_freshness(None)
        first = coordinator._health_refresh_task
        coordinator._check_freshness(None)
        assert coordinator._health_refresh_task is first
        assert first in entry._background_tasks
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert refresh.await_count == 1
