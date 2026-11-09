#!/usr/bin/env python3
"""Claude Code PreToolUse guard (fail closed).

Exit code 2 blocks the tool call; 0 allows it. Any error, malformed input or
missing configuration also exits 2.

Write tools (Edit, Write, NotebookEdit, and any tool not listed as a read tool)
may only touch this allow-list, relative to the workspace root, with ASCII
names compared case-insensitively:
  - CURRENT_STATE.md
  - 00_CONTROL/**, 02_RESEARCH/**, 04_LONG_TERM_RECORDS/**
  - 01_STRATEGY/** except 01_STRATEGY/STRATEGY_SEED.md
  - 03_TAX_YEARS/TY_<4 digits>/WORKING/** (strictly below WORKING)
Everything else is protected, including any path with a non-ASCII component,
any path that resolves outside the root, and any existing file that is the
same file as the seed.

Read tools (Read, Glob, Grep) are blocked only when their target resolves
outside the root, when a Glob pattern, a Grep glob or a Grep path has a
'..' segment anywhere (brace and extglob alternatives included), or when a
brace or extglob alternative in a Glob pattern or Grep glob starts with '/',
'~', a drive or a UNC root. A blank optional path or glob counts as absent.

Any inspected path argument (Read file_path, Glob pattern and path, Grep glob
and path, write file_path and notebook_path) with leading or trailing
whitespace is blocked, since the CLI might trim it into an outside path.

Runs on Python 3.8 or later; an older python3 is blocked.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 8):
    sys.stderr.write("[tax-advisor-guard] blocked: python3 >= 3.8 required\n")
    sys.exit(2)

import json
import os
import re

GUARD_MARKER = "[tax-advisor-guard]"  # the eval harness keys on this exact marker
PATH_KEYS = ("file_path", "notebook_path")
READ_TOOLS = ("Read", "Glob", "Grep")
YEAR_DIR = re.compile(r"ty_[0-9]{4}")
SEED = ("01_STRATEGY", "STRATEGY_SEED.md")
OPEN_DIRS = ("00_control", "02_research", "04_long_term_records")
GLOB_CHARS = frozenset("*?[]{}")
# Segment boundaries in a glob: path separators plus brace and extglob alternatives.
GLOB_SEGMENT_SPLIT = re.compile(r"[/{},()|]")
# Starts of brace and extglob alternatives.
ALTERNATIVE_SPLIT = re.compile(r"[{,(|]")
DRIVE = re.compile(r"[A-Za-z]:")
# Optional string fields that are treated as absent when blank.
OPTIONAL_KEYS = {"Glob": ("path",), "Grep": ("glob", "path")}
# Path-like fields each read tool is checked on.
READ_PATH_KEYS = {"Read": ("file_path",), "Glob": ("pattern", "path"), "Grep": ("glob", "path")}


def _relative_parts(path: str, root: str) -> list[str] | None:
    """Path components of `path` below `root`, or None when outside it."""
    rel = os.path.relpath(path, root)
    parts = [p for p in rel.split(os.sep) if p not in ("", ".")]
    if parts and parts[0] == "..":
        return _parts_via_samefile(path, root)
    return parts


def _split(path: str) -> list[str]:
    return [p for p in path.split(os.sep) if p]


def _parts_via_samefile(path: str, root: str) -> list[str] | None:
    """Fallback containment test for a root spelled with different casing.

    Accepts only a leading prefix of `path` with exactly the root's component
    count, equal to the root's components under casefold, that is the same
    file as the root. Every remaining component is kept, so no protected
    component can be dropped. Errors propagate so the caller fails closed.
    """
    path_parts = _split(path)
    root_parts = _split(root)
    count = len(root_parts)
    if len(path_parts) < count:
        return None
    if [p.casefold() for p in path_parts[:count]] != [p.casefold() for p in root_parts]:
        return None
    prefix = os.sep + os.sep.join(path_parts[:count])
    if not os.path.samefile(prefix, root):
        return None
    return path_parts[count:]


def _resolve(path: str) -> str:
    """realpath of the deepest existing ancestor, with the rest re-appended."""
    path = os.path.normpath(path)
    tail: list[str] = []
    head = path
    while not os.path.lexists(head):
        parent, name = os.path.split(head)
        if parent == head:
            break
        tail.append(name)
        head = parent
    resolved = os.path.realpath(head)
    for name in reversed(tail):
        resolved = os.path.join(resolved, name)
    return os.path.normpath(resolved)


def _writable(parts: list[str]) -> bool:
    """The R27 allow-list, applied to path components below the root."""
    if not parts or not all(p.isascii() for p in parts):
        return False
    low = [p.lower() for p in parts]
    if low == ["current_state.md"]:
        return True
    top, rest = low[0], low[1:]
    if top in OPEN_DIRS:
        return bool(rest)
    if top == "01_strategy":
        return bool(rest) and rest != ["strategy_seed.md"]
    if top == "03_tax_years":
        return len(low) >= 4 and bool(YEAR_DIR.fullmatch(low[1])) and low[2] == "working"
    return False


def _parts_protected(parts: list[str] | None) -> bool:
    return parts is None or not _writable(parts)


def _lexical_parts(path: str, root_lex: str, root_real: str) -> list[str] | None:
    for base in (root_lex, root_real):
        parts = _relative_parts(path, base)
        if parts is not None:
            return parts
    return None


def _forms(path: str, root: str) -> tuple[list[str] | None, list[list[str] | None], str]:
    """(lexical parts, resolved parts of each resolved form, the lexical absolute path)."""
    root_lex = os.path.abspath(root)
    root_real = os.path.realpath(root)
    if os.path.isabs(path):
        lexical = os.path.normpath(path)
    else:
        lexical = os.path.normpath(os.path.join(root_lex, path))
    # Resolve symlinks on the un-normalized path so ".." after a symlink
    # follows the real directory, as the filesystem would.
    raw = path if os.path.isabs(path) else os.path.join(root_lex, path)
    resolved_forms = {_resolve(lexical), os.path.realpath(raw)}
    resolved = [_relative_parts(r, root_real) for r in resolved_forms]
    return _lexical_parts(lexical, root_lex, root_real), resolved, lexical


def _is_seed(lexical: str, root: str) -> bool:
    seed = os.path.join(os.path.realpath(root), *SEED)
    if not (os.path.exists(lexical) and os.path.exists(seed)):
        return False
    return os.path.samefile(lexical, seed)


def is_protected(path: str, root: str) -> bool:
    try:
        lexical_parts, resolved, lexical = _forms(path, root)
        if _parts_protected(lexical_parts):
            return True
        if any(_parts_protected(r) for r in resolved):
            return True
        return _is_seed(lexical, root)
    except BaseException:
        return True


def is_outside(path: str, root: str) -> bool:
    """True when a read target is, or resolves to, a path outside the root."""
    try:
        if path.startswith("~"):
            return True
        lexical_parts, resolved, _ = _forms(path, root)
        return lexical_parts is None or any(r is None for r in resolved)
    except BaseException:
        return True


def _has_dotdot_segment(pattern: str) -> bool:
    """True when some segment of a glob could spell '..'.

    Backslash escapes are dropped first, then the pattern is split on '/',
    braces, commas, parentheses and '|'. A segment made only of '.', '[' and
    ']' with at least two dots ('..', '...', '[.][.]') counts; dots inside a
    longer name ('a..b.md') do not.
    """
    for segment in GLOB_SEGMENT_SPLIT.split(pattern.replace("\\", "")):
        if segment.count(".") >= 2 and not segment.strip(".[]"):
            return True
    return False


def _has_rooted_alternative(pattern: str) -> bool:
    """True when a brace or extglob alternative could start outside the root.

    An alternative is the text after '{', ',', '(' or '|'. It counts when it
    starts with '/', '~', a drive ('C:') or a UNC '\\\\'. Backslash escapes
    are dropped as for '..', and a leading '[' is skipped ('[/]etc').
    """
    for alternative in ALTERNATIVE_SPLIT.split(pattern)[1:]:
        if alternative.startswith("\\\\"):
            return True
    for alternative in ALTERNATIVE_SPLIT.split(pattern.replace("\\", ""))[1:]:
        alternative = alternative.lstrip("[")
        if alternative.startswith(("/", "~")) or DRIVE.match(alternative):
            return True
    return False


def _without_blank_optionals(tool: str, tool_input: dict) -> dict:
    """tool_input minus optional string fields that are empty or whitespace."""
    cleaned = dict(tool_input)
    for key in OPTIONAL_KEYS.get(tool, ()):
        value = cleaned.get(key)
        if isinstance(value, str) and not value.strip():
            del cleaned[key]
    return cleaned


def _is_padded(value: object) -> bool:
    """True for a string with leading or trailing whitespace (str.strip rules)."""
    return isinstance(value, str) and value != value.strip()


def _padded_argument(keys: tuple[str, ...], tool_input: dict) -> str | None:
    for key in keys:
        value = tool_input.get(key)
        if _is_padded(value):
            return value
    return None


def _glob_base(pattern: str) -> str | None:
    """The literal directory prefix of a glob pattern, or None when a '..'
    follows a wildcard (the walk could then leave any prefix we check)."""
    parts = pattern.split("/")
    for i, part in enumerate(parts):
        if GLOB_CHARS & set(part):
            if ".." in parts[i:]:
                return None
            prefix = "/".join(parts[:i])
            return prefix or ("/" if pattern.startswith("/") else ".")
    return pattern


def _display(path: str, root: str) -> str:
    try:
        root_lex = os.path.abspath(root)
        full = path if os.path.isabs(path) else os.path.join(root_lex, path)
        parts = _lexical_parts(os.path.normpath(full), root_lex, os.path.realpath(root))
        if parts is None:
            return path
        return "/".join(parts)
    except BaseException:
        return path


def _read_targets(tool: str, tool_input: dict) -> list[str] | None:
    """Paths a read tool will touch, or None when the input is unusable."""
    if tool == "Read":
        keys = ("file_path",)
        if "file_path" not in tool_input:
            return None
    else:
        keys = ("path",)
    if tool == "Grep" and "glob" in tool_input:
        value = tool_input["glob"]
        if not isinstance(value, str) or not value.strip():
            return None
    targets = []
    for key in keys:
        if key in tool_input:
            value = tool_input[key]
            if not isinstance(value, str) or not value.strip():
                return None
            targets.append(value)
    if tool == "Glob" and "pattern" in tool_input:
        pattern = tool_input["pattern"]
        if not isinstance(pattern, str) or not pattern.strip():
            return None
        base = _glob_base(pattern)
        if base is None:
            return None
        if not os.path.isabs(base) and targets:
            base = os.path.join(targets[0], base)
        targets.append(base)
    return targets or ["."]


def _escaping_argument(tool: str, tool_input: dict) -> str | None:
    """The Glob pattern, Grep glob or Grep path that could leave the root:
    a '..' segment in any of them, or a rooted alternative in a glob."""
    keys = {"Glob": ("pattern",), "Grep": ("glob", "path")}.get(tool, ())
    for key in keys:
        value = tool_input.get(key)
        if not isinstance(value, str):
            continue
        if _has_dotdot_segment(value):
            return value
        if key != "path" and _has_rooted_alternative(value):
            return value
    return None


def _block(path: str, root: str, raw: bool = False) -> int:
    shown = path if raw else _display(path, root)
    print(f"{GUARD_MARKER} protected path: {shown}", file=sys.stderr)
    return 2


def main() -> int:
    try:
        root = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not root:
            print("guard_paths: CLAUDE_PROJECT_DIR is not set", file=sys.stderr)
            return 2
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            print("guard_paths: input is not a JSON object", file=sys.stderr)
            return 2
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            print("guard_paths: tool_input is not an object", file=sys.stderr)
            return 2
        tool = data.get("tool_name")
        if tool in READ_TOOLS:
            tool_input = _without_blank_optionals(tool, tool_input)
            padded = _padded_argument(READ_PATH_KEYS[tool], tool_input)
            if padded is not None:
                return _block(padded, root, raw=True)
            escape = _escaping_argument(tool, tool_input)
            if escape is not None:
                return _block(escape, root, raw=True)
            targets = _read_targets(tool, tool_input)
            if targets is None:
                print(f"guard_paths: invalid {tool} input", file=sys.stderr)
                return 2
            for path in targets:
                if is_outside(path, root):
                    return _block(path, root)
            return 0
        paths = []
        for key in PATH_KEYS:
            if key in tool_input:
                value = tool_input[key]
                if not isinstance(value, str) or not value:
                    print(f"guard_paths: invalid {key}", file=sys.stderr)
                    return 2
                paths.append(value)
        if not paths:
            print("guard_paths: no file path in tool_input", file=sys.stderr)
            return 2
        for path in paths:
            if _is_padded(path):
                return _block(path, root, raw=True)
        for path in paths:
            if is_protected(path, root):
                return _block(path, root)
        return 0
    except BaseException as exc:
        try:
            print(f"guard_paths: blocked on error ({type(exc).__name__})", file=sys.stderr)
        except BaseException:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
