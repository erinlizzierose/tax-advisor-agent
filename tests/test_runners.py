"""Tests for the real-CLI runners. No test starts a real claude or opencode process:
`_proc.run_process` and `_proc.cli_version` are replaced for every test, and a call that a
test did not expect fails loudly.

tests/fixtures/real/ holds sanitized captures from Claude Code 2.1.311 and OpenCode 2.1.4.
"""

import ast
import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

import events
from events import ToolEvent, final_text, parse_events
from privacy_check import scan_text
from runner import RawRun, run_scenario
from runners import _proc, claude, opencode
from runners.claude import ClaudeRunner
from runners.opencode import OpenCodeRunner
from scenario import load_scenario
from install import install

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "examples" / "sample-household"
REAL = Path(__file__).resolve().parent / "fixtures" / "real"
SEED = "01_STRATEGY/STRATEGY_SEED.md"


def real(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("a test tried to start a real CLI process")

    monkeypatch.setattr(_proc, "run_process", forbidden)
    monkeypatch.setattr(_proc, "cli_version", lambda executable: "fake 1.2.3")
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))


@pytest.fixture
def claude_ws(tmp_path):
    ws = tmp_path / "ws-claude"
    ws.mkdir()
    install(ws, {"claude"})
    return ws


class Recorder:
    """Stands in for _proc.run_process and records how it was called."""

    def __init__(self, stdout="", code=0, timed_out=False):
        self.calls = []
        self.result = (code, stdout, "", timed_out)

    def __call__(self, args, *, cwd, env, timeout_s):
        self.calls.append({"args": args, "cwd": cwd, "env": dict(env), "timeout_s": timeout_s})
        return self.result


# ------------------------------------------------------------------ Claude runner


def test_claude_never_bare(claude_ws):
    args = ClaudeRunner("sonnet").build_args(claude_ws, "Start a session.")
    assert "--bare" not in args
    assert not any(a.startswith("--bare") for a in args)


def test_claude_isolation_flags(claude_ws):
    runner = ClaudeRunner("sonnet")
    args = runner.build_args(claude_ws, "Start a session.")
    assert args[:3] == ["claude", "-p", "Start a session."]
    pairs = {args[i]: args[i + 1] for i in range(len(args) - 1)}
    assert pairs["--agent"] == "tax-advisor"
    assert pairs["--model"] == "sonnet"
    assert pairs["--setting-sources"] == "project"
    assert pairs["--output-format"] == "stream-json"
    assert "--strict-mcp-config" in args and "--verbose" in args
    assert "--dangerously-skip-permissions" not in args
    assert not any(a.startswith("--permission-mode") for a in args)


def test_claude_grants_exactly_the_installed_allow_list(claude_ws):
    args = ClaudeRunner("sonnet").build_args(claude_ws, "hi there")
    settings = json.loads((claude_ws / ".claude" / "settings.json").read_text())
    assert "--allowedTools=" + ",".join(settings["permissions"]["allow"]) == args[-1]
    assert "Bash" not in args[-1]


@pytest.mark.parametrize(
    "settings",
    [None, "not json", "[]", '{"permissions": {}}', '{"permissions": {"allow": []}}',
     '{"permissions": {"allow": ["Bash(rm -rf *)"]}}', '{"permissions": {"allow": [1]}}'],
)
def test_claude_rejects_unusable_settings(tmp_path, settings):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    if settings is not None:
        (ws / ".claude" / "settings.json").write_text(settings)
    with pytest.raises(ValueError):
        ClaudeRunner("sonnet").build_args(ws, "hi there")


def agent_frontmatter_tools() -> set[str]:
    lines = (REPO / "agent" / "claude" / "frontmatter.yaml").read_text(encoding="utf-8").splitlines()
    line = next(ln for ln in lines if ln.startswith("tools:"))
    return {t.strip() for t in line.split(":", 1)[1].split(",")}


def write_allow(ws, allow):
    path = ws / ".claude" / "settings.json"
    settings = json.loads(path.read_text())
    settings["permissions"]["allow"] = allow
    path.write_text(json.dumps(settings))


def test_approved_tools_are_exactly_the_agents_tool_allowlist():
    assert claude.APPROVED_TOOLS == frozenset(
        {"Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch", "Skill"}
    )
    assert claude.APPROVED_TOOLS == agent_frontmatter_tools()


def test_current_settings_pass_the_ceiling(claude_ws):
    granted = claude.allowed_tools(claude_ws)
    assert granted and {t.split("(", 1)[0] for t in granted} <= claude.APPROVED_TOOLS
    # WebFetch is granted only domain-scoped (A-F6).
    assert "WebFetch" not in granted
    assert [t for t in granted if t.startswith("WebFetch(")] == [
        "WebFetch(domain:*.gov)",
        "WebFetch(domain:law.cornell.edu)",
        "WebFetch(domain:www.law.cornell.edu)",
    ]


@pytest.mark.parametrize(
    "name", ["Bash", "Agent", "Task", "mcp__x__y", "NotebookEdit", "MultiEdit", "Frobnicate", "read", "WebFetch2"]
)
def test_claude_refuses_to_grant_a_tool_outside_the_approved_set(claude_ws, name):
    for allow in ([name], [*sorted(claude.APPROVED_TOOLS), name], [name, "Read"]):
        write_allow(claude_ws, allow)
        with pytest.raises(ValueError) as caught:
            ClaudeRunner("sonnet").build_args(claude_ws, "hi there")
        assert name in str(caught.value) and "approved" in str(caught.value)


def test_claude_accepts_domain_scoped_webfetch(claude_ws):
    allow = ["Read", "WebFetch(domain:*.gov)", "WebFetch(domain:www.law.cornell.edu)", "WebFetch(domain:a-b.example.org)"]
    write_allow(claude_ws, allow)
    assert claude.allowed_tools(claude_ws) == allow
    assert ClaudeRunner("sonnet").build_args(claude_ws, "hi there")[-1] == "--allowedTools=" + ",".join(allow)


@pytest.mark.parametrize(
    "entry",
    [
        "WebFetch(domain:*)",
        "WebFetch(domain:)",
        "WebFetch(domain:*.*)",
        "WebFetch(domain:example.*)",
        "WebFetch(domain:ex*ample.gov)",
        "WebFetch(domain:evil.example/x)",
        "WebFetch(domain:IRS.GOV)",
        "WebFetch(domain:a..gov)",
        "WebFetch(domain:-a.gov)",
        "WebFetch(domain:a-.gov)",
        "WebFetch(domain:irs.gov) ",
        "WebFetch(domain: irs.gov)",
        "WebFetch(domain:irs.gov,Bash)",
        "WebFetch(domain:irs.gov)(x)",
        "WebFetch(url:https://irs.gov)",
        "WebFetch(irs.gov)",
        "Read(domain:irs.gov)",
        "Bash(domain:irs.gov)",
        "mcp__x(domain:irs.gov)",
    ],
)
def test_claude_rejects_malformed_or_unscoped_tool_specifiers(claude_ws, entry):
    write_allow(claude_ws, ["Read", entry])
    with pytest.raises(ValueError):
        claude.allowed_tools(claude_ws)


def test_claude_run_fails_closed_before_launching_anything(claude_ws):
    write_allow(claude_ws, ["Read", "Bash"])
    # the autouse fixture makes any process start raise AssertionError, not ValueError
    with pytest.raises(ValueError, match="Bash"):
        ClaudeRunner("sonnet").run(claude_ws, "Start a session.", 5)


def test_claude_ceiling_applies_to_every_unapproved_name_not_just_the_first(claude_ws):
    write_allow(claude_ws, ["Read", "Bash", "Agent"])
    with pytest.raises(ValueError) as caught:
        ClaudeRunner("sonnet").build_args(claude_ws, "hi there")
    assert "Bash" in str(caught.value) and "Agent" in str(caught.value)


@pytest.mark.parametrize("prompt", ["", "   ", "--help", "  -p x", "a\x00b"])
def test_prompts_that_look_like_flags_are_rejected(claude_ws, prompt):
    with pytest.raises(ValueError):
        ClaudeRunner("sonnet").build_args(claude_ws, prompt)
    with pytest.raises(ValueError):
        OpenCodeRunner("openai/gpt-6.1-sol").build_args(prompt)


@pytest.mark.parametrize("model", ["", "--bare", "a b", "x;rm", "$(id)"])
def test_bad_model_names_are_rejected(model):
    with pytest.raises(ValueError):
        ClaudeRunner(model)


def test_claude_env_drops_billing_keys_only():
    env = ClaudeRunner("sonnet").build_env(
        {"PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t"}
    )
    assert env == {"PATH": "/bin", "HOME": "/h"}


def test_claude_run_passes_workspace_timeout_and_records_version(claude_ws, monkeypatch):
    rec = Recorder(real("claude_seed_guard.jsonl"))
    monkeypatch.setattr(_proc, "run_process", rec)
    runner = ClaudeRunner("sonnet")
    assert runner.cli_version == "fake 1.2.3"
    raw = runner.run(claude_ws, "Start a session.", 77)
    call = rec.calls[0]
    assert call["cwd"] == claude_ws and call["timeout_s"] == 77
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert raw == RawRun(0, real("claude_seed_guard.jsonl"), "", False, 47509, None)


# ------------------------------------------------------------------ OpenCode runner


def test_opencode_standalone_no_auto():
    args = OpenCodeRunner("openai/gpt-6.1-sol").build_args("Start a session.")
    assert args == [
        "opencode", "run", "--standalone", "--agent", "tax-advisor",
        "--model", "openai/gpt-6.1-sol", "--format", "json", "Start a session.",
    ]
    assert "--auto" not in args


@pytest.mark.parametrize("model", ["anthropic/claude-sonnet-5-5", "sonnet", "gpt-6.1-sol", "openaix/y"])
def test_opencode_only_accepts_openai_models(model):
    with pytest.raises(ValueError):
        OpenCodeRunner(model)


def test_opencode_isolated_env(tmp_path):
    root = tmp_path / "root"
    base = {
        "PATH": "/bin",
        "HOME": "/real/home",
        "OPENAI_API_KEY": "k",
        "ANTHROPIC_API_KEY": "k2",
        "OPENCODE_CONFIG_CONTENT": "{}",
        "OPENCODE_CONFIG": "/x.json",
        "XDG_CONFIG_HOME": "/real/home/.config",
    }
    env = opencode.isolation_env(root, base)
    assert env["HOME"] == str(root / "home") and not any((root / "home").iterdir())
    assert env["XDG_CONFIG_HOME"] == str(root / "config") and not any((root / "config").iterdir())
    assert env["XDG_DATA_HOME"] == "/real/home/.local/share"
    assert env["XDG_STATE_HOME"] == "/real/home/.local/state"
    assert env["XDG_CACHE_HOME"] == "/real/home/.cache"
    assert env["PATH"] == "/bin"
    for dropped in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG"):
        assert dropped not in env


def test_opencode_isolated_env_keeps_explicit_data_dirs(tmp_path):
    base = {"HOME": "/real/home", "XDG_DATA_HOME": "/d", "XDG_STATE_HOME": "/s", "XDG_CACHE_HOME": "/c"}
    env = opencode.isolation_env(tmp_path, base)
    assert (env["XDG_DATA_HOME"], env["XDG_STATE_HOME"], env["XDG_CACHE_HOME"]) == ("/d", "/s", "/c")


def test_opencode_run_uses_a_fresh_env_per_run_and_cleans_up(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    seen = []

    def fake(args, *, cwd, env, timeout_s):
        seen.append((cwd, timeout_s, env["HOME"], env["XDG_CONFIG_HOME"]))
        assert Path(env["HOME"]).is_dir() and Path(env["XDG_CONFIG_HOME"]).is_dir()
        return 0, real("opencode_denied_patch.jsonl"), "", False

    monkeypatch.setattr(_proc, "run_process", fake)
    runner = OpenCodeRunner("openai/gpt-6.1-sol")
    assert runner.cli_version == "fake 1.2.3"
    raw = runner.run(ws, "Start a session.", 55)
    runner.run(ws, "Start a session.", 55)
    assert seen[0][:2] == (ws, 55) and seen[0][2:] != seen[1][2:]
    assert not Path(seen[0][2]).exists()
    assert raw.tokens == 17787 and raw.error_kind is None and not raw.timed_out


def test_timeout_never_sets_an_error_kind(claude_ws, monkeypatch):
    monkeypatch.setattr(_proc, "run_process", Recorder("", code=-9, timed_out=True))
    raw = ClaudeRunner("sonnet").run(claude_ws, "Start a session.", 5)
    assert raw.timed_out and raw.error_kind is None


# ------------------------------------------------------------------ process plumbing


def test_no_shell_true_anywhere_in_the_runners():
    for path in (REPO / "evals" / "harness" / "runners").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                pytest.fail(f"{path.name}: shell= keyword")
            if isinstance(node, ast.Attribute) and node.attr in {"system", "popen"} and getattr(node.value, "id", "") == "os":
                pytest.fail(f"{path.name}: os.{node.attr}")


def _closed_pipe(data: bytes):
    """A read end already holding data and at EOF, like a finished child's stdout."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    return os.fdopen(read_fd, "rb")


class _Stdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    def close(self):
        self.closed = True


class FakeProc:
    """Stands in for Popen: real pipe fds for stdout and stderr, and wait() instead of
    communicate(); the first wait of a "timeout" process raises TimeoutExpired."""

    pid = 4242
    returncode = -9

    def __init__(self, behavior, out=b"partial out", err=b"partial err"):
        self.behavior = behavior
        self.calls = 0
        self.stdout = _closed_pipe(out)
        self.stderr = _closed_pipe(err)
        self.stdin = _Stdin()

    def wait(self, timeout=None):
        self.calls += 1
        if self.behavior == "timeout" and self.calls == 1:
            raise subprocess.TimeoutExpired("cmd", timeout)
        return self.returncode


def test_run_process_uses_arg_list_new_session_closed_stdin_and_kills_the_group(monkeypatch, tmp_path):
    captured, killed = {}, []

    def fake_popen(args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return FakeProc("timeout")

    monkeypatch.undo()  # drop the autouse stand-ins; this test exercises the real run_process
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(_proc.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    code, out, err, timed_out = _proc.run_process(["echo", "x"], cwd=tmp_path, env={"A": "1"}, timeout_s=3)
    assert isinstance(captured["args"], list)
    kw = captured["kwargs"]
    assert not kw.get("shell") and kw["start_new_session"] is True
    assert kw["stdin"] is subprocess.DEVNULL and kw["cwd"] == tmp_path
    assert kw["env"] == {"A": "1", "PWD": str(tmp_path.resolve())}
    assert killed == [(4242, signal.SIGKILL)]
    assert (timed_out, out, err) == (True, "partial out", "partial err")


def test_run_process_also_kills_the_group_after_a_normal_exit(monkeypatch, tmp_path):
    killed = []
    monkeypatch.undo()
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: FakeProc("ok"))
    monkeypatch.setattr(_proc.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    _code, out, _err, timed_out = _proc.run_process(["echo", "x"], cwd=tmp_path, env={}, timeout_s=3)
    assert killed == [(4242, signal.SIGKILL)] and (timed_out, out) == (False, "partial out")


def test_run_process_passes_input_to_popen_and_writes_it_to_stdin_once(monkeypatch, tmp_path):
    seen = {}

    def fake_popen(args, **kwargs):
        seen["stdin"] = kwargs["stdin"]
        seen["proc"] = FakeProc("timeout")
        return seen["proc"]

    monkeypatch.undo()
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(_proc.os, "killpg", lambda pid, sig: None)
    _proc.run_process(["x"], cwd=tmp_path, env={}, timeout_s=3, input="prompt text")
    assert seen["stdin"] is subprocess.PIPE
    assert seen["proc"].stdin.data == b"prompt text" and seen["proc"].stdin.closed, "stdin is sent once, then closed"


def capture_popen(monkeypatch):
    seen = {}

    def fake_popen(args, **kwargs):
        seen.update(args=args, **kwargs)
        proc = FakeProc("ok", b"", b"")
        proc.pid, proc.returncode = 1, 0
        return proc

    monkeypatch.undo()  # drop the autouse stand-ins; these tests exercise the real run_process
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(_proc.os, "killpg", lambda pid, sig: None)  # never signal the fake pid
    return seen


def test_child_pwd_is_the_resolved_cwd_not_the_parents(monkeypatch, tmp_path):
    """OpenCode locates the project from $PWD, not from the process cwd: a stale PWD sent it
    to the repo instead of the throwaway workspace."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_dir)
    seen = capture_popen(monkeypatch)
    _proc.run_process(["x"], cwd=link, env={"PWD": "/some/other/repo", "KEEP": "1"}, timeout_s=3)
    assert seen["cwd"] == link
    assert seen["env"]["PWD"] == str(real_dir.resolve()) != "/some/other/repo"
    assert seen["env"]["KEEP"] == "1"


def test_child_pwd_is_set_when_the_env_has_none(monkeypatch, tmp_path):
    seen = capture_popen(monkeypatch)
    _proc.run_process(["x"], cwd=tmp_path, env={}, timeout_s=3)
    assert seen["env"] == {"PWD": str(tmp_path.resolve())}


def test_run_process_does_not_mutate_the_callers_env(monkeypatch, tmp_path):
    seen = capture_popen(monkeypatch)
    env = {"PWD": "/stale"}
    _proc.run_process(["x"], cwd=tmp_path, env=env, timeout_s=3)
    assert env == {"PWD": "/stale"} and seen["env"] is not env


SUBPROCESS_LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}


def test_every_subprocess_launch_in_evals_builds_its_env_with_pwd():
    """OpenCode reads $PWD, so any child started from evals/ must get a PWD that matches
    where it runs. Each subprocess.* launch must pass env=with_pwd(...)."""
    launches = 0
    for path in sorted((REPO / "evals").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "os" and node.func.attr in {"system", "popen", "spawnv", "execv"}:
                pytest.fail(f"{path.name}: os.{node.func.attr} bypasses the PWD helper")
            if not (isinstance(owner, ast.Name) and owner.id == "subprocess" and node.func.attr in SUBPROCESS_LAUNCHERS):
                continue
            launches += 1
            env = next((kw.value for kw in node.keywords if kw.arg == "env"), None)
            assert env is not None, f"{path.name}:{node.lineno} subprocess.{node.func.attr} has no env="
            assert "with_pwd" in ast.dump(env), f"{path.name}:{node.lineno} env is not built by with_pwd"
    assert launches >= 3  # _proc.cli_version, _proc.run_process, runner._git (the judge goes through run_process)


def test_cli_version_runs_with_a_pwd_that_matches_its_cwd(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="9.9.9\n", stderr="")

    monkeypatch.undo()
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("PWD", "/some/other/repo")
    monkeypatch.chdir(tmp_path)
    assert _proc.cli_version("x") == "9.9.9"
    assert seen["env"]["PWD"] == str(tmp_path.resolve())


def test_git_helper_runs_with_a_pwd_that_matches_the_repo(monkeypatch, tmp_path):
    import runner as runner_module

    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="abc\n", stderr="")

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    monkeypatch.setenv("PWD", "/some/other/repo")
    assert runner_module._git(tmp_path, "status") == "abc"
    assert seen["cwd"] == tmp_path and seen["env"]["PWD"] == str(tmp_path.resolve())


def test_both_runners_hand_the_workspace_to_run_process(claude_ws, monkeypatch):
    # run_process owns PWD; the runners must pass the workspace as cwd so it can set it
    rec = Recorder(real("opencode_denied_patch.jsonl"))
    monkeypatch.setattr(_proc, "run_process", rec)
    ClaudeRunner("sonnet").run(claude_ws, "Start a session.", 5)
    OpenCodeRunner("openai/gpt-6.1-sol").run(claude_ws, "Start a session.", 5)
    assert [c["cwd"] for c in rec.calls] == [claude_ws, claude_ws]


def test_cli_version_reads_first_line_and_degrades_to_unknown(monkeypatch):
    monkeypatch.undo()

    def ok(*a, **k):
        return subprocess.CompletedProcess(a[0], 0, stdout="2.1.311 (Claude Code)\n", stderr="")

    monkeypatch.setattr(subprocess, "run", ok)
    assert _proc.cli_version("claude") == "2.1.311 (Claude Code)"

    def missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert _proc.cli_version("claude") == "unknown"

    def failing(*a, **k):
        return subprocess.CompletedProcess(a[0], 1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", failing)
    assert _proc.cli_version("claude") == "unknown"


# ------------------------------------------------------------------ error_kind mapping


def claude_events(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"


OK_RESULT = {"type": "result", "subtype": "success", "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 2}}


def claude_kind(stdout, exit_code=0):
    return claude.error_kind_from(stdout, exit_code)


def test_claude_error_kind_from_real_captures():
    assert claude_kind(real("claude_noauth_error.jsonl")) == "auth"
    assert claude_kind(real("claude_seed_guard.jsonl")) is None
    assert claude_kind(real("claude_judge_structured.jsonl")) is None


@pytest.mark.parametrize(
    "stream, expected",
    [
        ([{"type": "assistant", "error": "rate_limit", "message": {}}, {"type": "result", "is_error": True}], "rate_limit"),
        ([{"type": "assistant", "error": "authentication_failed"}, {"type": "result", "is_error": True}], "auth"),
        ([{"type": "assistant", "error": "server_error"}, {"type": "result", "is_error": True}], "other"),
        ([{"type": "assistant", "error": "billing_error"}, {"type": "result", "is_error": True}], "other"),
        ([{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}, OK_RESULT], "rate_limit"),
        ([{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}, OK_RESULT], None),
        ([{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}, OK_RESULT], None),
        ([{"type": "result", "is_error": True, "api_error_status": 500}], "other"),
        ([{"type": "assistant", "error": "authentication_failed"},
          {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}, OK_RESULT], "rate_limit"),
        ([OK_RESULT], None),
        ([], "other"),
    ],
)
def test_claude_error_kind_mapping(stream, expected):
    assert claude_kind(claude_events(*stream)) == expected


def test_claude_tool_errors_and_agent_prose_are_not_run_errors():
    tool_error = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": True, "content": "rate_limit authentication_failed"}]},
    }
    prose = {"type": "assistant", "message": {"content": [{"type": "text", "text": "error: rate_limit, not logged in"}]}}
    assert claude_kind(claude_events(tool_error, prose, OK_RESULT)) is None


SUCCESS_RESULT = {"type": "result", "subtype": "success", "is_error": False, "usage": {"input_tokens": 1, "output_tokens": 2}}
SERVER_ERROR = {"type": "assistant", "error": "server_error", "message": {}}


def test_a_server_error_that_claude_retried_to_a_successful_result_is_not_an_error():
    stream = claude_events(SERVER_ERROR, SUCCESS_RESULT)
    assert claude_kind(stream) is None
    assert claude.recovered_note(stream) == "recovered: server_error x1"


@pytest.mark.parametrize(
    "result",
    [
        {"type": "result", "subtype": "error_during_execution", "is_error": False},
        {"type": "result", "subtype": "error_max_turns", "is_error": False},
        {"type": "result", "subtype": "error_max_budget_usd", "is_error": False},
        {"type": "result", "is_error": False},
        {"type": "result", "subtype": None, "is_error": False},
    ],
)
def test_a_result_that_is_not_subtype_success_is_not_a_completed_run(result):
    assert claude_kind(claude_events(result)) == "other"
    assert claude_kind(claude_events(SERVER_ERROR, result)) == "other"
    assert claude.recovered_note(claude_events(SERVER_ERROR, result)) == ""


def test_success_subtype_with_is_error_true_is_other():
    assert claude_kind(claude_events({"type": "result", "subtype": "success", "is_error": True})) == "other"


@pytest.mark.parametrize("is_error", [False, None])
def test_success_subtype_whose_is_error_is_not_true_completes(is_error):
    result = {"type": "result", "subtype": "success"}
    if is_error is not None:
        result["is_error"] = is_error
    assert claude_kind(claude_events(result), 0) is None


@pytest.mark.parametrize("code", [1, 2, 137, -9])
def test_a_successful_result_with_a_nonzero_exit_is_other(code):
    assert claude_kind(claude_events(SUCCESS_RESULT), code) == "other"
    assert claude_kind(claude_events(SERVER_ERROR, SUCCESS_RESULT), code) == "other"


def test_a_successful_result_with_exit_zero_has_no_error_kind():
    assert claude_kind(claude_events(SUCCESS_RESULT), 0) is None
    assert claude_kind(claude_events(SERVER_ERROR, SUCCESS_RESULT), 0) is None, "a recovered assistant error stays recovered"


def test_structured_rate_limit_and_auth_outrank_a_nonzero_exit():
    rejected = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}
    assert claude_kind(claude_events(rejected, SUCCESS_RESULT), 1) == "rate_limit"
    auth = {"type": "assistant", "error": "authentication_failed"}
    assert claude_kind(claude_events(auth), 1) == "auth"
    assert claude_kind(claude_events(auth, {"type": "result", "subtype": "success", "is_error": True}), 1) == "auth"
    assert claude_kind(claude_events({"type": "assistant", "error": "rate_limit"}), 1) == "rate_limit"
    assert claude_kind(claude_events(SERVER_ERROR), 1) == "other"


def test_claude_runner_passes_the_exit_code_into_the_error_kind(claude_ws, monkeypatch):
    monkeypatch.setattr(_proc, "run_process", Recorder(claude_events(SUCCESS_RESULT), code=1))
    raw = ClaudeRunner("sonnet").run(claude_ws, "Start a session.", 5)
    assert raw.error_kind == "other" and raw.exit_code == 1
    monkeypatch.setattr(_proc, "run_process", Recorder(claude_events(SUCCESS_RESULT), code=0))
    assert ClaudeRunner("sonnet").run(claude_ws, "Start a session.", 5).error_kind is None


def test_a_server_error_with_no_result_is_other():
    stream = claude_events(SERVER_ERROR)
    assert claude_kind(stream) == "other"
    assert claude.recovered_note(stream) == ""


def test_an_authentication_failure_with_no_result_is_auth():
    assert claude_kind(claude_events({"type": "assistant", "error": "authentication_failed"})) == "auth"


def test_a_rate_limit_rejection_with_no_result_is_rate_limit():
    rejected = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}
    assert claude_kind(claude_events(rejected)) == "rate_limit"
    assert claude_kind(claude_events({"type": "assistant", "error": "rate_limit"})) == "rate_limit"


def test_assistant_errors_before_a_failed_result_keep_the_existing_priority():
    failed = {"type": "result", "subtype": "success", "is_error": True}
    assert claude_kind(claude_events(SERVER_ERROR, failed)) == "other"
    auth = {"type": "assistant", "error": "authentication_failed"}
    assert claude_kind(claude_events(SERVER_ERROR, auth, failed)) == "auth"
    assert claude.recovered_note(claude_events(SERVER_ERROR, failed)) == ""


def test_every_kind_of_recovered_assistant_error_is_counted_in_the_note():
    stream = claude_events(
        SERVER_ERROR,
        {"type": "assistant", "error": "rate_limit"},
        SERVER_ERROR,
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "server_error"}]}},
        SUCCESS_RESULT,
    )
    assert claude_kind(stream) is None
    assert claude.recovered_note(stream) == "recovered: rate_limit x1, server_error x2"
    assert claude.recovered_note(claude_events(SUCCESS_RESULT)) == ""


def test_claude_runner_returns_the_recovered_note_and_no_error_kind(claude_ws, monkeypatch):
    monkeypatch.setattr(_proc, "run_process", Recorder(claude_events(SERVER_ERROR, SUCCESS_RESULT)))
    raw = ClaudeRunner("sonnet").run(claude_ws, "Start a session.", 5)
    assert raw.error_kind is None and raw.note == "recovered: server_error x1"


def test_a_recovered_run_is_a_scored_row_not_an_error_row(tmp_path, monkeypatch):
    scenario = load_scenario(REPO / "evals" / "scenarios" / "seed-enforcement" / "scenario.toml")
    stream = real("claude_seed_guard.jsonl").splitlines()
    stream.insert(3, json.dumps(SERVER_ERROR))
    monkeypatch.setattr(
        _proc, "run_process", lambda args, *, cwd, env, timeout_s: (0, "\n".join(stream).replace("<WORKSPACE>", str(cwd)) + "\n", "", False)
    )
    res = run_scenario(
        scenario, tool="claude", model="m", runner=ClaudeRunner("sonnet"), run_index=0, sample=SAMPLE,
        answer_key={}, agent_sha="a" * 40, denylist=[], transcripts_dir=tmp_path / "t", repo_root=tmp_path,
    )
    assert res.status == "pass", res.note
    assert "recovered: server_error x1" in res.note


def test_opencode_error_kind_from_real_captures():
    assert opencode.error_kind_from(real("opencode_noauth_error.jsonl"), 1) == "auth"
    # observed 2026-11-04 on OpenCode 2.1.4 when the ChatGPT plan's usage limit was reached
    assert opencode.error_kind_from(real("opencode_quota_error.jsonl"), 1) == "rate_limit"
    for name in ("opencode_denied_patch", "opencode_allowed_patch", "opencode_question_cancelled"):
        assert opencode.error_kind_from(real(name + ".jsonl"), 0) is None


@pytest.mark.parametrize(
    "error_type, expected",
    [
        ("provider.no-route", "auth"),
        ("provider.authentication", "auth"),
        ("provider.rate-limit", "rate_limit"),
        ("provider.rate_limit", "rate_limit"),
        ("Provider.Quota-Exceeded", "rate_limit"),
        ("provider.quota", "rate_limit"),
        ("provider.invalid-request", "other"),
        ("transport.timeout", "other"),
    ],
)
def test_opencode_error_kind_mapping(error_type, expected):
    line = json.dumps({"type": "error", "error": {"type": error_type, "message": "x"}})
    assert opencode.error_kind_from(line, 1) == expected


def test_opencode_error_event_with_a_plain_string_is_other():
    assert opencode.error_kind_from(json.dumps({"type": "error", "error": "plain string"}), 1) == "other"


# A real run ends with a `text` part after its last `step_finish(tool-calls)`; the final
# step of a captured run never emits a step_finish of its own. Without that trailing text
# there is no evidence the run completed.
DENIED_LINES = real("opencode_denied_patch.jsonl").splitlines()
# 0 step_start, 1 text, 2 read, 3 finish, 4 step_start, 5 read, 6 finish,
# 7 step_start, 8 patch (denied), 9 finish, 10 step_start, 11 text (final)


def transcript(upto):
    return "\n".join(DENIED_LINES[:upto]) + "\n"


@pytest.mark.parametrize("exit_code", [0, 1])
def test_opencode_empty_output_is_a_run_failure(exit_code):
    assert opencode.error_kind_from("", exit_code) == "other"
    assert opencode.error_kind_from("\nnot json\n[1]\n", exit_code) == "other"


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize(
    "upto, where",
    [
        (1, "after the first step_start"),
        (3, "inside a tool call that has not finished"),
        (7, "after an intermediate step_finish with tokens"),
        (9, "after the denied patch, before its step_finish"),
        (10, "after the last intermediate step_finish"),
        (11, "after the final step_start, before any text"),
    ],
)
def test_opencode_interrupted_transcripts_are_run_failures(upto, where, exit_code):
    assert opencode.error_kind_from(transcript(upto), exit_code) == "other", where


def test_opencode_nonzero_exit_after_an_intermediate_step_finish_is_a_run_failure():
    stdout = transcript(7)
    assert opencode.tokens_from(stdout) is not None  # token-bearing, yet not a completed run
    assert opencode.error_kind_from(stdout, 1) == "other"
    assert opencode.error_kind_from(stdout, 0) == "other"


def test_opencode_unfinished_tool_call_is_a_run_failure():
    running = json.loads(DENIED_LINES[8])
    running["part"]["state"] = {"status": "running", "input": running["part"]["state"]["input"]}
    stdout = "\n".join([*DENIED_LINES[:8], json.dumps(running)]) + "\n"
    assert opencode.error_kind_from(stdout, 0) == "other"


@pytest.mark.parametrize("exit_code", [1, 2, 137, -9, -15])
def test_opencode_completion_needs_a_clean_exit(exit_code):
    stdout = real("opencode_denied_patch.jsonl")
    assert opencode.error_kind_from(stdout, 0) is None
    assert opencode.error_kind_from(stdout, exit_code) == "other"


@pytest.mark.parametrize("exit_code", [1, -9])
def test_opencode_commentary_prefix_with_a_bad_exit_is_a_run_failure(exit_code):
    # step_start + the model's opening commentary, then the process died: the trailing text
    # part alone must not read as a final answer.
    stdout = "\n".join(DENIED_LINES[:2]) + "\n"
    assert opencode.error_kind_from(stdout, 0) is None  # residual heuristic, see opencode.completed
    assert opencode.error_kind_from(stdout, exit_code) == "other"


@pytest.mark.parametrize("exit_code", [1, -9])
def test_opencode_structured_error_still_beats_a_bad_exit(exit_code):
    line = json.dumps({"type": "error", "error": {"type": "provider.rate-limit", "message": "x"}})
    assert opencode.error_kind_from(line, exit_code) == "rate_limit"
    line = json.dumps({"type": "error", "error": {"type": "provider.no-route", "message": "x"}})
    assert opencode.error_kind_from(line, exit_code) == "auth"


@pytest.mark.parametrize("exit_code", [1, -9])
def test_killed_opencode_run_with_a_denial_in_it_is_an_error_row_not_a_pass(tmp_path, monkeypatch, exit_code):
    # the transcript holds the denied seed patch, so it would score "pass" if the exit were ignored
    res = enforce(tmp_path, monkeypatch, "opencode", OpenCodeRunner("openai/gpt-6.1-sol"),
                  lambda cwd: real("opencode_denied_patch.jsonl"), code=exit_code)
    assert res.status == "error" and "error_kind=other" in res.note
    assert res.status != "pass"


def test_claude_stream_without_a_result_event_is_a_run_failure_at_any_exit():
    full = real("claude_seed_guard.jsonl")
    cut = "\n".join(ln for ln in full.splitlines() if json.loads(ln).get("type") != "result") + "\n"
    assert claude_kind(full) is None
    assert claude_kind(cut) == "other"
    assert claude_kind("") == "other"


@pytest.mark.parametrize("exit_code", [1, -9])
def test_killed_claude_run_with_a_guard_denial_is_an_error_row_not_a_pass(tmp_path, monkeypatch, exit_code):
    cut_lines = [ln for ln in real("claude_seed_guard.jsonl").splitlines() if json.loads(ln).get("type") != "result"]
    res = enforce(tmp_path, monkeypatch, "claude", ClaudeRunner("sonnet"),
                  lambda cwd: "\n".join(cut_lines).replace("<WORKSPACE>", str(cwd)) + "\n", code=exit_code)
    assert res.status == "error" and "error_kind=other" in res.note


def test_opencode_tool_free_run_is_complete_with_only_step_start_and_text():
    # The shape of the captured skills-listing run: no tool call, no step_finish at all.
    lines = [
        {"type": "step_start", "part": {"type": "step-start"}},
        {"type": "text", "part": {"type": "text", "text": "Done."}},
    ]
    stdout = "\n".join(json.dumps(x) for x in lines) + "\n"
    assert opencode.error_kind_from(stdout, 0) is None
    assert opencode.error_kind_from(stdout.replace("Done.", "  "), 0) == "other"


def test_opencode_terminal_step_finish_also_counts_as_complete():
    # AI SDK finish reason "stop"; not seen in the captures, which end on the text part.
    lines = DENIED_LINES[:10] + [
        json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
        json.dumps({"type": "step_finish", "part": {"type": "step-finish", "reason": "stop", "tokens": {"input": 1}}}),
    ]
    assert opencode.error_kind_from("\n".join(lines) + "\n", 0) is None


def test_opencode_permission_denial_inside_a_completed_run_is_not_a_run_error():
    assert opencode.error_kind_from(real("opencode_denied_patch.jsonl"), 0) is None
    # The same denial in a run that was cut off is still a failure of the run itself.
    assert opencode.error_kind_from(transcript(10), 0) == "other"
    tool = {"type": "tool_use", "part": {"type": "tool", "id": "c", "tool": "patch",
                                         "state": {"status": "error", "error": "Permission denied: edit"}}}
    assert opencode.error_kind_from(json.dumps(tool), 0) == "other"


def test_opencode_error_event_still_wins_over_completion():
    stdout = real("opencode_denied_patch.jsonl") + json.dumps(
        {"type": "error", "error": {"type": "provider.rate-limit", "message": "x"}}
    )
    assert opencode.error_kind_from(stdout, 1) == "rate_limit"


def test_interrupted_opencode_output_becomes_an_error_row(tmp_path, monkeypatch):
    res = enforce(tmp_path, monkeypatch, "opencode", OpenCodeRunner("openai/gpt-6.1-sol"),
                  lambda cwd: transcript(10))
    assert res.status == "error" and "other" in res.note


# ------------------------------------------------------------------ tokens


def test_token_counts_from_real_captures():
    assert claude.tokens_from(real("claude_seed_guard.jsonl")) == 6 + 7080 + 38981 + 1442
    assert claude.tokens_from("") is None
    assert claude.tokens_from(claude_events({"type": "result", "usage": "n/a"})) is None
    assert opencode.tokens_from(real("opencode_denied_patch.jsonl")) == 17787
    assert opencode.tokens_from(real("opencode_noauth_error.jsonl")) is None


def test_opencode_tokens_sum_every_category():
    event = {"type": "step_finish", "part": {"type": "step-finish",
             "tokens": {"input": 1, "output": 2, "reasoning": 4, "cache": {"read": 8, "write": 16}}}}
    assert opencode.tokens_from(json.dumps(event)) == 31


# ------------------------------------------------------------------ parsing the real captures


def test_opencode_denial_is_the_captured_permission_denied_text():
    assert events.OPENCODE_DENIED.match("Permission denied: edit")
    stdout = real("opencode_denied_patch.jsonl")
    assert parse_events("opencode", stdout) == [
        ToolEvent("read", "README_TAX_AGENT.md", False),
        ToolEvent("read", SEED, False),
        ToolEvent("patch", SEED, True),
    ]
    assert "blocked" in final_text("opencode", stdout)


def test_opencode_allowed_patch_is_not_a_denial():
    stdout = real("opencode_allowed_patch.jsonl")
    assert parse_events("opencode", stdout) == [ToolEvent("patch", "00_CONTROL/smoke.md", False)]


def test_opencode_question_tool_is_cancelled_not_denied():
    stdout = real("opencode_question_cancelled.jsonl")
    assert parse_events("opencode", stdout) == [ToolEvent("question", None, False)]
    assert "cannot ask the user questions" in stdout


def test_opencode_patch_targets_come_from_patch_text():
    patch = (
        "*** Begin Patch\n*** Add File: a/new.md\n+x\n*** Update File: 01_STRATEGY/STRATEGY_SEED.md\n"
        "@@\n+TEST\n*** Delete File: old.md\n*** Update File: b.md\n*** Move to: c.md\n*** End Patch"
    )
    obj = {"type": "tool_use", "part": {"type": "tool", "id": "c1", "tool": "patch",
           "state": {"status": "error", "error": "Permission denied: edit", "input": {"patchText": patch}}}}
    parsed = parse_events("opencode", json.dumps(obj))
    assert [(e.tool_name, e.target, e.denied) for e in parsed] == [
        ("patch", t, True) for t in ("a/new.md", SEED, "old.md", "b.md", "c.md")
    ]


def test_opencode_patch_without_files_and_old_call_id_key_still_parse():
    obj = {"type": "tool_use", "part": {"type": "tool", "callID": "c1", "tool": "patch",
           "state": {"status": "completed", "input": {"patchText": "garbage"}}}}
    assert parse_events("opencode", json.dumps(obj)) == [ToolEvent("patch", None, False)]


def test_claude_seed_capture_has_the_marker_denial_for_both_spellings():
    stdout = real("claude_seed_guard.jsonl")
    assert parse_events("claude", stdout) == [
        ToolEvent("Write", "<WORKSPACE>/00_CONTROL/smoke.md", False),
        ToolEvent("Read", "<WORKSPACE>/" + SEED, False),
        ToolEvent("Read", "<WORKSPACE>/01_strategy/strategy_seed.md", False),
        ToolEvent("Edit", "<WORKSPACE>/" + SEED, True),
        ToolEvent("Edit", "<WORKSPACE>/01_strategy/strategy_seed.md", True),
    ]
    assert events.GUARD_MARKER in stdout


def test_real_fixtures_contain_no_home_paths_or_large_numbers():
    for path in REAL.glob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text and "/home/" not in text, path.name
        assert not scan_text(text, []), path.name


UUID_SHAPE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# The last group ends in a hex letter: an all-digit 12-character group would trip the
# privacy scanner's "digits" rule (9 or more digits in a row).
UUID_PLACEHOLDER = re.compile(r"00000000-0000-0000-0000-0{11}[a-f]\Z")
OPAQUE_ID = re.compile(r"\b(ses|msg|prt|call|toolu|req|srvtoolu|fc)_([A-Za-z0-9_]+)")


@pytest.mark.parametrize("path", sorted(REAL.glob("*.jsonl")), ids=lambda p: p.name)
def test_real_fixtures_hold_only_placeholder_identifiers(path):
    text = path.read_text(encoding="utf-8")
    assert "/Users/" not in text and "/home/" not in text
    for found in UUID_SHAPE.findall(text):
        assert UUID_PLACEHOLDER.match(found), f"{path.name}: real-looking UUID {found}"
    for prefix, rest in OPAQUE_ID.findall(text):
        assert re.fullmatch(r"FIXTURE_\d+", rest), f"{path.name}: opaque id {prefix}_{rest}"


@pytest.mark.parametrize("path", sorted(REAL.glob("claude_*.jsonl")), ids=lambda p: p.name)
def test_claude_fixture_tool_use_and_result_ids_still_pair(path):
    uses, results = set(), set()
    for obj in (json.loads(line) for line in path.read_text().splitlines()):
        for block in (obj.get("message") or {}).get("content", []) if isinstance(obj.get("message"), dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                uses.add(block["id"])
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                results.add(block["tool_use_id"])
    assert results <= uses
    assert all(re.fullmatch(r"toolu_FIXTURE_\d+", i) for i in uses | results)


def test_judge_payload_reads_the_real_structured_result():
    import judge

    stream = [json.loads(line) for line in real("claude_judge_structured.jsonl").splitlines()]
    result = next(o for o in stream if o["type"] == "result")
    assert set(result["structured_output"]) == {"pass", "reason", "quotes"}
    assert judge._payload(json.dumps(result)) == result["structured_output"]
    without_structured = dict(result)
    del without_structured["structured_output"]
    assert judge._payload(json.dumps(without_structured)) == result["structured_output"]


# ------------------------------------------------------------------ end to end with real captures


def enforce(tmp_path, monkeypatch, tool, runner, stdout_for, code=0):
    """Run the real seed-enforcement scenario against a runner whose process output is a
    captured transcript (with the workspace path filled in) and exit code."""
    scenario = load_scenario(REPO / "evals" / "scenarios" / "seed-enforcement" / "scenario.toml")

    def fake(args, *, cwd, env, timeout_s):
        return code, stdout_for(cwd), "", False

    monkeypatch.setattr(_proc, "run_process", fake)
    return run_scenario(
        scenario,
        tool=tool,
        model="m",
        runner=runner,
        run_index=0,
        sample=SAMPLE,
        answer_key={},
        agent_sha="a" * 40,
        denylist=[],
        transcripts_dir=tmp_path / "transcripts",
        repo_root=tmp_path,
    )


def test_seed_enforcement_passes_on_the_real_opencode_denial(tmp_path, monkeypatch):
    res = enforce(tmp_path, monkeypatch, "opencode", OpenCodeRunner("openai/gpt-6.1-sol"),
                  lambda cwd: real("opencode_denied_patch.jsonl"))
    assert res.status == "pass", res.note
    assert res.tokens == 17787 and res.cli_version == "fake 1.2.3"


def test_seed_enforcement_passes_on_the_real_claude_hook_denial(tmp_path, monkeypatch):
    res = enforce(tmp_path, monkeypatch, "claude", ClaudeRunner("sonnet"),
                  lambda cwd: real("claude_seed_guard.jsonl").replace("<WORKSPACE>", str(cwd)))
    assert res.status == "pass", res.note
    assert res.tokens == 47509


def test_seed_enforcement_is_inconclusive_when_opencode_never_attempts(tmp_path, monkeypatch):
    res = enforce(tmp_path, monkeypatch, "opencode", OpenCodeRunner("openai/gpt-6.1-sol"),
                  lambda cwd: real("opencode_allowed_patch.jsonl"))
    assert res.status == "inconclusive"


def test_no_auth_capture_becomes_an_error_row(tmp_path, monkeypatch):
    scenario = load_scenario(REPO / "evals" / "scenarios" / "seed-enforcement" / "scenario.toml")

    def fake(args, *, cwd, env, timeout_s):
        return 1, real("opencode_noauth_error.jsonl"), "", False

    monkeypatch.setattr(_proc, "run_process", fake)
    res = run_scenario(
        scenario, tool="opencode", model="m", runner=OpenCodeRunner("openai/gpt-6.1-sol"), run_index=0,
        sample=SAMPLE, answer_key={}, agent_sha="a" * 40, denylist=[], transcripts_dir=tmp_path / "t",
        repo_root=tmp_path,
    )
    assert res.status == "error" and "auth" in res.note
