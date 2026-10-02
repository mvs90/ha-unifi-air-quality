"""Tests for UP-AirQuality sensor entities."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.unifi_air_quality.models import AirQualityDevice, ProtectSnapshot
from custom_components.unifi_air_quality.sensor import (
    SENSOR_DESCRIPTIONS,
    UnifiAirQualitySensor,
    async_setup_entry,
)


def _device(*, connected: bool = True) -> AirQualityDevice:
    return AirQualityDevice(
        "device-id",
        "Office air",
        "UP-AirQuality",
        "1.0.16",
        connected,
        {
            "airQuality": {
                "co2": {"status": "neutral", "value": 418},
                "vape": {"status": "safe", "value": 0},
                "humidity": {"status": "neutral", "value": "invalid"},
            }
        },
    )


def _coordinator(device: AirQualityDevice):
    coordinator = MagicMock()
    coordinator.data = ProtectSnapshot("console", "Protect", "6.2", "1", (device,))
    coordinator.last_update_success = True
    return coordinator


async def test_platform_adds_every_measurement(hass) -> None:
    coordinator = _coordinator(_device())
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    added = []

    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == len(SENSOR_DESCRIPTIONS) == 12
    assert len({entity.unique_id for entity in added}) == 12


def test_sensor_reads_value_status_and_device_info() -> None:
    coordinator = _coordinator(_device())
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == "co2")
    entity = UnifiAirQualitySensor(coordinator, "device-id", description)

    assert entity.native_value == 418
    assert entity.extra_state_attributes == {"protect_status": "neutral"}
    assert entity.available
    assert entity.device_info["model"] == "UP-AirQuality"


def test_zero_value_is_preserved() -> None:
    coordinator = _coordinator(_device())
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == "vape_index")
    entity = UnifiAirQualitySensor(coordinator, "device-id", description)

    assert entity.native_value == 0


def test_tvoc_is_an_unscaled_index() -> None:
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == "tvoc")

    assert description.native_unit_of_measurement is None
    assert description.device_class is None


def test_invalid_or_missing_measurement_is_unknown() -> None:
    coordinator = _coordinator(_device())
    humidity = next(item for item in SENSOR_DESCRIPTIONS if item.key == "humidity")
    pm10 = next(item for item in SENSOR_DESCRIPTIONS if item.key == "pm10")

    assert (
        UnifiAirQualitySensor(coordinator, "device-id", humidity).native_value is None
    )
    missing = UnifiAirQualitySensor(coordinator, "device-id", pm10)
    assert missing.native_value is None
    assert missing.extra_state_attributes is None


def test_disconnected_or_removed_device_is_unavailable() -> None:
    coordinator = _coordinator(_device(connected=False))
    description = SENSOR_DESCRIPTIONS[0]
    entity = UnifiAirQualitySensor(coordinator, "device-id", description)
    assert not entity.available

    coordinator.data = ProtectSnapshot("console", "Protect", "6.2", "2", ())
    assert not entity.available
    assert entity.native_value is None
