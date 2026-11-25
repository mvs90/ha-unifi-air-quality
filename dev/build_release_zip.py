"""Build the HACS and Marketplace release asset from a committed Git tree."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = "custom_components/unifi_air_quality"
ASSET = "unifi_air_quality.zip"


def main() -> None:
    """Archive only integration files, with manifest.json at the ZIP root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref", help="Reviewed commit or release tag")
    parser.add_argument("output", type=Path, help="Output directory")
    args = parser.parse_args()
    git = shutil.which("git")
    if git is None:
        parser.error("Git is required")
    commit = subprocess.check_output(  # noqa: S603 -- fixed Git executable and argv.
        [git, "rev-parse", "--verify", "--end-of-options", f"{args.ref}^{{commit}}"],
        cwd=ROOT,
        text=True,
    ).strip()
    payload = subprocess.check_output(  # noqa: S603 -- verified commit and fixed path.
        [git, "archive", "--format=zip", f"{commit}:{COMPONENT}"],
        cwd=ROOT,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / ASSET
    with destination.open("xb") as file:
        file.write(payload)
    with ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["domain"] != "unifi_air_quality" or archive.testzip() is not None:
            raise ValueError("Invalid integration archive")
    print(f"{destination.name}: {manifest['version']} from {commit}")


if __name__ == "__main__":
    main()
