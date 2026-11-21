"""Ensure dependency exceptions cannot hide new or expired vulnerabilities."""

from datetime import date

from dev.check_dependency_audit import audit_findings


def test_scoped_dependency_exceptions_and_expiry():
    """An exception matches only its exact advisory, package and version."""
    report = {
        "dependencies": [
            {"name": "pillow", "version": "12.3.0", "vulns": [{"id": "advisory"}]}
        ]
    }
    exception = {
        "package": "pillow",
        "versions": ["12.3.0"],
        "advisories": ["advisory"],
        "expires": "2026-12-18",
        "owner": "maintainer",
        "reason": "HA pin; no image decoding in this integration",
    }
    assert audit_findings(report, [exception], date(2026, 12, 18)) == []
    assert audit_findings(report, [exception], date(2026, 12, 19))
    for changed in (
        {"package": "other"},
        {"versions": ["12.2.0"]},
        {"advisories": ["other"]},
        {"reason": ""},
        {"owner": ""},
    ):
        assert audit_findings(report, [{**exception, **changed}], date(2026, 11, 18))


def test_audit_service_failure_is_not_clean():
    """A missing or empty scanner inventory is a failing gate."""
    assert audit_findings({}, [], date(2026, 11, 18))
    assert audit_findings({"dependencies": []}, [], date(2026, 11, 18))


def test_incomplete_and_skipped_results_fail_closed():
    """Only the own editable distribution may be skipped alongside real results."""
    today = date(2026, 11, 18)
    for package in (
        {"name": "x", "version": "1"},
        {"name": "x", "skip_reason": "service failure"},
        {
            "name": "ha-unifi-air-quality",
            "skip_reason": "distribution marked as editable",
        },
        {"name": "x", "version": "1", "vulns": [None]},
        None,
    ):
        assert audit_findings({"dependencies": [package]}, [], today)
    assert (
        audit_findings(
            {
                "dependencies": [
                    {
                        "name": "ha-unifi-air-quality",
                        "skip_reason": "distribution marked as editable",
                    },
                    {"name": "homeassistant", "version": "2026.10.4", "vulns": []},
                ]
            },
            [],
            today,
        )
        == []
    )
