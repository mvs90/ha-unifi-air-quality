# HACS default-catalog readiness

Reviewed on 2026-10-28 for release `0.5.2`.

## Repository and packaging

The public repository has a description, search topics, issues enabled, and
an identifiable MIT license. It contains one integration under
`custom_components/unifi_air_quality`, a root `hacs.json`, installation and
removal instructions, and published non-prerelease GitHub releases.

The domain `unifi_air_quality` was checked against Home Assistant Core's
`dev` integration list and does not conflict with a core domain. This is an
independent custom integration, not an override of a core integration.

## Branding and validation

The selected artwork is shipped locally as `brand/icon.png` and
`brand/icon@2x.png`. Home Assistant supports local custom-integration images
since 2026.3, earlier than this integration's minimum supported HA version.
A separate submission to `home-assistant/brands` is not required.

The HACS workflow includes every check: it has no `ignore` input. Tests,
Ruff, HACS, and Hassfest must all pass on the final release commit. Publish
the full `v0.5.2` GitHub release after those checks, rather than only a tag,
so the submitted release includes the artwork and the complete validator.

## Submission

The repository is not yet included in `hacs/default`. After the release
checks pass, the owner can propose `mvs90/ha-unifi-air-quality` in that
repository's alphabetically sorted `integration` list and complete its PR
template. This preparation does not submit that external PR or imply HACS
maintainer approval.

## References

- [HACS general requirements](https://www.hacs.xyz/docs/publish/start/)
- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)
- [HACS default-catalog inclusion](https://www.hacs.xyz/docs/publish/include/)
- [Home Assistant local brand images](https://contributors.home-assistant.io/blog/2026/02/24/brands-proxy-api/)
- [Integration categories](https://contributors.home-assistant.io/core/integrations/categories/)
