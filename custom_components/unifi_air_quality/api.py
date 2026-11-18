"""Private Protect adapter isolated from Home Assistant concerns."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import re
import zlib
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from time import monotonic
from typing import Any
from urllib.parse import quote

from aiohttp import (
    ClientConnectionError,
    ClientPayloadError,
    ClientRequest,
    ClientResponse,
    ClientResponseError,
    ClientSession,
    ClientSSLError,
    ClientTimeout,
    ClientWSTimeout,
    WSMsgType,
    WSServerHandshakeError,
)

from .const import DATA_FRESHNESS_SECONDS, MEASUREMENT_ALIASES, MODEL
from .models import (
    AirQualityDevice,
    ProtectAlarm,
    ProtectAlarmEvent,
    ProtectSnapshot,
)
from .websocket import MAX_MESSAGE_BYTES, WebsocketDecodeError, decode_message

_LOGGER = logging.getLogger(__name__)

_BOOTSTRAP_PATH = "/proxy/protect/api/bootstrap"
_LOGIN_PATH = "/api/auth/login"
_WS_PATH = "/proxy/protect/ws/updates"
_SENSOR_PATH = "/proxy/protect/api/sensors/{device_id}"

_ALARM_EVENT_TYPES = {"sensorExtremeValues", "sensorVape"}
_RETIRED_ALARM_EVENT_LIMIT = 256
# Total bounded decoded JSON; a full Protect inventory can exceed WebSocket frames.
MAX_BOOTSTRAP_BYTES = 16 * 1024 * 1024
_HTTP_TIMEOUT = ClientTimeout(total=25, connect=10, sock_connect=10, sock_read=15)
_WS_OPEN_TIMEOUT_SECONDS = 12
_NON_ALARM_STATUSES = frozenset({"good", "neutral", "normal", "safe"})
_ALARM_STATUSES = frozenset({"high", "low", "critical", "detected"})
_MEASUREMENT_KEYS = frozenset(
    {
        "co2",
        "aqi",
        "humidity",
        "temperature",
        "pm1",
        "pm2_5",
        "pm4",
        "pm10",
        "pm1p0",
        "pm2p5",
        "pm4p0",
        "pm10p0",
        "tvoc",
        "voc",
        "nox",
        "vape",
        "vapeIndex",
    }
)


class ProtectApiError(Exception):
    """Base error raised by the Protect adapter."""


class ProtectCannotConnect(ProtectApiError):
    """Protect could not be reached."""


class ProtectInvalidAuth(ProtectApiError):
    """Protect rejected the supplied credentials."""


class ProtectPermissionDenied(ProtectApiError):
    """The account can read Protect but cannot change sensor settings."""


class ProtectWriteUnconfirmed(ProtectApiError):
    """A write could not be confirmed against current Protect state."""


class ProtectTlsError(ProtectCannotConnect):
    """The console TLS certificate is not trusted."""


class ProtectStreamPermissionDenied(ProtectCannotConnect):
    """REST monitoring is available but Protect denied the update channel."""


class ProtectProtocolError(ProtectApiError):
    """Protect returned an unexpected payload."""


async def reject_protect_redirects(
    request: ClientRequest,
    handler: Callable[[ClientRequest], Awaitable[ClientResponse]],
) -> ClientResponse:
    """Stop redirects before aiohttp can forward this private session's secrets.

    WebSocket handshakes do not expose the HTTP allow_redirects option. Attach
    this middleware to every entry and config-flow session so the same policy
    protects both normal requests and the underlying WebSocket upgrade request.
    """
    response = await handler(request)
    if 300 <= response.status < 400:
        response.close()
        raise ProtectProtocolError("Protect redirected an authenticated request")
    return response


async def _read_bootstrap_json(response: ClientResponse) -> Any:
    """Decode bounded wire and expanded bytes before parsing the inventory.

    aiohttp automatic decompression happens before application reads. Disable
    it for this request and bound zlib output too, so a compressed response
    cannot inflate an unbounded internal buffer before the length check runs.
    """
    size = response.headers.get("Content-Length")
    if size and size.isdecimal() and int(size) > MAX_BOOTSTRAP_BYTES:
        raise ProtectProtocolError("Protect bootstrap exceeds byte limit")
    encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
    if encoding not in ("identity", "gzip", "deflate"):
        raise ProtectProtocolError("Unsupported Protect content encoding")
    decoder = (
        zlib.decompressobj(
            16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        )
        if encoding != "identity"
        else None
    )
    body = bytearray()
    wire_bytes = 0
    try:
        while chunk := await response.content.read(64 * 1024):
            wire_bytes += len(chunk)
            if wire_bytes > MAX_BOOTSTRAP_BYTES:
                raise ProtectProtocolError("Protect bootstrap exceeds byte limit")
            remaining = MAX_BOOTSTRAP_BYTES - len(body)
            decoded = decoder.decompress(chunk, remaining + 1) if decoder else chunk
            if len(decoded) > remaining:
                raise ProtectProtocolError("Protect bootstrap exceeds byte limit")
            if decoder and decoder.unused_data:
                raise ProtectProtocolError(
                    "Protect bootstrap has trailing compressed data"
                )
            body.extend(decoded)
        if decoder and not decoder.eof:
            raise ProtectProtocolError("Protect bootstrap compression is incomplete")
        return json.loads(body)
    except (
        ClientPayloadError,
        zlib.error,
        ValueError,
        UnicodeError,
        RecursionError,
    ) as err:
        raise ProtectProtocolError(
            "Protect bootstrap cannot be decoded as JSON"
        ) from err


class _SupersededBootstrap(ProtectApiError):
    """An old request was refused after newer verified state was published."""


type _FieldPath = tuple[str, ...]
type _AlarmKey = tuple[str, str]


@dataclass(slots=True)
class _BootstrapPushes:
    """Fields confirmed by the stream while one HTTP bootstrap is in flight."""

    sensor_fields: dict[str, set[_FieldPath]] = field(default_factory=dict)
    event_alarms: set[_AlarmKey] = field(default_factory=set)
    cursor: str | None = None


def _host_for_url(host: str) -> str:
    """Allow a canonical hostname or IP literal, not an injected origin."""
    if not isinstance(host, str):
        raise ProtectProtocolError("Host must be a hostname or IP address")
    address = host.strip()
    if address.startswith("[") and address.endswith("]"):
        address = address[1:-1]
    if not address or any(c in address for c in "/\\@%?#[]"):
        raise ProtectProtocolError("Host must be a hostname or IP address")
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        if ":" in address or re.fullmatch(r"\d+(?:\.\d+){3}", address):
            raise ProtectProtocolError("Invalid IP address or hostname") from None
        try:
            address = address.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as err:
            raise ProtectProtocolError("Invalid hostname") from err
        if len(address) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in address.split(".")
        ):
            raise ProtectProtocolError("Invalid hostname") from None
        return address
    return f"[{ip.compressed}]" if ip.version == 6 else str(ip)


def _canonical_metric(metric: str) -> str:
    """Unify the old wire names under the existing HA entity payload keys."""
    return next(
        (canonical for canonical, old in MEASUREMENT_ALIASES.items() if metric == old),
        metric,
    )


def _deep_merge(target: dict[str, Any], update: Mapping[str, Any]) -> None:
    """Merge a partial WebSocket update without discarding nested readings."""
    for key, value in update.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            target[key] = value


def _normalize_sensor_update(update: Mapping[str, Any]) -> dict[str, Any]:
    """Apply older wire metric aliases to the current canonical cache keys.

    A legacy push must replace the displayed canonical value as well as renew
    its timestamp. Canonical fields win if a mixed-frame contains both names.
    """
    normalized = deepcopy(dict(update))
    readings = normalized.get("airQuality")
    if isinstance(readings, dict):
        for canonical, legacy in MEASUREMENT_ALIASES.items():
            if legacy in readings:
                readings.setdefault(canonical, readings[legacy])
                readings.pop(legacy)
    return normalized


def _pushed_fields(
    current: Mapping[str, Any], update: Mapping[str, Any]
) -> set[_FieldPath]:
    """Collect replaced fields, including values repeated by Protect."""
    paths = set()
    for name, value in update.items():
        before = current.get(name)
        if isinstance(before, dict) and isinstance(value, Mapping):
            paths.update((name, *child) for child in _pushed_fields(before, value))
        else:
            paths.add((name,))
    return paths


def _copy_pushed_fields(
    target: dict[str, Any], current: Mapping[str, Any], paths: set[_FieldPath]
) -> None:
    """Overlay only the pushed fields, retaining unrelated bootstrap values."""
    for path in paths:
        source, destination = current, target
        for name in path[:-1]:
            source = source.get(name)
            if not isinstance(source, Mapping):
                # A later push replaced the parent, which has its own field path.
                break
            if not isinstance(destination.get(name), dict):
                destination[name] = {}
            destination = destination[name]
        else:
            if path[-1] in source:
                destination[path[-1]] = deepcopy(source[path[-1]])


def _contains_update(confirmed: Mapping[str, Any], update: Mapping[str, Any]) -> bool:
    """Compare only requested fields while retaining other confirmed settings."""
    for key, expected in update.items():
        if key not in confirmed:
            # Missing is distinct from an explicitly persisted null.
            return False
        actual = confirmed[key]
        if isinstance(expected, Mapping):
            if not isinstance(actual, Mapping) or not _contains_update(
                actual, expected
            ):
                return False
        elif isinstance(expected, bool):
            if not isinstance(actual, bool) or actual != expected:
                return False
        elif isinstance(expected, (int, float)):
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                return False
            if not math.isfinite(actual) or actual != expected:
                return False
        elif type(actual) is not type(expected) or actual != expected:
            return False
    return True


def _is_air_quality_sensor(sensor: Mapping[str, Any]) -> bool:
    """Identify the device without depending on one firmware field."""
    if isinstance(sensor.get("airQuality"), Mapping):
        return True
    for key in ("type", "model", "productModel", "sku"):
        value = sensor.get(key)
        if isinstance(value, str):
            compact = "".join(char for char in value.upper() if char.isalnum())
            if compact == "UPAIRQUALITY":
                return True
    return False


def _validate_sensor(sensor: Mapping[str, Any]) -> None:
    """Reject incompatible known fields before publishing state or alarms."""
    if not isinstance(sensor.get("id"), str) or not sensor["id"]:
        raise ProtectProtocolError("Air quality sensor has no valid identifier")
    if "isConnected" in sensor and not isinstance(sensor["isConnected"], bool):
        raise ProtectProtocolError("Unsupported sensor connection state")
    for section in ("airQuality", "airQualitySettings", "ledSettings"):
        if section in sensor and not isinstance(sensor[section], Mapping):
            raise ProtectProtocolError("Unsupported sensor payload structure")
    # Optional future metrics are ignored; known metrics must retain semantics.
    for metric, reading in sensor.get("airQuality", {}).items():
        if metric not in _MEASUREMENT_KEYS or reading is None:
            continue
        value = reading.get("value") if isinstance(reading, Mapping) else reading
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ProtectProtocolError("Unsupported measurement value")
        if isinstance(reading, Mapping) and "status" in reading:
            status = reading["status"]
            if not isinstance(status, str) or status.casefold() not in (
                _NON_ALARM_STATUSES | _ALARM_STATUSES
            ):
                raise ProtectProtocolError("Unsupported measurement status")


def snapshot_from_bootstrap(
    payload: Mapping[str, Any],
    active_alarms: Mapping[str, tuple[ProtectAlarm, ...]] | None = None,
    uncertain_alarm_metrics: set[tuple[str, str]] | None = None,
) -> ProtectSnapshot:
    """Convert a raw private bootstrap response into the adapter model."""
    nvr = payload.get("nvr")
    sensors = payload.get("sensors")
    if not isinstance(nvr, Mapping) or not isinstance(sensors, list):
        raise ProtectProtocolError("Bootstrap is missing NVR or sensor data")

    console_id = nvr.get("id") or nvr.get("mac")
    if not isinstance(console_id, str) or not console_id:
        raise ProtectProtocolError("Bootstrap is missing a console identifier")

    devices: list[AirQualityDevice] = []
    seen_ids: set[str] = set()
    for sensor in sensors:
        if not isinstance(sensor, dict):
            raise ProtectProtocolError("Bootstrap contains invalid sensor records")
        device_id = sensor.get("id")
        if not isinstance(device_id, str) or not device_id:
            raise ProtectProtocolError("Bootstrap sensor has no valid identifier")
        if device_id in seen_ids:
            raise ProtectProtocolError("Bootstrap contains duplicate sensor IDs")
        seen_ids.add(device_id)
        if not _is_air_quality_sensor(sensor):
            # Unknown models are valid, but the inventory structure is not optional.
            continue
        _validate_sensor(sensor)
        name = sensor.get("name")
        firmware = sensor.get("firmwareVersion")
        devices.append(
            AirQualityDevice(
                id=device_id,
                name=name if isinstance(name, str) and name else MODEL,
                model=MODEL,
                firmware_version=firmware if isinstance(firmware, str) else None,
                is_connected=bool(sensor.get("isConnected", True)),
                raw=deepcopy(sensor),
                active_alarms=(active_alarms or {}).get(device_id, ()),
                uncertain_alarm_metrics=tuple(
                    metric
                    for sensor_id, metric in (uncertain_alarm_metrics or ())
                    if sensor_id == device_id
                ),
            )
        )

    console_name = nvr.get("name")
    version = nvr.get("version")
    last_update_id = payload.get("lastUpdateId")
    return ProtectSnapshot(
        console_id=console_id,
        console_name=(
            console_name
            if isinstance(console_name, str) and console_name
            else "Protect"
        ),
        protect_version=version if isinstance(version, str) else None,
        last_update_id=(last_update_id if isinstance(last_update_id, str) else None),
        devices=tuple(devices),
    )


class PrivateProtectClient:
    """Minimal async client for the private bootstrap and update stream."""

    def __init__(
        self,
        session: ClientSession,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool,
    ) -> None:
        """Initialize this component with its entry-scoped dependencies."""
        # Keep all mutable state scoped to this instance and its config entry.
        host_part = _host_for_url(host)
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ProtectProtocolError("Invalid Protect port")
        port_part = "" if port == 443 else f":{port}"
        self._base_url = f"https://{host_part}{port_part}"
        self._ws_url = f"wss://{host_part}{port_part}{_WS_PATH}"
        self._session = session
        self._username = username
        self._password = password
        self._ssl: bool | None = None if verify_ssl else False
        self._headers: dict[str, str] = {}
        self._snapshot: ProtectSnapshot | None = None
        self._raw_devices: dict[str, dict[str, Any]] = {}
        self._update_callback: Callable[[ProtectSnapshot], None] | None = None
        self._alarm_callbacks: set[Callable[[ProtectAlarmEvent], None]] = set()
        self._raw_alarm_events: dict[str, dict[str, Any]] = {}
        self._active_alarm_events: dict[str, ProtectAlarm] = {}
        self._alarm_event_devices: dict[str, str] = {}
        self._sensor_alarm_states: dict[tuple[str, str], ProtectAlarm] = {}
        self._alarm_states: dict[tuple[str, str], ProtectAlarm] = {}
        self._uncertain_alarm_metrics: set[tuple[str, str]] = set()
        # Sources without a matching event ID need an authoritative normal status.
        self._unresolvable_alarm_metrics: set[_AlarmKey] = set()
        self._ws_task: asyncio.Task[None] | None = None
        self.websocket_connected = False
        self._closing = False
        self._write_lock = asyncio.Lock()
        self._pending_bootstraps: dict[int, _BootstrapPushes] = {}
        self._refresh_request_id = 0
        self._last_refresh_applied = 0
        self._verified_at: dict[str, float] = {}
        self._metric_verified_at: dict[tuple[str, str], float] = {}
        self._alarm_verified_at: dict[tuple[str, str], float] = {}
        self.protocol_error = False
        self.last_successful_refresh: float | None = None
        self._auth_lock = asyncio.Lock()
        self._auth_generation = 0
        self._auth_failed = False
        self._auth_failure_callback: Callable[[], None] | None = None

    def set_auth_failure_callback(self, callback: Callable[[], None]) -> None:
        """Notify the owner when the stream requires new credentials."""
        self._auth_failure_callback = callback

    @property
    def snapshot(self) -> ProtectSnapshot | None:
        """Return the current snapshot, if initialized."""
        return self._snapshot

    def set_update_callback(
        self, callback: Callable[[ProtectSnapshot], None] | None
    ) -> None:
        """Set the callback for push updates."""
        self._update_callback = callback

    def subscribe_alarm_events(
        self, callback: Callable[[ProtectAlarmEvent], None]
    ) -> Callable[[], None]:
        """Subscribe to normalized alarm transitions and return an unsubscribe."""
        self._alarm_callbacks.add(callback)

        def unsubscribe() -> None:
            """Remove this alarm subscriber idempotently during entity teardown."""
            # Discard tolerates repeated unloads without affecting other subscribers.
            self._alarm_callbacks.discard(callback)

        return unsubscribe

    async def _async_authenticate(
        self, *, rejected_generation: int | None = None
    ) -> None:
        """Create one session, sharing renewals across concurrent requests."""
        async with self._auth_lock:
            if self._auth_failed:
                raise ProtectInvalidAuth("Protect requires reauthentication")
            if "Cookie" in self._headers and (
                rejected_generation is None
                or rejected_generation != self._auth_generation
            ):
                return
            self._headers.clear()
            try:
                await self._async_login()
            except ProtectInvalidAuth:
                self._auth_failed = True
                raise
            self._auth_generation += 1

    def _invalidate_session(self, generation: int) -> None:
        """Discard only the session rejected by this request."""
        if generation == self._auth_generation:
            self._headers.clear()
            self._auth_failed = True

    async def _async_login(self) -> None:
        """Create a local Protect session and retain only its auth headers."""
        # Classify certificate, credential and network failures without logging secrets.
        try:
            async with self._session.post(
                f"{self._base_url}{_LOGIN_PATH}",
                json={
                    "username": self._username,
                    "password": self._password,
                    "rememberMe": False,
                },
                ssl=self._ssl,
                allow_redirects=False,
                timeout=_HTTP_TIMEOUT,
            ) as response:
                if 300 <= response.status < 400:
                    raise ProtectProtocolError("Protect login redirected off origin")
                if response.status in (401, 403):
                    raise ProtectInvalidAuth("Protect rejected the credentials")
                response.raise_for_status()
                csrf = response.headers.get("x-csrf-token")
                if csrf:
                    self._headers["x-csrf-token"] = csrf
                cookies = SimpleCookie()
                for value in response.headers.getall("Set-Cookie", []):
                    cookies.load(value)
                if cookies:
                    self._headers["Cookie"] = "; ".join(
                        f"{key}={morsel.value}" for key, morsel in cookies.items()
                    )
        except ProtectInvalidAuth:
            raise
        except ClientSSLError as err:
            raise ProtectTlsError(
                "Trust the console certificate in Home Assistant"
            ) from err
        except (ClientConnectionError, ClientResponseError, TimeoutError) as err:
            raise ProtectCannotConnect("Unable to connect to Protect") from err

    def is_device_fresh(self, device_id: str) -> bool:
        """Require verified data within the freshness window, independent of TCP."""
        verified = self._verified_at.get(device_id)
        return (
            not self.protocol_error
            and not self._auth_failed
            and verified is not None
            and monotonic() - verified < DATA_FRESHNESS_SECONDS
        )

    @property
    def authentication_failed(self) -> bool:
        """Expose terminal authentication health without revealing session data."""
        return self._auth_failed

    def is_alarm_fresh(self, device_id: str, metric: str) -> bool:
        """Require a recent confirmed status/event for this individual alarm."""
        verified = self._alarm_verified_at.get((device_id, metric))
        return (
            self.is_device_fresh(device_id)
            and verified is not None
            and monotonic() - verified < DATA_FRESHNESS_SECONDS
        )

    def is_measurement_fresh(self, device_id: str, metric: str) -> bool:
        """Reject old values even when another metric on the same sensor updates."""
        verified = self._metric_verified_at.get((device_id, metric))
        if verified is None and metric in MEASUREMENT_ALIASES:
            verified = self._metric_verified_at.get(
                (device_id, MEASUREMENT_ALIASES[metric])
            )
        return (
            self.is_device_fresh(device_id)
            and verified is not None
            and monotonic() - verified < DATA_FRESHNESS_SECONDS
        )

    def _record_measurements(self, device_id: str, update: Mapping[str, Any]) -> None:
        """Count only verified numeric readings, never empty/unknown heartbeats."""
        readings = update.get("airQuality")
        if not isinstance(readings, Mapping):
            return
        for wire_metric, reading in readings.items():
            if wire_metric not in _MEASUREMENT_KEYS:
                continue
            metric = _canonical_metric(wire_metric)
            if wire_metric != metric and metric in readings:
                continue
            value = reading.get("value") if isinstance(reading, Mapping) else reading
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                # Schema validation already excluded non-finite numeric values.
                self._verified_at[device_id] = monotonic()
                self._metric_verified_at[(device_id, metric)] = monotonic()

    async def async_get_snapshot(self) -> ProtectSnapshot:
        """Refresh state and publish fail-closed health for every caller."""
        try:
            return await self._async_fetch_snapshot()
        except ProtectProtocolError:
            # Reconnect and write verification must have the same health semantics.
            self.protocol_error = True
            self._notify_update()
            raise
        except ProtectInvalidAuth:
            self._auth_failed = True
            if self._auth_failure_callback is not None:
                self._auth_failure_callback()
            raise

    async def _async_fetch_snapshot(self) -> ProtectSnapshot:
        """Publish a verified bootstrap with any newer confirmed stream fields."""
        await self._async_authenticate()
        self._refresh_request_id += 1
        request_id = self._refresh_request_id
        pushes = self._pending_bootstraps[request_id] = _BootstrapPushes()
        try:
            payload = await self._async_read_bootstrap(request_id=request_id)
        except (_SupersededBootstrap, ProtectProtocolError, ProtectInvalidAuth):
            if request_id < self._last_refresh_applied and self._snapshot is not None:
                # A failed superseded request cannot poison newer verified health.
                return self._snapshot
            raise
        finally:
            self._pending_bootstraps.pop(request_id)

        if request_id < self._last_refresh_applied:
            # A newer HTTP request has already published its response.
            return self._snapshot
        # Validate the response and the merged state before changing the cache.
        snapshot = snapshot_from_bootstrap(payload)
        if pushes.sensor_fields or pushes.event_alarms or pushes.cursor is not None:
            payload = self._merge_bootstrap_pushes(payload, snapshot, pushes)
            snapshot = snapshot_from_bootstrap(payload)
        self._reconcile_bootstrap_alarms(snapshot, pushes.event_alarms)
        snapshot = snapshot_from_bootstrap(
            payload, self._alarms_by_device(), self._uncertain_alarm_metrics
        )
        self._raw_devices = {
            device.id: deepcopy(device.raw) for device in snapshot.devices
        }
        self._snapshot = snapshot
        self._last_refresh_applied = request_id
        self.protocol_error = False
        self.last_successful_refresh = monotonic()
        self._verified_at = {
            device.id: self.last_successful_refresh for device in snapshot.devices
        }
        self._metric_verified_at.clear()
        for device in snapshot.devices:
            self._record_measurements(device.id, device.raw)
        return snapshot

    async def _async_read_bootstrap(
        self, *, request_id: int | None = None
    ) -> dict[str, Any]:
        """Read a bounded Protect inventory with origin and session isolation."""
        for attempt in range(2):
            generation = self._auth_generation
            try:
                async with self._session.get(
                    f"{self._base_url}{_BOOTSTRAP_PATH}",
                    headers=self._headers | {"Accept-Encoding": "gzip, deflate"},
                    ssl=self._ssl,
                    allow_redirects=False,
                    auto_decompress=False,
                    timeout=_HTTP_TIMEOUT,
                ) as response:
                    if 300 <= response.status < 400:
                        raise ProtectProtocolError(
                            "Protect bootstrap redirected off origin"
                        )
                    if response.status not in (401, 403):
                        response.raise_for_status()
                        payload = await _read_bootstrap_json(response)
                        break
            except ClientSSLError as err:
                raise ProtectTlsError(
                    "Trust the console certificate in Home Assistant"
                ) from err
            except (ClientConnectionError, ClientResponseError, TimeoutError) as err:
                raise ProtectCannotConnect("Unable to fetch Protect bootstrap") from err
            except ValueError as err:
                raise ProtectProtocolError("Protect bootstrap is not JSON") from err
            if request_id is not None and request_id < self._last_refresh_applied:
                # Late credential denial does not warrant renewal or global reauth.
                raise _SupersededBootstrap()
            if attempt:
                if generation == self._auth_generation:
                    self._invalidate_session(generation)
                    raise ProtectInvalidAuth("Protect session is not authorized")
                raise ProtectCannotConnect("A superseded session was rejected")
            await self._async_authenticate(rejected_generation=generation)

        if not isinstance(payload, dict):
            raise ProtectProtocolError("Protect bootstrap must be an object")
        return payload

    def _merge_bootstrap_pushes(
        self,
        payload: dict[str, Any],
        snapshot: ProtectSnapshot,
        pushes: _BootstrapPushes,
    ) -> dict[str, Any]:
        """Combine current bootstrap readings with fields changed during the read."""
        devices = {device.id: device.raw for device in snapshot.devices}
        for device_id, fields in pushes.sensor_fields.items():
            current = self._raw_devices.get(device_id)
            if current is None:
                devices.pop(device_id, None)
            elif () in fields:
                # Add/remove replaces device identity and all older fields.
                devices[device_id] = deepcopy(current)
            elif device_id in devices:
                _copy_pushed_fields(devices[device_id], current, fields)

        for device_id, metric in pushes.event_alarms:
            readings = devices.get(device_id, {}).get("airQuality", {})
            reading = readings.get(metric)
            if reading is None:
                reading = readings.get(MEASUREMENT_ALIASES.get(metric))
            if isinstance(reading, dict):
                # An older bootstrap status must not undo a recent event transition.
                current = self._raw_devices.get(device_id, {})
                source_readings = current.get("airQuality", {})
                recent = source_readings.get(metric)
                if recent is None:
                    recent = source_readings.get(MEASUREMENT_ALIASES.get(metric))
                if isinstance(recent, Mapping) and "status" in recent:
                    reading["status"] = recent["status"]
                else:
                    reading.pop("status", None)

        return payload | {
            "sensors": list(devices.values()),
            "lastUpdateId": pushes.cursor or snapshot.last_update_id,
        }

    async def async_update_device(
        self, device_id: str, update: Mapping[str, Any]
    ) -> None:
        """Apply a partial settings update to one Protect sensor."""
        if device_id not in self._raw_devices:
            raise ProtectProtocolError("Air quality device is not available")
        if not update:
            raise ProtectProtocolError("Sensor update must not be empty")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", device_id):
            # Even a percent-encoded slash can escape one API path segment.
            raise ProtectProtocolError("Invalid Protect sensor identifier")

        async with self._write_lock:
            if device_id not in self._raw_devices:
                raise ProtectWriteUnconfirmed("Sensor was removed before the write")
            await self._async_authenticate()
            for attempt in range(2):
                generation = self._auth_generation
                try:
                    async with self._session.patch(
                        f"{self._base_url}{_SENSOR_PATH.format(device_id=quote(device_id))}",
                        json=dict(update),
                        headers=self._headers.copy(),
                        ssl=self._ssl,
                        allow_redirects=False,
                        timeout=_HTTP_TIMEOUT,
                    ) as response:
                        if 300 <= response.status < 400:
                            raise ProtectProtocolError(
                                "Protect sensor write redirected off origin"
                            )
                        if response.status == 403:
                            raise ProtectPermissionDenied(
                                "Protect denied this setting change. Grant the local "
                                "account sensor settings write permission; monitoring "
                                "can continue with read-only access."
                            )
                        if response.status != 401:
                            response.raise_for_status()
                            break
                except (
                    ClientConnectionError,
                    ClientPayloadError,
                    ClientResponseError,
                    TimeoutError,
                ) as err:
                    raise ProtectCannotConnect(
                        "Unable to update Protect sensor"
                    ) from err
                if attempt:
                    self._invalidate_session(generation)
                    raise ProtectInvalidAuth(
                        "Protect session is not authorized to update the sensor"
                    )
                await self._async_authenticate(rejected_generation=generation)

            # Never publish the request itself: Protect may reject or normalize it.
            try:
                await self.async_get_snapshot()
            except ProtectApiError as err:
                raise ProtectWriteUnconfirmed(
                    "Protect accepted the request, but verification failed. "
                    "Refresh the sensor before retrying."
                ) from err
            self._notify_update()
            confirmed = self._raw_devices.get(device_id, {})
            if not _contains_update(confirmed, update):
                raise ProtectWriteUnconfirmed(
                    "Protect has not applied the requested value or normalized it. "
                    "The displayed setting reflects confirmed Protect state; "
                    "wait for the next refresh before retrying."
                )

    def start_websocket(self) -> None:
        """Start following private Protect updates in the background."""
        if self._ws_task is None or self._ws_task.done():
            self._closing = False
            self._ws_task = asyncio.create_task(self._websocket_loop())

    async def _websocket_loop(self) -> None:
        """Recover the update stream with capped backoff until shutdown."""
        # Resynchronize state before subscribing from the recovered cursor.
        backoff = 1
        while not self._closing:
            connected_at = monotonic()
            try:
                # Recover missed readings/alarms before following the new cursor.
                if self._snapshot is not None:
                    await self.async_get_snapshot()
                    self._notify_update()
                await self._websocket_once()
            except asyncio.CancelledError:
                raise
            except ProtectInvalidAuth:
                self._auth_failed = True
                if self._auth_failure_callback is not None:
                    self._auth_failure_callback()
                return
            except ProtectStreamPermissionDenied:
                # Back off without affecting periodic, authorized REST monitoring.
                backoff = max(backoff, 30)
            except (
                ProtectCannotConnect,
                ClientConnectionError,
                ClientPayloadError,
                WSServerHandshakeError,
                TimeoutError,
            ):
                pass
            except Exception as err:
                # Never include exception messages or private wire data here.
                _LOGGER.warning(
                    "Protect update stream failed (%s); reconnecting",
                    type(err).__name__,
                )
            finally:
                self.websocket_connected = False
                self._notify_update()
                # Only a sustained connection resets repeated-close backoff.
                if monotonic() - connected_at >= 30:
                    backoff = 1
            if not self._closing:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _websocket_once(self) -> None:
        """Consume one bounded authenticated WebSocket connection."""
        # Renew a rejected handshake only once, sharing concurrent session renewals.
        await self._async_authenticate()
        url = self._ws_url
        if self._snapshot and self._snapshot.last_update_id:
            url = f"{url}?lastUpdateId={quote(self._snapshot.last_update_id)}"
        for attempt in range(2):
            generation = self._auth_generation
            try:
                async with asyncio.timeout(_WS_OPEN_TIMEOUT_SECONDS):
                    websocket = await self._session.ws_connect(
                        url,
                        headers=self._headers.copy(),
                        ssl=self._ssl,
                        heartbeat=30,
                        max_msg_size=MAX_MESSAGE_BYTES,
                        timeout=ClientWSTimeout(ws_close=10),
                    )
                break
            except WSServerHandshakeError as err:
                if err.status == 403:
                    raise ProtectStreamPermissionDenied(
                        "Protect rejected the WebSocket channel"
                    ) from err
                if err.status != 401:
                    raise
                if attempt:
                    # Only a contemporaneous REST rejection proves bad credentials.
                    # A denied stream alone must not stop working REST monitoring.
                    try:
                        await self._async_read_bootstrap()
                    except ProtectInvalidAuth:
                        raise
                    raise ProtectStreamPermissionDenied(
                        "Protect rejected the WebSocket channel"
                    ) from err
            await self._async_authenticate(rejected_generation=generation)

        self.websocket_connected = True
        self._notify_update()
        async with websocket:
            async for message in websocket:
                if message.type is WSMsgType.BINARY:
                    self._process_websocket_payload(message.data)
                elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break

    def _process_websocket_payload(self, payload: bytes) -> None:
        """Fail closed on incompatible frames and let the stream resynchronize."""
        try:
            action, data = decode_message(payload)
            self._apply_websocket_update(action, data)
        except (WebsocketDecodeError, ProtectProtocolError) as err:
            self.protocol_error = True
            self._notify_update()
            raise ProtectProtocolError(
                "Unsupported Protect protocol; review diagnostics and firmware versions"
            ) from err

    def _apply_websocket_update(
        self, action: Mapping[str, Any], data: Mapping[str, Any]
    ) -> None:
        """Validate partial sensor updates before replacing the confirmed cache."""
        model_key = action.get("modelKey")
        if model_key == "event":
            self._process_alarm_event(action, data)
            return
        if model_key != "sensor":
            return
        device_id = action.get("id") or data.get("id")
        if not isinstance(device_id, str) or not device_id:
            raise ProtectProtocolError("Sensor update has no identifier")

        operation = action.get("action")
        previous = self._raw_devices.get(device_id)
        if operation == "remove":
            self._raw_devices.pop(device_id, None)
            self._clear_device_alarm_states(device_id)
        elif operation in ("add", "update"):
            data = _normalize_sensor_update(data)
            # Migrate cached legacy siblings before merging a partial alias frame.
            current = _normalize_sensor_update(
                self._raw_devices.get(device_id, {"id": device_id})
            )
            _deep_merge(current, data)
            if _is_air_quality_sensor(current):
                _validate_sensor(current)
            self._raw_devices[device_id] = current
            self._record_measurements(device_id, data)
            self._process_sensor_alarm_statuses(device_id, data)
        else:
            raise ProtectProtocolError("Unsupported sensor update operation")
        cursor = action.get("newUpdateId")
        if self._pending_bootstraps:
            # Coalesce edited paths instead of buffering frames for each request.
            fields = (
                _pushed_fields(previous, data)
                if operation == "update" and previous is not None
                else {()}
            )
            for pending in self._pending_bootstraps.values():
                pending.sensor_fields.setdefault(device_id, set()).update(fields)
                if operation == "remove":
                    pending.event_alarms = {
                        key for key in pending.event_alarms if key[0] != device_id
                    }
                if isinstance(cursor, str):
                    pending.cursor = cursor
        self._rebuild_snapshot(cursor)

    def _process_alarm_event(
        self, action: Mapping[str, Any], data: Mapping[str, Any]
    ) -> None:
        """Normalize relevant private Protect event frames."""
        event_id = action.get("id") or data.get("id")
        operation = action.get("action")
        if not isinstance(event_id, str) or operation not in (
            "add",
            "update",
            "remove",
        ):
            if data.get("type") in ("sensorExtremeValues", "sensorVape"):
                raise ProtectProtocolError("Unsupported alarm action schema")
            return

        cached = self._raw_alarm_events.get(event_id)
        current = deepcopy(cached) if cached is not None else {}
        _deep_merge(current, data)
        event_type = current.get("type")
        if not isinstance(event_type, str) or event_type not in _ALARM_EVENT_TYPES:
            if cached is not None:
                raise ProtectProtocolError("Unsupported alarm event type")
            return

        device_id = current.get("device")
        if not isinstance(device_id, str):
            device_id = self._alarm_event_devices.get(event_id)
        metric, status, value = self._alarm_details(current)
        if not isinstance(device_id, str) or metric is None:
            raise ProtectProtocolError("Alarm event is missing device or metric")

        if metric not in _MEASUREMENT_KEYS or (
            current.get("type") != "sensorVape"
            and (
                not isinstance(status, str) or status.casefold() not in _ALARM_STATUSES
            )
        ):
            raise ProtectProtocolError("Unsupported alarm event semantics")
        end = current.get("end")
        if end is not None and (
            isinstance(end, bool)
            or not isinstance(end, (int, float))
            or not math.isfinite(end)
            or end < 0
        ):
            raise ProtectProtocolError("Unsupported alarm end timestamp")
        key = (device_id, metric)
        cursor = action.get("newUpdateId")
        is_ended = operation == "remove" or end is not None
        alarm = ProtectAlarm(metric=metric, status=status, value=value)
        if is_ended:
            alarm = self._active_alarm_events.pop(event_id, None) or alarm
            known_event = self._raw_alarm_events.pop(event_id, None) is not None
            self._alarm_event_devices.pop(event_id, None)
        else:
            # A later end frame may contain only the event ID and timestamp.
            self._raw_alarm_events[event_id] = {
                "type": event_type,
                "device": device_id,
                "metadata": {
                    "sensorType": metric,
                    "status": status,
                    "sensorValue": value,
                },
            }
            self._alarm_event_devices[event_id] = device_id
            if operation == "update":
                return
            self._active_alarm_events[event_id] = alarm

        for pending in self._pending_bootstraps.values():
            pending.event_alarms.add(key)
            if isinstance(cursor, str):
                pending.cursor = cursor

        now = monotonic()
        self._verified_at[device_id] = now
        unresolved = self._has_retired_alarm_source(key)
        confirmed_by_sensor = key in self._sensor_alarm_states
        if (
            is_ended
            and (unresolved or key in self._unresolvable_alarm_metrics)
            and not confirmed_by_sensor
        ):
            # The new source ended, not necessarily a source retired at resync.
            # No all-clear is verified while any older end is still missing.
            self._uncertain_alarm_metrics.add(key)
            remaining = next(
                (
                    active
                    for other, active in self._active_alarm_events.items()
                    if self._alarm_event_devices.get(other) == device_id
                    and active.metric == metric
                ),
                None,
            )
            if remaining is None:
                self._alarm_states.pop(key, None)
            else:
                # Keep current attributes from a surviving source, not the ender.
                self._alarm_states[key] = remaining
            self._alarm_verified_at[key] = now
            self._rebuild_snapshot(cursor)
            return
        if is_ended and key in self._uncertain_alarm_metrics and known_event:
            # The final known end confirms the previously unknown aggregate.
            self._alarm_states.setdefault(key, alarm)
        if not unresolved and key not in self._unresolvable_alarm_metrics:
            self._uncertain_alarm_metrics.discard(key)
        self._alarm_verified_at[key] = now
        self._refresh_alarm_state(device_id, alarm.metric, alarm)
        self._rebuild_snapshot(cursor)

    def _has_retired_alarm_source(self, key: _AlarmKey) -> bool:
        """Identify orphaned event starts with no authoritative ending yet."""
        for event_id, event in self._raw_alarm_events.items():
            if event_id in self._active_alarm_events:
                continue
            if self._alarm_event_devices.get(event_id) != key[0]:
                continue
            if self._alarm_details(event)[0] == key[1]:
                return True
        return False

    def _reconcile_bootstrap_alarms(
        self, snapshot: ProtectSnapshot, event_alarms: set[_AlarmKey]
    ) -> None:
        """Replace transient sources with confirmed statuses after a full resync.

        Absence of a status is not proof an alarm ended. Expire orphaned sources
        silently and expose those metrics as unknown until confirmed again.
        """
        previous = self._alarm_states.copy()
        device_ids = {device.id for device in snapshot.devices}
        preserved = {key for key in event_alarms if key[0] in device_ids}
        self._active_alarm_events = {
            event_id: alarm
            for event_id, alarm in self._active_alarm_events.items()
            if (self._alarm_event_devices[event_id], alarm.metric) in preserved
        }
        # Keep a bounded history of event identities, independent of active state.
        retired = [
            event_id
            for event_id in self._raw_alarm_events
            if event_id not in self._active_alarm_events
        ]
        for event_id in retired[:-_RETIRED_ALARM_EVENT_LIMIT]:
            owner = self._alarm_event_devices.pop(event_id, None)
            event = self._raw_alarm_events.pop(event_id)
            if owner is not None:
                metric = self._alarm_details(event)[0]
                if metric is not None:
                    # Losing the event ID must never manufacture an all-clear.
                    self._unresolvable_alarm_metrics.add((owner, metric))
        for event_id, owner in tuple(self._alarm_event_devices.items()):
            if owner not in device_ids:
                self._raw_alarm_events.pop(event_id, None)
                self._alarm_event_devices.pop(event_id)

        # A retired sensor-status source has no event ID whose end could resolve
        # it. Keep that uncertainty even if a newer, independent event ends.
        self._unresolvable_alarm_metrics.update(
            self._sensor_alarm_states.keys() - preserved
        )
        self._sensor_alarm_states = {
            key: alarm
            for key, alarm in self._sensor_alarm_states.items()
            if key in preserved
        }
        self._alarm_states = {key: previous[key] for key in preserved & previous.keys()}
        now = monotonic()
        self._alarm_verified_at = {
            key: verified
            for key, verified in self._alarm_verified_at.items()
            if key[0] in device_ids
            and now - verified < DATA_FRESHNESS_SECONDS
            and (key in preserved or key not in previous)
        }
        self._unresolvable_alarm_metrics = {
            key for key in self._unresolvable_alarm_metrics if key[0] in device_ids
        }
        self._uncertain_alarm_metrics = {
            key
            for key in self._uncertain_alarm_metrics | (previous.keys() - preserved)
            if key[0] in device_ids
        }
        # Keep aggregate history only for metrics that can be confirmed.
        for device in snapshot.devices:
            readings = device.raw.get("airQuality")
            if not isinstance(readings, Mapping):
                continue
            for wire_metric, measurement in readings.items():
                metric = _canonical_metric(wire_metric)
                if (
                    isinstance(measurement, Mapping)
                    and isinstance(measurement.get("status"), str)
                    and measurement["status"]
                ):
                    key = (device.id, metric)
                    if key in previous:
                        self._alarm_states[key] = previous[key]
            self._process_sensor_alarm_statuses(device.id, device.raw)

    def _process_sensor_alarm_statuses(
        self, device_id: str, update: Mapping[str, Any]
    ) -> None:
        """Normalize alarm transitions carried by sensor-state updates."""
        readings = update.get("airQuality")
        if not isinstance(readings, Mapping):
            return
        for wire_metric, measurement in readings.items():
            if wire_metric not in _MEASUREMENT_KEYS or not isinstance(
                measurement, Mapping
            ):
                continue
            metric = _canonical_metric(wire_metric)
            if wire_metric != metric and metric in readings:
                continue
            status = measurement.get("status")
            if not isinstance(status, str) or not status:
                continue
            raw_value = measurement.get("value")
            value = (
                raw_value
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else None
            )
            alarm = ProtectAlarm(metric, status, value)
            key = (device_id, metric)
            if status.casefold() in _NON_ALARM_STATUSES:
                # A current normal reading, unlike a missing bootstrap status,
                # authoritatively closes every outstanding event for this metric.
                for event_id, event in tuple(self._raw_alarm_events.items()):
                    if (
                        event_id not in self._active_alarm_events
                        and self._alarm_event_devices.get(event_id) == device_id
                        and self._alarm_details(event)[0] == metric
                    ):
                        self._raw_alarm_events.pop(event_id, None)
                        self._active_alarm_events.pop(event_id, None)
                        self._alarm_event_devices.pop(event_id, None)
                self._unresolvable_alarm_metrics.discard(key)
            if key in self._uncertain_alarm_metrics:
                # Preserve aggregate history through unknown state: a confirmed
                # high status continues it, and normal produces one verified end.
                self._alarm_states.setdefault(key, alarm)
            self._uncertain_alarm_metrics.discard(key)
            # A verified status confirms device liveness, independently of a value.
            now = monotonic()
            self._verified_at[device_id] = now
            self._alarm_verified_at[key] = now
            if status.casefold() not in _NON_ALARM_STATUSES:
                self._sensor_alarm_states[key] = alarm
            else:
                self._sensor_alarm_states.pop(key, None)
            self._refresh_alarm_state(device_id, metric, alarm)

    def _refresh_alarm_state(
        self, device_id: str, metric: str, transition_alarm: ProtectAlarm
    ) -> None:
        """Merge sensor and event sources and emit only aggregate transitions."""
        key = (device_id, metric)
        previous = self._alarm_states.get(key)

        current = self._sensor_alarm_states.get(key)
        if current is None:
            current = next(
                (
                    alarm
                    for event_id, alarm in self._active_alarm_events.items()
                    if self._alarm_event_devices.get(event_id) == device_id
                    and alarm.metric == metric
                ),
                None,
            )

        if current is not None:
            self._alarm_states[key] = current
            if previous is None and key in self._uncertain_alarm_metrics:
                # This aggregate already started before becoming unknown. A new
                # source confirms activity but must not emit a duplicate start.
                return
        else:
            self._alarm_states.pop(key, None)
        if (previous is not None) == (current is not None):
            return
        event_alarm = current if current is not None else transition_alarm
        normalized = ProtectAlarmEvent(
            device_id=device_id,
            metric=metric,
            transition="started" if current is not None else "ended",
            status=event_alarm.status,
            value=event_alarm.value,
        )
        for callback in tuple(self._alarm_callbacks):
            try:
                callback(normalized)
            except Exception as err:
                _LOGGER.warning(
                    "Protect alarm subscriber failed (%s)", type(err).__name__
                )

    def _clear_device_alarm_states(self, device_id: str) -> None:
        """Discard all transient alarm bookkeeping for a removed device."""
        self._verified_at.pop(device_id, None)
        for timestamps in (self._metric_verified_at, self._alarm_verified_at):
            for key in tuple(timestamps):
                if key[0] == device_id:
                    timestamps.pop(key)
        self._uncertain_alarm_metrics = {
            key for key in self._uncertain_alarm_metrics if key[0] != device_id
        }
        self._unresolvable_alarm_metrics = {
            key for key in self._unresolvable_alarm_metrics if key[0] != device_id
        }
        for key in tuple(self._sensor_alarm_states):
            if key[0] == device_id:
                self._sensor_alarm_states.pop(key)
        for key in tuple(self._alarm_states):
            if key[0] == device_id:
                self._alarm_states.pop(key)
        for event_id, event_device_id in tuple(self._alarm_event_devices.items()):
            if event_device_id != device_id:
                continue
            self._alarm_event_devices.pop(event_id, None)
            self._active_alarm_events.pop(event_id, None)
            self._raw_alarm_events.pop(event_id, None)

    @staticmethod
    def _alarm_details(
        event: Mapping[str, Any],
    ) -> tuple[str | None, str | None, int | float | None]:
        """Extract metric, status and value from an event payload."""
        if event.get("type") == "sensorVape":
            return "vape", "detected", None
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            return None, None, None

        def text(name: str) -> Any:
            """Read scalar or wrapped text metadata from a Protect alarm."""
            # Firmware variants may wrap the same metadata field in a text object.
            value = metadata.get(name)
            return value.get("text") if isinstance(value, Mapping) else value

        metric = text("sensorType")
        status = text("status")
        value = text("sensorValue")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            try:
                value = float(value) if isinstance(value, str) else None
            except ValueError:
                value = None
        if value is not None and not math.isfinite(value):
            raise ProtectProtocolError("Unsupported non-finite alarm value")
        return (
            _canonical_metric(metric) if isinstance(metric, str) else None,
            status if isinstance(status, str) else None,
            value,
        )

    def _alarms_by_device(self) -> dict[str, tuple[ProtectAlarm, ...]]:
        """Group active alarm events by their sensor device."""
        grouped: dict[str, list[ProtectAlarm]] = {}
        for (device_id, _metric), alarm in self._alarm_states.items():
            grouped.setdefault(device_id, []).append(alarm)
        return {device_id: tuple(alarms) for device_id, alarms in grouped.items()}

    def _rebuild_snapshot(self, update_id: Any) -> None:
        """Publish a fresh immutable snapshot from confirmed cached data."""
        # Keep the last known cursor when the frame does not include a new one.
        if self._snapshot is None:
            return
        raw = {
            "nvr": {
                "id": self._snapshot.console_id,
                "name": self._snapshot.console_name,
                "version": self._snapshot.protect_version,
            },
            "sensors": list(self._raw_devices.values()),
            "lastUpdateId": (
                update_id
                if isinstance(update_id, str)
                else self._snapshot.last_update_id
            ),
        }
        self._snapshot = snapshot_from_bootstrap(
            raw, self._alarms_by_device(), self._uncertain_alarm_metrics
        )
        self._notify_update()

    def _notify_update(self) -> None:
        """Publish a confirmed snapshot to the coordinator."""
        if self._snapshot is not None and self._update_callback is not None:
            try:
                self._update_callback(self._snapshot)
            except Exception as err:
                _LOGGER.warning(
                    "Protect update subscriber failed (%s)", type(err).__name__
                )

    async def async_close(self) -> None:
        """Stop background work and close the client's dedicated HTTP session."""
        self._closing = True
        self._update_callback = None
        self._alarm_callbacks.clear()
        self._auth_failure_callback = None
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        # HA sessions share a connector: detach closes this session without
        # closing that connector or invoking HA's intentionally blocked close().
        self._session.detach()
