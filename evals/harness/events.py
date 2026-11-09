"""Parse CLI transcripts into tool events and final assistant text.

Formats, verified against real captures (tests/fixtures/real/, Claude Code 2.1.311 and
OpenCode 2.1.4):
- Claude Code `--output-format stream-json`: `assistant` messages carry `tool_use`
  blocks; `user` messages carry `tool_result` blocks matched by `tool_use_id`. A
  denial is an `is_error` result carrying the guard hook's marker.
- OpenCode `run --format json`: JSON lines; a tool call is one `type: "tool_use"` line
  whose `part` has `type == "tool"`, the call id in `part.id`, `part.tool`, and
  `part.state` (`status`, `input`, `error`). A rule denial is `status == "error"` with
  the exact error text `Permission denied: <action>`. GPT models edit through the
  `patch` tool, whose only path information is inside `input.patchText`.

Unknown lines are skipped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Denial signals are exact and narrow on purpose: a free-text keyword such as "blocked"
# also appears in ordinary tool errors and would score a false enforcement pass.
# Claude: guard_paths.py prefixes its stderr with this marker (ruling R13). Claude Code's
# own permission wording and `permission_denials` also fire for tools that were merely not
# granted, so they are not accepted; "Permission to use X has been denied." is documented
# only for Cowork tools.
GUARD_MARKER = "[tax-advisor-guard] protected path:"
# OpenCode: captured from a real denied `patch` call. The whole error string must be
# this and nothing else; an OS error such as "Permission denied: '/x'" does not match.
OPENCODE_DENIED = re.compile(r"\APermission denied: [a-z_]+\Z")
TARGET_KEYS = ("file_path", "filePath", "path", "notebook_path", "command", "pattern", "url")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)


@dataclass(frozen=True)
class ToolEvent:
    tool_name: str
    target: str | None
    denied: bool


def json_lines(stdout: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def _target(tool_input: Any) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in TARGET_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def _blocks(obj: dict[str, Any]) -> list[dict[str, Any]]:
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _parse_claude(stdout: str) -> list[ToolEvent]:
    order: list[str] = []
    uses: dict[str, tuple[str, str | None]] = {}
    denied_ids: set[str] = set()
    for obj in json_lines(stdout):
        kind = obj.get("type")
        if kind == "assistant":
            for block in _blocks(obj):
                tool_id, name = block.get("id"), block.get("name")
                if block.get("type") == "tool_use" and isinstance(tool_id, str) and isinstance(name, str):
                    if tool_id not in uses:
                        order.append(tool_id)
                    uses[tool_id] = (name, _target(block.get("input")))
        elif kind == "user":
            for block in _blocks(obj):
                if block.get("type") != "tool_result" or block.get("is_error") is not True:
                    continue
                if GUARD_MARKER in _content_text(block.get("content")):
                    denied_ids.add(str(block.get("tool_use_id")))
    return [ToolEvent(uses[i][0], uses[i][1], i in denied_ids) for i in order]


def _opencode_targets(tool_input: Any) -> list[str | None]:
    """Targets of one OpenCode call: every file a `patch` touches, else the usual key."""
    if isinstance(tool_input, dict) and isinstance(tool_input.get("patchText"), str):
        files = PATCH_FILE.findall(tool_input["patchText"])
        if files:
            return list(files)
    return [_target(tool_input)]


def _parse_opencode(stdout: str) -> list[ToolEvent]:
    """One event per call, or per file for a multi-file `patch` (all flagged alike: the
    transcript does not say which file of a denied patch matched the rule)."""
    order: list[str] = []
    calls: dict[str, list[ToolEvent]] = {}
    for n, obj in enumerate(json_lines(stdout)):
        part = obj.get("part")
        if not isinstance(part, dict) or part.get("type") != "tool" or not isinstance(part.get("tool"), str):
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        error = state.get("error")
        denied = (
            state.get("status") == "error" and isinstance(error, str) and bool(OPENCODE_DENIED.match(error))
        )
        call_id = next((part[k] for k in ("id", "callID") if isinstance(part.get(k), str)), f"line-{n}")
        if call_id not in calls:
            order.append(call_id)
        calls[call_id] = [ToolEvent(part["tool"], t, denied) for t in _opencode_targets(state.get("input"))]
    return [event for i in order for event in calls[i]]


def parse_events(tool: str, stdout: str) -> list[ToolEvent]:
    if tool == "claude":
        return _parse_claude(stdout)
    if tool == "opencode":
        return _parse_opencode(stdout)
    raise ValueError(f"unknown tool: {tool!r}")


def final_text(tool: str, stdout: str) -> str:
    """The agent's final answer text, for judging."""
    objects = json_lines(stdout)
    if tool == "claude":
        for obj in reversed(objects):
            if obj.get("type") == "result" and isinstance(obj.get("result"), str):
                return obj["result"]
        texts = [
            b["text"]
            for obj in objects
            if obj.get("type") == "assistant"
            for b in _blocks(obj)
            if b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        return texts[-1] if texts else ""
    if tool == "opencode":
        texts = [
            obj["part"]["text"]
            for obj in objects
            if isinstance(obj.get("part"), dict)
            and obj["part"].get("type") == "text"
            and isinstance(obj["part"].get("text"), str)
        ]
        return "\n".join(texts)
    raise ValueError(f"unknown tool: {tool!r}")
