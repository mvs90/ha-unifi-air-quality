# HACS default-catalog readiness

Reviewed on 2026-10-28 for release `0.5.3`.

## Repository and packaging

The public repository has a description, search topics, issues enabled, and
an identifiable MIT license. It contains one integration under
`custom_components/unifi_air_quality`, a root `hacs.json`, installation and
removal instructions, and published non-prerelease GitHub releases.

The domain `unifi_air_quality` was checked against Home Assistant Core's
`dev` integration list and does not conflict with a core domain. This is an
independent custom integration, not an override of a core integration.

The submitting account is the repository owner and is a personal account.
The integration is not country-restricted, so no `country` restriction is
needed in `hacs.json`. It is not an alpha/beta fork of a Core integration.

## Home Assistant compatibility

The complete 257-test suite passed against both actual container runtimes:

| Runtime | Version | Coverage |
| --- | --- | --- |
| Stable container | 2026.10.4 | 99.17% |
| Preview container | 2026.11.0.dev202610281312 | 99.17% |

The suite includes fresh registration of all 71 entities, 66 enabled by
default, five NOx entities disabled by default, every translation, custom-name
preservation, controls, authentication recovery, WebSocket alarms, diagnostics
redaction, and local brand-image delivery. Only Home Assistant's display of a
user-named entity changes in the preview: it also includes the device name.
The exact stored user name and entity ID remain unchanged.

CI tests the declared minimum 2026.8.0, the previous baseline 2026.8.1, and
stable 2026.10.4 with their matching test-helper releases. Preview tests use
the new official `pytest-home-assistant` package from Core commit
`4be5ba96f41e0f4052899e0f6e953c5ce2143e3c`, rather than replacing or patching
Home Assistant itself. The container runner installs test tools separately
and retains priority for the image's existing runtime packages.

Separate read-only checks of the current integration in both container images
successfully retrieved real Protect bootstrap data, connected the WebSocket,
and observed sensor pushes. A request guard allowed only reads and the login
needed to establish a session; no device settings were written and no live
alarms were induced. Credentials, addresses, identifiers, and raw live data
are not included in this report or its fixtures.

## Branding and validation

The selected artwork is shipped locally as `brand/icon.png` and
`brand/icon@2x.png`. Home Assistant supports local custom-integration images
since 2026.3, earlier than this integration's minimum supported HA version.
A separate submission to `home-assistant/brands` is not required.

The HACS workflow includes every check: it has no `ignore` input. Tests,
Ruff, HACS, and Hassfest must all pass on the final release commit. Publish
the full `v0.5.3` GitHub release after those checks, rather than only a tag,
so the submitted release includes the artwork and the complete validator.

Optional integration categories are omitted because the current Hassfest
validator rejects that manifest field. No validation check is bypassed.

## Submission

The repository is not yet included in `hacs/default`. After the release
checks pass, the owner can propose `mvs90/ha-unifi-air-quality` in that
repository's alphabetically sorted `integration` list and complete its PR
template from a new branch based on `hacs/default:master`. The PR must allow
maintainer edits and link the successful HACS/Hassfest runs and the release
published afterward. No review requests should be sent. This report does not
imply HACS maintainer approval; catalog acceptance remains a separate review.

## References

- [HACS general requirements](https://www.hacs.xyz/docs/publish/start/)
- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)
- [HACS default-catalog inclusion](https://www.hacs.xyz/docs/publish/include/)
- [Home Assistant local brand images](https://contributors.home-assistant.io/blog/2026/02/24/brands-proxy-api/)
