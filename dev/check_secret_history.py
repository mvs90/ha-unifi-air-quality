"""Scan introduced Git blobs, including secrets deleted before the final tree.

Only a commit, detector name and line number leave this process. Blob contents,
file names, hashes of detected values and detector exception text stay private.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings

COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
MAX_TEXT_BYTES = 16 * 1024 * 1024


def git(repository: Path, *arguments: str) -> bytes:
    """Run explicit Git arguments without a shell or unredacted failure output."""
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("Git is required for the history scan")
    result = subprocess.run(  # noqa: S603 -- resolved executable and explicit argv.
        [executable, "-C", str(repository), *arguments],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("History scanner Git operation failed")
    return result.stdout


def checked_commit(repository: Path, value: str) -> str:
    """Validate a full SHA from event metadata and require a local commit."""
    if not COMMIT_PATTERN.fullmatch(value):
        raise ValueError("History scan requires full commit SHAs")
    resolved = git(repository, "rev-parse", "--verify", value + "^{commit}")
    return resolved.decode().strip()


def reviewed_findings(baseline: dict) -> set[tuple[str, str, str]]:
    """Retain only exact file/type/value hashes explicitly reviewed as harmless."""
    return {
        (filename, finding["type"], finding["hashed_secret"])
        for filename, findings in baseline.get("results", {}).items()
        for finding in findings
        if finding.get("is_secret") is False
    }


def scan_content(content: bytes, filename: str, sample: Path, *, inline: bool = False):
    """Scan text while ignoring only valid baseline hash metadata, not raw values."""
    if b"\0" in content:
        return []
    if len(content) > MAX_TEXT_BYTES:
        raise RuntimeError("History text blob exceeds scan budget")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    if filename == ".secrets.baseline":
        # SHA-1 metadata is not plaintext. Keep all other baseline text scannable.
        content = re.sub(
            rb'("hashed_secret"\s*:\s*)"[0-9a-f]{40}"', rb"\1null", content
        )
    sample.write_bytes(content)
    with default_settings() as settings:
        settings.disable_filters("detect_secrets.filters.common.is_baseline_file")
        if not inline:
            settings.disable_filters(
                "detect_secrets.filters.allowlist.is_line_allowlisted"
            )
        secrets = SecretsCollection()
        secrets.scan_file(str(sample))
        return [finding for items in secrets.data.values() for finding in items]


def trusted_inline_findings(
    repository: Path, commit: str, filename: str, sample: Path
) -> set[tuple[str, str, str]]:
    """Allow exact values already inline-reviewed in the trusted base file only."""
    entry = (
        git(repository, "ls-tree", "-z", commit, "--", ":(literal)" + filename)
        .partition(b"\t")[0]
        .split()
    )
    if len(entry) != 3 or entry[1] != b"blob":
        return set()
    content = git(repository, "cat-file", "blob", entry[2].decode())
    all_findings = scan_content(content, filename, sample)
    unapproved = {
        (finding.type, finding.secret_hash)
        for finding in scan_content(content, filename, sample, inline=True)
    }
    return {
        (filename, finding.type, finding.secret_hash)
        for finding in all_findings
        if (finding.type, finding.secret_hash) not in unapproved
    }


def scan_history(
    repository: Path,
    head: str,
    base: str | None,
    baseline: dict,
    trusted_commit: str | None = None,
) -> tuple[list[tuple[str, str, int]], int]:
    """Inspect every changed text blob without trusting newly added exclusions."""
    head = checked_commit(repository, head)
    revision = head
    if base and base != "0" * 40:
        base = checked_commit(repository, base)
        revision = base + ".." + head
    trusted_commit = checked_commit(repository, trusted_commit or base or head)
    commits = git(repository, "rev-list", "--reverse", revision).decode().splitlines()
    reviewed = reviewed_findings(baseline)
    # These exact SHA-1 strings are already public commit identifiers in the
    # trusted ancestry, not newly supplied PR values. Other detectors still run.
    public_references = {
        hashlib.sha1(commit, usedforsecurity=False).hexdigest()
        for commit in git(repository, "rev-list", trusted_commit).splitlines()
    }
    findings = []
    scanned = set()
    checked_paths = set()
    with TemporaryDirectory(prefix="protect-secret-scan-") as directory:
        for commit in commits:
            changed = git(
                repository,
                "diff-tree",
                "--root",
                "--no-commit-id",
                "-r",
                "-m",
                "--no-renames",
                "--diff-filter=ACMT",
                "--name-only",
                "-z",
                commit,
            ).split(b"\0")
            for encoded_path in changed:
                if not encoded_path:
                    continue
                filename = os.fsdecode(encoded_path)
                entry = (
                    git(
                        repository,
                        "ls-tree",
                        "-z",
                        commit,
                        "--",
                        ":(literal)" + filename,
                    )
                    .partition(b"\t")[0]
                    .split()
                )
                if len(entry) != 3 or entry[1] != b"blob":
                    continue
                blob = entry[2].decode()
                if (filename, blob) in scanned:
                    continue
                scanned.add((filename, blob))
                sample = Path(directory) / ("sample" + Path(filename).suffix)
                if filename not in checked_paths:
                    reviewed.update(
                        trusted_inline_findings(
                            repository, trusted_commit, filename, sample
                        )
                    )
                    checked_paths.add(filename)
                content = git(repository, "cat-file", "blob", blob)
                for finding in scan_content(content, filename, sample):
                    if (
                        finding.type == "Hex High Entropy String"
                        and finding.secret_hash in public_references
                    ):
                        continue
                    if (filename, finding.type, finding.secret_hash) not in reviewed:
                        findings.append(
                            (commit[:12], finding.type, finding.line_number)
                        )
    return findings, len(commits)


def self_test() -> None:
    """Catch deleted keys, PR-controlled pragmas and plaintext in baseline files."""
    for filename, pragma in (
        ("intermediate.py", ""),
        ("intermediate.py", " # pragma: allowlist secret"),
        (".secrets.baseline", ""),
    ):
        with TemporaryDirectory(prefix="protect-secret-history-test-") as directory:
            repository = Path(directory)
            git(repository, "init", "--quiet")
            git(repository, "config", "user.name", "Scanner self-test")
            git(repository, "config", "user.email", "self-test@example.invalid")
            git(repository, "commit", "--allow-empty", "--quiet", "-m", "clean base")
            base = git(repository, "rev-parse", "HEAD").decode().strip()
            sample = repository / filename
            # Deliberately invalid AWS shape, assembled solely for detector checks.
            sample.write_text('value = "' + "AKIA" + "A" * 16 + '"' + pragma + "\n")
            # Public trusted-base SHA references must not masquerade as secrets.
            sample.write_text(sample.read_text() + f'upstream = "{base}"\n')
            git(repository, "add", filename)
            git(repository, "commit", "--quiet", "-m", "synthetic intermediate")
            sample.unlink()
            git(repository, "add", "--update")
            git(repository, "commit", "--quiet", "-m", "clean final tree")
            head = git(repository, "rev-parse", "HEAD").decode().strip()
            findings, count = scan_history(repository, head, base, {})
            if (
                count != 2
                or len(findings) != 1
                or not any(item[1] == "AWS Access Key" for item in findings)
            ):
                raise RuntimeError("Intermediate-commit secret detection failed")
            if git(repository, "ls-tree", "-r", "--name-only", head).strip():
                raise RuntimeError("Self-test final checkout is not empty")
            clean, count = scan_history(repository, head, head, {})
            if clean or count:
                raise RuntimeError("Empty history range produced findings")
    print(
        "History self-test passed: deleted keys, new pragmas "
        "and baseline plaintext detected"
    )


def main() -> int:
    """Scan PR/push ranges or the complete ancestry of a release/periodic run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.self_test:
            self_test()
            return 0
        repository = Path.cwd()
        head = (
            os.environ.get("HISTORY_HEAD")
            or git(repository, "rev-parse", "HEAD").decode().strip()
        )
        base = os.environ.get("HISTORY_BASE") or None
        if head == "0" * 40:
            print("Deleted ref: no introduced commits to scan")
            return 0
        # A PR cannot approve a new secret by editing its own baseline.
        # Release/manual runs use the accepted commit's reviewed baseline.
        trusted = base if base and base != "0" * 40 else head
        if os.environ.get("GITHUB_EVENT_NAME") == "push" and (
            not base or base == "0" * 40
        ):
            # A new branch's PR-controlled baseline is not a trust anchor.
            trusted = (
                git(repository, "rev-parse", "refs/remotes/origin/main")
                .decode()
                .strip()
            )
        baseline_commit = checked_commit(repository, trusted)
        baseline = json.loads(
            git(repository, "show", baseline_commit + ":.secrets.baseline")
        )
        findings, count = scan_history(
            repository, head, base, baseline, baseline_commit
        )
        if findings:
            print("Secret-history findings (values and file names redacted):")
            for commit, detector, line in findings:
                print(f"commit {commit}: {detector}, line {line}")
            return 1
        print(f"Secret history passed: {count} commits scanned")
        return 0
    except Exception:
        # Scanner failures must fail closed without echoing untrusted Git/blob text.
        print("Secret history scan failed; inspect the selected Git range locally")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
