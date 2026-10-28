# Changelog

## 0.5.1

- Ship the English translation catalog required by custom integrations. Fix
  repeated device names and generic "Problem" alarm names on English and
  fallback-language installations (issue #5).
- Preserve existing entity IDs and user-assigned names when corrected names
  are loaded after an update.
- Add complete French, Spanish, Italian, Dutch, Polish, Portuguese (Portugal
  and Brazil), Swedish, Danish, Czech, Russian, Ukrainian, Simplified Chinese,
  and Japanese translations, including setup dialogs and selection options.
- Disable the NOx measurement, alarm state, alarm switch, and both threshold
  entities by default; they can be enabled manually. Preserve existing user
  choices when upgrading.
- Add fresh-install naming, fallback-language, rename/reload, and NOx opt-in
  regression tests using Home Assistant's real entity registry.

## 0.5.0

- Automatically renew expired Protect sessions for bootstrap refreshes,
  configuration updates, and WebSocket handshakes, with one bounded retry.
- Share authentication across concurrent requests and keep temporary connection
  failures separate from rejected credentials. Fixes issue #3.
- Add the unitless NOx index, NOx alarm binary sensor, alarm switch, low/high
  thresholds, and `nox_alarm_started`/`nox_alarm_ended` events. Fixes issue #2.
- Preserve an unknown NOx measurement when Protect returns `null` or omits the
  field; numeric readings depend on the device firmware.
- Add regression coverage for session expiration, concurrent renewals, failed
  logins, offline sensors, and NOx setup and event handling.
