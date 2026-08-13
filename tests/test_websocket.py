"""Tests for the private Protect WebSocket frame decoder."""

import json
import struct
import zlib

import pytest

from custom_components.unifi_air_quality.websocket import (
    WebsocketDecodeError,
    decode_message,
)

HEADER = struct.Struct("!bbbbi")


def _frame(data: dict, *, compressed: bool = False) -> bytes:
    payload = json.dumps(data).encode()
    if compressed:
        payload = zlib.compress(payload)
    return HEADER.pack(1, 1, int(compressed), 0, len(payload)) + payload


def test_decode_message() -> None:
    action = {
        "action": "update",
        "modelKey": "sensor",
        "id": "sensor-id",
        "newUpdateId": "update-id",
    }
    data = {"airQuality": {"co2": 700}}
    assert decode_message(_frame(action) + _frame(data, compressed=True)) == (
        action,
        data,
    )


def test_decode_message_rejects_truncated_frame() -> None:
    with pytest.raises(WebsocketDecodeError):
        decode_message(b"invalid")


def test_decode_message_rejects_trailing_data() -> None:
    with pytest.raises(WebsocketDecodeError):
        decode_message(_frame({}) + _frame({}) + b"extra")


@pytest.mark.parametrize(
    "payload",
    [
        HEADER.pack(1, 1, 0, 0, 100) + b"{}",
        HEADER.pack(1, 0, 0, 0, 2) + b"{}",
        HEADER.pack(1, 1, 1, 0, 3) + b"bad",
        HEADER.pack(1, 1, 0, 0, 3) + b"bad",
        HEADER.pack(1, 1, 0, 0, 2) + b"[]",
    ],
)
def test_decode_message_rejects_invalid_action_frame(payload: bytes) -> None:
    with pytest.raises(WebsocketDecodeError):
        decode_message(payload)
