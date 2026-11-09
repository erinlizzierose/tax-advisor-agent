"""Run a scenario prompt through `claude -p` (Claude Code), verified on 2.1.311.

Isolation. `--setting-sources project` keeps user settings, user CLAUDE.md, user agents
and plugins out; `--strict-mcp-config` keeps every MCP server out; `--bare` is never used
because it would also drop the project's agent, skills and hook. Claude Code's own bundled
skills stay visible and cannot be switched off.

Trust. A fresh temp workspace is never "trusted", and in `-p` mode Claude Code then drops
the project's `permissions.allow` (hooks and deny rules still apply). Without a grant the
agent could not write anything. The runner therefore passes the allow list that the
installed `.claude/settings.json` carries as `--allowedTools`, which stands in for the
trusted state a real user has. That grant is capped at APPROVED_TOOLS: settings naming any
other tool make the runner raise before it launches. The PreToolUse guard hook still runs
before any allow.

Billing. `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are removed from the child's
environment (_proc.without_anthropic_billing, shared with the judge) so a stray key cannot
move the run from the subscription to API billing.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Mapping

from events import json_lines
from runner import ErrorKind, RawRun

from . import _proc

AGENT = "tax-advisor"
# The ceiling on what `--allowedTools` may grant: the agent's own tool allowlist in
# agent/claude/frontmatter.yaml (a test keeps the two equal).
APPROVED_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch", "Skill"})
TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
# The one scoped form accepted: `WebFetch(domain:<host>)` or `WebFetch(domain:*.<host>)`, a
# lowercase DNS name with no other wildcard (Claude Code docs: a leading `*.` matches any
# subdomain depth; `*` elsewhere, or a bare `*`, would widen the grant).
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
SCOPED_WEBFETCH = re.compile(rf"WebFetch\(domain:(?:\*\.)?{_LABEL}(?:\.{_LABEL})*\)\Z")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
# Top-level `error` on an assistant stream event (observed: authentication_failed; the
# other values are from the Agent SDK's AssistantMessageError type).
ASSISTANT_ERRORS: dict[str, ErrorKind] = {"authentication_failed": "auth", "rate_limit": "rate_limit"}
PRIORITY: tuple[ErrorKind, ...] = ("rate_limit", "auth", "other")


def allowed_tools(workspace: Path) -> list[str]:
    """The allow entries the installed settings.json grants, all inside APPROVED_TOOLS.

    Entries are plain tool names, or `WebFetch(domain:...)` scoped to one well-formed host.
    The settings are only a source for the list, not an authority over it: a name outside
    the ceiling (Bash, Agent, Task, an mcp__ tool, anything unknown) raises, and nothing is
    launched. Such names are never silently dropped."""
    try:
        settings = json.loads((workspace / ".claude" / "settings.json").read_text(encoding="utf-8"))
        allow = settings["permissions"]["allow"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read permissions.allow from .claude/settings.json ({type(exc).__name__})") from None
    if not isinstance(allow, list) or not allow or not all(
        isinstance(t, str) and (TOOL_NAME.match(t) or SCOPED_WEBFETCH.match(t)) for t in allow
    ):
        raise ValueError("permissions.allow must be a non-empty list of tool names or WebFetch(domain:<host>)")
    outside = [t for t in allow if t not in APPROVED_TOOLS and not SCOPED_WEBFETCH.match(t)]
    if outside:
        raise ValueError(
            f"permissions.allow has tools outside the approved set: {', '.join(outside)} "
            f"(approved: {', '.join(sorted(APPROVED_TOOLS))})"
        )
    return list(allow)


def tokens_from(stdout: str) -> int | None:
    """Input + output + cache tokens from the final `result` event, or None."""
    for obj in reversed(json_lines(stdout)):
        if obj.get("type") == "result":
            usage = obj.get("usage")
            if not isinstance(usage, dict):
                return None
            values = [usage.get(k) for k in TOKEN_FIELDS]
            return sum(v for v in values if isinstance(v, int) and not isinstance(v, bool))
    return None


def _scan_stream(stdout: str) -> tuple[list[str], bool, dict | None]:
    """(assistant `error` values in order, whether a rate_limit_event was rejected, the last
    `result` event or None)."""
    assistant_errors: list[str] = []
    rejected = False
    result: dict | None = None
    for obj in json_lines(stdout):
        kind = obj.get("type")
        if kind == "assistant" and isinstance(obj.get("error"), str):
            assistant_errors.append(obj["error"])
        elif kind == "rate_limit_event":
            info = obj.get("rate_limit_info")
            rejected = rejected or (isinstance(info, dict) and info.get("status") == "rejected")
        elif kind == "result":
            result = obj
    return assistant_errors, rejected, result


def _completed(result: dict | None) -> bool:
    """The run ended in a `result` event with subtype `success` whose `is_error` is not true.
    Other subtypes (error_during_execution, error_max_turns, ...) can carry is_error false."""
    return result is not None and result.get("subtype") == "success" and result.get("is_error") is not True


def error_kind_from(stdout: str, exit_code: int) -> ErrorKind | None:
    """Structured failure signals only: an assistant event's `error`, a rejected
    `rate_limit_event`, a `result` that is not a success, no `result` event at all (the
    process died), or a nonzero exit code. Tool errors and agent prose never count.

    An assistant event's `error` is a transient failure when the run still ends in a
    successful `result`: Claude Code retried and finished, so those errors are not run errors
    (`recovered_note` reports them). A rejected rate limit event stays an error either way.
    A nonzero exit is `other`, but a structured rate_limit or auth signal outranks it."""
    assistant_errors, rejected, result = _scan_stream(stdout)
    kinds: list[ErrorKind] = []
    if not _completed(result):
        kinds.extend(ASSISTANT_ERRORS.get(name, "other") for name in assistant_errors)
        kinds.append("other")
    if exit_code != 0:
        kinds.append("other")
    if rejected:
        kinds.append("rate_limit")
    for candidate in PRIORITY:
        if candidate in kinds:
            return candidate
    return None


def recovered_note(stdout: str) -> str:
    """"recovered: server_error x1, ..." for assistant errors in a run that still completed;
    empty otherwise."""
    assistant_errors, _rejected, result = _scan_stream(stdout)
    if not _completed(result) or not assistant_errors:
        return ""
    counts = Counter(assistant_errors)
    return "recovered: " + ", ".join(f"{name} x{counts[name]}" for name in sorted(counts))


class ClaudeRunner:
    @staticmethod
    def validate_model(model: str) -> None:
        """Raise ValueError unless the model name is acceptable. Spawns nothing."""
        _proc.check_model(model)

    def __init__(self, model: str, *, executable: str = "claude") -> None:
        self.validate_model(model)
        self.model = model
        self.executable = executable
        self.cli_version = _proc.cli_version(executable)

    def build_args(self, workspace: Path, prompt: str) -> list[str]:
        _proc.check_prompt(prompt)
        return [
            self.executable,
            "-p",
            prompt,
            "--agent",
            AGENT,
            "--model",
            self.model,
            *_proc.CLAUDE_ISOLATION_FLAGS,
            "--output-format",
            "stream-json",
            "--verbose",
            "--allowedTools=" + ",".join(allowed_tools(workspace)),
        ]

    def build_env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        source = os.environ if base is None else base
        return _proc.without_anthropic_billing(source)

    def run(self, workspace: Path, prompt: str, timeout_s: int) -> RawRun:
        args = self.build_args(workspace, prompt)
        code, out, err, timed_out = _proc.run_process(
            args, cwd=workspace, env=self.build_env(), timeout_s=timeout_s
        )
        kind = None if timed_out else error_kind_from(out, code)
        note = "" if timed_out else recovered_note(out)
        return RawRun(code, out, err, timed_out, tokens_from(out), kind, note)
