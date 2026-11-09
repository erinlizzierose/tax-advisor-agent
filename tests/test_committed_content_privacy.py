"""Committed content must pass the same privacy scan the owner runs before a public commit.

These tests use the owner's local denylist (privacy_check.DEFAULT_DENYLIST) and skip when it is
absent. Failures name files and lines only, never the matched value.
"""

import re
import subprocess
from pathlib import Path

import pytest

import privacy_check

REPO = Path(__file__).resolve().parent.parent
DENYLIST_PATH = privacy_check.DEFAULT_DENYLIST.expanduser()
SCANNED_ROOTS = ("agent", "docs", "evals", "examples")
RESULTS_PREFIX = "evals/results/"
# A string-literal concatenation such as "ab" + "cd" (either quote style).
CONCATENATION = re.compile(r"""["']\s*\+\s*["']""")

needs_denylist = pytest.mark.skipif(
    not DENYLIST_PATH.is_file(),
    reason=f"owner denylist not found at {DENYLIST_PATH}; cannot check committed content",
)


def denylist_terms() -> list[str]:
    return [t.lower() for t in privacy_check.load_denylist(DENYLIST_PATH)]


def repo_files(*roots: str) -> list[Path]:
    """Tracked and not-yet-ignored files under the given roots, so new files are covered too."""
    done = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *roots],
        cwd=REPO, capture_output=True, text=True, check=True, timeout=60,
    )
    return [REPO / rel for rel in done.stdout.split("\0") if rel and (REPO / rel).is_file()]


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


@needs_denylist
def test_no_python_source_reassembles_a_denylist_term_from_string_pieces():
    terms = denylist_terms()
    offenders = []
    for path in repo_files("tests", "scripts", "evals", "agent", "docs", "examples"):
        if path.suffix != ".py" or path == Path(__file__).resolve():
            continue
        joined = CONCATENATION.sub("", read(path)).lower()
        if any(term in joined for term in terms):
            offenders.append(path.relative_to(REPO).as_posix())
    assert offenders == [], "denylist term spelled out as split string pieces in: " + ", ".join(offenders)


@needs_denylist
def test_committed_content_passes_the_privacy_scan():
    terms = privacy_check.load_denylist(DENYLIST_PATH)
    findings = []
    for path in repo_files(*SCANNED_ROOTS):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(RESULTS_PREFIX):
            continue
        for finding in privacy_check.scan_text(read(path), terms):
            findings.append(f"{rel}:{finding.line} ({finding.kind})")
    assert findings == [], "privacy findings in committed content: " + ", ".join(findings)


def test_the_scan_covers_the_files_it_claims_to():
    rels = {p.relative_to(REPO).as_posix() for p in repo_files(*SCANNED_ROOTS)}
    assert "evals/answer_key.toml" in rels
    assert "docs/operating-manual.md" in rels
