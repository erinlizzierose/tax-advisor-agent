"""Subprocess plumbing shared by the CLI runners. Argument lists only, never a shell."""

from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Mapping

MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#-]*\Z")
VERSION_TIMEOUT_S = 30
KILL_GRACE_S = 10
# After the main process has exited and its group was killed, how long the pipe readers may
# keep going before run_process stops waiting for them.
PIPE_GRACE_S = 3
POLL_S = 0.1
READ_CHUNK = 65536
# Every `claude` child (the agent runner and the judge) is started without these, so a stray key
# cannot move a run from the subscription to API billing, and with these flags, which keep user
# settings, user CLAUDE.md, user agents, plugins and every MCP server out.
ANTHROPIC_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
CLAUDE_ISOLATION_FLAGS = ("--setting-sources", "project", "--strict-mcp-config")


def without_anthropic_billing(env: Mapping[str, str]) -> dict[str, str]:
    """A copy of env without the Anthropic API credentials."""
    return {k: v for k, v in env.items() if k not in ANTHROPIC_BILLING_ENV}


def check_prompt(prompt: str) -> None:
    """A prompt that starts with '-' would be parsed as a CLI flag."""
    if not prompt.strip() or "\x00" in prompt or prompt.lstrip().startswith("-"):
        raise ValueError("prompt must be non-empty, contain no NUL, and not start with '-'")


def check_model(model: str) -> None:
    if not MODEL_PATTERN.match(model):
        raise ValueError(f"unsupported model name: {model!r}")


def cli_version(executable: str) -> str:
    """First line of `<executable> --version`, or 'unknown'."""
    try:
        done = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=with_pwd(os.environ, Path.cwd()),
            timeout=VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    lines = done.stdout.strip().splitlines()
    return lines[0].strip() if done.returncode == 0 and lines else "unknown"


def with_pwd(env: Mapping[str, str], cwd: Path | str) -> dict[str, str]:
    """A copy of env whose PWD is the resolved cwd.

    OpenCode finds its project (agents, config) from $PWD, not from the process cwd. With
    the parent's PWD left in place it looked in the repo instead of the throwaway workspace
    ("Agent not found"), so every child gets a PWD that matches where it really runs."""
    child = dict(env)
    child["PWD"] = str(Path(cwd).resolve())
    return child


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _drain(stream: IO[bytes], sink: bytearray, stop: threading.Event) -> None:
    """Copy everything the pipe yields into sink until EOF or `stop`. Polls with select so the
    thread can be told to give up without closing the pipe from another thread."""
    fd = stream.fileno()
    try:
        while not stop.is_set():
            if not select.select([fd], [], [], POLL_S)[0]:
                continue
            chunk = os.read(fd, READ_CHUNK)
            if not chunk:
                return
            sink.extend(chunk)
    except (OSError, ValueError):
        pass


def _feed(stream: IO[bytes], data: bytes) -> None:
    """Send data on the child's stdin, then close it. A child that exits first breaks the pipe."""
    try:
        stream.write(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _decode(data: bytearray) -> str:
    """UTF-8 with replacement and universal newlines, as text mode would give."""
    return bytes(data).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def run_process(
    args: list[str], *, cwd: Path, env: Mapping[str, str], timeout_s: int, input: str | None = None
) -> tuple[int, str, str, bool]:
    """Run args in cwd. Returns (exit_code, stdout, stderr, timed_out).

    The child gets its own process group, and the whole group is killed when the child times
    out AND when it exits normally, so no grandchild (hook, helper server) can outlive the
    call and write into the workspace after the final snapshot. The same group kill runs if
    the caller is interrupted (Ctrl-C), so no child outlives the harness. stdin is closed so a
    CLI never waits on input, unless `input` is given: then it is sent on stdin. PWD matches cwd.

    The run is over when the MAIN process exits, not when its pipes reach EOF: output is read
    by threads, `timed_out` is True only if the main process itself outlived `timeout_s`, and
    after the exit (and the group kill) the readers get PIPE_GRACE_S to finish. Known limit: a
    descendant that left the group (setsid) is out of reach of the group kill. If it keeps the
    pipes open, the readers are abandoned after the grace period and the output read so far is
    returned; the descendant itself keeps running."""
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=with_pwd(env, cwd),
        stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = bytearray(), bytearray()
    stop = threading.Event()
    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, out, stop), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err, stop), daemon=True),
    ]
    if input is not None:
        readers.append(threading.Thread(target=_feed, args=(proc.stdin, input.encode("utf-8")), daemon=True))
    timed_out = False
    try:
        for thread in readers:
            thread.start()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
        _kill_group(proc.pid)
        if timed_out:
            try:
                proc.wait(timeout=KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                pass
        deadline = time.monotonic() + (KILL_GRACE_S if timed_out else PIPE_GRACE_S)
        for thread in readers:
            thread.join(max(0.0, deadline - time.monotonic()))
    except BaseException:
        _kill_group(proc.pid)
        try:
            proc.wait(timeout=KILL_GRACE_S)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise
    finally:
        stop.set()
        for thread in readers:
            thread.join(POLL_S * 5)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    return proc.returncode, _decode(out), _decode(err), timed_out
