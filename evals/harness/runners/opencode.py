"""Run a scenario prompt through `opencode run` (OpenCode), verified on 2.1.4.

Isolation. Erin's global OpenCode config (AGENTS.md, plugins, MCP, skills, providers) lives
in `$XDG_CONFIG_HOME/opencode`, and `~/.claude` and `~/.agents` are read for extra skills
and instructions. Each run therefore gets a fresh empty `XDG_CONFIG_HOME` and a fresh empty
`HOME`, while `XDG_DATA_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` keep their real values
so provider credentials (kept in the data directory) still work. `OPENCODE_DISABLE_*` flags
are not usable: they would also hide the workspace's own `.claude/skills`. OpenCode's two
built-in skills (`OpenCode`, `Report`) stay visible and cannot be switched off.

Safety. `--standalone` gives the run a private server; `--auto` is never passed, so any
permission that is not explicitly allowed is refused. Only the `openai/` provider
(ChatGPT/Codex subscription) is accepted. `OPENAI_API_KEY` and `ANTHROPIC_*` keys are
removed from the child's environment, as are the variables that inject config.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Mapping

from events import json_lines
from runner import ErrorKind, RawRun

from . import _proc

AGENT = "tax-advisor"
PROVIDER_PREFIX = "openai/"
BILLING_ENV = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
CONFIG_ENV = ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT", "OPENCODE_PERMISSION")
# `error.type` of a top-level `{"type": "error"}` event. Observed: provider.no-route (what
# a run without credentials produces, and also what an unknown model produces) and
# provider.quota (2026-11-04, OpenCode 2.1.4: "The usage limit has been reached" when the
# ChatGPT plan's usage limit ran out). The other spellings are inferred from the reason tags
# in OpenCode's LLM error schema (Authentication, RateLimit, QuotaExceeded) and were not
# provoked.
ERROR_TYPES: dict[str, ErrorKind] = {
    "provider.no-route": "auth",
    "provider.authentication": "auth",
    "provider.quota": "rate_limit",
    "provider.rate-limit": "rate_limit",
    "provider.quota-exceeded": "rate_limit",
}
PRIORITY: tuple[ErrorKind, ...] = ("rate_limit", "auth", "other")


# The transcript token for each real XDG directory a run keeps. OpenCode writes there (for
# example truncated tool output under <data>/opencode/tool-output/), and the path shows up in
# tool results, so the harness passes these directories to the sanitizer as its own literals.
XDG_TOKENS = {"XDG_DATA_HOME": "<DATA_HOME>", "XDG_STATE_HOME": "<STATE_HOME>", "XDG_CACHE_HOME": "<CACHE_HOME>"}


def xdg_dirs(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The real data, state and cache directories a run keeps (XDG defaults under HOME)."""
    source = os.environ if base is None else base
    real_home = Path(source.get("HOME") or Path.home())
    return {
        "XDG_DATA_HOME": source.get("XDG_DATA_HOME") or str(real_home / ".local" / "share"),
        "XDG_STATE_HOME": source.get("XDG_STATE_HOME") or str(real_home / ".local" / "state"),
        "XDG_CACHE_HOME": source.get("XDG_CACHE_HOME") or str(real_home / ".cache"),
    }


def isolation_env(root: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for one run: empty config and home under root, real data/state/cache."""
    source = dict(os.environ if base is None else base)
    env = {k: v for k, v in source.items() if k not in BILLING_ENV and k not in CONFIG_ENV}
    fake_home, fake_config = root / "home", root / "config"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_config.mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(fake_home), XDG_CONFIG_HOME=str(fake_config), **xdg_dirs(source))
    return env


def tokens_from(stdout: str) -> int | None:
    """Sum of every token category over all `step-finish` events, or None if there are none."""
    total, seen = 0, False
    for obj in json_lines(stdout):
        part = obj.get("part")
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            continue
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            continue
        seen = True
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        for value in (tokens.get("input"), tokens.get("output"), tokens.get("reasoning"), cache.get("read"), cache.get("write")):
            if isinstance(value, int) and not isinstance(value, bool):
                total += value
    return total if seen else None


def completed(stdout: str) -> bool:
    """True only if the transcript shows the run reached its final answer.

    In the captured runs every `step_finish` has reason `tool-calls` (an intermediate step);
    the final step emits no `step_finish`, and the stream ends on its non-empty `text` part.
    The last event therefore decides: a non-empty `text`, or a `step_finish` with the AI SDK
    terminal reason `stop` (not seen in captures), means complete. Anything after that (a
    new step, any tool call, an intermediate `step_finish`) means the run went on, so a
    transcript that ends there was cut off. Empty output is not complete."""
    done = False
    for obj in json_lines(stdout):
        part = obj.get("part")
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("step-start", "tool"):
            done = False
        elif kind == "text":
            text = part.get("text")
            done = isinstance(text, str) and bool(text.strip())
        elif kind == "step-finish":
            done = part.get("reason") == "stop"
    return done


def error_kind_from(stdout: str, exit_code: int) -> ErrorKind | None:
    """Structured failure signals only: a top-level `error` event (these win), else any
    non-zero or signal exit, else output that is not a completed run (see `completed`).
    A run is complete only with a clean exit AND the completion evidence. Tool errors,
    including permission denials, are inside a run and never count.

    Residual, accepted: OpenCode emits no explicit run-end event, so a zero-exit stream that
    was cut off right after a text part still looks complete. That case is a heuristic."""
    kinds: list[ErrorKind] = []
    for obj in json_lines(stdout):
        if obj.get("type") != "error":
            continue
        error = obj.get("error")
        raw_type = error.get("type") if isinstance(error, dict) else None
        normalized = raw_type.lower().replace("_", "-") if isinstance(raw_type, str) else ""
        kinds.append(ERROR_TYPES.get(normalized, "other"))
    if not kinds and (exit_code != 0 or not completed(stdout)):
        kinds.append("other")
    for candidate in PRIORITY:
        if candidate in kinds:
            return candidate
    return None


class OpenCodeRunner:
    @staticmethod
    def validate_model(model: str) -> None:
        """Raise ValueError unless the model name is acceptable. Spawns nothing."""
        _proc.check_model(model)
        if not model.startswith(PROVIDER_PREFIX):
            raise ValueError(f"only {PROVIDER_PREFIX}* models run here (ChatGPT/Codex subscription)")

    def __init__(self, model: str, *, executable: str = "opencode") -> None:
        self.validate_model(model)
        self.model = model
        self.executable = executable
        self.cli_version = _proc.cli_version(executable)

    def build_args(self, prompt: str) -> list[str]:
        _proc.check_prompt(prompt)
        return [
            self.executable,
            "run",
            "--standalone",
            "--agent",
            AGENT,
            "--model",
            self.model,
            "--format",
            "json",
            prompt,
        ]

    def run(self, workspace: Path, prompt: str, timeout_s: int) -> RawRun:
        args = self.build_args(prompt)
        with tempfile.TemporaryDirectory(prefix="taxeval-oc-") as root:
            env = isolation_env(Path(root))
            code, out, err, timed_out = _proc.run_process(args, cwd=workspace, env=env, timeout_s=timeout_s)
        kind = None if timed_out else error_kind_from(out, code)
        return RawRun(code, out, err, timed_out, tokens_from(out), kind)
