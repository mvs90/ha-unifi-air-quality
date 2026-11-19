"""Constants for UniFi Protect Air Quality."""

from datetime import timedelta

DOMAIN = "unifi_air_quality"

CONF_VERIFY_SSL = "verify_ssl"
DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = True

BOOTSTRAP_REFRESH_INTERVAL = timedelta(seconds=60)
# Each device, numeric reading, and alarm has its own verification timestamp.
# Ages equal to this limit are already stale; socket connectivity is insufficient.
DATA_FRESHNESS_SECONDS = 120

MANUFACTURER = "Ubiquiti"
MODEL = "UP-AirQuality"

# Canonical payload key -> legacy wire key. Entity unique IDs use separate keys
# from their platform descriptions, so normalizing a wire alias cannot rename one.
MEASUREMENT_ALIASES = {
    "pm1p0": "pm1",
    "pm2p5": "pm2_5",
    "pm4p0": "pm4",
    "pm10p0": "pm10",
    "vape": "vapeIndex",
}
