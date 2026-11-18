# UniFi Protect Air Quality for Home Assistant

<p align="center">
  <img src="custom_components/unifi_air_quality/brand/icon@2x.png"
       alt="UniFi Protect Air Quality integration logo" width="180">
</p>

An experimental, local-push Home Assistant custom integration for the UniFi
Protect **UP-AirQuality** sensor. It communicates directly with the console;
MQTT, a cloud account, and an additional bridge are not required.

> [!WARNING]
> This alpha uses Protect's undocumented private bootstrap and WebSocket APIs
> because the Public Integration API does not expose continuous air-quality
> readings yet. A dedicated API boundary keeps a later migration isolated.

## Current milestone: measurements, controls, and alarm triggers

Version `0.5.4` can:

- configure a local Protect console through the Home Assistant UI;
- authenticate with a dedicated local Protect user;
- find adopted UP-AirQuality devices in the private bootstrap response;
- follow private WebSocket updates with a 60-second bootstrap safety refresh;
- register each sensor in the Home Assistant device registry;
- expose twelve measurement entities with native Home Assistant device and
  state classes; and
- configure LEDs, night mode, vape sensitivity, and per-metric
  alarms directly from Home Assistant;
- expose current alarm states as binary sensors and WebSocket alarm transitions
  as an event entity for automations; and
- export allowlisted current sensor data with per-export aliases through Home Assistant
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

The five NOx entities (measurement, alarm state, alarm switch, and both
thresholds) are disabled by default in Home Assistant. Enable them manually
from the device's entity list when your firmware provides usable readings.
Existing enabled/disabled choices are preserved when upgrading.

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

This is the maintained `Daniel-Dietz/ha-unifi-air-quality` fork. Add
`https://github.com/Daniel-Dietz/ha-unifi-air-quality` as a custom repository
of type **Integration** in HACS, install it, and restart Home Assistant.
Then open **Settings → Devices & services → Add integration** and select
**UniFi Protect Air Quality**. Preserve your existing integration entry when
switching to this fork; entity IDs and saved TLS choices remain unchanged.

### Manual installation

For manual installation, copy `custom_components/unifi_air_quality` into the
same directory under your Home Assistant configuration directory and restart.

Setup dialogs, entity names, and selection options are translated into English,
German, French, Spanish, Italian, Dutch, Polish, Portuguese (Portugal and Brazil),
Swedish, Danish, Czech, Russian, Ukrainian, Simplified Chinese, and Japanese.
Other languages fall back to English.

If an older installation shows repeated device names or generic
"Problem" alarms, update to `0.5.1` or newer and restart Home Assistant. The
display names are corrected without recreating the integration. Existing
entity IDs and names you assigned yourself are preserved.

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
Each config entry and each setup/reauthentication attempt uses its own HTTP
session and cookie jar, never Home Assistant's shared session. After rejected
credentials, automatic password logins stop until reauthentication succeeds;
the WebSocket also requests reauthentication immediately.

New setups verify HTTPS and WSS certificates by default. Use a console
certificate issued by a CA trusted by Home Assistant and a hostname matching
its SAN. For a private CA, install/trust that CA in the Home Assistant runtime
using your deployment's supported CA mechanism, then verify access before setup.
Do not merely trust a certificate in a browser: HA's runtime must trust it.
Existing entries retain their explicit TLS choice. Turning verification off is
an explicit exception: it allows network attackers to intercept credentials,
session cookies and readings. Prefer a trusted local-CA certificate.

Reading and monitoring require Protect read permission. Changing LEDs, night
mode or thresholds additionally requires sensor-settings write permission.
A forbidden PATCH (403) returns an actionable control error and preserves the
read session; only an expired PATCH (401) gets one bounded session renewal.
Settings are read back through bootstrap after each successful PATCH. If Protect
normalizes or delays a setting, HA displays confirmed state and reports that the
request is not yet confirmed. Refresh/wait before retrying; no optimistic value
is published. Writes are serialized, and an in-flight older HTTP response cannot
overwrite a newer push or completed refresh.

## Diagnostics

After setup, open the integration's menu and select **Download diagnostics**.
The export includes only current allowlisted measurements, selected settings,
connection/protocol health and numeric Protect/device firmware versions. It omits
hostnames, credentials, cookies, tokens, MACs, serials, names, history and unknown
API fields, including newly introduced nested fields. Device IDs receive random
aliases scoped to one export: correlation within that file is possible, while
there is no stable cross-export hash/fingerprint. This is data minimization,
not a guarantee of irreversible anonymity.

**Measurements, settings, firmware and device counts can still reveal household
or operational information. Review exports before sharing; do not post them
publicly without review.** Report issues to this fork's issue tracker.

## Recovery, availability and private-API compatibility

Full refreshes are scheduled at least every 60 seconds (with a five-second
health-timer granularity), independently of push traffic; in-flight refreshes
are not duplicated. A reading or confirmed alarm status is fresh for **120 seconds** after a verified
bootstrap or numeric update for that metric. An update to another metric,
an empty heartbeat, or an unknown field cannot refresh an old reading. HA
reevaluates availability every five seconds, so stale readings become unavailable
within **125 seconds**. A connected WebSocket alone is insufficient evidence of
fresh measurements. Device-offline flags still take effect immediately.
Brief transport outages preserve only still-fresh data; successful refreshes
or numeric/status pushes restore it automatically. Protocol/authentication failures
make the affected integration unavailable immediately.

Reconnects refresh bootstrap before following its cursor. Current measurement
statuses replace transient alarm caches, clearing missed end events. An orphaned
alarm without a confirming status expires to **unknown**, without fabricating an
`ended` event. Existing public alarm event types and unique IDs are retained.
Multiple event IDs and sensor/event sources remain aggregated during streaming.
Repeated reconnect failures use 1, 2, 4, 8, 16, 30 second delays (30-second cap);
a sustained connection resets this sequence. Logs contain error classes, not raw
frames or exception payloads. Unload cancels the task and releases listeners.

New devices are discovered on all seven entity platforms during push/refresh
without reload. Removed devices' entities remain unavailable and retain registry
settings/history; reappearance reuses the same IDs and user names.

The undocumented bootstrap, sensor PATCH and two-frame WebSocket APIs may change
with any Protect/firmware upgrade. Known required schema/status changes produce
an actionable HA update error and sanitized diagnostics instead of false alerts.
Unknown optional fields are tolerated. Frames are limited to **1 MiB wire data
per frame**, **2 MiB decompressed per frame**, and two frames per message; bounded
zlib expansion rejects bombs, truncation and concatenated streams. Incompatible
frames trigger a controlled reconnect/resync.

The regression fixtures cover the existing scalar Protect variant (fixture
labels Protect **6.2.0**, firmware **1.0.0**) and the structured NOx/status variant
whose versions were not recorded. These are **synthetic protocol fixtures**, not
claims that hardware/firmware releases have been certified. This change has not
been tested against a live Protect console. No other version support is claimed.
Before a firmware upgrade, back up HA/configuration, record versions, test a
reviewed bootstrap/diagnostic export, compare live readings with Protect, verify
reconnect/offline behavior and a permitted control readback. Check alarms in a
safe test environment; do not induce live safety alarms. Keep a rollback path
and report changed schemas with reviewed diagnostic data.

## Development

Requirements: Docker and Python 3.14.2 or newer for the current test environment.

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

Home Assistant is available at <http://localhost:8123>. The image and default
test dependencies are pinned to `2026.10.4`. CI also checks `2026.8.0` (the
declared minimum) and the previous `2026.8.1` baseline with matching test helpers.

Run the full suite in a disposable Home Assistant container, without mounting
live configuration or publishing any ports:

```bash
docker run --rm -v "$PWD:/work:ro" --entrypoint python3 \
  ghcr.io/home-assistant/home-assistant:2026.10.4 /work/dev/run_container_tests.py
```

The same runner supports development images and the new official Home Assistant
test-helper package. The preview helper source is pinned for reproducibility;
use `HA_TEST_HELPERS` to select the matching package when testing a newer image.
See [the compatibility report](docs/hacs-readiness.md#home-assistant-compatibility)
for the exact preview build checked. Disposable tests never mount the real
Home Assistant configuration and do not contact Protect.
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
- Automatic console discovery is not implemented.
- This fork uses HACS custom-repository installation and bundled `brand/` images.
  Historical upstream submission results are recorded separately in
  [HACS submission readiness](docs/hacs-readiness.md).

## Acknowledgements

This integration builds on the groundwork shared by
[Tommo-101](https://github.com/Tommo-101) in
[UPAQ-MQTT](https://github.com/Tommo-101/UPAQ-MQTT).
Thank you for the original work and for sharing the approach to accessing
UP-AirQuality data through UniFi Protect. That contribution helped make this
direct Home Assistant integration possible.

This project is not affiliated with Ubiquiti Inc. UniFi and UniFi Protect are
trademarks of their respective owner.

## Support this project

If you find this project useful, you can support its development and maintenance
with a voluntary contribution via [GitHub Sponsors](https://github.com/sponsors/mvs90) or
[PayPal](https://www.paypal.me/mvs90).
Thank you for your support!
