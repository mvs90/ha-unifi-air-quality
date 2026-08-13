"""Private Protect adapter isolated from Home Assistant concerns."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import quote

from aiohttp import (
    ClientConnectionError,
    ClientResponseError,
    ClientSession,
    ClientWSTimeout,
    WSMsgType,
    WSServerHandshakeError,
)

from .const import MODEL
from .models import (
    AirQualityDevice,
    ProtectAlarm,
    ProtectAlarmEvent,
    ProtectSnapshot,
)
from .websocket import WebsocketDecodeError, decode_message

_LOGGER = logging.getLogger(__name__)

_BOOTSTRAP_PATH = "/proxy/protect/api/bootstrap"
_LOGIN_PATH = "/api/auth/login"
_WS_PATH = "/proxy/protect/ws/updates"
_SENSOR_PATH = "/proxy/protect/api/sensors/{device_id}"

_ALARM_EVENT_TYPES = {"sensorExtremeValues", "sensorVape"}
_NON_ALARM_STATUSES = frozenset({"good", "neutral", "normal", "safe"})


class ProtectApiError(Exception):
    """Base error raised by the Protect adapter."""


class ProtectCannotConnect(ProtectApiError):
    """Protect could not be reached."""


class ProtectInvalidAuth(ProtectApiError):
    """Protect rejected the supplied credentials."""


class ProtectProtocolError(ProtectApiError):
    """Protect returned an unexpected payload."""


def _host_for_url(host: str) -> str:
    normalized = host.strip().strip("[]")
    if not normalized or "://" in normalized or "/" in normalized:
        raise ProtectProtocolError("Host must be a hostname or IP address")
    return f"[{normalized}]" if ":" in normalized else normalized


def _deep_merge(target: dict[str, Any], update: Mapping[str, Any]) -> None:
    """Merge a partial WebSocket update without discarding nested readings."""
    for key, value in update.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            target[key] = value


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


def snapshot_from_bootstrap(
    payload: Mapping[str, Any],
    active_alarms: Mapping[str, tuple[ProtectAlarm, ...]] | None = None,
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
    for sensor in sensors:
        if not isinstance(sensor, dict) or not _is_air_quality_sensor(sensor):
            continue
        device_id = sensor.get("id")
        if not isinstance(device_id, str) or not device_id:
            continue
        name = sensor.get("name")
        firmware = sensor.get("firmwareVersion")
        devices.append(
            AirQualityDevice(
                id=device_id,
                name=name if isinstance(name, str) and name else MODEL,
                model=MODEL,
                firmware_version=firmware if isinstance(firmware, str) else None,
                is_connected=bool(sensor.get("isConnected", True)),
                raw=sensor.copy(),
                active_alarms=(active_alarms or {}).get(device_id, ()),
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
        host_part = _host_for_url(host)
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
        self._ws_task: asyncio.Task[None] | None = None
        self.websocket_connected = False
        self._closing = False
        self._write_lock = asyncio.Lock()

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
            self._alarm_callbacks.discard(callback)

        return unsubscribe

    async def _async_authenticate(self) -> None:
        try:
            async with self._session.post(
                f"{self._base_url}{_LOGIN_PATH}",
                json={
                    "username": self._username,
                    "password": self._password,
                    "rememberMe": False,
                },
                ssl=self._ssl,
            ) as response:
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
        except (ClientConnectionError, ClientResponseError, TimeoutError) as err:
            raise ProtectCannotConnect("Unable to connect to Protect") from err

    async def async_get_snapshot(self) -> ProtectSnapshot:
        """Authenticate if needed and retrieve a raw bootstrap snapshot."""
        if "Cookie" not in self._headers:
            await self._async_authenticate()
        try:
            async with self._session.get(
                f"{self._base_url}{_BOOTSTRAP_PATH}",
                headers=self._headers,
                ssl=self._ssl,
            ) as response:
                if response.status in (401, 403):
                    self._headers.clear()
                    raise ProtectInvalidAuth("Protect session is not authorized")
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except ProtectInvalidAuth:
            raise
        except (ClientConnectionError, ClientResponseError, TimeoutError) as err:
            raise ProtectCannotConnect("Unable to fetch Protect bootstrap") from err
        except ValueError as err:
            raise ProtectProtocolError("Protect bootstrap is not JSON") from err

        if not isinstance(payload, dict):
            raise ProtectProtocolError("Protect bootstrap must be an object")
        snapshot = snapshot_from_bootstrap(payload, self._alarms_by_device())
        self._raw_devices = {
            device.id: device.raw.copy() for device in snapshot.devices
        }
        self._snapshot = snapshot
        return snapshot

    async def async_update_device(
        self, device_id: str, update: Mapping[str, Any]
    ) -> None:
        """Apply a partial settings update to one Protect sensor."""
        if device_id not in self._raw_devices:
            raise ProtectProtocolError("Air quality device is not available")
        if not update:
            raise ProtectProtocolError("Sensor update must not be empty")

        async with self._write_lock:
            if "Cookie" not in self._headers:
                await self._async_authenticate()
            try:
                async with self._session.patch(
                    f"{self._base_url}{_SENSOR_PATH.format(device_id=quote(device_id))}",
                    json=dict(update),
                    headers=self._headers,
                    ssl=self._ssl,
                ) as response:
                    if response.status in (401, 403):
                        self._headers.clear()
                        raise ProtectInvalidAuth(
                            "Protect session is not authorized to update the sensor"
                        )
                    response.raise_for_status()
            except ProtectInvalidAuth:
                raise
            except (ClientConnectionError, ClientResponseError, TimeoutError) as err:
                raise ProtectCannotConnect("Unable to update Protect sensor") from err

            _deep_merge(self._raw_devices[device_id], update)
            self._rebuild_snapshot(None)

    def start_websocket(self) -> None:
        """Start following private Protect updates in the background."""
        if self._ws_task is None or self._ws_task.done():
            self._closing = False
            self._ws_task = asyncio.create_task(self._websocket_loop())

    async def _websocket_loop(self) -> None:
        backoff = 1
        while not self._closing:
            try:
                await self._websocket_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except ProtectInvalidAuth:
                self._headers.clear()
                try:
                    await self._async_authenticate()
                except ProtectApiError:
                    pass
            except (ClientConnectionError, WSServerHandshakeError, TimeoutError):
                pass
            finally:
                self.websocket_connected = False
            if not self._closing:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _websocket_once(self) -> None:
        if "Cookie" not in self._headers:
            await self._async_authenticate()
        url = self._ws_url
        if self._snapshot and self._snapshot.last_update_id:
            url = f"{url}?lastUpdateId={quote(self._snapshot.last_update_id)}"
        try:
            websocket = await self._session.ws_connect(
                url,
                headers=self._headers,
                ssl=self._ssl,
                heartbeat=30,
                timeout=ClientWSTimeout(ws_close=10),
            )
        except WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise ProtectInvalidAuth("WebSocket session is not authorized") from err
            raise

        self.websocket_connected = True
        async with websocket:
            async for message in websocket:
                if message.type is WSMsgType.BINARY:
                    self._process_websocket_payload(message.data)
                elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break

    def _process_websocket_payload(self, payload: bytes) -> None:
        try:
            action, data = decode_message(payload)
        except WebsocketDecodeError:
            _LOGGER.debug("Ignored an unsupported Protect WebSocket frame")
            return
        model_key = action.get("modelKey")
        if model_key == "event":
            self._process_alarm_event(action, data)
            return
        if model_key != "sensor":
            return
        device_id = action.get("id") or data.get("id")
        if not isinstance(device_id, str):
            return

        operation = action.get("action")
        if operation == "remove":
            self._raw_devices.pop(device_id, None)
            self._clear_device_alarm_states(device_id)
        elif operation in ("add", "update"):
            current = self._raw_devices.setdefault(device_id, {"id": device_id})
            _deep_merge(current, data)
            self._process_sensor_alarm_statuses(device_id, data)
        else:
            return
        self._rebuild_snapshot(action.get("newUpdateId"))

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
            return

        current = self._raw_alarm_events.setdefault(event_id, {})
        _deep_merge(current, data)
        event_type = current.get("type")
        if event_type not in _ALARM_EVENT_TYPES:
            self._raw_alarm_events.pop(event_id, None)
            return

        device_id = current.get("device")
        if not isinstance(device_id, str):
            device_id = self._alarm_event_devices.get(event_id)
        metric, status, value = self._alarm_details(current)
        if not isinstance(device_id, str) or metric is None:
            return

        self._alarm_event_devices[event_id] = device_id
        is_ended = operation == "remove" or current.get("end") is not None
        if operation == "update" and not is_ended:
            return

        alarm = ProtectAlarm(metric=metric, status=status, value=value)
        if is_ended:
            previous = self._active_alarm_events.pop(event_id, None)
            if previous is not None:
                alarm = previous
            self._raw_alarm_events.pop(event_id, None)
            self._alarm_event_devices.pop(event_id, None)
        else:
            self._active_alarm_events[event_id] = alarm
        self._refresh_alarm_state(device_id, alarm.metric, alarm)
        self._rebuild_snapshot(action.get("newUpdateId"))

    def _process_sensor_alarm_statuses(
        self, device_id: str, update: Mapping[str, Any]
    ) -> None:
        """Normalize alarm transitions carried by sensor-state updates."""
        readings = update.get("airQuality")
        if not isinstance(readings, Mapping):
            return
        for metric, measurement in readings.items():
            if not isinstance(metric, str) or not isinstance(measurement, Mapping):
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
            callback(normalized)

    def _clear_device_alarm_states(self, device_id: str) -> None:
        """Discard all transient alarm bookkeeping for a removed device."""
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
        return (
            metric if isinstance(metric, str) else None,
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
        self._snapshot = snapshot_from_bootstrap(raw, self._alarms_by_device())
        if self._update_callback is not None:
            self._update_callback(self._snapshot)

    async def async_close(self) -> None:
        """Stop background work without closing HA's shared HTTP session."""
        self._closing = True
        self._update_callback = None
        self._alarm_callbacks.clear()
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
