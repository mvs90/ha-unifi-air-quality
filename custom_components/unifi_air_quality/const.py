"""Constants for UniFi Protect Air Quality."""

from datetime import timedelta

DOMAIN = "unifi_air_quality"

CONF_VERIFY_SSL = "verify_ssl"
DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = False

BOOTSTRAP_REFRESH_INTERVAL = timedelta(minutes=15)

MANUFACTURER = "Ubiquiti"
MODEL = "UP-AirQuality"
