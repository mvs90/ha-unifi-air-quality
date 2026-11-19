# Install, migrate and roll back a pinned fork build

## Supported distribution and identity

The fork identifies this change set as **0.6.0** in `manifest.json`,
`pyproject.toml` and `CHANGELOG.md`. Its supported distribution is an **immutable
full commit**, accepted into `Daniel-Dietz/ha-unifi-air-quality` main after the
Tests, Security, HACS and Hassfest jobs and independent review pass. The accepted
PR/merge record identifies the full 40-character commit. Fixes target the latest
accepted pinned commit; earlier commits must be upgraded to receive fixes.
Feature branches and unmerged candidates are not supported deployments.

A version number alone does not identify a commit. Record both `0.6.0` and the
full accepted SHA, plus the Home Assistant version, in the installation notes.
The installed component's `manifest.json` identifies the fork through its
repository/issue links and reports `0.6.0`. The fixed ZIP linked from the README
uses the full accepted commit in its download URL and original archive filename.
Keep that ZIP with its original filename as the exact build record; users do not
need Git or a manually created marker for this installation path. Include the
archive filename and installed version in bug reports. The `BUILD_COMMIT` marker
described below is an alternative for staged deployments that do not retain the
archive. It contains the same public commit ID and no credentials.

No published GitHub Release or release tag is claimed by this policy. Until a
maintainer publishes an immutable release from an accepted commit, **HACS tracks
the moving default branch** for a repository without releases. Use the pinned
manual procedure for reproducible supported deployment. A future `v0.6.0`
release must point to one accepted commit and never be retargeted; its release
notes must link the final checks and upgrade/rollback evidence.

Home Assistant **2026.10.4** is the minimum and the tested/audited baseline.
Upgrade older HA installations first. That HA upgrade is a separate operation
with its own full backup; this component's rollback cannot downgrade HA itself.

## Prepare once

1. Take a full Home Assistant backup and confirm how to restore it. Record the
   existing package source/version, console entry, entity IDs, user names and
   important automations. Save a copy of the currently installed
   `custom_components/unifi_air_quality` outside the active component directory.
2. Select the full accepted commit from the fork's merged PR and verify its check
   and review results. Download that exact Git commit, not the branch ZIP. For
   example, on a separate machine, set `PINNED_COMMIT` to that full SHA and run:

   ```bash
   git clone --no-checkout https://github.com/Daniel-Dietz/ha-unifi-air-quality.git fork-staging
   git -C fork-staging fetch origin "$PINNED_COMMIT"
   git -C fork-staging checkout --detach "$PINNED_COMMIT"
   git -C fork-staging rev-parse HEAD
   printf '%s\n' "$PINNED_COMMIT" > fork-staging/custom_components/unifi_air_quality/BUILD_COMMIT
   ```

   Confirm `rev-parse HEAD` equals the recorded full SHA and the staged manifest
   says `0.6.0`. An equivalent immutable archive URL is
   `https://github.com/Daniel-Dietz/ha-unifi-air-quality/archive/FULL_COMMIT_SHA.tar.gz`;
   replace the placeholder with the same accepted 40-character SHA. Keep the
   original archive filename containing that SHA, or record it in `BUILD_COMMIT`
   after extraction. Keep the archive or staged package for recovery.
3. Plan a short restart window. Keep one component directory named exactly
   `unifi_air_quality`; the upstream and fork share that domain and cannot run
   side by side. Do not copy a second integration with a different domain name.

## Fresh installation

For a new installation, copy the complete staged `unifi_air_quality` folder into
`<config>/custom_components/`, then restart Home Assistant. Add **UniFi Protect
Air Quality** in **Settings → Devices & services**, using a dedicated local
Protect account and verified TLS. Check that readings agree with Protect, and
record the installed version and original archive filename (or `BUILD_COMMIT`).
Test controls only where you have write permission and can safely verify the
result; do not induce a real safety alarm.

## Upstream-to-fork or fork-to-fork upgrade

1. If HACS manages the upstream package, remove its downloaded repository/package
   from HACS before taking over manual management. **Keep the integration's
   existing config entry in Home Assistant.** Removing a HACS package and deleting
   the integration under Devices & services are different operations. Do not
   delete the latter. Keep the saved component backup even if HACS removes files.
2. Stop Home Assistant. Move the previous component folder out of
   `custom_components` and install the complete staged folder in its place. A
   complete replacement avoids leaving old module files behind. Leave the HA
   configuration and `.storage` files untouched; do not re-add the integration.
3. Start Home Assistant. The existing config entry loads the replacement code.
   Confirm its console, entity IDs, device/entity custom names, disabled entities,
   integration options and automations still match the recorded state. Check
   readings and availability after a normal refresh and inspect sanitized
   diagnostics if there is an error.
4. Keep the previous component backup and full HA backup until the new build has
   completed the intended observation period. Leave the upstream HACS package
   uninstalled while manually managing this domain, so an upstream update cannot
   overwrite the fork. Do not enable two package managers for the same directory.

If file access depends on a Home Assistant app, the
[README update procedure](../README.md#update) provides an alternative to stopping
Core: disable **all** entries for this integration, complete any requested
restart before replacing files, replace the complete component folder, restart
HA, then enable those same entries. Disabling preserves their configuration.
This also applies when restoring a saved component package.

Marketplace's **Delete and uninstall** removes the integration entries, devices
and entities. It is not equivalent to the HACS package-only removal described
above. Keep using Marketplace's update action for a Marketplace-managed copy.

When immutable fork releases become available, HACS can manage the fork custom
repository. Select a concrete release in HACS, record its SHA/version, and use
its documented download/restart flow. Moving-branch HACS downloads do not provide
the commit pin promised by the manual policy above.

## Rollback

Stop Home Assistant, replace the complete active component folder with the saved
previous package (including its build marker, if present), and start HA. Preserve
the config entry and `.storage`; do not delete/recreate entities. Confirm the
same identity/customization checklist and readings. Version 0.6.0 keeps config
entry schema version 1 and does not require a destructive data migration.

The disposable test below proves a return to upstream 0.5.4 preserves stored
identities; it does not make upstream 0.5.4 a supported security baseline for this
fork. Prefer a previously accepted fork commit for normal rollback. If the
problem also changed HA Core or other integrations, use the matching full HA
backup rather than assuming component files alone restore those changes.

## Reconfigure host, port or certificates

Use the integration entry's **Reconfigure** action to change the host, HTTPS port
or certificate verification. The flow reuses saved credentials, validates the
new endpoint, checks the returned console ID against the existing entry, then
updates and reloads that entry. It preserves its ID, options and registry names.
A certificate failure or a different console leaves the saved settings intact.
For certificate migration, install a runtime-trusted CA certificate with a
matching hostname first, then enable verification in Reconfigure. If credentials
also changed, complete reauthentication first. Do not recreate the config entry
to change an address or certificate choice.

## Reproducible disposable validation

Run `python dev/run_migration_smoke.py` in the documented test environment. It
exports upstream **0.5.4** at commit
`f4692adaffc2310974efd42a2b3856a156396a8a` and uses the current checkout for the
fork, asserting the loaded module path and manifest version. Each phase runs a separate HA 2026.10.4 process with exactly one package:

| Phase | Verified result |
| --- | --- |
| Fresh fork install | All 71 entities register on seven platforms |
| Upstream install | One console entry and customized entity/device registry stored |
| Upgrade to fork | Entry/console/entity IDs, integration/entity options, user names and disabled state unchanged |
| Rollback to upstream | The same stored identities and customizations remain unchanged |

The harness serializes the real HA config-entry, entity-registry and device-
registry stores between processes. It uses synthetic Protect data, and mocks
only the Protect client and test storage I/O. It does not contact hardware or
operate the HACS UI. HACS download/source selection is therefore documented but
not claimed as an automated UI test. A maintainer should additionally verify
source selection in a disposable HACS instance when publishing the first tagged
release. This distinction keeps the existing reproducible package/HA evidence
separate from any future HACS release test.

References: [HACS release behavior](https://www.hacs.xyz/docs/publish/integration/#github-releases-optional)
and [HA reconfiguration](https://contributors.home-assistant.io/core/integrations/reauth-reconfigure/#reconfiguration).
