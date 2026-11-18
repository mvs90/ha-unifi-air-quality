"""Fail closed on untriaged advisories in the resolved HA/test environment."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path


def audit_findings(report: dict, exceptions: list[dict], today: date) -> list[str]:
    """Match individual advisories against package, version and expiry limits."""
    failures = []
    # Missing dependency data is a scanner/service failure, never a clean audit.
    if not isinstance(report.get("dependencies"), list) or not report["dependencies"]:
        return ["Dependency audit returned no package inventory"]
    for package in report["dependencies"]:
        for advisory in package.get("vulns", []):
            accepted = any(
                item["package"] == package["name"]
                and package["version"] in item["versions"]
                and advisory["id"] in item["advisories"]
                and date.fromisoformat(item["expires"]) >= today
                and item["owner"]
                and item["reason"]
                for item in exceptions
            )
            if not accepted:
                # Print identifiers, never package response bodies or local paths.
                failures.append(
                    f"{package['name']} {package['version']}: {advisory['id']}"
                )
    return failures


def main() -> int:
    """Validate an audit report using the reviewed repository exception policy."""
    report = json.loads(Path(sys.argv[1]).read_text())
    exceptions = json.loads(Path("security-advisories.json").read_text())
    failures = audit_findings(report, exceptions, date.today())
    if failures:
        print("Untriaged or expired dependency findings:\n" + "\n".join(failures))
        return 1
    print("Dependency audit passed; all upstream exceptions remain scoped and current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
