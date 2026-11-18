# Reading a diagnostic export

Download diagnostics from the integration entry in **Settings → Devices &
services**. Exports contain allowlisted readings and settings, a safe health
category, and verification ages. Credentials, hostnames, device identifiers and
raw exception messages are excluded. Device aliases are random for each export;
aliases from separate downloads cannot identify the same sensor.

## Transport health

| `transport.health` | Meaning and useful next step |
| --- | --- |
| `healthy` | The stream is connected and the exported cached numeric readings are fresh. Check each device's freshness and alarm fields when diagnosing one sensor. |
| `polling` | The stream is disconnected, but REST-verified readings remain fresh. Check update-channel access and connectivity if this persists. |
| `degraded` | At least one device is offline or one cached reading has expired, while some device data is still current. Locate the affected field under `freshness`. |
| `stale` | No device has sufficiently recent verified data, or an empty inventory itself is too old. Check console connectivity and the next successful refresh. |
| `protocol_error` | Protect returned an unsupported response. Compare firmware versions and provide the sanitized export with a compatibility report. The old cache is not considered current. |
| `authentication_failed` | Protect rejected authentication terminally. Reauthenticate with the intended local account. Its prior cached readings remain unavailable. |
| `empty_inventory` | A recent valid inventory contained no supported air-quality sensors. Check adoption and account visibility on the intended console. |
| `uninitialized` | No successful full inventory has yet been recorded. Complete setup or resolve its reported connection error. |

`bootstrap_age_seconds` is the elapsed time since the last successfully validated
full inventory. The `websocket_connected`, `authentication_failed` and
`protocol_error` flags explain the underlying transport state without exposing
server error text. A connected WebSocket alone is not proof of current readings.

## Device and metric freshness

Every exported device includes a `freshness` object:

- `age_seconds` is the elapsed time since any verified data for that device.
- `fresh` means verified data is within the 120-second freshness window and the
  client has no terminal authentication or protocol failure. A device can be
  freshly reported **offline**; inspect `isConnected` as well.
- `measurements` reports `age_seconds` and `fresh` separately for each canonical
  metric. A new CO₂ reading does not renew an old particulate or NOx reading.
- `alarms` has independent ages and freshness flags for confirmed statuses or
  alarm events. A fresh numeric reading does not necessarily confirm an alarm's
  end. Alarm absence in a stale cache does not establish an all-clear.

A `null` age means that this session has never verified that field; it does not
mean zero seconds. Ages remain visible after an authentication or protocol
failure, while the corresponding freshness flags become false. The export
contains elapsed seconds rather than host clock values or absolute timestamps.

The returned values are a troubleshooting snapshot. Use Home Assistant entity
availability and alarm state for automations, not the diagnostic export.
