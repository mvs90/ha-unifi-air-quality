"""Exercise fresh fork install, upstream upgrade and rollback in isolated HA.

Run from an existing checkout in the documented test environment. ``--upstream``
selects a locally available Git ref for a compatible upstream 0.5.4 package;
it defaults to the pinned UPSTREAM_COMMIT. The worker checks the expected
0.5.4/0.6.1 manifests, so this is not a migration test for arbitrary versions.

All Protect data is synthetic. Four phases start separate Python processes with
exactly one component directory. The fresh-fork phase has its own HA storage;
upstream install, fork upgrade and rollback share serialized config/device/
entity storage. The checkout, real HA configurations and consoles are untouched.
The command exits 0 only when all phases pass; failed subprocesses raise errors.
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
from zipfile import ZipFile

# Public immutable upstream commit, verified against trusted Git ancestry.
UPSTREAM_COMMIT = "f4692adaffc2310974efd42a2b3856a156396a8a"  # pragma: allowlist secret
COMPONENT_DIRECTORY = "custom_components/unifi_air_quality"


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


def install_release_zip(path: Path, source: Path, target: Path) -> None:
    """Check the ZIP against the checkout and unpack only expected files."""
    expected = {
        file.relative_to(source).as_posix(): file
        for file in source.rglob("*")
        if file.is_file() and "__pycache__" not in file.parts
    }
    with ZipFile(path) as package:
        files = [item.filename for item in package.infolist() if not item.is_dir()]
        if sorted(files) != sorted(expected):
            raise ValueError("Release ZIP does not match the component file list")
        for name, source_file in expected.items():
            data = package.read(name)
            if data != source_file.read_bytes():
                raise ValueError("Release ZIP does not match the component contents")
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)


def main() -> int:
    """Run four isolated package phases and compare their persisted identities."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default=UPSTREAM_COMMIT)
    parser.add_argument(
        "--release-zip",
        type=Path,
        help="Install the locally built release ZIP instead of the component checkout",
    )
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
        [executable, "archive", upstream, COMPONENT_DIRECTORY],
        directory=repository,
    )
    with TemporaryDirectory(prefix="protect-migration-") as temporary:
        directory = Path(temporary)
        component = directory / COMPONENT_DIRECTORY
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
            elif args.release_zip is not None:
                install_release_zip(
                    args.release_zip, repository / COMPONENT_DIRECTORY, component
                )
            else:
                shutil.copytree(
                    repository / COMPONENT_DIRECTORY, component
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
