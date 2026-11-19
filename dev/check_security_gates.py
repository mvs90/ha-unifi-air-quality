"""Check that Ruff and detect-secrets detect synthetic violations.

Run ``python dev/check_security_gates.py`` in an environment containing both
tools. The temporary Python sample is scanned, never executed. Exit 0 confirms
both detectors fired; missing detection raises an error and fails the command.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings


def main() -> int:
    """Require Ruff S307 and a synthetic AWS-shaped secret to trigger detection."""
    with TemporaryDirectory() as directory:
        sample = Path(directory) / "violation.py"
        # S307 must reject dynamic execution even if no real payload is executed.
        sample.write_text('eval(input("synthetic"))\n')
        result = subprocess.run(  # noqa: S603 -- fixed interpreter/module/arguments.
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--isolated",
                "--select",
                "S",
                str(sample),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 1 or "S307" not in result.stdout:
            raise RuntimeError("Static security detection self-test failed")
        # Assemble a synthetic AWS-shaped key solely to verify detector wiring.
        sample.write_text('key = "' + "AKIA" + "A" * 16 + '"\n')
        with default_settings():
            secrets = SecretsCollection()
            secrets.scan_file(str(sample))
        if not secrets.data:
            raise RuntimeError("Secret detection self-test failed")
    print("Static security and secret detection self-tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
