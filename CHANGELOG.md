# Changelog

## 0.5.0

- Automatically renew expired Protect sessions for bootstrap refreshes,
  configuration updates, and WebSocket handshakes, with one bounded retry.
- Share authentication across concurrent requests and keep temporary connection
  failures separate from rejected credentials. Fixes issue #3.
- Add the unitless NOx index, NOx alarm binary sensor, alarm switch, low/high
  thresholds, and `nox_alarm_started`/`nox_alarm_ended` events. Fixes issue #2.
- Add regression coverage for session expiration, concurrent renewals, failed
  logins, offline sensors, and NOx setup and event handling.
