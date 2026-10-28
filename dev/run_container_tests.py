"""Run the full suite in a disposable Home Assistant image, not a live instance.

Example:
    docker run --rm -v "$PWD:/work:ro" --entrypoint python3 \
        ghcr.io/home-assistant/home-assistant:2026.10.4 \
        /work/dev/run_container_tests.py

The published test helpers target stable HA. Only their test dependencies are
added; the image's Home Assistant and existing runtime packages keep priority.
No live configuration or credentials should be mounted in this container.
"""

from __future__ import annotations

import importlib.metadata as metadata
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

STABLE_TEST_HELPERS = "pytest-homeassistant-custom-component==0.13.375"
PREVIEW_TEST_HELPERS = (
    "pytest-home-assistant @ https://github.com/home-assistant/core/archive/"
    "4be5ba96f41e0f4052899e0f6e953c5ce2143e3c.tar.gz"
    "#subdirectory=packages/pytest-home-assistant"
)


def main() -> int:
    """Install isolated test tools and exercise the unchanged HA runtime."""
    version = metadata.version("homeassistant")
    helpers = os.environ.get(
        "HA_TEST_HELPERS",
        PREVIEW_TEST_HELPERS if ".dev" in version else STABLE_TEST_HELPERS,
    )
    helper_name = canonicalize_name(Requirement(helpers).name)
    print(f"Testing installed Home Assistant {version}", flush=True)
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="air-quality-tests-") as temporary:
        target = Path(temporary) / "dependencies"
        install = ["uv", "pip", "install", "--target", str(target)]
        install_environment = os.environ.copy()
        for setting in (
            "UV_INDEX",
            "UV_INDEX_URL",
            "UV_EXTRA_INDEX_URL",
            "PIP_INDEX_URL",
            "PIP_EXTRA_INDEX_URL",
        ):
            install_environment.pop(setting, None)
        install_environment["UV_NO_CONFIG"] = "1"
        install_environment["UV_DEFAULT_INDEX"] = "https://pypi.org/simple"
        subprocess.run(
            [*install, "--no-deps", helpers],
            check=True,
            env=install_environment,
        )
        distribution = next(
            item
            for item in metadata.distributions(path=[str(target)])
            if canonicalize_name(item.metadata["Name"]) == helper_name
        )
        requirements = []
        for item in distribution.requires or []:
            requirement = Requirement(item)
            if requirement.name.lower() == "homeassistant":
                continue
            try:
                metadata.version(requirement.name)
            except metadata.PackageNotFoundError:
                requirements.append(item)
        if helper_name == "pytest-home-assistant":
            requirements.append("pytest-cov==7.1.0")
        subprocess.run([*install, *requirements], check=True, env=install_environment)
        # Append rather than prepend: never replace the image's HA dependencies
        # with the stable test helper's pinned versions on a preview image.
        sys.path.append(str(target))
        sys.path.insert(0, str(repository))
        os.environ["COVERAGE_FILE"] = str(Path(temporary) / ".coverage")
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.chdir(repository)
        assert metadata.version("homeassistant") == version

        # Preview HA must establish its httpx2 compatibility aliases before
        # third-party pytest plugins import httpx.
        __import__("homeassistant")

        import pytest  # noqa: PLC0415

        return pytest.main(
            [
                "--tb=short",
                "--show-capture=no",
                "-p",
                "no:respx",
                "-o",
                f"cache_dir={temporary}/cache",
                f"--basetemp={temporary}/pytest",
            ]
        )


if __name__ == "__main__":
    raise SystemExit(main())
