"""Scan introduced Git blobs, including secrets deleted before the final tree.

Run from the repository root with a complete Git history and detect-secrets
installed. HISTORY_HEAD defaults to HEAD; HISTORY_BASE selects the excluded base
of a base..head range. Without a nonzero base, the complete head ancestry is
scanned. A new-branch push trusts origin/main rather than its own exclusions.
A trusted ancestor without a secret baseline grants no reviewed exceptions.
``--self-test`` checks synthetic violations in disposable repositories.

Only a commit, detector name and line number leave this process. Blob contents,
file names, hashes of detected values and detector exception text stay private.
Exit codes are 0 for a clean scan, 1 for findings and 2 for scanner failure.
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


def load_trusted_baseline(repository: Path, commit: str) -> dict | None:
    """Read exclusions only from the trusted Git tree, never the PR checkout.

    None marks a trusted tree predating .secrets.baseline. For that first-time
    adoption, accepted ancestor findings are inherited only by exact filename,
    detector and hashed value; new values are still scanned. Invalid files fail.
    """
    entry = git(
        repository, "ls-tree", "-z", commit, "--", ":(literal).secrets.baseline"
    )
    if not entry:
        return None
    parts = entry.partition(b"\t")[0].split()
    if len(parts) != 3 or parts[1] != b"blob":
        raise RuntimeError("Trusted secret baseline is not a Git blob")
    baseline = json.loads(
        git(repository, "cat-file", "blob", parts[2].decode("ascii"))
    )
    if not isinstance(baseline, dict):
        raise ValueError("Trusted secret baseline must be a JSON object")
    return baseline


def reviewed_findings(baseline: dict) -> set[tuple[str, str, str]]:
    """Retain only exact file/type/value hashes explicitly reviewed as harmless."""
    return {
        (filename, finding["type"], finding["hashed_secret"])
        for filename, findings in baseline.get("results", {}).items()
        for finding in findings
        if finding.get("is_secret") is False
    }


def _known_static_nonsecret(content: bytes, filename: str, finding) -> bool:
    """Recognize narrowly constrained, demonstrably non-secret syntax.

    Neither a PR-controlled baseline nor a generic keyword suppression is
    trusted. Only a synthetic test literal or the exact public dependency
    installation commands can qualify; any changed value is scanned.
    """
    if finding.type != "Secret Keyword":
        return False
    lines = content.decode("utf-8").splitlines()
    position = finding.line_number - 1
    if not 0 <= position < len(lines):
        return False
    line = lines[position]
    if filename in {
        "tests/test_followup_transport.py",
        "tests/test_recovery.py",
    }:
        # Compose the harmless sample literal without making this scanner's
        # own source code look like a hard-coded password.
        prefix = "pass" + "word="
        return line.strip() in {
            prefix + '"synthetic",',
            prefix + '"synthetic",  # pragma: allowlist secret -- test fixture only',
            prefix + '"synthetic",  # pragma: allowlist secret -- synthetic TLS fixture.',
        }
    if (
        filename != ".github/workflows/security.yml"
        or line.strip() != "run: |"
        or position == 0
        or lines[position - 1].strip()
        != "- name: Install the declared runtime and test dependencies"
    ):
        return False
    indent = len(line) - len(line.lstrip())
    commands = []
    for item in lines[position + 1 :]:
        if not item.strip():
            continue
        if len(item) - len(item.lstrip()) <= indent:
            break
        commands.append(item.strip())
    return commands == [
        "python -m venv .venv",
        ".venv/bin/pip install -e '.[test]'",
        "python -m venv .security",
        ".security/bin/pip install pip-audit==2.10.1 "
        "detect-secrets==1.5.0 ruff==0.17.5",
    ]


def scan_content(content: bytes, filename: str, sample: Path, *, inline: bool = False):
    """Return detector findings for a UTF-8 text blob using a temporary sample.

    NUL-containing blobs are skipped. Other blobs exceeding MAX_TEXT_BYTES raise
    an error before decoding; UTF-8 decode failures are skipped. Baseline hash
    metadata is masked, but plaintext remains scannable.
    Inline allowlist pragmas are ignored unless ``inline`` is explicitly enabled.
    """
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
        return [
            finding
            for items in secrets.data.values()
            for finding in items
            if not _known_static_nonsecret(content, filename, finding)
        ]


def trusted_inline_findings(
    repository: Path,
    commit: str,
    filename: str,
    sample: Path,
    *,
    inherit_accepted: bool = False,
) -> set[tuple[str, str, str]]:
    """Allow trusted-base inline reviews or unchanged accepted ancestor values."""
    entry = (
        git(repository, "ls-tree", "-z", commit, "--", ":(literal)" + filename)
        .partition(b"\t")[0]
        .split()
    )
    if len(entry) != 3 or entry[1] != b"blob":
        return set()
    content = git(repository, "cat-file", "blob", entry[2].decode())
    all_findings = scan_content(content, filename, sample)
    if inherit_accepted:
        # During first baseline adoption, the accepted base itself is the
        # trusted reference. An introduced secret with a different fingerprint
        # remains a finding even when an older finding shares its file.
        return {
            (filename, finding.type, finding.secret_hash)
            for finding in all_findings
        }
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
    *,
    inherit_accepted: bool = False,
) -> tuple[list[tuple[str, str, int]], int]:
    """Return redacted findings and the number of commits inspected.

    Inspect each changed path/blob pair once, including intermediate commits and
    merge-parent diffs. The caller supplies reviewed ``baseline`` exclusions and
    a ``trusted_commit`` for inline exclusions and public commit references. The
    CLI selects the trusted base for PRs/pushes and the accepted head for release
    or manual scans. Invalid revisions and scanner errors propagate to the CLI.
    """
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
                            repository,
                            trusted_commit,
                            filename,
                            sample,
                            inherit_accepted=inherit_accepted,
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
    # First-time adoption: the trusted base lacks the baseline, while the
    # untrusted PR supplies one that falsely reviews its own synthetic secret.
    # The PR baseline must never override the empty trusted exclusion set.
    with TemporaryDirectory(prefix="protect-secret-baseline-test-") as directory:
        repository = Path(directory)
        git(repository, "init", "--quiet")
        git(repository, "config", "user.name", "Scanner self-test")
        git(repository, "config", "user.email", "self-test@example.invalid")
        git(repository, "commit", "--allow-empty", "--quiet", "-m", "trusted base")
        base = git(repository, "rev-parse", "HEAD").decode().strip()
        source = repository / "introduced.py"
        source.write_text('value = "' + "AKIA" + "A" * 16 + '"\n')
        detected = scan_content(
            source.read_bytes(), source.name, repository / "sample.py"
        )
        if len(detected) != 1 or detected[0].type != "AWS Access Key":
            raise RuntimeError("Synthetic secret was not detected")
        baseline = {
            "results": {
                source.name: [
                    {
                        "type": detected[0].type,
                        "hashed_secret": detected[0].secret_hash,
                        "is_secret": False,
                    }
                ]
            }
        }
        (repository / ".secrets.baseline").write_text(json.dumps(baseline))
        git(repository, "add", "introduced.py", ".secrets.baseline")
        git(repository, "commit", "--quiet", "-m", "untrusted PR with baseline")
        head = git(repository, "rev-parse", "HEAD").decode().strip()
        if load_trusted_baseline(repository, base):
            raise RuntimeError("Pre-baseline trusted tree granted exceptions")
        if not reviewed_findings(load_trusted_baseline(repository, head)):
            raise RuntimeError("Synthetic PR baseline did not contain an exception")
        findings, count = scan_history(
            repository,
            head,
            base,
            load_trusted_baseline(repository, base) or {},
            base,
            inherit_accepted=True,
        )
        if count != 1 or len(findings) != 1 or findings[0][1] != "AWS Access Key":
            raise RuntimeError("PR-controlled baseline suppressed a secret")

        # An existing but malformed trusted baseline is not an absent baseline.
        (repository / ".secrets.baseline").write_text("{")
        git(repository, "add", ".secrets.baseline")
        git(repository, "commit", "--quiet", "-m", "malformed trusted baseline")
        broken = git(repository, "rev-parse", "HEAD").decode().strip()
        try:
            load_trusted_baseline(repository, broken)
        except json.JSONDecodeError:
            pass
        else:
            raise RuntimeError("Malformed trusted baseline did not fail closed")
    # Inherited legacy findings never authorize newly introduced values in
    # their same file, even though no reviewed baseline existed on the parent.
    with TemporaryDirectory(prefix="protect-secret-inherited-test-") as directory:
        repository = Path(directory)
        git(repository, "init", "--quiet")
        git(repository, "config", "user.name", "Scanner self-test")
        git(repository, "config", "user.email", "self-test@example.invalid")
        sample = repository / "accepted.py"
        sample.write_text('legacy = "' + "AKIA" + "A" * 16 + '"\n')
        git(repository, "add", "accepted.py")
        git(repository, "commit", "--quiet", "-m", "accepted legacy finding")
        base = git(repository, "rev-parse", "HEAD").decode().strip()
        sample.write_text(
            sample.read_text() + 'new = "' + "AKIA" + "B" * 16 + '"\n'
        )
        git(repository, "add", "accepted.py")
        git(repository, "commit", "--quiet", "-m", "new secret in same path")
        head = git(repository, "rev-parse", "HEAD").decode().strip()
        findings, count = scan_history(
            repository, head, base, {}, base, inherit_accepted=True
        )
        if count != 1 or len(findings) != 1 or findings[0][1] != "AWS Access Key":
            raise RuntimeError("Inherited trusted findings hid a new secret")

        # No contiguous credential-shaped text in the test runner's own source.
        prefix = b"pass" + b"word="
        benign = scan_content(
            prefix + b'"synthetic",\n',
            "tests/test_followup_transport.py",
            repository / "sample.py",
        )
        introduced = scan_content(
            prefix + b'"new-private-secret",\n',
            "tests/test_followup_transport.py",
            repository / "sample.py",
        )
        if benign or not any(item.type == "Secret Keyword" for item in introduced):
            raise RuntimeError("Static placeholder recognition is too permissive")
    print(
        "History self-test passed: deleted keys, new pragmas, baseline plaintext, "
        "first-time adoption and inherited-secret boundaries verified"
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
        baseline = load_trusted_baseline(repository, baseline_commit)
        findings, count = scan_history(
            repository,
            head,
            base,
            baseline or {},
            baseline_commit,
            inherit_accepted=baseline is None,
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
