# Changelog

## 0.5.3

- Verify the full 257-test suite against Home Assistant 2026.10.4 and preview
  2026.11.0.dev202610281312, with 99.17% coverage in both container runtimes.
- Add CI coverage of the declared 2026.8.0 minimum, the previous 2026.8.1
  baseline, and 2026.10.4, each with matching Home Assistant test helpers.
- Update the default developer container and test dependencies to 2026.10.4.
- Support the preview's official test-helper package, updated device-registry
  API, and device-prefixed display names while preserving exact entity IDs and
  user-assigned registry names.
- Add an isolated container test runner and refresh the HACS readiness report.
- Confirm real bootstrap and WebSocket communication without device-setting
  writes or new live alarms. The integration's runtime behavior is unchanged.

## 0.5.2

- Adopt the maintainer-selected sensor-and-house logo and ship local brand
  icons at 256 and 512 pixels, with transparent backgrounds.
- Enable the complete HACS validator, including the previously ignored brands
  check.
- Document HACS submission readiness and the local branding mechanism.
- Credit and thank Tommo-101 for the foundational UPAQ-MQTT work.

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
