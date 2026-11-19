"""Transport-independent snapshots and normalized alarm transition records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProtectAlarm:
    """An active aggregate alarm using canonical metric keys and optional detail."""

    metric: str
    status: str | None = None
    value: int | float | None = None


@dataclass(frozen=True, slots=True)
class ProtectAlarmEvent:
    """A device metric's aggregate ``started`` or ``ended`` transition."""

    device_id: str
    metric: str
    transition: str
    status: str | None = None
    value: int | float | None = None


@dataclass(frozen=True, slots=True)
class AirQualityDevice:
    """A UP-AirQuality device with copied payload and aggregate alarm state.

    Uncertain metrics lack a confirmed all-clear after resynchronization. This
    record is frozen, but its raw dictionary is mutable and must be treated as
    read-only by consumers; freshness is tracked separately by the client.
    """

    id: str
    name: str
    model: str
    firmware_version: str | None
    is_connected: bool
    raw: dict[str, Any]
    active_alarms: tuple[ProtectAlarm, ...] = ()
    uncertain_alarm_metrics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProtectSnapshot:
    """A console inventory and stream cursor, independent of transport health.

    A snapshot alone does not prove availability: consumers also check the
    client's verification timestamps, authentication, and protocol state.
    """

    console_id: str
    console_name: str
    protect_version: str | None
    last_update_id: str | None
    devices: tuple[AirQualityDevice, ...]
