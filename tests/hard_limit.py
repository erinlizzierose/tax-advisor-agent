"""A hard wall-clock limit for a test step that starts a real process, so a stuck CLI becomes a
failure with a clear message instead of a hung suite."""

import threading

import pytest


def call_with_hard_limit(fn, limit_s, what):
    """Run fn() in a daemon thread and return its result. If it has not finished after limit_s
    seconds, fail the test with a message naming `what`; the daemon thread cannot keep pytest
    alive. An exception raised by fn is re-raised."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True, name=f"hard-limit-{what}")
    thread.start()
    thread.join(limit_s)
    if thread.is_alive():
        pytest.fail(f"{what} did not finish within {limit_s} s (hard limit); the step was abandoned, not waited for")
    if "error" in box:
        raise box["error"]
    return box["value"]
