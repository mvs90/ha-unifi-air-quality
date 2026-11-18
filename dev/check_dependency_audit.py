"""Fail closed on untriaged advisories in the resolved HA/test environment."""

from __future__ import annotations

import hashlib
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
    audited = 0
    for package in report["dependencies"]:
        if not isinstance(package, dict):
            failures.append("Malformed dependency entry")
            continue
        if package.get("skip_reason"):
            # The repository itself is editable and has no distribution advisory feed.
            if (
                package.get("name") == "ha-unifi-air-quality"
                and package["skip_reason"] == "distribution marked as editable"
            ):
                continue
            failures.append("Unaudited dependency: " + str(package.get("name")))
            continue
        if (
            not isinstance(package.get("name"), str)
            or not isinstance(package.get("version"), str)
            or not isinstance(package.get("vulns"), list)
        ):
            failures.append("Incomplete dependency audit entry")
            continue
        audited += 1
        for advisory in package["vulns"]:
            if not isinstance(advisory, dict) or not isinstance(
                advisory.get("id"), str
            ):
                failures.append("Malformed advisory entry")
                continue
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
    if not audited:
        failures.append("No dependencies were successfully audited")
    return failures


def main() -> int:
    """Validate an audit report using the reviewed repository exception policy."""
    report = json.loads(Path(sys.argv[1]).read_text())
    exceptions = json.loads(Path("security-advisories.json").read_text())
    failures = audit_findings(report, exceptions, date.today())
    if failures:
        print("Untriaged or expired dependency findings:\n" + "\n".join(failures))
        return 1
    inventory = sorted(
        (package["name"], package["version"])
        for package in report["dependencies"]
        if not package.get("skip_reason")
    )
    supported_ha = json.loads(Path("hacs.json").read_text())["homeassistant"]
    if ("homeassistant", supported_ha) not in inventory:
        print("Dependency inventory does not cover the declared HA baseline")
        return 1
    digest = hashlib.sha256(json.dumps(inventory).encode()).hexdigest()
    print(
        f"Dependency audit passed: HA {supported_ha}, {len(inventory)} packages; "
        f"inventory SHA-256 {digest}; scoped exceptions current"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
