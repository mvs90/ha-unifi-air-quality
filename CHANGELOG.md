# Changelog

## 0.6.2 — UP-AirQuality device discovery

- Do not register other Protect sensor models as UP-AirQuality based on an
  empty or incidental air-quality field. Keep partial WebSocket updates from
  overriding known hardware identity.
- Allow explicit removal of previously misclassified devices from the Home
  Assistant integration when Protect confirms a known unsupported model.
  Devices remain paired in Protect.

## 0.6.1 — Protect bootstrap compatibility

- Keep the integration running when Protect reports an unrecognized, empty or
  null measurement status. Valid numeric readings and other alarms still
  update; an unverified status cannot start or clear an alarm. Fixes #16.

## 0.6.0 — reliability and security improvements

- Publish a HACS and Marketplace ZIP asset so catalogs can display GitHub
  download counts without integration telemetry.

- Organize installation methods as Marketplace, HACS and manual download; expand
  configuration, alarm-display, recovery and code-level documentation.
- Show a question mark in a circle for available alarm entities whose current
  state is unknown; retain the separate unavailable-state presentation.
- Show a red exclamation mark in a circle for fresh, confirmed active alarms in
  Home Assistant's device view; remove the picture on uncertainty or lost availability.
- Show a green check for fresh, confirmed OK alarm states in Home Assistant's
  device view; replace it on alarms and remove it on uncertainty or lost availability.
- Reconcile recovered alarm state; isolate forbidden writes from read sessions.
- Verify TLS by default for new entries and recover unexpected stream failures.
- Enforce freshness, schema validation and bounded WebSocket decoding.
- Verify setting writes against server state; preserve newer concurrent updates.
- Discover devices dynamically across all platforms without ID/name churn.
- Minimize diagnostics with allowlists and random per-export aliases.
- Add security/regression coverage and function-level documentation.
- Bound HTTPS responses, deadlines and authenticated origins; preserve fresh session state.
- Add endpoint/TLS reconfiguration while preserving the existing console identity.
- Require Home Assistant 2026.10.4, align dependency audits and bind development to loopback.
- Scan introduced commit history for secrets, including deleted intermediate values.
- Document pinned installation, upstream migration and rollback with preserved registries.
- Retain unresolved sensor/event alarms and canonical metric values through refresh races.
- Allow safe cleanup of permanently unpaired devices and scope obsolete controls per entry.
- Report relative per-metric freshness and authentication health in private diagnostics.
- Translate TLS/reconfiguration guidance in every catalog and identify upstream sponsorship.

## 0.5.4

- Isolate authentication cookies in a dedicated HTTP session for each config
  entry and each setup/reauthentication validation attempt. Close sessions on
  unload, failed setup, validation completion, and cancellation.
- Stop WebSocket reconnects and further password logins after credentials are
  rejected. Ask Home Assistant to start reauthentication instead of repeatedly
  risking a UniFi OS account lockout. Transient connection failures still retry.
- Add regression coverage for cookie isolation and authentication lifecycle.
- Thanks to Frenck for identifying these issues during the HACS review.

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
