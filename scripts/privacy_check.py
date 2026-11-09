#!/usr/bin/env python3
"""Scan text for personal identifiers before they reach a public commit.

Usage: privacy_check.py [--denylist PATH] [--staged | --history | PATH...]

Exit codes: 0 clean, 1 findings, 2 denylist missing, bad target, or git failure.
Findings are always redacted; matched values are never printed in full. --staged and --history
also report binary files (kind `binary`, line 0, with the path), since their content cannot be
read as text; any text inside them is still scanned.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

DEFAULT_DENYLIST = Path("~/.config/tax-agent/denylist.txt")

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("ein", re.compile(r"\b\d{2}-\d{7}\b")),
    ("digits", re.compile(r"\b\d{9,}\b")),
]


@dataclass(frozen=True)
class Finding:
    kind: str
    redacted: str
    line: int


def redact(value: str) -> str:
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]


_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
MAX_DECODE_PASSES = 6


def _unicode_escape(match: re.Match[str]) -> str:
    code = int(match.group(1), 16)
    return match.group(0) if 0xD800 <= code <= 0xDFFF else chr(code)


def _decode_once(text: str) -> str:
    """One layer of encoding removed: %XX, \\uXXXX and the JSON escape \\/ . Newlines are
    never introduced by \\n, so line numbers stay put."""
    text = unquote(text)
    text = _UNICODE_ESCAPE.sub(_unicode_escape, text)
    return text.replace("\\/", "/")


def decoded_variants(text: str) -> list[str]:
    """The text, then each further-decoded form until nothing changes (layers of
    percent-encoding and JSON escaping nest, e.g. JSON text inside a JSON string)."""
    variants = [text]
    for _ in range(MAX_DECODE_PASSES):
        decoded = _decode_once(variants[-1])
        if decoded == variants[-1]:
            break
        variants.append(decoded)
    return variants


EXCESSIVE_ENCODING = "excessive-encoding"


def _undecoded_line(text: str) -> int | None:
    """The first line that is still changing under one more decoding pass, if any."""
    for number, line in enumerate(text.splitlines() or [text], start=1):
        if _decode_once(line) != line:
            return number
    return None


def scan_text(text: str, denylist: list[str]) -> list[Finding]:
    """Findings in the text as written, plus any that only appear once an encoding layer is
    removed. A finding seen in several forms is reported once per occurrence.

    Fails closed: if the text is still changing after MAX_DECODE_PASSES, whatever is left
    could hide anything, so an `excessive-encoding` finding is added."""
    best: dict[tuple[str, str, int], int] = {}
    merged: list[Finding] = []
    variants = decoded_variants(text)
    if _decode_once(variants[-1]) != variants[-1]:
        line = _undecoded_line(variants[-1]) or 1
        merged.append(Finding(EXCESSIVE_ENCODING, redact(EXCESSIVE_ENCODING), line))
    for variant in variants:
        counts: dict[tuple[str, str, int], int] = {}
        for finding in _scan_one(variant, denylist):
            key = (finding.kind, finding.redacted, finding.line)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > best.get(key, 0):
                best[key] = counts[key]
                merged.append(finding)
    return merged


def _scan_one(text: str, denylist: list[str]) -> list[Finding]:
    terms = [t.strip().lower() for t in denylist if t.strip()]
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                findings.append(Finding(kind, redact(match.group()), number))
        lowered = line.lower()
        for term in terms:
            start = lowered.find(term)
            while start != -1:
                matched = line[start : start + len(term)]
                findings.append(Finding("denylist", redact(matched), number))
                start = lowered.find(term, start + len(term))
    return findings


def load_denylist(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, errors="replace", check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def parse_added_lines(diff: str) -> list[str]:
    """Return the content of every line added inside a hunk.

    `+++ b/path` is only a header before a file section's first `@@`; inside a
    hunk every `+` line is content, even if the content itself starts with `++`.
    """
    added: list[str] = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff "):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    return added


BINARY = "binary"

# A configured external diff or textconv driver could print anything (or nothing) in place of
# the real content, and without --text git replaces a binary file's content by one line.
RAW_DIFF_FLAGS = ["--no-color", "--no-ext-diff", "--no-textconv", "--text"]

_BINARY_LINE = re.compile(r"^Binary files (.*) differ$")
_NUMSTAT_BINARY = re.compile(r"^-\t-\t(.+)$")
_DIFF_HEADER = "diff --git a/"
_INDEX_LINE = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)")


def binary_paths_from_numstat(numstat: str) -> list[str]:
    """Paths git counts as binary (`-<TAB>-<TAB>path` in --numstat)."""
    return [m.group(1) for line in numstat.splitlines() if (m := _NUMSTAT_BINARY.match(line))]


def blob_size(blob: str) -> int:
    return int(run_git(["cat-file", "-s", blob]).strip())


def _header_path(header: str) -> str:
    """The path in `diff --git a/P b/P`; the whole remainder if the two sides differ."""
    rest = header[len(_DIFF_HEADER) :]
    half = (len(rest) - len(" b/")) // 2
    if half > 0 and rest[half : half + 3] == " b/" and rest[half + 3 :] == rest[:half]:
        return rest[:half]
    return rest


def binary_paths_from_diff(diff: str, size_of: Callable[[str], int] = blob_size) -> list[str]:
    """What a diff could not show as text: the target of every `Binary files … differ` line,
    and every file whose content changed (different blobs in its `index` line, not deleted,
    not empty afterwards) yet has no hunk."""
    found: list[str] = []
    section: dict[str, object] | None = None

    def close() -> None:
        if section and section["blob"] and not (section["hunk"] or section["deleted"] or section["binary"]):
            if size_of(str(section["blob"])) > 0:
                found.append(str(section["path"]))

    for line in diff.splitlines():
        if line.startswith(_DIFF_HEADER):
            close()
            section = {"path": _header_path(line), "blob": None, "hunk": False, "deleted": False, "binary": False}
        elif section is None:
            continue
        elif line.startswith("@@"):
            section["hunk"] = True
        elif line.startswith("deleted file mode"):
            section["deleted"] = True
        elif (match := _BINARY_LINE.match(line)) is not None:
            section["binary"] = True
            found.append(match.group(1))
        elif (match := _INDEX_LINE.match(line)) is not None and match.group(1) != match.group(2):
            section["blob"] = match.group(2)
    close()
    return found


def _unique(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def staged_scan() -> tuple[str, list[str]]:
    """The added text, and the staged paths whose content is binary or could not be shown."""
    diff = run_git(["diff", "--cached", *RAW_DIFF_FLAGS, "-U0"])
    numstat = run_git(
        ["diff", "--cached", "--numstat", "--no-color", "--no-ext-diff", "--no-textconv", "--diff-filter=d"]
    )
    binary = _unique(binary_paths_from_numstat(numstat) + binary_paths_from_diff(diff))
    return "\n".join(parse_added_lines(diff)), binary


def history_scan() -> tuple[str, list[str]]:
    try:
        log = run_git(["log", "-p", "--all", *RAW_DIFF_FLAGS])
        numstat = run_git(
            ["log", "--all", "--numstat", "--format=", "--no-color", "--no-ext-diff", "--no-textconv"]
        )
    except RuntimeError as exc:
        if "does not have any commits" in str(exc):
            return "", []
        raise
    return log, _unique(binary_paths_from_numstat(numstat) + binary_paths_from_diff(log))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan for personal identifiers.")
    parser.add_argument("--denylist", type=Path, default=DEFAULT_DENYLIST)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan added lines in the index")
    mode.add_argument("--history", action="store_true", help="scan all of git history")
    parser.add_argument("paths", nargs="*", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    denylist_path = args.denylist.expanduser()
    if not denylist_path.is_file():
        print(f"privacy_check: denylist not found: {denylist_path}", file=sys.stderr)
        return 2
    denylist = load_denylist(denylist_path)

    sources: list[tuple[str, str]] = []
    binary: list[tuple[str, str]] = []
    try:
        if args.staged:
            text, paths = staged_scan()
            sources.append(("staged changes", text))
            binary += [("staged changes", p) for p in paths]
        elif args.history:
            text, paths = history_scan()
            sources.append(("git history", text))
            binary += [("git history", p) for p in paths]
        else:
            for path in args.paths:
                if path.is_dir():
                    print(f"privacy_check: target is a directory: {path}", file=sys.stderr)
                    return 2
                if not path.is_file():
                    print(f"privacy_check: target not found: {path}", file=sys.stderr)
                    return 2
                sources.append((str(path), path.read_text(encoding="utf-8", errors="replace")))
    except RuntimeError as exc:
        print(f"privacy_check: {exc}", file=sys.stderr)
        return 2

    total = 0
    for label, path in binary:
        total += 1
        print(f"{label}:0: {BINARY}: {path}")
    for label, text in sources:
        for finding in scan_text(text, denylist):
            total += 1
            print(f"{label}:{finding.line}: {finding.kind}: {finding.redacted}")
    if total:
        print(f"privacy_check: {total} finding(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
