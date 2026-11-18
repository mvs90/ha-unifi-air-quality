# Main-branch review and validation policy

`.github/rulesets/main.json` is a ready-to-import GitHub branch ruleset for this
repository. Committing the file does **not** activate repository settings. An
administrator must import/apply it and verify the resulting effective rules.
Until that verification is recorded, branch protection remains unconfirmed.

The ruleset targets `refs/heads/main`, blocks deletion and force pushes, requires
pull requests with resolved review conversations, requires an up-to-date branch,
and has no bypass actors. These are the exact final GitHub Actions job contexts:

| Workflow | Required check context |
| --- | --- |
| Tests | `Home Assistant 2026.10.4` |
| Security | `Security scan` |
| HACS | `HACS validation` |
| Hassfest | `Hassfest validation` |

The names are deliberately distinct. A workflow name alone is not a check
context. The JSON binds each check to the verified GitHub Actions app
(`integration_id: 15368`). Confirm that source when importing. Do not remove a required check
to make a pending merge pass; repair or rerun its workflow instead.

## Apply and verify

1. Wait until these exact jobs have run on an ordinary PR so GitHub can identify
   them. Open repository **Settings → Rules → Rulesets**, import
   `.github/rulesets/main.json`, and set enforcement to **Active**. An equivalent
   authenticated repository-rulesets REST POST can use the same JSON body.
2. Read back the saved ruleset and the effective rules for `main`. Confirm all
   four contexts, resolved conversations, current base, deletion/force-push
   blocks, and the empty bypass list. Save the read-back evidence privately or
   link the non-sensitive ruleset page in the governance issue.
3. Use a disposable documentation-only PR. Confirm GitHub shows merge blocked
   while a required check is pending/failing and while a review conversation is
   unresolved. Resolve the conversation and let every required job pass; then
   confirm merge becomes available. Close the probe PR without merging it.
4. If any check is missing or a direct update remains permitted, leave the
   governance issue open and correct the settings. Do not test destructive force
   pushes or deletion against the real main branch.

## Maintainer review

A maintainer independently reviews the final commit, addresses valid findings,
and verifies all required results before merge. Request the repository's Codex
review and check its reviewed SHA against the final PR head. Re-run the review
after material fixes. The ruleset requires zero formal approving reviews so a
single-maintainer repository is operable; this does not waive the independent
review process. Codex comments do not count as a GitHub approving review.

Publish release tags only from an accepted main commit. Keep existing release
names and tags immutable. Required-check name changes must update this file and
the live ruleset together after the replacement job has run successfully.

## Emergency change

There is no standing bypass identity. If an outage genuinely prevents the normal
path, an administrator must record the incident, exact ref/commit, reason, scope,
owner and expiry before authorizing a temporary exception. Keep it confined to
that change, restore and read back the original rules immediately afterward, and
complete the omitted review/checks with a follow-up PR. Record every exception in
the incident or repository issue; never silently disable all protection.
