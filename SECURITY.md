# Security policy

Please report security issues privately through GitHub's security advisory
feature. Do not open a public issue containing credentials or unredacted
diagnostics.

Only the latest release is supported. This integration is intended for local
network access to Protect and must not be used to expose a console publicly.

## Automated checks and dependency ownership

PRs and pushes run Ruff's security (`S`) rules, secret scanning, and a
`pip-audit` check of the actually resolved current Home Assistant/test environment.
The weekly Security run checks for newly published advisories. Synthetic S307 and
AWS-key samples verify both scanners can fail. Logs contain advisory IDs and
package versions; secret values and full audit reports are not published.

Actions are pinned to official 40-character commits (release tags are peeled to
commits). HACS/Hassfest wrappers internally use mutable image tags, so their official
images are invoked directly with registry-verified immutable SHA-256 digests.
Review the official `hacs/action:main` and `home-assistant/hassfest:latest` image
digests weekly alongside Dependabot PRs, update their workflow pins and run the
complete validators before merging. Image updates are maintainer-owned; the
weekly workflow does not grant write access to create update PRs. Dependabot checks action and Python pins
weekly at 09:00 Europe/Berlin. Review upstream changes and all CI results before
merging its PRs. No workflow needs write permissions; checkout does not persist
credentials in the Security job.

`security-advisories.json` tracks individual accepted upstream advisories by exact
package version, owner, reason and expiry. It currently records Pillow 12.3.0
advisories requiring 13.0.0: HA 2026.10.4 pins the affected version. This integration
never invokes Pillow or decodes images; HA serves the bundled static PNG files.
Overriding HA's dependency pin would invalidate the supported runtime. The owner
must move the test baseline to a compatible patched HA release. This acceptance
does not assert that the rest of a user's HA installation is unaffected. A new
advisory, different version or expired exception fails the gate. No entire advisory
category or package is ignored. Review these exceptions weekly; do not extend
expiry without a fresh assessment.

The secret baseline contains reviewed translation labels, workflow option names
and synthetic test credentials only, with hashed findings marked non-secret.
Review each newly detected finding; never regenerate and accept the baseline
without inspection. Ruff suppressions are limited to pytest assertions/synthetic
credentials and individual developer-runner subprocess calls with explicit argv
and no shell. Real credentials must never enter source control.
