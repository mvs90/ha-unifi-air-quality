# Integration artwork

[logo-source.png](logo-source.png) is the exact image selected and supplied by the maintainer.
It is a stylized sensor-and-house illustration, not an official product photo
or an official Home Assistant logo.

The runtime images are in
[`custom_components/unifi_air_quality/brand/`](../../custom_components/unifi_air_quality/brand/):

- `icon.png`: 256 × 256 pixels.
- `icon@2x.png`: 512 × 512 pixels.

Both preserve transparency. They use the same selected artwork, with only
transparent outer margins trimmed and uniform resizing applied. The original
source image is retained unchanged here for future exports.

The icon is also used as the logo through Home Assistant's standard image
fallbacks; identical logo files are intentionally not duplicated.

These PNGs identify the integration. The green OK and red alarm symbols are
separate inline SVG entity pictures in
[`binary_sensor.py`](../../custom_components/unifi_air_quality/binary_sensor.py).
Unknown alarm states use Home Assistant's `mdi:help-circle` icon, and unavailable
alarms keep its standard presentation. Cards decide whether to display entity
pictures; see the [alarm display guide](../../README.md#alarm-thresholds-and-automations).

Home Assistant serves the bundled PNGs through its local brand-image API. This
mechanism is described in the official
[custom integration brand-image guide](https://contributors.home-assistant.io/blog/2026/02/24/brands-proxy-api/).
