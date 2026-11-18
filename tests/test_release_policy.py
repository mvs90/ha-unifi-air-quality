"""Keep advertised versions and required validation gates aligned."""

import json
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_supported_ha_baseline_matches_tests_and_audit():
    """Every advertised HA baseline is the exact resolved Security test extra."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    baseline = json.loads((ROOT / "hacs.json").read_text())["homeassistant"]
    workflow = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())
    matrix = workflow["jobs"]["tests"]["strategy"]["matrix"]["include"]
    assert [row["homeassistant"] for row in matrix] == [baseline]
    assert (
        f"homeassistant=={baseline}"
        in project["project"]["optional-dependencies"]["test"]
    )
    compose = yaml.safe_load((ROOT / "dev/compose.yaml").read_text())
    assert compose["services"]["homeassistant"]["image"].endswith(":" + baseline)


def test_fork_versions_are_consistent():
    """Manifest, Python distribution and changelog identify one fork version."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    manifest = json.loads(
        (ROOT / "custom_components/unifi_air_quality/manifest.json").read_text()
    )
    version = manifest["version"]
    assert project["project"]["version"] == version == "0.6.0"
    assert f"## {version} " in (ROOT / "CHANGELOG.md").read_text()


def test_ruleset_requires_actual_unique_job_contexts():
    """An importable no-bypass ruleset references the exact emitted check names."""
    ruleset = json.loads((ROOT / ".github/rulesets/main.json").read_text())
    assert ruleset["bypass_actors"] == []
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/main"]
    rules = {rule["type"]: rule.get("parameters", {}) for rule in ruleset["rules"]}
    assert "deletion" in rules and "non_fast_forward" in rules
    assert rules["pull_request"]["required_review_thread_resolution"]
    expected = {"Home Assistant 2026.10.4"}
    for name, job in (
        ("security", "scan"),
        ("hacs", "validate"),
        ("hassfest", "validate"),
    ):
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        assert workflow["permissions"] == {"contents": "read"}
        expected.add(workflow["jobs"][job]["name"])
    checks = rules["required_status_checks"]
    assert checks["strict_required_status_checks_policy"]
    assert {check["context"] for check in checks["required_status_checks"]} == expected
    assert {check["integration_id"] for check in checks["required_status_checks"]} == {
        15368
    }
    assert len(expected) == 4
