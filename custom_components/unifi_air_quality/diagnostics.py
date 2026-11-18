"""Allowlisted diagnostics with identifiers scoped to a single export."""

from __future__ import annotations

import math
import re
from typing import Any
from uuid import uuid4

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import UnifiAirQualityConfigEntry
from .api import _ALARM_STATUSES, _MEASUREMENT_KEYS, _NON_ALARM_STATUSES
from .const import CONF_VERIFY_SSL

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
    """Export version syntax only, never arbitrary server-supplied text."""
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
    """
    aliases = {} if aliases is None else aliases
    prefix = prefix or uuid4().hex
    if not isinstance(value, dict):
        return _REDACTED
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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiAirQualityConfigEntry
) -> dict[str, Any]:
    """Export current troubleshooting state, excluding history and credentials."""
    runtime = entry.runtime_data
    snapshot = runtime.coordinator.data
    aliases: dict[str, str] = {}
    prefix = uuid4().hex
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
            "protect_version": safe_version(snapshot.protect_version),
            "air_quality_device_count": len(snapshot.devices),
            "active_alarm_count": sum(
                len(device.active_alarms) for device in snapshot.devices
            ),
        },
        "devices": [
            sanitize_raw(device.raw, aliases=aliases, prefix=prefix)
            for device in snapshot.devices
        ],
    }
