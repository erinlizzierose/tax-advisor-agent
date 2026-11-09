"""Zero-cost probe against the real OpenCode CLI. It never reaches a model: the model name
does not exist, so a run that finds the agent stops at `provider.no-route`.

OpenCode resolves its project from $PWD, not from the process cwd. When the runner left the
parent's PWD in place, OpenCode looked for agents in the repo (not the throwaway workspace)
and answered `Agent not found: "tax-advisor"`. This test catches that regression.

Skipped when `opencode` is not on PATH. Deselect with:  pytest -m "not opencode_probe"

Time-boxed: the runner kills the CLI's whole process group after PROBE_TIMEOUT_S, and a hard
limit above that abandons the step as a failure, so this test can fail but never hang the suite.
"""

import shutil

import pytest

from events import json_lines
from hard_limit import call_with_hard_limit
from install import install
from runners import _proc
from runners.opencode import OpenCodeRunner

PROBE_TIMEOUT_S = 120
HARD_LIMIT_S = PROBE_TIMEOUT_S + _proc.KILL_GRACE_S + 60  # run + group kill + cleanup headroom

pytestmark = [
    pytest.mark.opencode_probe,
    pytest.mark.skipif(shutil.which("opencode") is None, reason="opencode is not on PATH"),
]


def test_agent_is_found_in_the_workspace_even_when_the_parent_pwd_is_elsewhere(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    install(workspace, {"opencode"})
    monkeypatch.setenv("PWD", str(tmp_path.parent))  # a parent shell sitting somewhere else

    raw = call_with_hard_limit(
        lambda: OpenCodeRunner("openai/does-not-exist").run(workspace, "ping", PROBE_TIMEOUT_S),
        HARD_LIMIT_S,
        "the opencode probe",
    )

    if raw.timed_out:
        pytest.fail(
            f"the opencode probe timed out after {PROBE_TIMEOUT_S} s and its process group was killed; "
            "opencode did not stop at provider.no-route (a stuck CLI or a changed startup path)"
        )
    assert "Agent not found" not in raw.stdout + raw.stderr
    errors = [o for o in json_lines(raw.stdout) if o.get("type") == "error"]
    assert errors, raw.stdout[:500]
    assert errors[0]["error"]["type"] == "provider.no-route"
    assert raw.error_kind == "auth"
    assert raw.tokens is None
