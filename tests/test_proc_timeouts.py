"""Nothing in the suite may wait forever on a real process.

`_proc.run_process` is what the opencode probe uses: it gives the child its own process group,
kills the whole group on timeout and never waits unboundedly afterwards. These tests start small
real Python processes (no CLI, no model) to prove that, and cover the hard-limit helper the probe
uses as a last resort.
"""

import os
import subprocess
import sys
import time

import pytest

import runner
from hard_limit import call_with_hard_limit
from runners import _proc

SLEEPER = "import time; time.sleep(120)"
GRANDCHILD_SLEEP = "import time; time.sleep(30)"


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie that nobody reaped yet is as good as gone
        stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(stat.strip()) and not stat.strip().startswith("Z")


def wait_gone(pid, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)


def test_run_process_kills_the_child_and_its_grandchildren_on_timeout(tmp_path):
    script = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {SLEEPER!r}])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    grandchild = None
    try:
        started = time.monotonic()
        code, out, _err, timed_out = _proc.run_process(
            [sys.executable, "-c", script], cwd=tmp_path, env=os.environ, timeout_s=3
        )
        assert timed_out is True and time.monotonic() - started < 40
        grandchild = int(out.split()[0])
        assert wait_gone(grandchild), "the grandchild survived the group kill"
    finally:
        if grandchild is not None and alive(grandchild):
            os.kill(grandchild, 9)


def test_run_process_does_not_wait_for_a_daemon_that_keeps_the_pipes_open(tmp_path, monkeypatch):
    """A helper that detaches into its own session survives the group kill and keeps the
    child's stdout open. run_process must still return, after the timeout plus the grace."""
    monkeypatch.setattr(_proc, "KILL_GRACE_S", 1)
    pidfile = tmp_path / "daemon.pid"
    script = (
        "import subprocess, sys, time\n"
        f"d = subprocess.Popen([sys.executable, '-c', {SLEEPER!r}], start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(d.pid))\n"
        "time.sleep(120)\n"
    )
    try:
        started = time.monotonic()
        _code, _out, _err, timed_out = _proc.run_process(
            [sys.executable, "-c", script], cwd=tmp_path, env=os.environ, timeout_s=3
        )
        assert timed_out is True and time.monotonic() - started < 30
    finally:
        if pidfile.exists():
            pid = int(pidfile.read_text())
            if alive(pid):
                os.kill(pid, 9)


def test_a_child_that_finishes_in_time_is_not_reported_as_timed_out(tmp_path):
    code, out, _err, timed_out = _proc.run_process(
        [sys.executable, "-c", "print('ok')"], cwd=tmp_path, env=os.environ, timeout_s=30
    )
    assert (code, out.strip(), timed_out) == (0, "ok", False)


def test_run_process_kills_a_grandchild_left_behind_by_a_child_that_exited_normally(tmp_path):
    """E-F7: a helper the child leaves running (pipes closed, so communicate returns) must not
    outlive run_process, or it could write into the workspace after the 'after' snapshot."""
    late = tmp_path / "late-write.txt"
    grandchild_code = f"import time; time.sleep(3); open({str(late)!r}, 'w').write('late')"
    script = (
        "import subprocess, sys\n"
        f"g = subprocess.Popen([sys.executable, '-c', {grandchild_code!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print(g.pid, flush=True)\n"
    )
    grandchild = None
    try:
        code, out, _err, timed_out = _proc.run_process(
            [sys.executable, "-c", script], cwd=tmp_path, env=os.environ, timeout_s=30
        )
        assert (code, timed_out) == (0, False)
        grandchild = int(out.split()[0])
        assert wait_gone(grandchild), "the grandchild survived a normal exit"
        time.sleep(4)
        assert not late.exists(), "the grandchild wrote after run_process returned"
    finally:
        if grandchild is not None and alive(grandchild):
            os.kill(grandchild, 9)


def test_run_process_ignores_a_group_that_is_already_gone(tmp_path, monkeypatch):
    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(_proc.os, "killpg", gone)
    code, out, _err, timed_out = _proc.run_process(
        [sys.executable, "-c", "print('ok')"], cwd=tmp_path, env=os.environ, timeout_s=30
    )
    assert (code, out.strip(), timed_out) == (0, "ok", False)


def test_run_process_sends_input_on_stdin_and_closes_stdin_otherwise(tmp_path):
    reader = "import sys; print(sys.stdin.read().upper(), end='')"
    code, out, _err, timed_out = _proc.run_process(
        [sys.executable, "-c", reader], cwd=tmp_path, env=os.environ, timeout_s=30, input="hello é\n"
    )
    assert (code, out, timed_out) == (0, "HELLO É\n", False)
    code, out, _err, _ = _proc.run_process([sys.executable, "-c", reader], cwd=tmp_path, env=os.environ, timeout_s=30)
    assert (code, out) == (0, ""), "without input stdin is closed, so a CLI never waits on it"


def test_run_process_with_input_still_times_out_and_kills_the_group(tmp_path):
    started = time.monotonic()
    _code, _out, _err, timed_out = _proc.run_process(
        [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(120)"],
        cwd=tmp_path, env=os.environ, timeout_s=2, input="x" * 200_000,
    )
    assert timed_out is True and time.monotonic() - started < 40


def test_a_grandchild_holding_stdout_open_does_not_make_a_finished_run_time_out(tmp_path):
    """E-F7: the child exits 0 having printed 'done'; its grandchild (same process group) still holds
    the stdout pipe. run_process must wait for the child, not for pipe EOF: not a timeout, and the
    group kill ends the grandchild."""
    script = (
        "import subprocess, sys\n"
        f"g = subprocess.Popen([sys.executable, '-c', {GRANDCHILD_SLEEP!r}])\n"
        "print('done', flush=True)\n"
    )
    started = time.monotonic()
    code, out, _err, timed_out = _proc.run_process(
        [sys.executable, "-c", script], cwd=tmp_path, env=os.environ, timeout_s=20
    )
    assert (code, timed_out) == (0, False)
    assert "done" in out
    assert time.monotonic() - started < 10, "waited for the grandchild instead of the child"


def test_a_detached_grandchild_holding_stdout_open_does_not_stall_run_process(tmp_path, monkeypatch):
    """A helper that calls setsid() cannot be reached by the group kill and keeps the pipe open
    for as long as it lives. run_process still returns promptly, with what was read so far."""
    monkeypatch.setattr(_proc, "PIPE_GRACE_S", 1)
    pidfile = tmp_path / "detached.pid"
    script = (
        "import subprocess, sys\n"
        f"g = subprocess.Popen([sys.executable, '-c', {GRANDCHILD_SLEEP!r}], start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(g.pid))\n"
        "print('done', flush=True)\n"
    )
    try:
        started = time.monotonic()
        code, out, _err, timed_out = _proc.run_process(
            [sys.executable, "-c", script], cwd=tmp_path, env=os.environ, timeout_s=20
        )
        assert (code, timed_out) == (0, False)
        assert "done" in out
        assert time.monotonic() - started < 10
    finally:
        if pidfile.exists():
            pid = int(pidfile.read_text())
            if alive(pid):
                os.kill(pid, 9)


def test_a_timeout_is_reported_only_when_the_main_process_exceeds_it(tmp_path):
    started = time.monotonic()
    code, out, _err, timed_out = _proc.run_process(
        [sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(120)"],
        cwd=tmp_path, env=os.environ, timeout_s=2,
    )
    assert timed_out is True and time.monotonic() - started < 30
    assert "partial" in out, "output read before the kill is kept"


def test_output_larger_than_a_pipe_buffer_and_both_streams_are_collected(tmp_path):
    script = "import sys; sys.stdout.write('o' * 300000); sys.stderr.write('e' * 300000)"
    code, out, err, timed_out = _proc.run_process(
        [sys.executable, "-c", script], cwd=tmp_path, env=os.environ, timeout_s=30
    )
    assert (code, len(out), len(err), timed_out) == (0, 300000, 300000, False)


def test_crlf_output_is_normalized_like_text_mode(tmp_path):
    code, out, _err, _ = _proc.run_process(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\r\\nb\\rc\\n')"],
        cwd=tmp_path, env=os.environ, timeout_s=30,
    )
    assert out == "a\nb\nc\n"


class _Interrupted(subprocess.Popen):
    """A real Popen whose first wait (a long one) and communicate raise KeyboardInterrupt, as
    Ctrl-C does. Only the first process created is affected, so helpers such as `ps` run normally."""

    target = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._armed = type(self).target is None
        if self._armed:
            type(self).target = self

    def wait(self, timeout=None):
        if self._armed and timeout is not None and timeout > 5:
            self._armed = False
            raise KeyboardInterrupt
        return super().wait(timeout)

    def communicate(self, *args, **kwargs):
        if self is type(self).target:
            raise KeyboardInterrupt
        return super().communicate(*args, **kwargs)


def test_ctrl_c_kills_the_childs_process_group_and_propagates(tmp_path, monkeypatch):
    """N6: the child runs in its own session, so an interrupt in the parent must kill it."""
    _Interrupted.target = None
    monkeypatch.setattr(_proc.subprocess, "Popen", _Interrupted)
    try:
        with pytest.raises(KeyboardInterrupt):
            _proc.run_process(
                [sys.executable, "-c", SLEEPER], cwd=tmp_path, env=os.environ, timeout_s=60
            )
        proc = _Interrupted.target
        assert proc is not None
        assert wait_gone(proc.pid), "the child survived the interrupt"
        assert proc.returncode is not None, "the child was not reaped"
    finally:
        proc = _Interrupted.target
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass


def test_without_anthropic_billing_drops_only_the_two_keys_and_copies():
    env = {"PATH": "/bin", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "u", "OTHER": "1"}
    clean = _proc.without_anthropic_billing(env)
    assert clean == {"PATH": "/bin", "ANTHROPIC_BASE_URL": "u", "OTHER": "1"}
    assert len(env) == 5 and clean is not env


def test_claude_isolation_flags_are_one_constant():
    assert _proc.CLAUDE_ISOLATION_FLAGS == ("--setting-sources", "project", "--strict-mcp-config")


def test_hard_limit_returns_the_value_and_reraises_errors():
    assert call_with_hard_limit(lambda: 7, 5, "quick step") == 7
    with pytest.raises(ZeroDivisionError):
        call_with_hard_limit(lambda: 1 / 0, 5, "failing step")


def test_hard_limit_turns_a_stuck_step_into_a_clear_failure():
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match=r"stuck step did not finish within 0\.5 s"):
        call_with_hard_limit(lambda: time.sleep(30), 0.5, "stuck step")
    assert time.monotonic() - started < 10


def test_git_helper_has_a_timeout_and_reports_it_as_a_runtime_error(tmp_path, monkeypatch):
    seen = {}

    def hang(args, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", hang)
    with pytest.raises(RuntimeError, match="timed out"):
        runner._git(tmp_path, "status")
    assert 0 < seen["timeout"] <= 120
