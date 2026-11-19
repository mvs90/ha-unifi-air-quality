# Contributing

For installation, configuration and troubleshooting, use the [README](README.md).
This guide is for changes to the repository. The [documentation index](docs/README.md)
links the user, maintenance and historical guides.

## Reports and changes

Bug reports, reviewed minimized diagnostics, tests, and pull requests are
welcome. Never post credentials, cookies, tokens, public IP addresses, MAC
addresses, serial numbers, unredacted device IDs, or household/device names.
Follow [SECURITY.md](SECURITY.md) for private vulnerability reporting.

Describe the user-visible problem, the changed behavior and any compatibility
limits. Changes to the private protocol adapter should include a sanitized
fixture and focused tests. Preserve existing entity unique IDs and event types.
Do not infer missing protocol fields from one device or firmware version.

## Local test environment

Use Python **3.14.2 or later in the 3.14 series** and a full Git clone. The
declared test extra installs Home Assistant **2026.10.4** and its matching test
helpers. Run these commands from the repository root in a POSIX shell:

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m ruff check .
python -m pytest
python dev/run_migration_smoke.py
```

The test settings in [pyproject.toml](pyproject.toml) enable branch coverage and
require at least 98% coverage for the full suite. The migration runner also needs
the recorded upstream commit in the local Git history; see the
[migration guide](docs/install-upgrade-rollback.md#reproducible-disposable-validation).
Run tests in this disposable environment, without live Home Assistant storage
or Protect credentials.

For a focused investigation, select the relevant test file. The coverage gate
applies to the complete integration, so a deliberately partial run can use
`python -m pytest tests/test_api.py --no-cov`; it does not replace the full suite.
Formatting changes should be confined to the files and lines being changed.

An alternative exercises the installed packages in a disposable HA container:

```bash
docker run --rm -v "$PWD:/work:ro" --entrypoint python3 \
  ghcr.io/home-assistant/home-assistant:2026.10.4 \
  /work/dev/run_container_tests.py
```

This command runs the pytest suite. It does not run the separate migration,
security or packaging jobs. The runner installs test tools in a temporary
directory and requires access to their package sources. It keeps the image's
runtime packages first on the import path. Do not mount a live `/config` folder.

## Repository and module map

Only `custom_components/unifi_air_quality/` is installed into Home Assistant.
Within that directory:

| File or group | Responsibility |
| --- | --- |
| `__init__.py` | Entry setup/unload, session ownership, platform loading, device registration and explicit removal checks. |
| `config_flow.py` | Initial connection, password reauthentication and same-console host/port/TLS reconfiguration. |
| `api.py` | Home Assistant-independent private Protect transport, validation, authentication, cache reconciliation, freshness and verified writes. |
| `websocket.py` | Bounded binary-frame parsing; it does not own the network connection. |
| `coordinator.py` | Publish snapshots to HA, request reauthentication and schedule refresh/freshness checks. |
| `models.py`, `const.py` | Shared snapshot/alarm models, metric aliases and timing/connection defaults. |
| `entity.py` | Dynamic discovery, stable entity identities, common availability and settings-write helpers. |
| `sensor.py`, `binary_sensor.py`, `event.py` | Numeric readings, current alarm states/pictures and incoming alarm events. |
| `number.py`, `select.py`, `switch.py`, `time.py` | Supported device settings and their Protect write semantics. |
| `diagnostics.py` | Allowlisted exports, per-export aliases and relative freshness/health information. |
| `strings.json`, `translations/` | Form text, errors, entity names and translated options. |
| `manifest.json`, `brand/` | Integration metadata and locally served brand images. |

At the repository root, `hacs.json` declares the package name and minimum HA
version; `pyproject.toml` holds the Python/test/lint configuration. `tests/` holds
synthetic fixtures and regressions. `dev/` contains disposable maintenance tools.
CI workflows are under `.github/workflows/`, and the importable branch ruleset
is under `.github/rulesets/`. The original artwork is under `assets/branding/`.

Keep Home Assistant-specific logic outside `api.py` and `websocket.py` so that
the private transport can be replaced independently. Do not place UI setup or
registry operations in the adapter.

## Documentation and compatibility

Every Python function, including private helpers, callbacks, fixtures and tests,
must have a meaningful docstring header. Describe what it does or returns;
document ownership, side effects, important failure cases and concurrency where
they are not obvious. Explain non-obvious validation and state transitions with
inline comments. `tests/test_documentation.py` checks that headers exist; their
accuracy still requires review.

Update the user guide when configuration, controls, availability or installation
changes. Update the relevant detailed guide instead of adding development
instructions to the user README. Keep historical results explicitly dated and
separate from current compatibility claims. Check relative links and heading
anchors after moving sections. Documentation-only changes must not alter
executable statements, default values, translations or dependency/workflow pins.

For functional changes, add regression coverage for changed bootstrap, push and
write semantics. Changes to `strings.json` must keep all translation catalogs'
keys and placeholders consistent. Changes to entity identities, config-entry
schema or packaging need explicit upgrade/rollback consideration. Consult the
[device lifecycle guide](docs/device-lifecycle.md) before altering cleanup.

## Maintenance tools and checks

Run tools from the repository root. The Security workflow installs its scanner
tools in a separate environment; the test extra alone does not install them.
Use the pinned versions and exact invocation in
[security.yml](.github/workflows/security.yml) when reproducing those checks.

| Tool | Input and purpose |
| --- | --- |
| `dev/run_container_tests.py` | Runs pytest inside a disposable HA image; `HA_TEST_HELPERS` can select a compatible helper package. |
| `dev/run_migration_smoke.py` | Runs fresh install, upstream install, fork upgrade and upstream rollback in separate processes using serialized test storage. |
| `dev/check_compose_binding.py /path/to/compose.json` | Checks `docker compose -f dev/compose.yaml config --format json` output for the single loopback-only listener; starts no containers. |
| `dev/check_dependency_audit.py /path/to/pip-audit.json` | Checks a resolved inventory against `security-advisories.json`, exception expiry and the declared HA baseline. |
| `dev/check_secret_history.py` | Scans committed history; `HISTORY_BASE` and `HISTORY_HEAD` select full SHA bounds, and `--self-test` exercises detection in a temporary repository. |
| `dev/check_security_gates.py` | Proves that Ruff security rules and the secret detector reject synthetic samples. |

The history scanner does not inspect uncommitted changes. CI also scans the
tracked working tree. The tools print redacted findings; do not add sensitive
payloads to their logs. The security policy explains reviewed exceptions and
trusted-baseline handling.

The optional [development Compose file](dev/compose.yaml) exposes HA only at
`127.0.0.1:8123`, mounts `dev/config` as its disposable configuration, and mounts
the integration read-only. Start it with `docker compose -f dev/compose.yaml up -d`
and stop it with `docker compose -f dev/compose.yaml down`. Do not use production
configuration or publish that listener to the network.

HACS and Hassfest validate packaging in CI. The required job names, final-head
review process and live-rule verification are documented in the
[maintainer ruleset guide](docs/maintainer-ruleset.md). Security policy and
exception changes require explicit review; do not regenerate a secret baseline
or loosen a check merely to make CI pass.
