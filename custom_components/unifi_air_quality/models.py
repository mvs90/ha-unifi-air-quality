"""Transport-independent data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AirQualityDevice:
    """A UP-AirQuality device and its latest private payload."""

    id: str
    name: str
    model: str
    firmware_version: str | None
    is_connected: bool
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProtectSnapshot:
    """A point-in-time, adapter-neutral Protect snapshot."""

    console_id: str
    console_name: str
    protect_version: str | None
    last_update_id: str | None
    devices: tuple[AirQualityDevice, ...]
