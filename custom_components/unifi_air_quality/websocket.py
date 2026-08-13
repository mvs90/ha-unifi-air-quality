"""Decode the undocumented UniFi Protect private WebSocket format."""

from __future__ import annotations

import json
import struct
import zlib
from typing import Any

_HEADER = struct.Struct("!bbbbi")
_HEADER_SIZE = _HEADER.size
_JSON_FORMAT = 1


class WebsocketDecodeError(ValueError):
    """Raised when a private Protect WebSocket message cannot be decoded."""


def _decode_frame(payload: bytes, offset: int) -> tuple[dict[str, Any], int]:
    """Decode one Protect frame and return it with the next offset."""
    try:
        _packet_type, payload_format, deflated, _unknown, size = _HEADER.unpack_from(
            payload, offset
        )
    except struct.error as err:
        raise WebsocketDecodeError("Invalid WebSocket frame header") from err

    start = offset + _HEADER_SIZE
    end = start + size
    if size < 0 or end > len(payload):
        raise WebsocketDecodeError("Invalid WebSocket frame size")

    raw = payload[start:end]
    if deflated:
        try:
            raw = zlib.decompress(raw)
        except zlib.error as err:
            raise WebsocketDecodeError("Invalid compressed WebSocket frame") from err

    if payload_format != _JSON_FORMAT:
        raise WebsocketDecodeError("Unsupported WebSocket payload format")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise WebsocketDecodeError("Invalid WebSocket JSON payload") from err
    if not isinstance(decoded, dict):
        raise WebsocketDecodeError("WebSocket payload must be an object")
    return decoded, end


def decode_message(payload: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode action and data frames from a private Protect message."""
    action, offset = _decode_frame(payload, 0)
    data, end = _decode_frame(payload, offset)
    if end != len(payload):
        raise WebsocketDecodeError("Unexpected trailing WebSocket data")
    return action, data
