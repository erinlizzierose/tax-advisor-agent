"""Remove CLI metadata noise from a transcript BEFORE the privacy scan and before it is saved.

Real CLI output carries machine-specific metadata that is not private but trips the scanner's
9-digit rule or leaks the machine layout: epoch-millisecond timestamps, long float costs, the
home directory in Claude's `init` event, and opaque session, request and message ids.

Rulings R18 and R19: this is a metadata allowlist, not a global rewrite.

(a) Every rule is (tool, event-type selector, EXACT scalar leaf path) -> kind. The selector is
the event's top-level `type` plus, where it matters, Claude's `subtype` or OpenCode's
`part.type`; a `None` subtype matches any. An event of an unlisted type is returned verbatim,
and so is any leaf that is not listed for its type. There are no container-wide rules: a rule
is applied only to a scalar, so a list or dict found at a rule path (and everything inside it,
such as the entries of `skills`, `slash_commands`, `tools` or `mcp_servers`) is never
rewritten. Assistant text, tool inputs, tool results and command output are never rewritten.
Kinds:
- "time": an epoch number of 10 to 13 digits (or numeric string) -> 0; an ISO datetime ->
  1970-01-01T00:00:00Z
- "id":   `ses_ msg_ prt_ call_ req_ fc_` ids (8+ characters with a digit) -> `<prefix>_ID_<n>`,
          UUIDs -> `00000000-0000-0000-0000-a<n>`. Nothing else in the string changes.
- "home": the home directory (and its /private variants, and any /Users/<name>) -> `<HOME>`
          and nothing else, never id replacement.
- "float": a float with 9 or more fraction digits is rounded to 6 places. Besides the cost
  and usage leaves of `result`, this covers Claude's `user` -> `tool_use_result.durationSeconds`,
  the only rule inside a tool result: the CLI's own timing, not the tool's output.
- "thinking_signature" / "thinking_data": the opaque cryptographic blob of a Claude thinking
  block, `message.content[*].signature` on a `thinking` or `redacted_thinking` item, and
  `message.content[*].data` on a `redacted_thinking` item -> `<THINKING_SIGNATURE>` /
  `<THINKING_DATA>`. These are random base64 that can contain a short denylist term by chance.
  A rule of this kind applies only when the content item's own `type` says so; the same key on a
  `text` item, a tool input or anywhere else is left alone, and so is the thinking text.
Tool-call ids (`toolu_...`) are never rewritten.

(b) The one exception is a short list of literals the HARNESS created and passes in explicitly
(`harness_paths`: the run's workspace path and the temp root, and for OpenCode the real XDG
data, state and cache directories its isolation env keeps, as `<DATA_HOME>`, `<STATE_HOME>`,
`<CACHE_HOME>`; never the home directory itself or above it). Those are replaced everywhere,
including authored content and dict keys, in plain, /private-prefixed, JSON-escaped and
percent-encoded forms. They are harness-generated, so replacing them cannot hide user data.
Nothing is inferred by pattern.

Ruling R20: a literal is replaced only at a genuine path boundary on both sides. On the left,
the start of the string or one of whitespace, a quote, `= ( [ , : < > |` (never a "/" from a
longer path); on the right, a "/" in any of its encoded forms, the end of the string, or a
character that cannot continue a path segment. A literal that is short (under 12 characters)
or a generic temp root (`/tmp`, `/var/tmp` and their /private forms) is never replaced.

Also R20: a dict whose keys would collide once normalized keeps its original keys and values,
and so does a JSON line with duplicate keys. Either one adds a `sanitize-collision` finding,
which withholds the transcript. A value is never dropped or overwritten.

Lines that are not JSON objects are returned unchanged except for the harness literals.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterable, Iterator

from privacy_check import Finding, redact

HOME_TOKEN = "<HOME>"
THINKING_SIGNATURE_TOKEN = "<THINKING_SIGNATURE>"
THINKING_DATA_TOKEN = "<THINKING_DATA>"
# kind -> the content-item `type` values whose sibling leaf of that kind is opaque
OPAQUE_BLOCK_TYPES = {
    "thinking_signature": frozenset({"thinking", "redacted_thinking"}),
    "thinking_data": frozenset({"redacted_thinking"}),
}
SANITIZE_COLLISION = "sanitize-collision"
EPOCH_ZERO_ISO = "1970-01-01T00:00:00Z"

Selector = tuple[str, "str | None"]

_CLAUDE_IDS = {"uuid": "id", "session_id": "id"}
# Verified against real captures (Claude Code 2.1.311, OpenCode 2.1.4).
CLAUDE_METADATA: dict[Selector, dict[str, str]] = {
    ("system", "init"): {
        **_CLAUDE_IDS,
        "timestamp": "time",
        "cwd": "home",
        "original_cwd": "home",
        "memory_paths.auto": "home",
        "plugins.*.path": "home",
    },
    ("system", None): {**_CLAUDE_IDS, "timestamp": "time"},
    ("assistant", None): {
        **_CLAUDE_IDS,
        "request_id": "id",
        "message.id": "id",
        "timestamp": "time",
        "message.content.*.signature": "thinking_signature",
        "message.content.*.data": "thinking_data",
    },
    # durationSeconds: the CLI's timing of a tool call (a long float, seen on web tools), the
    # one leaf under tool_use_result that is CLI metadata rather than the tool's own result.
    ("user", None): {**_CLAUDE_IDS, "timestamp": "time", "tool_use_result.durationSeconds": "float"},
    ("rate_limit_event", None): {
        **_CLAUDE_IDS,
        "rate_limit_info.resetsAt": "time",
        "rate_limit_info.unifiedWindows.*.resetsAt": "time",
    },
    ("result", None): {
        **_CLAUDE_IDS,
        "total_cost_usd": "float",
        "usage.*": "float",
        "modelUsage.*.costUSD": "float",
        "model_usage_since_last_result.*.costUSD": "float",
    },
}
_OPENCODE_TOP = {"timestamp": "time", "sessionID": "id"}
_OPENCODE_PART = {**_OPENCODE_TOP, "part.id": "id", "part.messageID": "id", "part.sessionID": "id"}
OPENCODE_METADATA: dict[Selector, dict[str, str]] = {
    ("step_start", "step-start"): dict(_OPENCODE_PART),
    ("step_finish", "step-finish"): {**_OPENCODE_PART, "part.cost": "float"},
    ("text", "text"): {**_OPENCODE_PART, "part.time.start": "time", "part.time.end": "time"},
    ("tool_use", "tool"): {
        **_OPENCODE_PART,
        "part.partID": "id",
        "part.state.time.start": "time",
        "part.state.time.end": "time",
    },
    ("error", None): dict(_OPENCODE_TOP),
}
ALLOWLISTS = {"claude": CLAUDE_METADATA, "opencode": OPENCODE_METADATA}

OTHER_USER_HOME = re.compile(r"/Users/[^/\s\"'\\]+")
OPAQUE_ID = re.compile(r"\b(ses|msg|prt|call|req|fc)_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{8,}\b")
UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
UUID_PLACEHOLDER = re.compile(r"00000000-0000-0000-0000-(?:a\d{11}|0{11}[a-f])\Z")
ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\Z")
LONG_FRACTION = re.compile(r"\.\d{9,}")
# How a "/" can be written in a path: plain, JSON-escaped (once, twice), \u002f, %2F, %252F.
SLASH_FORMS = ("/", "\\/", "\\\\/", "\\u002f", "\\u002F", "\\\\u002f", "\\\\u002F", "%2F", "%2f", "%252F", "%252f")
GENERIC_TEMP_ROOTS = frozenset({"/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp"})
MIN_LITERAL_LENGTH = 12
# Left: the start, a character that cannot be part of a path segment, or a `file://` scheme
# written with the same slash form as the path.
LEFT_BOUNDARY = (
    r"(?:(?<![^\s\"'`=(\[,:<>|])|"
    + "|".join("(?<=" + re.escape(f"file:{s}{s}") + ")" for s in SLASH_FORMS)
    + ")"
)
# Right: the end, a slash in any form, a separator, or an escape that ends a JSON string or
# line (\" \n \r \t, at any escaping depth). Anything else, including an escaped letter such
# as \u0078 or %78, could continue the path segment, so it blocks the replacement.
RIGHT_BOUNDARY = (
    r"(?=\Z|[\s\"'`=()\[\]{},:;<>|]|"
    + "|".join(re.escape(s) for s in SLASH_FORMS)
    + r"|\\+[\"nrt])"
)


def path_variants(path: Path) -> list[str]:
    """The path as given, resolved, and with/without the macOS /private prefix, longest first.
    Empty for a root-like path, which must never be rewritten."""
    text = str(path)
    if len(text) < 2:
        return []
    found = {text}
    try:
        found.add(str(path.resolve()))
    except OSError:
        pass
    for variant in list(found):
        if variant.startswith("/private/"):
            found.add(variant[len("/private"):])
        else:
            found.add("/private" + variant)
    return sorted((v for v in found if len(v) > 1), key=len, reverse=True)


def replaceable_variants(literal: Path | str) -> list[str]:
    """`path_variants`, or nothing when any variant is short or a generic temp root: such a
    literal could match inside unrelated authored paths, so it is never replaced."""
    variants = path_variants(Path(literal))
    if any(len(v) < MIN_LITERAL_LENGTH or v in GENERIC_TEMP_ROOTS for v in variants):
        return []
    return variants


def default_home_dirs() -> list[str]:
    return path_variants(Path.home())


def _event_selector(tool: str, event: dict[str, Any]) -> tuple[str | None, str | None]:
    etype = event.get("type")
    if tool == "claude":
        sub = event.get("subtype")
    else:
        part = event.get("part")
        sub = part.get("type") if isinstance(part, dict) else None
    return (etype if isinstance(etype, str) else None, sub if isinstance(sub, str) else None)


def rules_for(tool: str, event: dict[str, Any]) -> list[tuple[tuple[str, ...], str, str]]:
    """(segment patterns, kind, dotted pattern) for this event's type; empty if unlisted."""
    table = ALLOWLISTS.get(tool, {})
    etype, sub = _event_selector(tool, event)
    rules: dict[str, str] = {}
    for (rtype, rsub), leaves in table.items():
        if rtype == etype and (rsub is None or rsub == sub):
            rules.update(leaves)
    return [(tuple(path.split(".")), kind, path) for path, kind in rules.items()]


def _matches(path: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    return len(path) == len(pattern) and all(fnmatchcase(seg, pat) for seg, pat in zip(path, pattern))


def iter_metadata(tool: str, event: dict[str, Any]) -> Iterator[tuple[str, Any, str]]:
    """Every node an event's rules point at, as (dotted path, value, rule pattern). Includes
    containers reached through a wildcard; the sanitizer skips those. For tests."""
    rules = rules_for(tool, event)

    def walk(value: Any, path: tuple[str, ...]) -> Iterator[tuple[str, Any, str]]:
        for pattern, _kind, dotted in rules:
            if _matches(path, pattern):
                yield ".".join(path), value, dotted
        if isinstance(value, dict):
            for key, item in value.items():
                yield from walk(item, path + (key,))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from walk(item, path + (str(index),))

    if isinstance(event, dict):
        for key, item in event.items():
            yield from walk(item, (key,))


class _Sanitizer:
    def __init__(self, tool: str, home_dirs: Iterable[str], harness_paths: Iterable[tuple[Path | str, str]]) -> None:
        self.tool = tool
        self.homes = [
            re.compile(re.escape(p) + r"(?![A-Za-z0-9_])")
            for p in sorted(set(home_dirs), key=len, reverse=True)
            if len(p) > 1
        ]
        self.forms: dict[str, str] = {}
        for literal, token in harness_paths:
            for variant in replaceable_variants(literal):
                for slash in SLASH_FORMS:
                    self.forms.setdefault(variant.replace("/", slash), token)
        # One pass, longest first: the workspace path contains the temp root as a prefix, and
        # a single pass means a replacement never creates a new boundary for another literal.
        self.harness = (
            re.compile(
                LEFT_BOUNDARY
                + "(?:"
                + "|".join(re.escape(f) for f in sorted(self.forms, key=len, reverse=True))
                + ")"
                + RIGHT_BOUNDARY
            )
            if self.forms
            else None
        )
        self.ids: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.uuids: dict[str, str] = {}
        self.collided = False

    # --- harness literals: everywhere, at path boundaries only
    def literals(self, value: str) -> str:
        if self.harness is None:
            return value
        return self.harness.sub(lambda m: self.forms[m.group(0)], value)

    def text(self, value: str) -> str:
        """`literals`, except that a string holding JSON is left completely unchanged (and the
        line flagged) when the rewrite would make two keys of any mapping in it identical, at
        any depth of JSON nested in its strings. A string that is not JSON is plain text."""
        rewritten = self.literals(value)
        if rewritten == value or value.lstrip()[:1] not in ("{", "["):
            return rewritten
        try:
            json.loads(value)
        except (ValueError, RecursionError):
            return rewritten
        if _embedded_collision(rewritten, 0):
            self.collided = True
            return value
        return rewritten

    # --- kinds, applied to a scalar leaf only
    def _id(self, match: re.Match[str]) -> str:
        found = match.group(0)
        if found not in self.ids:
            prefix = match.group(1)
            self.counts[prefix] = self.counts.get(prefix, 0) + 1
            self.ids[found] = f"{prefix}_ID_{self.counts[prefix]}"
        return self.ids[found]

    def _uuid(self, match: re.Match[str]) -> str:
        found = match.group(0)
        if UUID_PLACEHOLDER.match(found):
            return found
        if found not in self.uuids:
            self.uuids[found] = f"00000000-0000-0000-0000-a{len(self.uuids) + 1:011d}"
        return self.uuids[found]

    def _apply(self, value: Any, kind: str, parent: Any = None) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if kind in OPAQUE_BLOCK_TYPES:
            if isinstance(value, str) and isinstance(parent, dict) and parent.get("type") in OPAQUE_BLOCK_TYPES[kind]:
                return THINKING_SIGNATURE_TOKEN if kind == "thinking_signature" else THINKING_DATA_TOKEN
            return value
        if kind == "time":
            if isinstance(value, int):
                return 0 if 10 <= len(str(abs(value))) <= 13 else value
            if isinstance(value, str):
                if ISO_DATETIME.match(value):
                    return EPOCH_ZERO_ISO
                if re.fullmatch(r"\d{10,13}", value):
                    return "0"
            return value
        if kind == "id" and isinstance(value, str):
            return UUID.sub(self._uuid, OPAQUE_ID.sub(self._id, value))
        if kind == "home" and isinstance(value, str):
            for pattern in self.homes:
                value = pattern.sub(HOME_TOKEN, value)
            return OTHER_USER_HOME.sub(HOME_TOKEN, value)
        if kind == "float" and isinstance(value, float) and LONG_FRACTION.search(repr(value)):
            return round(value, 6)
        return value

    def walk(
        self,
        value: Any,
        path: tuple[str, ...],
        rules: list[tuple[tuple[str, ...], str, str]],
        parent: Any = None,
    ) -> Any:
        if isinstance(value, dict):
            keys = [self.text(k) for k in value]
            if len(set(keys)) != len(keys):
                self.collided = True
                return value
            return {new: self.walk(v, path + (k,), rules, value) for new, (k, v) in zip(keys, value.items())}
        if isinstance(value, list):
            return [self.walk(v, path + (str(i),), rules) for i, v in enumerate(value)]
        for pattern, kind, _dotted in rules:
            if _matches(path, pattern):
                value = self._apply(value, kind, parent)
                break
        return self.text(value) if isinstance(value, str) else value

    def line(self, line: str) -> tuple[str, bool]:
        """(sanitized line, whether a collision kept part of it verbatim)."""
        self.collided = False
        if line.lstrip().startswith("{"):
            try:
                parsed = json.loads(line, object_pairs_hook=_unique_keys)
            except _DuplicateKeys:
                return line, True
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, dict):
                cleaned = self.walk(parsed, (), rules_for(self.tool, parsed))
                if cleaned == parsed:
                    return line, self.collided
                return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")), self.collided
        return self.text(line), self.collided


class _DuplicateKeys(Exception):
    """A JSON object repeats a key; rebuilding it would drop a value."""


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _DuplicateKeys
    return result


MAX_EMBEDDED_DEPTH = 8


def _embedded_collision(text: str, depth: int) -> bool:
    """True if this rewritten embedded JSON, or JSON embedded in any of its strings, has a
    mapping with repeated keys. Fails closed: rewritten text that no longer parses at the top,
    or nesting deeper than MAX_EMBEDDED_DEPTH, counts as a collision."""
    try:
        parsed = json.loads(text, object_pairs_hook=_unique_keys)
    except _DuplicateKeys:
        return True
    except (ValueError, RecursionError):
        return depth == 0
    stack = [parsed]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and item.lstrip()[:1] in ("{", "["):
            if depth + 1 > MAX_EMBEDDED_DEPTH:
                try:
                    json.loads(item)
                except (ValueError, RecursionError):
                    continue
                return True
            if _embedded_collision(item, depth + 1):
                return True
    return False


@dataclass(frozen=True)
class Sanitized:
    text: str
    findings: list[Finding] = field(default_factory=list)


def sanitize(
    text: str,
    *,
    tool: str,
    home_dirs: Iterable[str] | None = None,
    harness_paths: Iterable[tuple[Path | str, str]] = (),
) -> Sanitized:
    """The transcript with CLI metadata (see the module docstring) normalized and the
    harness-created path literals replaced, plus a `sanitize-collision` finding for each line
    where a collision kept keys and values verbatim. Any finding must withhold the transcript.
    Authored content is otherwise never rewritten. An unknown tool is returned unchanged."""
    if tool not in ALLOWLISTS:
        return Sanitized(text)
    sanitizer = _Sanitizer(
        tool, default_home_dirs() if home_dirs is None else home_dirs, list(harness_paths)
    )
    lines: list[str] = []
    findings: list[Finding] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        cleaned, collided = sanitizer.line(raw)
        lines.append(cleaned)
        if collided:
            findings.append(Finding(SANITIZE_COLLISION, redact(SANITIZE_COLLISION), number))
    return Sanitized("\n".join(lines), findings)


def sanitize_transcript(
    text: str,
    *,
    tool: str,
    home_dirs: Iterable[str] | None = None,
    harness_paths: Iterable[tuple[Path | str, str]] = (),
) -> str:
    """Only the text of `sanitize`. A caller that saves the result must use `sanitize` and
    honor its findings instead."""
    return sanitize(text, tool=tool, home_dirs=home_dirs, harness_paths=harness_paths).text
