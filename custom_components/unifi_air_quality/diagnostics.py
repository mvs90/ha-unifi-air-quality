"""Anonymized diagnostics for UniFi Protect Air Quality."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import UnifiAirQualityConfigEntry

_REDACT_CONFIG = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME}
_IDENTIFIER_KEYS = {
    "id",
    "mac",
    "serial",
    "serialNumber",
    "authUserId",
    "accessKey",
}
_SECRET_KEYS = {
    "password",
    "token",
    "cookie",
    "credential",
    "privateKey",
    "apiKey",
}
_SAFE_STRING_KEYS = {
    "modelKey",
    "model",
    "type",
    "productModel",
    "sku",
    "marketName",
    "firmwareVersion",
    "state",
    "status",
    "unit",
    "mode",
}


def _anonymous_identifier(value: str) -> str:
    digest = sha256(value.encode()).hexdigest()[:10]
    return f"<anonymous:{digest}>"


def sanitize_raw(value: Any, key: str | None = None) -> Any:
    """Preserve wire shape and measurements while removing identifying strings."""
    if key in _SECRET_KEYS:
        return "**REDACTED**"
    if isinstance(value, dict):
        return {
            item_key: sanitize_raw(item, item_key) for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_raw(item, key) for item in value]
    if isinstance(value, str):
        if key in _IDENTIFIER_KEYS:
            return _anonymous_identifier(value)
        if key in _SAFE_STRING_KEYS:
            return value
        return "**REDACTED**"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "**REDACTED**"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiAirQualityConfigEntry
) -> dict[str, Any]:
    """Return safe diagnostics for a config entry."""
    runtime = entry.runtime_data
    snapshot = runtime.coordinator.data
    return {
        "config_entry": async_redact_data(dict(entry.data), _REDACT_CONFIG),
        "transport": {
            "adapter": "private_bootstrap_websocket",
            "websocket_connected": runtime.client.websocket_connected,
            "protect_version": snapshot.protect_version,
            "air_quality_device_count": len(snapshot.devices),
            "active_alarm_count": sum(
                len(device.active_alarms) for device in snapshot.devices
            ),
        },
        "devices": [sanitize_raw(device.raw) for device in snapshot.devices],
    }
