"""Exercise fresh fork install, upstream upgrade and rollback in isolated HA.

All Protect data is synthetic. Each phase starts a separate Python process with
exactly one component directory and serialized HA config/device/entity storage.
The parent checkout, real HA configurations and Protect consoles are untouched.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

# Public immutable upstream commit, verified against trusted Git ancestry.
UPSTREAM_COMMIT = "f4692adaffc2310974efd42a2b3856a156396a8a"  # pragma: allowlist secret


def run(
    arguments: list[str], *, directory: Path, environment: dict | None = None
) -> bytes:
    """Execute fixed tool arguments and surface only the disposable test output."""
    result = subprocess.run(  # noqa: S603 -- explicit argv, isolated test workspace.
        arguments,
        cwd=directory,
        env=environment,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        print(result.stdout.decode(errors="replace"))
        print(result.stderr.decode(errors="replace"))
        raise RuntimeError("Disposable migration phase failed")
    return result.stdout


def main() -> int:
    """Swap package files between three fresh processes and compare saved identities."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default=UPSTREAM_COMMIT)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("Git is required for the upstream package export")
    upstream = (
        run(
            [executable, "rev-parse", "--verify", args.upstream + "^{commit}"],
            directory=repository,
        )
        .decode()
        .strip()
    )
    archive = run(
        [executable, "archive", upstream, "custom_components/unifi_air_quality"],
        directory=repository,
    )
    with TemporaryDirectory(prefix="protect-migration-") as temporary:
        directory = Path(temporary)
        component = directory / "custom_components/unifi_air_quality"
        shutil.copyfile(
            repository / "tests/_migration_worker.py", directory / "test_migration.py"
        )
        shutil.copyfile(
            repository / "tests/fixtures/bootstrap_nox.json", directory / "fixture.json"
        )
        shutil.copyfile(
            repository / "tests/test_config_flow.py", directory / "_test_config_flow.py"
        )
        shutil.copyfile(
            repository / "tests/ha_test_helpers.py", directory / "ha_test_helpers.py"
        )
        fixture_module = directory / "_test_config_flow.py"
        fixture_module.write_text(
            fixture_module.read_text().replace(
                "from .ha_test_helpers", "from ha_test_helpers"
            )
        )
        environment = {**os.environ, "PYTHONPATH": str(directory)}
        for phase in (
            "fresh_fork",
            "upstream_install",
            "fork_upgrade",
            "upstream_rollback",
        ):
            if component.exists():
                shutil.rmtree(component)
            if phase.startswith("upstream"):
                with tarfile.open(fileobj=io.BytesIO(archive)) as package:
                    package.extractall(directory, filter="data")
            else:
                shutil.copytree(
                    repository / "custom_components/unifi_air_quality", component
                )
            state_name = (
                "fresh_state.json" if phase == "fresh_fork" else "migration_state.json"
            )
            environment.update(
                MIGRATION_PHASE=phase, MIGRATION_STATE=str(directory / state_name)
            )
            output = run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "test_migration.py",
                    "-o",
                    "asyncio_mode=auto",
                    "-o",
                    "addopts=",
                    "--confcutdir",
                    str(directory),
                ],
                directory=directory,
                environment=environment,
            ).decode()
            for line in output.splitlines():
                if phase + ":" in line or "passed" in line:
                    print(line)
    print(f"Migration smoke passed; upstream commit {upstream}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
