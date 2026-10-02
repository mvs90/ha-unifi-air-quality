# UniFi Protect Air Quality for Home Assistant

An experimental, local-push Home Assistant custom integration for the UniFi
Protect **UP-AirQuality** sensor. It communicates directly with the console;
MQTT, a cloud account, and an additional bridge are not required.

> [!WARNING]
> This alpha uses Protect's undocumented private bootstrap and WebSocket APIs
> because the Public Integration API does not expose continuous air-quality
> readings yet. A dedicated API boundary keeps a later migration isolated.

## Current milestone: measurements, controls, and alarm triggers

Version `0.5.0` can:

- configure a local Protect console through the Home Assistant UI;
- authenticate with a dedicated local Protect user;
- find adopted UP-AirQuality devices in the private bootstrap response;
- follow private WebSocket updates with a 15-minute bootstrap safety refresh;
- register each sensor in the Home Assistant device registry;
- expose twelve measurement entities with native Home Assistant device and
  state classes; and
- configure LEDs, night mode, vape sensitivity, and per-metric
  alarms directly from Home Assistant;
- expose current alarm states as binary sensors and WebSocket alarm transitions
  as an event entity for automations; and
- export field-preserving, anonymized raw sensor data through Home Assistant
  diagnostics.

The entities update from Protect WebSocket pushes and include Protect's
classification (`safe`, `neutral`, and similar values) as the
`protect_status` attribute.

### Alarm states and automation triggers

Each metric has a read-only **Alarm** binary sensor. It turns on when Protect
reports a non-normal measurement status and off when the condition clears.
This covers both alarm event frames and firmware versions that carry the
transition only in the live sensor update.

The device also has one **Air quality alarm event** entity with these event
types for every metric:

- `<metric>_alarm_started`
- `<metric>_alarm_ended`

For example, CO2 emits `co2_alarm_started` and `co2_alarm_ended`; vape emits
`vape_alarm_started` and `vape_alarm_ended`. Select this event entity as an
automation trigger and choose the desired event type. Event data contains only
the metric, Protect status, and numeric alarm value when supplied—never a
credential or private Protect event identifier. Duplicate event and sensor
updates are collapsed into one state transition.

NOx also emits `nox_alarm_started` and `nox_alarm_ended`. The NOx index is read
directly from Protect's `airQuality.nox` field, even when the Protect app does
not display it. It is a relative, unitless index rather than a ppm reading.
Older firmware that omits this field reports an unknown NOx measurement.
Protect may also include the field but return `null`, as observed on the test
device. In that case the NOx sensor remains unknown until the device firmware
provides a numeric reading; adding the entity cannot create a missing reading.

| Entity | Native unit |
| --- | --- |
| Air quality index | AQI |
| Carbon dioxide | ppm |
| Humidity | % |
| Temperature | °C |
| PM1, PM2.5, PM4, PM10 | µg/m³ |
| Total volatile organic compounds | index (unitless) |
| VOC index | index (unitless) |
| NOx index | index (unitless) |
| Vape index | index (unitless) |

### Configuration controls

All confirmed writable fields currently exposed by Protect are grouped under
the device's **Configuration** section:

- status light, activity feedback, LED ring brightness, and LED ring metric;
- night mode, brightness, start time, and end time;
- vape detection and vape sensitivity;
- alarm enable switches for AQI, CO2, humidity, temperature, PM1, PM2.5, PM4,
  PM10, TVOC, VOC, NOx, and vape; and
- supported low and high thresholds for every alarm metric. Protect fixes the
  vape lower threshold at zero, so only its writable high threshold is shown.

An alarm switch is on only when Protect has at least one non-null threshold.
Setting a threshold automatically enables that alarm. Turning an alarm off
clears both thresholds; enter a new low or high threshold to enable it again.
The LED ring switch uses brightness zero for off and persistently restores the
last non-zero brightness when switched back on. The brightness control remains
available independently.

Vape is a firmware-specific exception: its lower threshold is fixed at zero,
its high threshold follows the vape-sensitivity value, and sending `null` does
not clear those limits. The integration therefore disables a vape alarm via
its `isEnabled` flag while preserving the device-managed thresholds.

Protect reports unset thresholds as `null`; their Home Assistant number
entities render this as `0` for a consistent numeric display. Internally the
integration still distinguishes an unset threshold from an explicitly set
zero, so alarm switches remain accurate. The opaque `alertInterval` and
`readingInterval` fields are not exposed because Protect provides neither
documented units nor supported ranges for them.

## Installation

### HACS custom repository

1. In HACS, open **Integrations** and add
   `https://github.com/mvs90/ha-unifi-air-quality` as a custom repository of
   type **Integration**.
2. Install **UniFi Protect Air Quality** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for
   **UniFi Protect Air Quality**.

For manual installation, copy `custom_components/unifi_air_quality` into the
same directory under your Home Assistant configuration directory and restart.

## Protect account

Create a dedicated **local** UniFi OS user with Protect read access. Do not use
an owner account, Ubiquiti cloud SSO, or expose Protect to the internet. The
password is stored by Home Assistant in the config entry and is never included
in integration logs or diagnostics.

Expired sessions are renewed automatically with the stored credentials. The
integration retries a rejected bootstrap request, settings update, or WebSocket
handshake once. Concurrent requests share the renewed session. Home Assistant
requests reauthentication only when login or the freshly authenticated request
is rejected; a temporarily unreachable console remains a connection failure.

Most consoles use a self-signed certificate. Leave certificate verification
off for those systems; enable it when the console has a certificate trusted by
the Home Assistant host.

## Diagnostics

After setup, open the integration's menu and select **Download diagnostics**.
The export removes credentials, network locations, names, MAC addresses,
serials, and stable identifiers. String values that are not explicitly known
to be harmless are also redacted, while field names, numbers, booleans, and
nested structure remain available for protocol development.

Review the JSON yourself before sharing it. Open a GitHub issue and attach only
the reviewed diagnostic file.

## Development

Requirements: Docker and Python 3.13 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
docker compose -f dev/compose.yaml up -d
```

The Docker configuration bind-mounts this checkout's `custom_components`
directory, so a container restart picks up code changes:

```bash
docker compose -f dev/compose.yaml restart homeassistant
docker compose -f dev/compose.yaml logs -f homeassistant
```

Home Assistant is available at <http://localhost:8123>. The image is pinned to
`2026.8.1`; update both `dev/compose.yaml` and the test dependency together.
The unit suite uses branch coverage with a minimum of 98%,
including malformed frames, authentication failures, WebSocket reconnects,
alarm start/end transitions, multi-source duplicate suppression, device
lifecycle cleanup, entity lifecycle, NOx measurements and controls, automatic
session renewal, concurrent expired requests, and all writable control
combinations.

## Removal

Remove the config entry under **Settings → Devices & services**, then uninstall
the repository in HACS and restart Home Assistant.

## Known limitations

- The private API can change with any Protect update.
- Username/password authentication is required until the public API exposes
  continuous readings and device-change notifications.
- Newly adopted or removed sensors require reloading the integration before
  their entities are added or removed.
- Automatic console discovery is not implemented.
- The alpha is installable as a HACS custom repository. Submission to HACS's
  default catalog additionally requires a brand entry in the central Home
  Assistant brands repository and a stable GitHub release.

This project is not affiliated with Ubiquiti Inc. UniFi and UniFi Protect are
trademarks of their respective owner.
