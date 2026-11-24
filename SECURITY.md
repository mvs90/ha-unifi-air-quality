# Security policy

Please report security issues privately through the repository's
[Security page](https://github.com/mvs90/ha-unifi-air-quality/security).
Use **Report a vulnerability** when available. If private reporting is not
available, open an issue asking only for a private security contact; include no
vulnerability details. See [GitHub's private reporting guide](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately). Do not open a public issue containing credentials or
unredacted diagnostics.

For a [published release](https://github.com/mvs90/ha-unifi-air-quality/releases),
report the release tag and installed manifest version. For an unreleased build,
also record the exact commit; a moving branch name does not identify the code.
See [the install/rollback guide](docs/install-upgrade-rollback.md).
This integration is intended for local network access to Protect and must not
be used to expose a console publicly.

## Automated checks and dependency ownership

PRs and pushes run Ruff's security (`S`) rules, secret scanning, and a
`pip-audit` check of the actually resolved **Home Assistant 2026.10.4**/test
environment. This is the pinned CI baseline and the declared HACS minimum;
older 2026.8.x support was withdrawn because its aiohttp, cryptography, PyJWT and
uv advisories exceeded the scoped current-baseline policy. Upgrade HA rather
than adding broad exceptions. Each successful audit logs the HA version, number
of audited packages and SHA-256 of the sorted package/version inventory.
The weekly Security run checks for newly published advisories against that
baseline, not each user's installed HA version. Synthetic S307 and
AWS-key samples verify both scanners can fail. Logs contain advisory IDs and
package versions; secret values and full audit reports are not published.

Actions are pinned to official 40-character commits (release tags are peeled to
commits). HACS/Hassfest wrappers internally use mutable image tags, so their official
images are invoked directly with registry-verified immutable SHA-256 digests.
Review the official `hacs/action:main` and `home-assistant/hassfest:latest` image
digests weekly alongside dependency updates, update their workflow pins and run the
complete validators before merging. Image updates are maintainer-owned; the
weekly workflow does not grant write access to create update PRs. Review action and Python dependency pins weekly. Review upstream
changes and all CI results before merging its PRs. No workflow needs write permissions; checkout does not persist
credentials in the Security job.

`security-advisories.json` tracks individual accepted upstream advisories by exact
package version, owner, reason and expiry. It currently records Pillow 12.3.0
advisories requiring 13.0.0: HA 2026.10.4 pins the affected version. This integration
never invokes Pillow or decodes images; HA serves the bundled static PNG files.
Overriding HA's dependency pin would invalidate the supported runtime. The owner
must move the test baseline to a compatible patched HA release. This acceptance
does not assert that the rest of a user's HA installation is unaffected. A new
advisory, different affected version or finding with an expired acceptance fails
the gate. An unused exception is not by itself a scanner finding. No entire
advisory category or package is ignored. Review these exceptions weekly; do not
extend expiry without a fresh assessment.

The secret baseline contains reviewed translation labels, workflow option names
and synthetic test credentials only, with hashed findings marked non-secret.
Review each newly detected finding; never regenerate and accept the baseline
without inspection. Ruff suppressions are limited to pytest assertions/synthetic
credentials and individual developer-runner subprocess calls with explicit argv
and no shell. Real credentials must never enter source control.

## Commit-history secret scanning

The Security job fetches full history with a read-only token and no persisted
credentials. It retains the tracked-tree scan and scans every changed text blob
in PR and push commit ranges, including an intermediate secret later removed.
PR/push scans use the **base commit's** reviewed hash baseline, so changing that
baseline in the same PR cannot self-approve a secret. New inline allowlist pragmas
also cannot hide a value: only exact file/type/hash matches already approved
inline in the trusted base are accepted. Baseline files are scanned too; only
valid SHA-1 `hashed_secret` metadata is neutralized. Hex-entropy findings matching
an exact commit ID already reachable from the trusted base are recognized as
public Git references; other detectors still apply, and new PR commit IDs or
unknown hex values receive no such exemption. Release, manual and weekly
runs scan the accepted head's full reachable history, using its reviewed
baseline. For a new-branch push without a prior commit, the trusted baseline is
the fetched `origin/main`, not the newly pushed branch's baseline. Binary and
non-UTF-8 assets are outside the text scanner; excessive text blobs or scanner/Git
failures fail the check instead of silently passing. The history
self-test creates two synthetic commits with an invalid AWS-shaped key, deletes
it before the final tree, and requires detection. Logs reveal only the commit,
detector and line number; secret values, file names and raw detector output are
not published. The CI job definitions are maintained under `.github/workflows/`;
branch protection is configured separately by repository maintainers.

If a real credential is committed, revoke/rotate it immediately; removing its
current line is insufficient. Stop publication, privately notify the maintainer,
identify affected commits/releases and follow GitHub's sensitive-data removal
procedure with collaborators. Rewrite or remove affected refs only through the
explicitly authorized incident process; coordinate any required fresh clones.
Re-run tree and history scans before resuming. Never copy a credential into an
issue, review comment, CI artifact or scanner allowlist. An exact harmless test
fixture may be reviewed separately as non-secret; that is not an exception for
a real credential.
