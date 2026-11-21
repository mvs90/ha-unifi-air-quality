"""Allowlisted diagnostics with identifiers scoped to a single export."""

from __future__ import annotations

import math
import re
from time import monotonic
from typing import Any
from uuid import uuid4

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import UnifiAirQualityConfigEntry
from .api import _ALARM_STATUSES, _MEASUREMENT_KEYS, _NON_ALARM_STATUSES
from .const import CONF_VERIFY_SSL, DATA_FRESHNESS_SECONDS, MEASUREMENT_ALIASES

_REDACTED = "**REDACTED**"
# Unknown keys are omitted, including numeric fields and nested containers.
_CONTAINERS = {"airQuality": _MEASUREMENT_KEYS}
_SETTINGS = {
    "ringLedBrightness",
    "ringLedMetric",
    "nightModeEnabled",
    "nightModeBrightness",
    "isEnabled",
    "activityFeedback",
    "lowThreshold",
    "highThreshold",
    "sensitivity",
}
_SETTINGS_GROUPS = {f"{metric}Settings" for metric in _MEASUREMENT_KEYS} | {
    "vapeSensitivitySettings"
}


def safe_version(value: Any) -> str | None:
    """Return two to four numeric version components, otherwise None."""
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        return value
    return None


def sanitize_raw(
    value: Any,
    key: str | None = None,
    *,
    aliases: dict[str, str] | None = None,
    prefix: str | None = None,
) -> Any:
    """Retain current measurements/settings from a small structural allowlist.

    Random per-export aliases preserve within-export correlation only. No hash
    or stable fingerprint of a MAC, serial, host or device identifier is exported.
    Pass one shared aliases mapping and prefix across devices in an export.
    Unknown keys are omitted; disallowed values of allowed keys are redacted.
    """
    aliases = {} if aliases is None else aliases
    prefix = prefix or uuid4().hex
    if not isinstance(value, dict):
        return _REDACTED
    if key == "airQuality":
        # Export the same effective canonical reading as the entity/freshness
        # model, not an older duplicate alias left in a historical payload.
        value = value.copy()
        for canonical, legacy in MEASUREMENT_ALIASES.items():
            if legacy in value:
                value.setdefault(canonical, value.pop(legacy))
    result = {}
    allowed = (
        _CONTAINERS.get(key, set())
        if key == "airQuality"
        else _SETTINGS | _SETTINGS_GROUPS
        if key in {"airQualitySettings", "ledSettings"} | _SETTINGS_GROUPS
        else {"value", "status"}
        if key in _MEASUREMENT_KEYS
        else {
            "id",
            "model",
            "type",
            "isConnected",
            "firmwareVersion",
            "airQuality",
            "airQualitySettings",
            "ledSettings",
        }
    )
    for field, item in value.items():
        if field not in allowed:
            continue
        if field == "id" and isinstance(item, str):
            result[field] = aliases.setdefault(
                item, f"<export:{prefix}:device{len(aliases) + 1}>"
            )
        elif field == "firmwareVersion":
            result[field] = safe_version(item)
        elif field in {"model", "type"}:
            result[field] = "UP-AirQuality" if item == "UP-AirQuality" else _REDACTED
        elif field == "status":
            result[field] = (
                item
                if isinstance(item, str)
                and item.casefold() in _NON_ALARM_STATUSES | _ALARM_STATUSES
                else _REDACTED
            )
        elif isinstance(item, dict):
            result[field] = sanitize_raw(item, field, aliases=aliases, prefix=prefix)
        elif (
            item is None
            or isinstance(item, bool)
            or (isinstance(item, (int, float)) and math.isfinite(item))
        ):
            result[field] = item
        else:
            result[field] = _REDACTED
    return result


def _health_category(
    client: Any, devices: list[dict[str, Any]], refresh_age: float | None
) -> str:
    """Classify auth, protocol, inventory, connectivity, and measurement health.

    This summary does not aggregate per-alarm freshness; inspect the separate
    alarm flags for those states. No exceptions or server messages are exported.
    """
    if client.authentication_failed:
        return "authentication_failed"
    if client.protocol_error:
        return "protocol_error"
    if refresh_age is None:
        return "uninitialized"
    if not devices:
        return "empty_inventory" if refresh_age < DATA_FRESHNESS_SECONDS else "stale"
    if not any(device["freshness"]["fresh"] for device in devices):
        return "stale"
    for device in devices:
        if not device["freshness"]["fresh"] or device.get("isConnected") is False:
            return "degraded"
        # Null/absent optional readings are unknown, not stale cached numbers.
        for metric, reading in device.get("airQuality", {}).items():
            value = reading.get("value") if isinstance(reading, dict) else reading
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and not device["freshness"]["measurements"][metric]["fresh"]
            ):
                return "degraded"
    return "healthy" if client.websocket_connected else "polling"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiAirQualityConfigEntry
) -> dict[str, Any]:
    """Export allowlisted settings, relative freshness ages, and transport health.

    Device aliases are new for every export. Credentials, host names, stream
    cursors, event history, and arbitrary server text are not included.
    """
    runtime = entry.runtime_data
    snapshot = runtime.coordinator.data
    aliases: dict[str, str] = {}
    prefix = uuid4().hex
    devices = []
    for device in snapshot.devices:
        sanitized = sanitize_raw(device.raw, aliases=aliases, prefix=prefix)
        sanitized["id"] = aliases.setdefault(
            device.id, f"<export:{prefix}:device{len(aliases) + 1}>"
        )
        sanitized["freshness"] = runtime.client.diagnostic_freshness(device.id)
        devices.append(sanitized)
    last_refresh = runtime.client.last_successful_refresh
    refresh_age = (
        None if last_refresh is None else round(max(0.0, monotonic() - last_refresh), 1)
    )
    return {
        "config_entry": {
            CONF_HOST: _REDACTED,
            CONF_USERNAME: _REDACTED,
            CONF_PASSWORD: _REDACTED,
            CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL),
        },
        "transport": {
            "adapter": "private_bootstrap_websocket",
            "websocket_connected": runtime.client.websocket_connected,
            "protocol_error": bool(runtime.client.protocol_error),
            "authentication_failed": bool(runtime.client.authentication_failed),
            "health": _health_category(runtime.client, devices, refresh_age),
            "bootstrap_age_seconds": refresh_age,
            "protect_version": safe_version(snapshot.protect_version),
            "air_quality_device_count": len(snapshot.devices),
            "active_alarm_count": sum(
                len(device.active_alarms) for device in snapshot.devices
            ),
        },
        "devices": devices,
    }
