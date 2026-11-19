# Historical upstream HACS readiness report

This retained report concerns **mvs90/ha-unifi-air-quality**: the review of
upstream `0.5.3` on **2026-10-28**, followed by the `0.5.4` authentication changes.
Its test counts, runtime versions, repository observations and hardware checks
are historical. They are not new tests or catalog approval for this fork.

For current work, use the [user installation guide](../README.md#install-the-integration),
[installation and recovery policy](install-upgrade-rollback.md),
[contribution guide](../CONTRIBUTING.md) and
[required check policy](maintainer-ruleset.md). The fork's declared minimum and
CI baseline is Home Assistant 2026.10.4; the old preview result below is not a
claim that the current fork has been tested against every later HA release.

## Upstream authentication follow-up: 0.5.4

The upstream follow-up addressed Frenck's authentication review: config entries
and validation attempts used isolated authentication sessions. Owned sessions
were detached on unload, setup failure and validation completion/cancellation,
preserving Home Assistant's shared network connector. WebSocket credential
rejection stopped reconnects and requested reauthentication; the client also
blocked further automatic password attempts until a new client was created.
The API adapter remained independent of Home Assistant.

Regression tests used synthetic credentials and a loopback HTTP server for
Set-Cookie responses, account isolation, the untouched shared session and
cleanup. They required no live alarms or device-setting writes. The historical
compatibility results below were recorded for the preceding `0.5.3` baseline.

## Repository and packaging at the review

The upstream report recorded a public repository with a description, topics,
issues enabled, an identifiable MIT license and published non-prerelease GitHub
releases. It contained one integration under
`custom_components/unifi_air_quality`, a root `hacs.json`, and installation and
removal instructions. These release observations concern upstream, not the
fork's pinned-commit distribution.

At that review, `unifi_air_quality` was checked against Home Assistant Core's
`dev` integration list without a domain conflict. The proposed submitter was
the repository owner using a personal account. The integration had no country
restriction and was not an alpha/beta override of a Core integration.

## Historical Home Assistant compatibility

The report recorded 257 tests passing in these container runtimes:

| Runtime | Version | Coverage |
| --- | --- | --- |
| Stable container | 2026.10.4 | 99.17% |
| Preview container | 2026.11.0.dev202610281312 | 99.17% |

That suite covered registration of 71 entities, with 66 enabled by default and
five NOx entities disabled, translations, custom-name preservation, controls,
authentication recovery, WebSocket alarms, diagnostic redaction and local brand
images. The preview displayed a device-name prefix for user-named entities while
preserving the stored user name and entity ID.

The upstream preview tests used the official `pytest-home-assistant` package
from Core commit `4be5ba96f41e0f4052899e0f6e953c5ce2143e3c`. Test tools were installed
separately, retaining priority for the image's runtime packages. These results
do not extend the fork's support policy to older HA releases; the fork withdrew
2026.8.x support after its dependency review.

The original report also recorded read-only checks against Protect in both
images: bootstrap retrieval, WebSocket connection and sensor pushes. A request
guard allowed reads and session login, with no device-setting writes or induced
alarms. Credentials, addresses, identifiers and raw live data were not retained
in the report or fixtures. These records do not describe hardware checks
against the current fork.

## Branding and validator history

The reviewed upstream release shipped local `brand/icon.png` and
`brand/icon@2x.png`. Home Assistant supports local custom-integration brand
images from 2026.3. The report required the full HACS validator with no ignored
checks, plus Ruff, tests and Hassfest before publishing a release for submission.
It omitted an optional integration-category manifest field because the validator
used at that time rejected it. That observation is tied to the historical
validator and should not be treated as a permanent manifest restriction.

## Catalog submission

The original report prepared submission of `mvs90/ha-unifi-air-quality` to the
HACS default catalog. It did not constitute catalog acceptance. Submitting this
fork is a separate action under its own repository name and requires a fresh
review of the current official requirements, release and validator results.
For ordinary installation, follow the custom-repository instructions in the
user guide; this report is not an installation prerequisite.

## References

- [HACS general requirements](https://www.hacs.xyz/docs/publish/start/)
- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)
- [HACS default-catalog inclusion](https://www.hacs.xyz/docs/publish/include/)
- [Home Assistant local brand images](https://contributors.home-assistant.io/blog/2026/02/24/brands-proxy-api/)
