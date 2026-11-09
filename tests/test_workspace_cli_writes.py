"""N3: files a CLI might write into its own project config dir change the protected snapshot.

Live smoke runs on 2026-10-30 (Claude Code 2.1.311, OpenCode v2.1.4) showed neither CLI
writing into .claude/ or .opencode/. The audit is deliberately fail-closed with no exemptions,
so a future CLI that does write there makes every run fail as "protected files changed".
"""

import shutil
from pathlib import Path

import pytest

from workspace import prepare_workspace, protected_snapshot

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "examples" / "sample-household"

CLI_WRITES = [
    ".opencode/package.json",
    ".opencode/node_modules/x/index.js",
    ".opencode/.gitignore",
    ".claude/settings.local.json",
]


@pytest.fixture
def workspace():
    ws = prepare_workspace(SAMPLE, {}, {"claude", "opencode"})
    try:
        yield ws
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.parametrize("rel", CLI_WRITES)
def test_cli_write_into_config_dir_changes_snapshot(workspace, rel):
    before = protected_snapshot(workspace)
    assert rel not in before
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}\n")
    after = protected_snapshot(workspace)
    assert after != before
    assert rel in after


def test_installed_config_trees_are_audited(workspace):
    snapshot = protected_snapshot(workspace)
    assert any(p.startswith(".claude/") for p in snapshot)
    assert any(p.startswith(".opencode/") for p in snapshot)
