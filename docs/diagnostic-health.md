# Reading a diagnostic export

Download diagnostics from the integration entry in **Settings → Devices &
services**. Exports contain allowlisted readings and settings, a safe health
category, and verification ages. Credentials, hostnames, device identifiers and
raw exception messages are excluded. Device aliases are random for each export;
they do not provide a stable identifier across downloads. Review the export
before sharing it: readings and settings still describe your installation.

## Transport health

| `transport.health` | Meaning and useful next step |
| --- | --- |
| `healthy` | The stream is connected and the exported cached numeric readings are fresh. Check each device's freshness and alarm fields when diagnosing one sensor. |
| `polling` | The stream is disconnected, but cached verified device data and numeric readings remain fresh. They may include the last stream updates; this category does not prove that a new REST refresh succeeded. Check update-channel access if this persists. |
| `degraded` | At least one device is offline, its device-level data has expired, or one cached numeric reading has expired, while some device data is still current. Locate the affected device or field under `freshness`. |
| `stale` | No device has sufficiently recent verified data, or an empty inventory itself is too old. Check console connectivity and the next successful refresh. |
| `protocol_error` | Protect returned an unsupported response. Compare firmware versions and provide the sanitized export with a compatibility report. The old cache is not considered current. |
| `authentication_failed` | Protect rejected authentication terminally. Reauthenticate with the intended local account. Its prior cached readings remain unavailable. |
| `empty_inventory` | A recent valid inventory contained no supported air-quality sensors. Check adoption and account visibility on the intended console. |
| `uninitialized` | No successful full inventory has yet been recorded. Complete setup or resolve its reported connection error. |

`bootstrap_age_seconds` is the elapsed time since the last successfully validated
full inventory. The `websocket_connected`, `authentication_failed` and
`protocol_error` flags explain the underlying transport state without exposing
server error text. A connected WebSocket alone is not proof of current readings.

Health categories are evaluated in this order: authentication failure, protocol
failure, initialization, empty inventory, then device/measurement freshness and
stream connectivity. `healthy` and `polling` summarize numeric data; they do not
require every alarm to be confirmed. Unknown or absent optional measurements do
not by themselves produce `degraded`.

## Device and metric freshness

Every exported device includes a `freshness` object:

- `age_seconds` is the elapsed time since any verified data for that device.
- `fresh` means verified data is less than 120 seconds old and the
  client has no terminal authentication or protocol failure. A device can be
  freshly reported **offline**; inspect `isConnected` as well.
- `measurements` reports `age_seconds` and `fresh` separately for each canonical
  metric. A new CO₂ reading does not renew an old particulate or NOx reading.
- `alarms` has independent ages and freshness flags for confirmed statuses or
  alarm events. A fresh numeric reading does not necessarily confirm an alarm's
  end. Alarm absence in a stale cache does not establish an all-clear.

A `null` age means no verification timestamp is retained for that field. It may
never have been verified, or its previous confirmation may have been discarded
during resynchronization or device removal. It does not mean zero seconds. Ages
remain visible after an authentication or protocol failure, while the
corresponding freshness flags become false. The export
contains elapsed seconds rather than host clock values or absolute timestamps.

The per-metric flags also require device-level freshness. They do not include
every entity availability condition: the Home Assistant entity additionally
requires its device to be connected and its coordinator to be healthy.

## Counts and omitted fields

`air_quality_device_count` counts supported sensors in the exported inventory.
`active_alarm_count` counts cached alarm records, including unresolved records
retained during recovery; it is not the number of currently available alarm
entities. In particular, zero does not establish that every metric is safe.

Only numeric version strings are retained in `protect_version` and
`firmwareVersion`; other formats appear as `null`. Unsupported raw keys are
omitted, and non-allowlisted string values appear as `**REDACTED**`. These are
privacy filters, not proof that the original Protect fields were absent.

The returned values are a troubleshooting snapshot. Use Home Assistant entity
availability and alarm state for automations, not the diagnostic export.
