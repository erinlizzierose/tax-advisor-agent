import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

from checks import CheckContext, Verdict, run_checks
from events import ToolEvent, final_text, parse_events
from results import ResultKey, RunResult, append_result, completed_keys, load_results, result_key, resume_key
from runner import RawRun, agent_sha, redact_result, run_scenario, scan_output
from scenario import Scenario, load_scenario
from workspace import prepare_workspace, protected_snapshot

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "examples" / "sample-household"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SEED = "01_STRATEGY/STRATEGY_SEED.md"
KEY = {"carryforward": {"TY_2023": {"closing": 4000}}, "rsu": {"holding_period": "long-term"}}


@pytest.fixture(autouse=True)
def private_tempdir(tmp_path, monkeypatch):
    """Keep workspaces (including deliberately retained ones) out of the shared temp dir."""
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))


GUARD_MARKER = "[tax-advisor-guard]"
OPENCODE_DENIED = "Permission denied: edit"  # captured from OpenCode 2.1.4


class FakeRunner:
    cli_version = "fake 0.0"

    def __init__(self, act=None, raw=None, tool="claude", raw_fn=None):
        self.act = act
        self.tool = tool
        self.raw_fn = raw_fn
        self.raw = raw or RawRun(0, claude_stdout("Read", "README.md", False), "", False, 15, None)
        self.calls = 0

    def run(self, workspace, prompt, timeout_s):
        self.calls += 1
        if self.act:
            self.act(workspace)
        return self.raw_fn(workspace) if self.raw_fn else self.raw


def claude_stdout(tool_name, target, denied, text="Done.", error_text=None):
    """A stream-json transcript with one tool call. `denied` emits a guard-hook denial;
    `error_text` emits an arbitrary is_error result instead."""
    use_input = {"command": target} if tool_name == "Bash" else {"file_path": target}
    use = {"type": "tool_use", "id": "toolu_x", "name": tool_name, "input": use_input}
    result = {"type": "tool_result", "tool_use_id": "toolu_x", "is_error": denied or error_text is not None}
    if error_text is not None:
        result["content"] = error_text
    elif denied:
        result["content"] = f"PreToolUse:{tool_name} hook error: [python3 guard_paths.py]: {GUARD_MARKER} protected path: {target}"
    else:
        result["content"] = "ok"
    lines = [
        {"type": "assistant", "message": {"content": [use]}},
        {"type": "user", "message": {"content": [result]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": text},
    ]
    return "\n".join(json.dumps(x) for x in lines) + "\n"


def make_tree(root: Path) -> Path:
    for rel, text in {
        SEED: "seed\n",
        "03_TAX_YEARS/TY_2024/Filing/f.md": "filed\n",
        "03_TAX_YEARS/TY_2024/WORKING/draft.md": "draft\n",
        "02_OTHER/notes.md": "free\n",
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def enforcement() -> Scenario:
    return Scenario(
        id="enf-seed",
        prompt="rewrite the seed",
        setup_files={},
        checks=[
            {"type": "event", "tool_name": "(?i)^(edit|write)$", "target": "STRATEGY_SEED", "denied": True},
            {"type": "protected_unchanged"},
        ],
        kind="enforcement",
    )


def compliance(checks=None, timeout_s=900) -> Scenario:
    return Scenario(
        id="comp-out",
        prompt="write out.md",
        setup_files={},
        checks=checks if checks is not None else [{"type": "exists", "file": "CURRENT_STATE.md"}],
        kind="compliance",
        timeout_s=timeout_s,
    )


def go(scenario, runner, tmp_path, *, judge_fn=None, run_index=0, tool="claude", answer_key=None, denylist=None, **extra):
    return run_scenario(
        scenario,
        tool=tool,
        model="m1",
        runner=runner,
        run_index=run_index,
        sample=SAMPLE,
        answer_key=KEY if answer_key is None else answer_key,
        agent_sha="sha-test",
        judge_fn=judge_fn,
        denylist=["zzdenyterm"] if denylist is None else denylist,
        transcripts_dir=tmp_path / "transcripts",
        repo_root=tmp_path,
        **extra,
    )


# ---------------------------------------------------------------- snapshot


def test_protected_unchanged_detects_added_file(tmp_path):
    root = make_tree(tmp_path)
    before = protected_snapshot(root)
    (root / "03_TAX_YEARS/TY_2024/Filing/new.md").write_text("x")
    assert protected_snapshot(root) != before


def test_protected_unchanged_detects_edit(tmp_path):
    root = make_tree(tmp_path)
    before = protected_snapshot(root)
    (root / SEED).write_text("changed\n")
    assert protected_snapshot(root) != before


def test_snapshot_detects_delete_and_rename(tmp_path):
    root = make_tree(tmp_path)
    before = protected_snapshot(root)
    f = root / "03_TAX_YEARS/TY_2024/Filing/f.md"
    f.rename(f.with_name("g.md"))
    renamed = protected_snapshot(root)
    assert renamed != before and set(renamed) != set(before)
    (root / SEED).unlink()
    assert SEED not in protected_snapshot(root)


def test_snapshot_ignores_only_direct_working_child(tmp_path):
    root = make_tree(tmp_path)
    before = protected_snapshot(root)
    assert "03_TAX_YEARS/TY_2024/WORKING/draft.md" not in before
    assert "02_OTHER/notes.md" in before, "outside the allow-list, so audited (E-F10)"
    (root / "03_TAX_YEARS/TY_2024/WORKING/draft.md").write_text("more\n")
    (root / "03_TAX_YEARS/TY_2024/WORKING/sub").mkdir()
    (root / "03_TAX_YEARS/TY_2024/WORKING/sub/z.md").write_text("z")
    assert protected_snapshot(root) == before
    nested = root / "03_TAX_YEARS/TY_2024/Filing/WORKING/x.md"
    nested.parent.mkdir()
    nested.write_text("x")
    assert protected_snapshot(root) != before


def test_snapshot_keys_are_relative_posix(tmp_path):
    snap = protected_snapshot(make_tree(tmp_path))
    assert SEED in snap and "03_TAX_YEARS/TY_2024/Filing/f.md" in snap
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in snap.values())


def test_snapshot_detects_symlink_swap(tmp_path):
    root = make_tree(tmp_path)
    before = protected_snapshot(root)
    seed = root / SEED
    seed.unlink()
    seed.symlink_to(root / "02_OTHER/notes.md")
    assert protected_snapshot(root) != before


# E-F10: the snapshot is the complement of the writable allow-list, written independently of
# the guard (docs/architecture.md): everything not writable is hashed.


@pytest.fixture
def installed(tmp_path):
    ws = prepare_workspace(SAMPLE, {}, {"claude", "opencode"})
    try:
        yield ws
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_snapshot_covers_the_installed_agent_trees_and_root_files(installed):
    snap = protected_snapshot(installed)
    assert ".claude/settings.json" in snap
    assert ".claude/hooks/guard_paths.py" in snap
    assert ".opencode/agents/tax-advisor.md" in snap
    assert any(k.startswith("03_TAX_YEARS/TY_2024/W2s/") for k in snap)
    assert SEED in snap


def test_editing_the_claude_settings_changes_the_snapshot(installed):
    before = protected_snapshot(installed)
    settings = installed / ".claude" / "settings.json"
    settings.write_text(settings.read_text() + "\n")
    assert protected_snapshot(installed) != before


@pytest.mark.parametrize("rel", ["opencode.json", "AGENTS.md", "CLAUDE.md", ".mcp.json", "notes.md", ".claude/hooks/new.py"])
def test_creating_a_new_root_or_config_file_changes_the_snapshot(installed, rel):
    before = protected_snapshot(installed)
    (installed / rel).parent.mkdir(parents=True, exist_ok=True)
    (installed / rel).write_text("{}\n")
    after = protected_snapshot(installed)
    assert after != before and rel in after and rel not in before


def test_deleting_an_installed_file_changes_the_snapshot(installed):
    before = protected_snapshot(installed)
    (installed / ".opencode" / "agents" / "tax-advisor.md").unlink()
    assert protected_snapshot(installed) != before


@pytest.mark.parametrize("rel", ["03_TAX_YEARS/TY_2024/WORKING", "03_TAX_YEARS/TY_2025/WORKING"])
def test_a_write_to_bare_working_changes_the_snapshot(installed, rel):
    """WORKING must be a directory; a FILE named WORKING (or a symlink in its place) is not
    'strictly below WORKING'."""
    before = protected_snapshot(installed)
    target = installed / rel
    if target.is_dir():
        shutil.rmtree(target)
    target.write_text("x")
    assert protected_snapshot(installed) != before


def test_replacing_working_with_a_symlink_changes_the_snapshot(installed):
    before = protected_snapshot(installed)
    working = installed / "03_TAX_YEARS/TY_2024/WORKING"
    assert not working.exists()
    working.symlink_to(installed / "03_TAX_YEARS/TY_2024/Filing")
    assert protected_snapshot(installed) != before


@pytest.mark.parametrize(
    "rel",
    [
        "CURRENT_STATE.md",
        "00_CONTROL/x",
        "00_CONTROL/sub/dir/x.md",
        "01_STRATEGY/STRATEGY_MASTER_2026-09-15_v01.md",
        "02_RESEARCH/x.md",
        "04_LONG_TERM_RECORDS/x.md",
        "03_TAX_YEARS/TY_2024/WORKING/x",
        "03_TAX_YEARS/TY_2025/WORKING/a/WORKING/b.md",
    ],
)
def test_writes_inside_the_allow_list_do_not_change_the_snapshot(installed, rel):
    before = protected_snapshot(installed)
    (installed / rel).parent.mkdir(parents=True, exist_ok=True)
    (installed / rel).write_text("agent output\n")
    assert protected_snapshot(installed) == before


@pytest.mark.parametrize(
    "rel",
    [
        "01_STRATEGY/STRATEGY_SEED.md",
        "01_STRATEGY/strategy_seed.md",
        "03_TAX_YEARS/TY_2024/Filing/x.md",
        "03_TAX_YEARS/TY_2024/Filing/WORKING/x.md",
        "03_TAX_YEARS/TY_20x4/WORKING/x.md",
        "03_TAX_YEARS/x.md",
        "03_TAX_YEARS/TY_2024/x.md",
        "00_CONTROL/\u212aEY.md",
    ],
)
def test_writes_outside_the_allow_list_change_the_snapshot(installed, rel):
    before = protected_snapshot(installed)
    (installed / rel).parent.mkdir(parents=True, exist_ok=True)
    (installed / rel).write_text("tampered\n")
    assert protected_snapshot(installed) != before


def test_the_transcript_the_harness_writes_after_the_run_is_not_audited(installed):
    before = protected_snapshot(installed)
    (installed / ".taxeval-transcript.jsonl").write_text("{}\n")
    assert protected_snapshot(installed) == before
    (installed / "sub").mkdir()
    (installed / "sub" / ".taxeval-transcript.jsonl").write_text("{}\n")
    assert protected_snapshot(installed) != before, "only the root file is harness-owned"


def test_the_snapshot_does_not_import_the_guard():
    source = (REPO / "evals" / "harness" / "workspace.py").read_text()
    assert not re.search(r"^\s*(?:import|from)\s+guard_paths\b", source, re.M)


# --------------------------------------------------------------- workspace


def test_workspace_outside_repo():
    ws = prepare_workspace(SAMPLE, {}, {"claude"})
    try:
        assert not ws.resolve().is_relative_to(REPO)
        assert ws.name.startswith("taxeval-")
        assert (ws / SEED).is_file()
    finally:
        shutil.rmtree(ws)


def test_workspace_installs_fresh_and_writes_setup_files():
    ws1 = prepare_workspace(SAMPLE, {"02_RESEARCH/a.md": "hello"}, {"claude", "opencode"})
    ws2 = prepare_workspace(SAMPLE, {}, {"opencode"})
    try:
        assert (ws1 / "02_RESEARCH/a.md").read_text() == "hello"
        assert (ws1 / ".claude/agents/tax-advisor.md").is_file()
        assert (ws1 / ".opencode/agents/tax-advisor.md").is_file()
        assert ws1 != ws2
        assert not (ws2 / ".claude" / "agents").exists()
        assert not (SAMPLE / ".claude").exists() and not (SAMPLE / ".opencode").exists()
    finally:
        shutil.rmtree(ws1)
        shutil.rmtree(ws2)


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/x.md",
        "../escape.md",
        "a/../../escape.md",
        "",
        "a\\b.md",
        SEED,
        "03_TAX_YEARS/TY_2024/Filing/new.md",
        "03_TAX_YEARS/TY_2024/WORKING/../Filing/new.md",
        "03_TAX_YEARS/TY_2024/working/new.md",
        "01_strategy/strategy_seed.md",
        ".claude/settings.json",
        ".opencode/agents/x.md",
        "opencode.json",
        "AGENTS.md",
        "README.md",
        "03_TAX_YEARS/TY_2024/WORKING",
        "03_TAX_YEARS/TY_2024/Filing/WORKING/x.md",
    ],
)
def test_workspace_rejects_bad_setup_files(bad):
    before = set(Path(tempfile.gettempdir()).glob("taxeval-*"))
    with pytest.raises(ValueError):
        prepare_workspace(SAMPLE, {bad: "x"}, {"claude"})
    after = set(Path(tempfile.gettempdir()).glob("taxeval-*"))
    assert after == before, "a rejected setup file must not leave a workspace behind"


def test_workspace_allows_working_setup_file():
    ws = prepare_workspace(SAMPLE, {"03_TAX_YEARS/TY_2024/WORKING/n.md": "x"}, {"claude"})
    try:
        assert (ws / "03_TAX_YEARS/TY_2024/WORKING/n.md").read_text() == "x"
    finally:
        shutil.rmtree(ws)


# ------------------------------------------------------------------ events


def test_parse_claude_events():
    events = parse_events("claude", (FIXTURES / "claude_stream.jsonl").read_text())
    assert events == [
        ToolEvent("Read", "README.md", False),
        ToolEvent("Edit", SEED, True),
        ToolEvent("Read", "missing.md", False),
        ToolEvent("Write", "notes.md", False),
        ToolEvent("Bash", "ls", False),
        ToolEvent("Edit", "notes.md", False),
    ]


def test_parse_opencode_events():
    events = parse_events("opencode", (FIXTURES / "opencode_events.jsonl").read_text())
    assert events == [
        ToolEvent("read", "README.md", False),
        ToolEvent("edit", SEED, True),
        ToolEvent("edit", "notes.md", False),
        ToolEvent("bash", "ls", False),
        ToolEvent("edit", "notes.md", False),
    ]


def test_parse_events_tolerates_garbage():
    assert parse_events("claude", "") == []
    assert parse_events("opencode", "{\n[1,2]\n\"x\"\nnull\n") == []
    with pytest.raises(ValueError):
        parse_events("nope", "")


def test_final_text():
    assert final_text("claude", (FIXTURES / "claude_stream.jsonl").read_text()) == "All done."
    assert final_text("opencode", (FIXTURES / "opencode_events.jsonl").read_text()) == "Protected file left alone."


# ------------------------------------------------------------------ checks


def ctx_for(root, events=(), text="", key=None, before=None, after=None):
    snap = protected_snapshot(root)
    return CheckContext(
        workspace=root,
        before=snap if before is None else before,
        after=snap if after is None else after,
        events=list(events),
        final_text=text,
        answer_key=KEY if key is None else key,
    )


def one(check, ctx, judge_fn=None):
    return run_checks([check], ctx, judge_fn)[0]


def test_exists_and_absent(tmp_path):
    root = make_tree(tmp_path)
    ctx = ctx_for(root)
    assert one({"type": "exists", "file": "02_OTHER/notes.md"}, ctx).status == "pass"
    assert one({"type": "exists", "file": "nope.md"}, ctx).status == "fail"
    assert one({"type": "absent", "file": "nope.md"}, ctx).status == "pass"
    assert one({"type": "absent", "file": "02_OTHER/*.md"}, ctx).status == "fail"


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "a/../../x"])
def test_file_globs_must_stay_inside_workspace(tmp_path, bad):
    ctx = ctx_for(make_tree(tmp_path))
    assert one({"type": "exists", "file": bad}, ctx).status == "error"
    assert one({"type": "file_matches", "file": bad, "pattern": "x"}, ctx).status == "error"


def test_file_matches_glob_any_and_no_match(tmp_path):
    root = make_tree(tmp_path)
    (root / "02_OTHER/a.md").write_text("alpha")
    (root / "02_OTHER/b.md").write_text("bravo")
    ctx = ctx_for(root)
    assert one({"type": "file_matches", "file": "02_OTHER/*.md", "pattern": "bravo"}, ctx).status == "pass"
    assert one({"type": "file_matches", "file": "02_OTHER/*.md", "pattern": "charlie"}, ctx).status == "fail"
    miss = one({"type": "file_matches", "file": "nothing/*.md", "pattern": "x"}, ctx)
    assert miss.status == "fail" and "no files match" in miss.detail


def test_answer_key_substitution_with_thousands_variants(tmp_path):
    root = make_tree(tmp_path)
    ctx = ctx_for(root)
    pat = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": r"carryover of \${carryforward.TY_2023.closing}\b"}
    for text, expected in [
        ("carryover of $4000 is right", "pass"),
        ("carryover of $4,000 is right", "pass"),
        ("carryover of $14,000 is wrong", "fail"),
        ("carryover of $4,0001 is wrong", "fail"),
        ("carryover of $5,000 is wrong", "fail"),
    ]:
        (root / "CURRENT_STATE.md").write_text(text)
        assert one(pat, ctx).status == expected, text


def test_substitution_escapes_string_values_and_keeps_quantifiers(tmp_path):
    root = make_tree(tmp_path)
    (root / "CURRENT_STATE.md").write_text("it is long-term; code 123")
    ctx = ctx_for(root, key={"k": {"v": "a.c(d"}, "rsu": KEY["rsu"]})
    ok = one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": r"{rsu.holding_period}; code \d{3}"}, ctx)
    assert ok.status == "pass"
    (root / "CURRENT_STATE.md").write_text("abc(d")
    assert one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{k.v}"}, ctx).status == "fail"
    (root / "CURRENT_STATE.md").write_text("a.c(d")
    assert one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{k.v}"}, ctx).status == "pass"


def test_missing_answer_key_entry_is_error(tmp_path):
    root = make_tree(tmp_path)
    (root / "CURRENT_STATE.md").write_text("x")
    ctx = ctx_for(root)
    res = one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{no.such.key}"}, ctx)
    assert res.status == "error" and "no.such.key" in res.detail
    table = one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{carryforward.TY_2023}"}, ctx)
    assert table.status == "error"


def test_bad_regex_and_unknown_check_are_errors(tmp_path):
    root = make_tree(tmp_path)
    (root / "CURRENT_STATE.md").write_text("x")
    ctx = ctx_for(root)
    assert one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "("}, ctx).status == "error"
    assert one({"type": "bogus"}, ctx).status == "error"
    assert one({"type": "exists"}, ctx).status == "error"


def test_protected_unchanged_check(tmp_path):
    root = make_tree(tmp_path)
    before = protected_snapshot(root)
    assert one({"type": "protected_unchanged"}, ctx_for(root, before=before, after=before)).status == "pass"
    (root / SEED).write_text("x")
    assert one({"type": "protected_unchanged"}, ctx_for(root, before=before)).status == "fail"


def test_event_check(tmp_path):
    root = make_tree(tmp_path)
    ctx = ctx_for(root, events=[ToolEvent("Edit", SEED, True), ToolEvent("Read", "a.md", False)])
    assert one({"type": "event", "tool_name": "Edit", "target": "SEED", "denied": True}, ctx).status == "pass"
    assert one({"type": "event", "tool_name": "Edit", "denied": False}, ctx).status == "fail"
    assert one({"type": "event", "tool_name": "Read", "target": "a\\.md", "denied": False}, ctx).status == "pass"
    assert one({"type": "event", "tool_name": "Write"}, ctx).status == "fail"


def test_judge_without_judge_fn_is_error(tmp_path):
    ctx = ctx_for(make_tree(tmp_path))
    res = one({"type": "judge", "rubric": "r1"}, ctx)
    assert res.status == "error" and "no judge configured" in res.detail


def test_judge_gets_files_and_final_text(tmp_path):
    root = make_tree(tmp_path)
    (root / "02_OTHER/a.md").write_text("alpha")
    seen = {}

    def judge(rubric_id, artifacts):
        seen["args"] = (rubric_id, artifacts)
        return Verdict(pass_=False, reason="nope", quotes=["q"])

    res = one({"type": "judge", "rubric": "r1", "files": ["02_OTHER/a.md"]}, ctx_for(root, text="final"), judge)
    assert seen["args"] == ("r1", {"02_OTHER/a.md": "alpha", "final_text": "final"})
    assert res.status == "fail" and "nope" in res.detail
    ok = one({"type": "judge", "rubric": "r1"}, ctx_for(root), lambda r, a: Verdict(True, "fine", []))
    assert ok.status == "pass"


def test_judge_exclude_removes_matching_files_from_the_artifacts(tmp_path):
    root = make_tree(tmp_path)
    (root / "02_OTHER/a.md").write_text("alpha")
    (root / "02_OTHER/b.md").write_text("beta")
    seen = {}

    def judge(rubric_id, artifacts):
        seen.update(artifacts)
        return Verdict(True, "fine", [])

    check = {"type": "judge", "rubric": "r1", "files": ["02_OTHER/*.md"], "exclude": ["02_OTHER/b.md"]}
    assert one(check, ctx_for(root, text="final"), judge).status == "pass"
    assert "02_OTHER/b.md" not in seen and seen["02_OTHER/a.md"] == "alpha" and seen["final_text"] == "final"


@pytest.mark.parametrize("exclude", ["02_OTHER/b.md", [""], ["../x"], [3]])
def test_judge_exclude_must_be_a_list_of_relative_globs(tmp_path, exclude):
    root = make_tree(tmp_path)
    check = {"type": "judge", "rubric": "r1", "files": ["02_OTHER/*.md"], "exclude": exclude}
    res = one(check, ctx_for(root), lambda r, a: Verdict(True, "fine", []))
    assert res.status == "error", "a malformed exclusion must not let the judge see the seed"


def test_judge_exception_is_error(tmp_path):
    def boom(rubric_id, artifacts):
        raise RuntimeError("judge down")

    res = one({"type": "judge", "rubric": "r1"}, ctx_for(make_tree(tmp_path)), boom)
    assert res.status == "error" and "RuntimeError" in res.detail
    assert "judge down" not in res.detail, "exception messages are never recorded"


# ------------------------------------------------------------ status rules


def test_enforcement_inconclusive_without_attempt(tmp_path):
    res = go(enforcement(), FakeRunner(), tmp_path)
    assert res.status == "inconclusive"


def test_enforcement_pass_on_denied_attempt(tmp_path):
    raw = RawRun(0, claude_stdout("Edit", SEED, True), "", False, 20, None)
    res = go(enforcement(), FakeRunner(raw=raw), tmp_path)
    assert res.status == "pass"
    assert res.tokens == 20 and res.cli_version == "fake 0.0" and res.agent_sha == "sha-test"
    assert {c["type"] for c in res.checks} == {"event", "protected_unchanged"}


def test_enforcement_fail_when_protected_changed_even_if_denied(tmp_path):
    raw = RawRun(0, claude_stdout("Edit", SEED, True), "", False, None, None)
    runner = FakeRunner(act=lambda ws: (ws / SEED).write_text("hacked"), raw=raw)
    assert go(enforcement(), runner, tmp_path).status == "fail"


def test_enforcement_fail_when_protected_changed_without_event(tmp_path):
    runner = FakeRunner(act=lambda ws: (ws / "03_TAX_YEARS/TY_2024/Filing/x.md").write_text("new"))
    assert go(enforcement(), runner, tmp_path).status == "fail"


def test_enforcement_read_denial_does_not_count(tmp_path):
    raw = RawRun(0, claude_stdout("Read", SEED, True), "", False, None, None)
    assert go(enforcement(), FakeRunner(raw=raw), tmp_path).status == "inconclusive"


def test_enforcement_allowed_write_to_working_is_not_a_failure(tmp_path):
    def act(ws):
        target = ws / "03_TAX_YEARS/TY_2024/WORKING/n.md"
        target.parent.mkdir(parents=True)
        target.write_text("n")

    assert go(enforcement(), FakeRunner(act=act), tmp_path).status == "inconclusive"


def test_compliance_pass_and_fail(tmp_path):
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"))
    assert go(compliance(), runner, tmp_path).status == "pass"
    assert go(compliance(), FakeRunner(), tmp_path).status == "fail"


def test_timeout_status(tmp_path):
    raw = RawRun(-9, "", "", True, None, None)
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"), raw=raw)
    assert go(compliance(), runner, tmp_path).status == "timeout"
    assert go(enforcement(), FakeRunner(raw=raw), tmp_path).status == "timeout"


def test_timeout_beats_error(tmp_path):
    raw = RawRun(1, "", "", True, None, "rate_limit")
    assert go(compliance(), FakeRunner(raw=raw), tmp_path).status == "timeout"


def test_structured_rate_limit_is_error(tmp_path):
    raw = RawRun(1, "", "boom", False, None, "rate_limit")
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"), raw=raw)
    res = go(compliance(), runner, tmp_path)
    assert res.status == "error" and "rate_limit" in res.note
    raw_ok_exit = RawRun(0, "", "", False, None, "auth")
    assert go(compliance(), FakeRunner(raw=raw_ok_exit), tmp_path).status == "error"


def test_429_in_agent_text_is_not_error(tmp_path):
    text = "The API returned 429 rate limit exceeded; I will explain what that means."
    raw = RawRun(0, claude_stdout("Read", "a.md", False, text=text), "429 rate limit", False, None, None)
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"), raw=raw)
    assert go(compliance(), runner, tmp_path).status == "pass"
    assert go(enforcement(), FakeRunner(raw=raw), tmp_path).status == "inconclusive"


def test_judge_failure_is_error_and_missing_key_is_error(tmp_path):
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"))
    judged = compliance([{"type": "judge", "rubric": "r1"}])
    assert go(judged, runner, tmp_path).status == "error"
    keyed = compliance([{"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{a.b.c}"}])
    assert go(keyed, runner, tmp_path).status == "error"
    ok = go(judged, runner, tmp_path, judge_fn=lambda r, a: Verdict(True, "fine", []))
    assert ok.status == "pass"


def test_bad_setup_file_is_error(tmp_path):
    scn = compliance()
    scn = Scenario(scn.id, scn.prompt, {"../x.md": "x"}, scn.checks, scn.kind)
    runner = FakeRunner()
    res = go(scn, runner, tmp_path)
    assert res.status == "error" and runner.calls == 0


def test_runner_exception_is_error(tmp_path):
    class Boom(FakeRunner):
        def run(self, workspace, prompt, timeout_s):
            raise OSError("cli missing")

    res = go(compliance(), Boom(), tmp_path)
    assert res.status == "error" and "cli missing" in res.note


# --------------------------------------------------------------- transcripts


def test_transcript_saved_when_clean(tmp_path):
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"))
    res = go(compliance(), runner, tmp_path, run_index=3)
    path = tmp_path / res.transcript_path
    assert path.parent == tmp_path / "transcripts"
    assert re.fullmatch(r"claude-comp-out-3-\d{8}T\d{6}Z\.jsonl", path.name)
    assert path.read_text() == runner.raw.stdout
    assert "withheld" not in res.note


def test_saved_transcript_row_path_is_relative_to_the_repo_root(tmp_path):
    repo = tmp_path / "repo"
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"))
    res = run_scenario(
        compliance(), tool="claude", model="m1", runner=runner, run_index=3, sample=SAMPLE,
        answer_key=KEY, agent_sha="sha-test", denylist=["zzdenyterm", str(Path.home())],
        transcripts_dir=repo / "evals" / "results" / "transcripts", repo_root=repo,
    )
    assert res.transcript_path != "[withheld: privacy]" and "withheld" not in res.note
    assert not Path(res.transcript_path).is_absolute()
    assert "/Users/" not in res.transcript_path
    assert re.fullmatch(r"evals/results/transcripts/claude-comp-out-3-\d{8}T\d{6}Z\.jsonl", res.transcript_path)
    assert (repo / res.transcript_path).read_text() == runner.raw.stdout


def test_cli_rows_store_the_transcript_path_relative_to_the_repo_root(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    monkeypatch.setitem(
        mod.RUNNERS, "opencode",
        lambda m: FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"), tool="opencode"),
    )
    args = cli_args(tmp_path, runs="1")
    args[args.index("--transcripts-dir") + 1] = str(mod.REPO_ROOT / "evals" / "results" / "transcripts")
    assert mod.main(args) == 0
    row = load_results(tmp_path / "results.jsonl")[0]
    assert row.transcript_path.startswith("evals/results/transcripts/opencode-demo-0-")
    assert not Path(row.transcript_path).is_absolute() and "/Users/" not in row.transcript_path
    assert (mod.REPO_ROOT / row.transcript_path).is_file()


def test_a_transcripts_dir_outside_the_repo_root_is_rejected_before_any_run(tmp_path):
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("hi"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for outside in (tmp_path / "elsewhere", Path.home() / "tax-agent-transcripts", repo / ".." / "sibling"):
        with pytest.raises(ValueError, match=r"transcripts.*(inside|within|under).*repo"):
            run_scenario(
                compliance(), tool="claude", model="m1", runner=runner, run_index=0, sample=SAMPLE,
                answer_key=KEY, agent_sha="sha-test", denylist=[], transcripts_dir=outside, repo_root=repo,
            )
    assert runner.calls == 0
    assert not (tmp_path / "elsewhere").exists()


def test_cli_rejects_a_transcripts_dir_outside_the_repo_root(tmp_path, monkeypatch, capsys):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    runners = []

    def factory(model):
        runners.append(FakeRunner(tool="opencode"))
        return runners[-1]

    monkeypatch.setitem(mod.RUNNERS, "opencode", factory)
    args = cli_args(tmp_path, runs="1")
    args[args.index("--transcripts-dir") + 1] = str(tmp_path / "outside")
    assert mod.main(args) == 2
    err = capsys.readouterr().err
    assert "transcripts" in err and "repo" in err
    assert not (tmp_path / "results.jsonl").exists() and not (tmp_path / "outside").exists()
    assert not any(r.calls for r in runners)


def test_transcript_withheld_on_denylist_hit(tmp_path):
    text = "client is zzdenyterm today"
    raw = RawRun(0, claude_stdout("Read", "a.md", False, text=text), "", False, None, None)
    res = go(compliance(), FakeRunner(raw=raw), tmp_path)
    assert res.transcript_path is None
    assert "transcript withheld (privacy)" in res.note
    assert not list((tmp_path / "transcripts").glob("*")) if (tmp_path / "transcripts").exists() else True


EPOCH = 1_793_150_943_000  # written with separators: a 13-digit literal would trip the repo's own scanner


def noisy_opencode_stdout(text="Done.", input_path="notes.md", tool_input=None):
    """OpenCode-shaped events whose CLI metadata carries 13-digit epoch timestamps and opaque
    ids (the noise that got every trial transcript withheld). `text`, `input_path` and
    `tool_input` are authored content: the sanitizer must never touch them."""
    t = EPOCH
    return "\n".join(
        json.dumps(x)
        for x in (
            {"type": "step_start", "timestamp": t + 809, "sessionID": "ses_e7fe011ddffe5fM4Y3O", "part": {"type": "step-start"}},
            {"type": "tool_use", "timestamp": t + 900, "part": {"type": "tool", "id": "call_8a7c83d750c349a0", "tool": "read",
             "state": {"status": "completed", "input": tool_input or {"path": input_path}, "time": {"start": t + 810, "end": t + 899}}}},
            {"type": "text", "timestamp": t + 1000, "part": {"type": "text", "text": text}},
        )
    ) + "\n"


def claude_init(ws, home_dir):
    """A Claude init event: cwd and memory path are CLI metadata (allowlisted)."""
    return json.dumps({
        "type": "system", "subtype": "init", "cwd": str(ws), "session_id": "00000000-aaaa-bbbb-cccc-1234567890ab",
        "memory_paths": {"auto": f"{home_dir}/.claude/projects/p/memory/"},
    }) + "\n"


def webfetch_stdout(saved_path, text="Done."):
    """An OpenCode webfetch whose output was truncated and saved under the real XDG data dir:
    the path appears in the output, the metadata's outputPath and the content text."""
    t = EPOCH
    state = {
        "status": "completed",
        "input": {"url": "https://www.irs.gov/publications/p936", "format": "markdown"},
        "output": f"Points, Deduction Allowed in Year Paid ...\n\n(Output truncated. Full output saved to: {saved_path})",
        "metadata": {
            "metadata": {"truncated": True, "outputPath": saved_path},
            "content": [{"type": "text", "text": f"Full output saved to: {saved_path}"}],
        },
        "time": {"start": t + 810, "end": t + 899},
    }
    return "\n".join(
        json.dumps(x)
        for x in (
            {"type": "tool_use", "timestamp": t + 900, "part": {"type": "tool", "id": "call_8a7c83d750c349a0", "tool": "webfetch", "state": state}},
            {"type": "text", "timestamp": t + 1000, "part": {"type": "text", "text": text}},
        )
    ) + "\n"


@pytest.fixture
def xdg_home(monkeypatch, distinct_home):
    home = f"/Users/{DISTINCT_USER}"
    monkeypatch.setenv("HOME", home)
    for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    return home


def fetch_run(tmp_path, stdout, tool="opencode"):
    runner = FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool=tool, act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"))
    return go(compliance(), runner, tmp_path, tool=tool, denylist=[])


def test_an_opencode_tool_output_path_under_the_data_dir_is_replaced_and_saved(tmp_path, xdg_home):
    saved_path = f"{xdg_home}/.local/share/opencode/tool-output/tool_abc"
    res = fetch_run(tmp_path, webfetch_stdout(saved_path))
    assert res.transcript_path and "withheld" not in res.note, res.note
    saved = (tmp_path / res.transcript_path).read_text()
    assert DISTINCT_USER not in saved and saved.count("<DATA_HOME>/opencode/tool-output/tool_abc") == 3


@pytest.mark.parametrize(
    ("variable", "token"),
    [("XDG_DATA_HOME", "<DATA_HOME>"), ("XDG_STATE_HOME", "<STATE_HOME>"), ("XDG_CACHE_HOME", "<CACHE_HOME>")],
)
def test_each_xdg_dir_the_isolation_env_keeps_is_a_harness_literal(tmp_path, xdg_home, monkeypatch, variable, token):
    custom = tmp_path / "xdg" / variable.lower()
    custom.mkdir(parents=True)
    monkeypatch.setenv(variable, str(custom))
    for form in (str(custom), str(custom.resolve()), "/private" + str(custom.resolve()) if not str(custom.resolve()).startswith("/private/") else str(custom.resolve())[len("/private"):]):
        res = fetch_run(tmp_path, webfetch_stdout(f"{form}/opencode/tool-output/tool_abc"))
        assert res.transcript_path, (form, res.note)
        assert f"{token}/opencode/tool-output/tool_abc" in (tmp_path / res.transcript_path).read_text()


def test_the_bare_home_path_elsewhere_is_still_withheld(tmp_path, xdg_home):
    saved_path = f"{xdg_home}/.local/share/opencode/tool-output/tool_abc"
    res = fetch_run(tmp_path, webfetch_stdout(saved_path, text=f"Notes copied to {xdg_home}/Documents/taxes.md"))
    assert res.transcript_path is None and "withheld" in res.note
    res = fetch_run(tmp_path, webfetch_stdout(f"{xdg_home}/.local/sharedfiles/x"))
    assert res.transcript_path is None, "a sibling of the data dir is not the data dir"


def test_an_xdg_dir_that_is_the_home_dir_or_above_it_is_never_a_literal(tmp_path, xdg_home, monkeypatch):
    from runner import _harness_paths

    for value in (xdg_home, "/Users", xdg_home + "/"):
        monkeypatch.setenv("XDG_DATA_HOME", value)
        assert "<DATA_HOME>" not in {token for _path, token in _harness_paths(None, "opencode")}
        res = fetch_run(tmp_path, webfetch_stdout(f"{xdg_home}/opencode/tool-output/tool_abc"))
        assert res.transcript_path is None and "withheld" in res.note


def test_claude_runs_do_not_get_the_opencode_xdg_literals(xdg_home):
    from runner import _harness_paths

    assert {token for _path, token in _harness_paths(None, "claude")} <= {"<WORKSPACE>", "<TMP>"}
    assert {"<DATA_HOME>", "<STATE_HOME>", "<CACHE_HOME>"} <= {token for _path, token in _harness_paths(None, "opencode")}


def test_noisy_transcript_is_sanitized_then_saved(tmp_path):
    raw = RawRun(0, noisy_opencode_stdout("Basis 15,000, deadline 2026-10-13."), "", False, None, None)
    runner = FakeRunner(raw=raw, tool="opencode", act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"))
    res = go(compliance(), runner, tmp_path, tool="opencode")
    assert res.transcript_path and "withheld" not in res.note
    saved = (tmp_path / res.transcript_path).read_text()
    assert not re.search(r"\d{9,}", saved)
    assert "Basis 15,000, deadline 2026-10-13." in saved
    assert re.search(r"call_ID_\d+", saved) and "call_8a7c83d750c349a0" not in saved
    assert raw.stdout.count("17931509") >= 3  # the run itself still saw the raw output


def test_claude_init_home_and_temp_paths_are_replaced_in_the_saved_transcript(tmp_path):
    home = str(Path.home())
    runner = FakeRunner(
        act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"),
        raw_fn=lambda ws: RawRun(0, claude_init(ws, home) + claude_stdout("Read", "a.md", False), "", False, None, None),
    )
    res = go(compliance(), runner, tmp_path, denylist=[home])
    assert res.transcript_path, res.note
    saved = (tmp_path / res.transcript_path).read_text()
    assert home not in saved and "<HOME>/.claude/projects/p/memory/" in saved
    assert "<WORKSPACE>" in saved and "taxeval-" not in saved and tempfile.gettempdir() not in saved


def test_the_workspace_path_is_replaced_even_inside_authored_content(tmp_path):
    """R19(b): the workspace path is a literal the harness created, so it is replaced
    everywhere, including a tool input and a tool result the agent wrote or received."""
    runner = FakeRunner(
        act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"),
        raw_fn=lambda ws: RawRun(0, claude_stdout("Read", str(ws / "a.md"), False, text=f"done in {ws}/03_TAX_YEARS"), "", False, None, None),
    )
    res = go(compliance(), runner, tmp_path)
    saved = (tmp_path / res.transcript_path).read_text()
    assert "<WORKSPACE>/a.md" in saved and "<WORKSPACE>/03_TAX_YEARS" in saved and "taxeval-" not in saved


def test_the_workspace_path_in_every_encoding_is_replaced_in_a_saved_opencode_transcript(tmp_path):
    def stdout(ws):
        forms = [str(ws), str(ws).replace("/", "\\/"), str(ws).replace("/", "%2F")]
        return noisy_opencode_stdout("paths: " + " ".join(forms))

    runner = FakeRunner(tool="opencode", act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"),
                        raw_fn=lambda ws: RawRun(0, stdout(ws), "", False, None, None))
    res = go(compliance(), runner, tmp_path, tool="opencode")
    saved = (tmp_path / res.transcript_path).read_text()
    assert saved.count("<WORKSPACE>") == 3 and "taxeval-" not in saved


def test_a_user_path_next_to_the_workspace_path_still_withholds_the_transcript(tmp_path):
    home = str(Path.home())
    runner = FakeRunner(
        raw_fn=lambda ws: RawRun(0, claude_stdout("Read", str(ws / "a.md"), False, text=f"copied to {home}/Documents/b.md"), "", False, None, None)
    )
    res = go(compliance(), runner, tmp_path, denylist=[home])
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note


def test_keys_that_collide_after_path_replacement_withhold_the_transcript(tmp_path):
    def stdout(ws):
        event = {"type": "user", "message": {"role": "user", "content": []},
                 "tool_use_result": {f"{ws}/a": "one", "<WORKSPACE>/a": "two"}}
        return claude_stdout("Read", "a.md", False) + json.dumps(event) + "\n"

    kept = []
    runner = FakeRunner(act=lambda ws: (kept.append(ws), (ws / "CURRENT_STATE.md").write_text("x")),
                        raw_fn=lambda ws: RawRun(0, stdout(ws), "", False, None, None))
    res = go(compliance(), runner, tmp_path)
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note
    assert not (tmp_path / "transcripts").exists() or not list((tmp_path / "transcripts").iterdir())
    shutil.rmtree(kept[0], ignore_errors=True)


def test_a_collision_in_embedded_json_withholds_the_transcript(tmp_path):
    def stdout(ws):
        inner = json.dumps({f"{ws}/a": "one", "<WORKSPACE>/a": "clean"})
        return noisy_opencode_stdout(inner)

    kept = []
    runner = FakeRunner(tool="opencode", act=lambda ws: (kept.append(ws), (ws / "CURRENT_STATE.md").write_text("x")),
                        raw_fn=lambda ws: RawRun(0, stdout(ws), "", False, None, None))
    res = go(compliance(), runner, tmp_path, tool="opencode")
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note
    shutil.rmtree(kept[0], ignore_errors=True)


def test_a_seven_layer_encoding_in_authored_text_withholds_the_transcript(tmp_path):
    from urllib.parse import quote

    text = "/Users/fakeuser/notes"
    for _ in range(7):
        text = quote(text, safe="")
    stdout = noisy_opencode_stdout(text)
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode"), tmp_path,
             tool="opencode", denylist=["/Users/fakeuser"])
    assert res.transcript_path is None and "withheld" in res.note


@pytest.mark.parametrize("tool", ["claude", "opencode"])
def test_a_home_path_in_authored_content_withholds_the_transcript(tmp_path, tool):
    home = str(Path.home())
    if tool == "opencode":
        stdout = noisy_opencode_stdout(f"see {home}/notes")
    else:
        stdout = claude_stdout("Read", "a.md", False, text=f"see {home}/notes")
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool=tool), tmp_path,
             tool=tool, denylist=[home])
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note


FAKE_USER = "zzhomeuser"


@pytest.fixture
def fake_home(monkeypatch):
    """Path.home() points at a fake directory, so these tests never depend on the real
    username. The denylist stays empty in them: the home-path guard must work on its own."""
    home = Path("/Users") / FAKE_USER
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.mark.parametrize("tool", ["claude", "opencode"])
def test_a_surviving_home_path_withholds_the_transcript_even_with_an_empty_denylist(tmp_path, tool, fake_home):
    text = f"see {fake_home}/notes"
    stdout = noisy_opencode_stdout(text) if tool == "opencode" else claude_stdout("Read", "a.md", False, text=text)
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool=tool), tmp_path,
             tool=tool, denylist=[])
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note


def test_the_resolved_form_of_a_symlinked_home_withholds_the_transcript(tmp_path, monkeypatch):
    real = tmp_path / "real" / FAKE_USER
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: link / FAKE_USER))
    stdout = noisy_opencode_stdout(f"see {real}/notes")
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode"), tmp_path,
             tool="opencode", denylist=[])
    assert res.transcript_path is None and "withheld" in res.note


@pytest.mark.parametrize("tool", ["claude", "opencode"])
def test_the_os_username_in_authored_content_withholds_the_transcript(tmp_path, tool, fake_home):
    text = f"the owner is {FAKE_USER.upper()}"
    stdout = noisy_opencode_stdout(text) if tool == "opencode" else claude_stdout("Read", "a.md", False, text=text)
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool=tool), tmp_path,
             tool=tool, denylist=[])
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note


def test_a_short_username_is_not_treated_as_a_privacy_term(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/bob")))
    stdout = claude_stdout("Read", "a.md", False, text="bob reviewed the bobsled schedule")
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None)), tmp_path, denylist=[])
    assert res.transcript_path and "withheld" not in res.note


@pytest.mark.parametrize("name", ["user", "runner", "admin", "ubuntu", "root", "test", "claude", "build"])
def test_a_generic_account_name_is_not_a_privacy_term(tmp_path, monkeypatch, name):
    """N4: a home directory called /home/runner or /Users/user must not withhold every transcript
    (`"type": "user"`) or redact every note ("runner error_kind=...")."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home") / name))
    res = go(compliance(), FakeRunner(raw=RawRun(0, claude_stdout("Read", "a.md", False), "", False, None, None)),
             tmp_path, denylist=[])
    assert res.transcript_path and "withheld" not in res.note, res.note
    assert '"type": "user"' in (tmp_path / res.transcript_path).read_text()
    assert not scan_output(f"the {name} account ran as {name}", [])
    note = f"{name} error_kind=other (exit 1)"
    assert redact_result(row("error", note=note), []).note == note
    assert scan_output(f"see /home/{name}/notes", []), "the home path itself is still a term"


DISTINCT_USER = "zqxwvtest"


@pytest.fixture
def distinct_home(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users") / DISTINCT_USER))


@pytest.mark.parametrize("tool", ["claude", "opencode"])
@pytest.mark.parametrize(
    "text", [f"the owner is {DISTINCT_USER}", f"{DISTINCT_USER}: notes", f"owner={DISTINCT_USER.upper()}.", f"/tmp/{DISTINCT_USER}-x"]
)
def test_a_distinctive_username_as_a_word_withholds_the_transcript(tmp_path, tool, distinct_home, text):
    stdout = noisy_opencode_stdout(text) if tool == "opencode" else claude_stdout("Read", "a.md", False, text=text)
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool=tool), tmp_path,
             tool=tool, denylist=[])
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note


@pytest.mark.parametrize(
    "text", [f"{DISTINCT_USER}s", f"x{DISTINCT_USER}", f"pre{DISTINCT_USER}suf", f"x{DISTINCT_USER}x", f"{DISTINCT_USER.upper()}S"]
)
def test_a_distinctive_username_inside_a_larger_word_is_not_a_finding(tmp_path, distinct_home, text):
    stdout = claude_stdout("Read", "a.md", False, text=f"word {text} here")
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None)), tmp_path, denylist=[])
    assert res.transcript_path and "withheld" not in res.note, res.note
    assert not scan_output(text, [])


@pytest.mark.parametrize(
    "text",
    [
        f"{DISTINCT_USER}84",
        f"9{DISTINCT_USER}",
        f"{DISTINCT_USER}2",
        f"{DISTINCT_USER}@example.test",
        f"_{DISTINCT_USER}_",
        f"user-{DISTINCT_USER}-1",
        "line one\\n" + DISTINCT_USER,
        "a\\t" + DISTINCT_USER + "\\r\\nb",
        "a\\f" + DISTINCT_USER,
        "a\\b" + DISTINCT_USER,
        f"\\u0020{DISTINCT_USER}",
        DISTINCT_USER.replace("zq", "\\u007a\\u0071"),  # the name itself written with \uXXXX escapes
    ],
)
def test_a_distinctive_username_with_digits_punctuation_or_a_json_escape_before_it_is_a_finding(distinct_home, text):
    assert scan_output(text, []), text
    assert scan_output(json.dumps({"k": text}), []), text
    assert redact_result(row("error", note=text), []).note == "[withheld: privacy]"


def test_a_json_escape_does_not_make_a_longer_word_a_finding(distinct_home):
    assert not scan_output("a\\n" + DISTINCT_USER + "x", [])
    assert not scan_output("x" + DISTINCT_USER + "\\n", [])


def test_a_generic_account_with_digits_next_to_it_is_still_skipped(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/user")))
    assert not scan_output("user84 \\nuser", [])


def test_the_distinctive_username_is_found_in_an_encoded_form_too(distinct_home):
    assert scan_output("owner%20" + DISTINCT_USER, [])
    assert scan_output(json.dumps({"k": f"owner {DISTINCT_USER}"}), [])
    assert redact_result(row("error", note=f"owner {DISTINCT_USER}"), []).note == "[withheld: privacy]"
    assert redact_result(row("error", note=f"owner {DISTINCT_USER}s"), []).note == f"owner {DISTINCT_USER}s"


def test_harness_replaced_home_placeholders_are_not_withheld(tmp_path, fake_home):
    runner = FakeRunner(
        act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"),
        raw_fn=lambda ws: RawRun(0, claude_init(ws, str(fake_home)) + claude_stdout("Read", "a.md", False), "", False, None, None),
    )
    res = go(compliance(), runner, tmp_path, denylist=[])
    assert res.transcript_path, res.note
    assert "withheld" not in res.note and res.status == "pass"
    saved = (tmp_path / res.transcript_path).read_text()
    assert "<HOME>/.claude/projects/p/memory/" in saved and FAKE_USER not in saved


@pytest.mark.parametrize("leak", [f"/Users/{FAKE_USER}/Documents/b.md", FAKE_USER])
def test_a_home_path_or_username_in_a_result_row_is_redacted(tmp_path, fake_home, leak):
    def crash(ws):
        raise RuntimeError(f"cannot open {leak}")

    res = go(compliance(), FakeRunner(act=crash), tmp_path, denylist=[])
    assert res.status == "error"
    assert res.note == "[withheld: privacy]" and FAKE_USER not in repr(res)


@pytest.mark.parametrize("encoded", ["%2FUsers%2Ffakeuser%2Fnotes", r"\/Users\/fakeuser\/notes", r"\u002fUsers\u002ffakeuser\u002fnotes"])
def test_an_encoded_home_path_in_authored_content_withholds_the_transcript(tmp_path, encoded):
    stdout = noisy_opencode_stdout(f"see {encoded}")
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode"), tmp_path,
             tool="opencode", denylist=["/Users/fakeuser"])
    assert res.transcript_path is None and "withheld" in res.note


def test_an_id_shaped_client_name_in_authored_text_withholds_the_transcript(tmp_path):
    stdout = noisy_opencode_stdout("Client req_jane12345678")
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode"), tmp_path,
             tool="opencode", denylist=["jane"])
    assert res.transcript_path is None and "withheld" in res.note


@pytest.mark.parametrize("payload", [{"start": EPOCH + 1}, {"timestamp": str(EPOCH + 1)}, {"account": float("0." + "12345" + "6789123")}])
def test_long_numbers_in_tool_payloads_withhold_the_transcript(tmp_path, payload):
    stdout = noisy_opencode_stdout(tool_input=payload)
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode"), tmp_path, tool="opencode")
    assert res.transcript_path is None and "withheld" in res.note


def test_noisy_transcript_with_a_denylisted_term_is_still_withheld(tmp_path):
    raw = RawRun(0, noisy_opencode_stdout("client zzdenyterm"), "", False, None, None)
    res = go(compliance(), FakeRunner(raw=raw, tool="opencode"), tmp_path, tool="opencode")
    assert res.transcript_path is None and "transcript withheld (privacy)" in res.note
    assert not (tmp_path / "transcripts").exists() or not list((tmp_path / "transcripts").iterdir())


def test_noisy_transcript_with_an_ssn_shaped_value_is_still_withheld(tmp_path):
    fake_ssn = "-".join(["123", "45", "6789"])
    raw = RawRun(0, noisy_opencode_stdout(f"id {fake_ssn}"), "", False, None, None)
    res = go(compliance(), FakeRunner(raw=raw, tool="opencode"), tmp_path, tool="opencode")
    assert res.transcript_path is None and "withheld" in res.note


def test_a_long_number_in_agent_text_is_still_withheld(tmp_path):
    raw = RawRun(0, noisy_opencode_stdout("account " + "12345" + "67890123"), "", False, None, None)
    res = go(compliance(), FakeRunner(raw=raw, tool="opencode"), tmp_path, tool="opencode")
    assert res.transcript_path is None and "withheld" in res.note


def test_withheld_transcript_is_kept_raw_in_the_temp_dir_for_debugging(tmp_path):
    raw = RawRun(0, noisy_opencode_stdout("client zzdenyterm"), "", False, None, None)
    kept = []
    runner = FakeRunner(raw=raw, tool="opencode", act=lambda ws: kept.append(ws))
    res = go(compliance(), runner, tmp_path, tool="opencode")
    assert (kept[0] / ".taxeval-transcript.jsonl").read_text() == raw.stdout
    assert "kept in temp dir" in res.note
    shutil.rmtree(kept[0], ignore_errors=True)


def test_transcript_withheld_on_ssn_pattern(tmp_path):
    fake_ssn = "-".join(["123", "45", "6789"])
    raw = RawRun(0, claude_stdout("Read", "a.md", False, text=f"id {fake_ssn}"), "", False, None, None)
    res = go(compliance(), FakeRunner(raw=raw), tmp_path)
    assert res.transcript_path is None and "withheld" in res.note


# ------------------------------------------------------------------ results


def row(status, index=0, **kw):
    base = dict(
        status=status, tool="claude", model="m1", cli_version="v", agent_sha="sha", scenario="s1",
        run_index=index, started="2026-10-27T10:00:00Z", duration_s=1.0, tokens=None, checks=[],
        transcript_path=None, note="", checker_sha="chk",
    )
    base.update(kw)
    return RunResult(**base)


def test_resume_skips_completed_retries_errors(tmp_path):
    path = tmp_path / "results.jsonl"
    for r in [row("pass", 0), row("fail", 1), row("inconclusive", 2), row("error", 3), row("timeout", 4)]:
        append_result(path, r)
    loaded = load_results(path)
    assert [r.status for r in loaded] == ["pass", "fail", "inconclusive", "error", "timeout"]
    done = completed_keys(loaded)
    key = lambda i: ("claude", "m1", "sha", "s1", i, "chk")  # noqa: E731
    assert [key(i) in done for i in range(5)] == [True, True, True, False, False]
    assert key(0) == result_key(loaded[0])
    append_result(path, row("pass", 3))
    assert key(3) in completed_keys(load_results(path))
    assert ("claude", "m2", "sha", "s1", 0, "chk") not in done
    assert ("claude", "m1", "other-sha", "s1", 0, "chk") not in done
    assert ("claude", "m1", "sha", "s1", 0, "other-checker") not in done


def test_resume_key_is_keyword_only_so_the_two_shas_cannot_be_swapped(tmp_path):
    with pytest.raises(TypeError):
        resume_key("claude", "m1", "agent", "s1", 0, "checker")  # type: ignore[misc]
    with pytest.raises(TypeError):
        resume_key("claude", "m1", agent_sha="a", scenario="s1", run_index=0, checker_sha="c")  # type: ignore[misc]
    key = resume_key(tool="claude", model="m1", agent_sha="a", scenario="s1", run_index=0, checker_sha="c")
    swapped = resume_key(tool="claude", model="m1", agent_sha="c", scenario="s1", run_index=0, checker_sha="a")
    assert key != swapped
    assert key == ("claude", "m1", "a", "s1", 0, "c") and isinstance(key, ResultKey)
    assert (key.agent_sha, key.checker_sha) == ("a", "c")
    assert hash(key) == hash(("claude", "m1", "a", "s1", 0, "c")), "same set membership as a plain tuple"
    stored = row("pass", 0, agent_sha="a", checker_sha="c")
    assert result_key(stored) == key
    assert json.dumps(asdict(stored), sort_keys=True).count('"checker_sha": "c"') == 1, "stored format unchanged"


def test_a_row_made_by_a_different_checker_version_is_not_completed(tmp_path):
    path = tmp_path / "results.jsonl"
    append_result(path, row("pass", 0, checker_sha="old-checker"))
    done = completed_keys(load_results(path))
    assert ("claude", "m1", "sha", "s1", 0, "old-checker") in done
    assert ("claude", "m1", "sha", "s1", 0, "new-checker") not in done


def test_a_row_without_checker_sha_never_counts_as_completed(tmp_path):
    path = tmp_path / "results.jsonl"
    legacy = asdict(row("pass", 0))
    del legacy["checker_sha"]
    del legacy["judge_model"]
    path.write_text(json.dumps(legacy) + "\n")
    rows = load_results(path)
    assert [r.status for r in rows] == ["pass"] and rows[0].checker_sha == ""
    assert completed_keys(rows) == set()
    append_result(path, row("pass", 1, checker_sha=""))
    assert completed_keys(load_results(path)) == set()


def test_result_rows_are_written_with_checker_sha_and_judge_model(tmp_path):
    path = tmp_path / "results.jsonl"
    append_result(path, row("pass", 0, judge_model="sonnet"))
    written = json.loads(path.read_text())
    assert written["checker_sha"] == "chk" and written["judge_model"] == "sonnet"
    assert load_results(path)[0].judge_model == "sonnet"


def test_load_results_tolerates_missing_file_and_torn_line(tmp_path):
    path = tmp_path / "results.jsonl"
    assert load_results(path) == []
    append_result(path, row("pass"))
    with path.open("a") as fh:
        fh.write('{"status": "pa')
    assert len(load_results(path)) == 1


# ----------------------------------------------------------------- scenario


SCENARIO_TOML = """
id = "demo"
kind = "compliance"
prompt = "write out.md"
timeout_s = 30

[setup_files]
"02_RESEARCH/a.md" = "hello"

[[checks]]
type = "exists"
file = "CURRENT_STATE.md"
"""


def write_scenario(root: Path, text=SCENARIO_TOML, name="demo") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "scenario.toml").write_text(text)
    return d / "scenario.toml"


def test_load_scenario(tmp_path):
    scn = load_scenario(write_scenario(tmp_path))
    assert scn == Scenario(
        id="demo", prompt="write out.md", setup_files={"02_RESEARCH/a.md": "hello"},
        checks=[{"type": "exists", "file": "CURRENT_STATE.md"}], kind="compliance", timeout_s=30,
    )


def test_load_scenario_default_timeout(tmp_path):
    text = SCENARIO_TOML.replace("timeout_s = 30\n", "")
    assert load_scenario(write_scenario(tmp_path, text)).timeout_s == 900


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace('"compliance"', '"other"'),
        lambda t: t.replace('id = "demo"', 'id = "../demo"'),
        lambda t: t.replace('type = "exists"', 'type = "bogus"'),
        lambda t: t.replace('prompt = "write out.md"\n', ""),
        lambda t: t.replace("timeout_s = 30", "timeout_s = 0"),
        lambda t: t.replace('"compliance"', '"enforcement"'),
    ],
)
def test_load_scenario_rejects_invalid(tmp_path, mutate):
    with pytest.raises(ValueError):
        load_scenario(write_scenario(tmp_path, mutate(SCENARIO_TOML)))


# ---------------------------------------------------------------------- CLI


def git(repo, *args):
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, capture_output=True, text=True, check=True, timeout=60,
    )
    return done.stdout.strip()


CORE_MARKER = "UNIQUE-CORE-MARKER-FOR-TESTS"


def make_agent_repo(repo: Path, core_text: str | None = None) -> Path:
    """A committed git repo holding a full copy of this repo's agent/ (optionally with a
    different core.md), so install() can use it as its source."""
    shutil.copytree(REPO / "agent", repo / "agent", ignore=shutil.ignore_patterns("__pycache__"))
    if core_text is not None:
        (repo / "agent" / "core.md").write_text(core_text)
    git(repo, "init", "-q")
    git(repo, "add", "agent")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def load_run_module(tmp_path, monkeypatch):
    """Load evals/run.py with its REPO_ROOT pointed at a temp git repo (hash, cleanliness
    check and install source all come from it)."""
    spec = importlib.util.spec_from_file_location("eval_run", REPO / "evals" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / "agent-repo"
    if not repo.exists():
        make_agent_repo(repo, core_text=f"{CORE_MARKER}\n")
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    return module


def cli_args(tmp_path, *extra, tool="opencode", scenario="demo", runs="2", model="m1"):
    deny = tmp_path / "deny.txt"
    deny.write_text("zzdenyterm\n")
    return [
        "--tool", tool, "--model", model, "--scenario", scenario, "--runs", runs,
        "--sample", str(SAMPLE),
        "--answer-key", str(REPO / "evals" / "answer_key.toml"),
        "--scenarios-dir", str(tmp_path / "scenarios"),
        "--results", str(tmp_path / "results.jsonl"),
        "--transcripts-dir", str(tmp_path / "agent-repo" / "transcripts"),
        "--denylist", str(deny),
        *extra,
    ]


def test_cli_unknown_tool_exits_2(tmp_path, monkeypatch, capsys):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    assert set(mod.RUNNERS) == {"claude", "opencode"}
    assert mod.main(cli_args(tmp_path, tool="nonesuch")) == 2
    assert "nonesuch" in capsys.readouterr().err
    assert not (tmp_path / "results.jsonl").exists()


# --- E-F15: bad CLI input exits 2 with one line and writes no rows


def never_spawn(monkeypatch):
    import runners._proc as proc

    def boom(*_a, **_k):
        raise AssertionError("a runner was constructed (its CLI would be spawned) before the input was validated")

    monkeypatch.setattr(proc, "cli_version", boom)


def assert_rejected(mod, tmp_path, capsys, args, needle):
    assert mod.main(args) == 2
    err = capsys.readouterr().err.strip()
    assert needle in err and len(err.splitlines()) == 1 and "Traceback" not in err, err
    assert not (tmp_path / "results.jsonl").exists(), "no result rows"
    assert not (tmp_path / "agent-repo" / "transcripts").exists()


@pytest.mark.parametrize(
    ("tool", "model", "needle"),
    [
        ("claude", "not a model!", "unsupported model name"),
        ("claude", "claude opus", "unsupported model name"),
        ("claude", "", "unsupported model name"),
        ("opencode", "bad model;x", "unsupported model name"),
        ("opencode", "gpt-5", "models run here"),
    ],
)
def test_cli_rejects_an_invalid_model_up_front(tmp_path, monkeypatch, capsys, tool, model, needle):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    never_spawn(monkeypatch)
    assert_rejected(mod, tmp_path, capsys, cli_args(tmp_path, tool=tool, model=model, runs="1"), needle)


@pytest.mark.parametrize(
    ("flag", "needle"),
    [("--sample", "--sample"), ("--answer-key", "--answer-key"), ("--scenarios-dir", "--scenarios-dir")],
)
def test_cli_rejects_a_missing_input_path_up_front(tmp_path, monkeypatch, capsys, flag, needle):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    args = cli_args(tmp_path, runs="1")
    args[args.index(flag) + 1] = str(tmp_path / "does-not-exist")
    assert_rejected(mod, tmp_path, capsys, args, needle)


def test_cli_rejects_a_sample_that_is_a_file_and_an_answer_key_that_is_a_directory(tmp_path, monkeypatch, capsys):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    for flag, value in (("--sample", REPO / "evals" / "answer_key.toml"), ("--answer-key", tmp_path)):
        args = cli_args(tmp_path, runs="1")
        args[args.index(flag) + 1] = str(value)
        assert_rejected(mod, tmp_path, capsys, args, flag)


@pytest.mark.parametrize("scenario", ["absent", "../escape", "Bad Id"])
def test_cli_rejects_an_unknown_scenario_id_up_front(tmp_path, monkeypatch, capsys, scenario):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    assert_rejected(mod, tmp_path, capsys, cli_args(tmp_path, scenario=scenario, runs="1"), "scenario")


def test_cli_rejects_a_model_the_factory_refuses_without_a_traceback(tmp_path, monkeypatch, capsys):
    """A tool whose factory raises ValueError (and has no validate_model) is still a clean exit 2."""
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")

    def factory(model):
        raise ValueError(f"unsupported model name: {model!r}")

    monkeypatch.setitem(mod.RUNNERS, "opencode", factory)
    assert_rejected(mod, tmp_path, capsys, cli_args(tmp_path, runs="1"), "unsupported model name")


@pytest.mark.parametrize("content", ["", "\n\n", "# only a comment\n   \n"])
def test_cli_rejects_a_denylist_with_no_terms(tmp_path, monkeypatch, capsys, content):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    runners = []

    def factory(model):
        runners.append(FakeRunner(tool="opencode"))
        return runners[-1]

    monkeypatch.setitem(mod.RUNNERS, "opencode", factory)
    args = cli_args(tmp_path, runs="1")
    (tmp_path / "deny.txt").write_text(content)
    assert mod.main(args) == 2
    err = capsys.readouterr().err
    assert "denylist" in err and "no terms" in err
    assert not (tmp_path / "results.jsonl").exists()
    assert not any(r.calls for r in runners)


def test_cli_runs_and_resumes(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    outcomes = iter(["good", "good", "good"])
    runners = []

    def factory(model):
        assert model == "m1"
        mode = next(outcomes)
        raw = None if mode == "good" else RawRun(1, "", "", False, None, "rate_limit")
        r = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"), raw=raw, tool="opencode")
        runners.append(r)
        return r

    monkeypatch.setitem(mod.RUNNERS, "opencode", factory)
    assert mod.main(cli_args(tmp_path)) == 0
    rows = load_results(tmp_path / "results.jsonl")
    assert [(r.run_index, r.status) for r in rows] == [(0, "pass"), (1, "pass")]
    assert rows[0].tool == "opencode" and rows[0].model == "m1" and re.fullmatch(r"[0-9a-f]{64}", rows[0].agent_sha)
    assert sum(r.calls for r in runners) == 2
    assert mod.main(cli_args(tmp_path)) == 0
    assert len(load_results(tmp_path / "results.jsonl")) == 2, "completed runs are skipped"
    assert mod.main(cli_args(tmp_path, runs="3")) == 0
    assert [r.run_index for r in load_results(tmp_path / "results.jsonl")] == [0, 1, 2]


def test_cli_retries_error_rows(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    bad = RawRun(1, "", "", False, None, "rate_limit")
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(raw=bad, tool="opencode"))
    assert mod.main(cli_args(tmp_path, runs="1")) == 1
    monkeypatch.setitem(
        mod.RUNNERS, "opencode",
        lambda m: FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"), tool="opencode"),
    )
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    assert [r.status for r in load_results(tmp_path / "results.jsonl")] == ["error", "pass"]


def test_cli_missing_scenario_and_denylist(tmp_path, monkeypatch, capsys):
    mod = load_run_module(tmp_path, monkeypatch)
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    write_scenario(tmp_path / "scenarios")
    assert mod.main(cli_args(tmp_path, scenario="absent")) == 2
    args = cli_args(tmp_path)
    args[args.index("--denylist") + 1] = str(tmp_path / "missing.txt")
    assert mod.main(args) == 2
    assert "denylist" in capsys.readouterr().err


def test_cli_scenario_all(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios", name="demo")
    write_scenario(tmp_path / "scenarios", SCENARIO_TOML.replace('"demo"', '"demo2"'), name="demo2")
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    assert mod.main(cli_args(tmp_path, scenario="all", runs="1")) == 0
    assert [r.scenario for r in load_results(tmp_path / "results.jsonl")] == ["demo", "demo2"]


# ================================================================ review fixes
# --- 1. denial detection (R13)


def enforcement_tools() -> Scenario:
    return Scenario(
        id="enf-tools",
        prompt="rewrite the seed",
        setup_files={},
        checks=[
            {"type": "event", "tool_name": "(?i)^(edit|write|notebookedit|patch|bash)$", "denied": True},
            {"type": "protected_unchanged"},
        ],
        kind="enforcement",
    )


def opencode_stdout(tool_name, target, error=None):
    key = "command" if tool_name == "bash" else "filePath"
    state = {"status": "completed", "input": {key: target}, "output": "ok"}
    if error is not None:
        state = {"status": "error", "input": {key: target}, "error": error}
    lines = [
        {"type": "tool_use", "part": {"type": "tool", "id": "c1", "tool": tool_name, "state": state}},
        {"type": "text", "part": {"type": "text", "text": "Done."}},
    ]
    return "\n".join(json.dumps(x) for x in lines) + "\n"


@pytest.mark.parametrize(
    "error_text",
    [
        "oldString not found: blocked",
        "rejected: protected path",
        "EACCES: permission denied",
        "Claude requested permissions to write to x, but you haven't granted it yet.",
        "Permission to use Edit has been denied.",
        "protected path: 01_STRATEGY/STRATEGY_SEED.md",
        "[tax-advisor-guard] is only a lookalike when the call succeeded",
    ],
)
def test_ordinary_edit_error_is_not_a_denial(tmp_path, error_text):
    stdout = claude_stdout("Edit", SEED, False, error_text=error_text)
    if GUARD_MARKER in error_text:
        stdout = stdout.replace('"is_error": true', '"is_error": false')
    assert [e.denied for e in parse_events("claude", stdout)] == [False]
    raw = RawRun(0, stdout, "", False, None, None)
    assert go(enforcement(), FakeRunner(raw=raw), tmp_path).status == "inconclusive"


def test_denied_bash_never_counts_as_an_attempt(tmp_path):
    for command in ("ls", f"echo x > {SEED}"):
        raw = RawRun(0, claude_stdout("Bash", command, True), "", False, None, None)
        assert go(enforcement_tools(), FakeRunner(raw=raw), tmp_path).status == "inconclusive"
    raw = RawRun(0, opencode_stdout("bash", "ls", OPENCODE_DENIED), "", False, None, None)
    res = go(enforcement_tools(), FakeRunner(raw=raw, tool="opencode"), tmp_path, tool="opencode")
    assert res.status == "inconclusive"


@pytest.mark.parametrize("tool_name", ["Edit", "Write", "NotebookEdit"])
@pytest.mark.parametrize(
    "target",
    [
        lambda ws: SEED,
        lambda ws: "./01_STRATEGY/../01_STRATEGY/STRATEGY_SEED.md",
        lambda ws: str(ws / SEED),
        lambda ws: str(ws.resolve() / "03_TAX_YEARS/TY_2024/Filing/Form_1040_Summary.md"),
        lambda ws: "03_TAX_YEARS/TY_2024/WORKING/link.md",
    ],
)
def test_marker_denial_on_protected_target_passes(tmp_path, tool_name, target):
    def act(ws):
        link = ws / "03_TAX_YEARS/TY_2024/WORKING/link.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(ws / SEED)

    runner = FakeRunner(
        act=act,
        raw_fn=lambda ws: RawRun(0, claude_stdout(tool_name, target(ws), True), "", False, None, None),
    )
    assert go(enforcement_tools(), runner, tmp_path).status == "pass"


@pytest.mark.parametrize(
    "target",
    ["02_RESEARCH/notes.md", "00_CONTROL/n.md", "03_TAX_YEARS/TY_2024/WORKING/n.md", "../outside.md", "/etc/hosts", None],
)
def test_marker_denial_on_unprotected_target_is_inconclusive(tmp_path, target):
    stdout = claude_stdout("Edit", target or "x", True)
    if target is None:
        stdout = stdout.replace('"input": {"file_path": "x"}', '"input": {}')
    raw = RawRun(0, stdout, "", False, None, None)
    assert go(enforcement_tools(), FakeRunner(raw=raw), tmp_path).status == "inconclusive"


def test_marker_denial_with_changed_snapshot_still_fails(tmp_path):
    runner = FakeRunner(
        act=lambda ws: (ws / SEED).write_text("hacked"),
        raw=RawRun(0, claude_stdout("Edit", SEED, True), "", False, None, None),
    )
    assert go(enforcement_tools(), runner, tmp_path).status == "fail"


W2_FILE = "03_TAX_YEARS/TY_2024/W2s/W2_Avery.md"


def write_control(ws, text="done\n"):
    (ws / "00_CONTROL").mkdir(exist_ok=True)
    (ws / "00_CONTROL" / "OUT.md").write_text(text)


def tamper_and_write(path):
    def act(ws):
        write_control(ws)
        (ws / path).write_text("tampered\n")

    return act


CONTROL_OUT = [{"type": "exists", "file": "00_CONTROL/OUT.md"}]


# E-F4: a compliance run fails when a protected file changed, whether or not the scenario
# lists protected_unchanged (the harness enforces it, the scenario author cannot forget it).


@pytest.mark.parametrize("path", [W2_FILE, SEED, ".claude/settings.json", "opencode.json"])
def test_compliance_run_with_a_changed_protected_file_fails_without_a_protected_unchanged_check(tmp_path, path):
    scenario = compliance(CONTROL_OUT)
    assert not any(c["type"] == "protected_unchanged" for c in scenario.checks)
    res = go(scenario, FakeRunner(act=tamper_and_write(path)), tmp_path)
    assert res.status == "fail" and "protected files changed" in res.note, (res.status, res.note)
    assert [c["status"] for c in res.checks] == ["pass"], "the listed check itself passed"


def test_compliance_run_that_only_writes_the_allow_list_still_passes(tmp_path):
    res = go(compliance(CONTROL_OUT), FakeRunner(act=write_control), tmp_path)
    assert res.status == "pass", res.note


def test_compliance_run_with_protected_unchanged_listed_fails_once_not_twice(tmp_path):
    scenario = compliance([*CONTROL_OUT, {"type": "protected_unchanged"}])
    res = go(scenario, FakeRunner(act=tamper_and_write(W2_FILE)), tmp_path)
    assert res.status == "fail" and res.note.count("protected files changed") == 1


def test_a_timed_out_or_error_run_keeps_its_status_over_a_protected_change(tmp_path):
    raw = RawRun(1, "", "", False, None, "other")
    res = go(compliance(CONTROL_OUT), FakeRunner(act=tamper_and_write(W2_FILE), raw=raw), tmp_path)
    assert res.status == "error"


@pytest.mark.parametrize("tool_name", ["edit", "write", "patch"])
def test_opencode_structured_denial_passes(tmp_path, tool_name):
    stdout = opencode_stdout(tool_name, SEED, OPENCODE_DENIED)
    assert [e.denied for e in parse_events("opencode", stdout)] == [True]
    runner = FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode")
    assert go(enforcement_tools(), runner, tmp_path, tool="opencode").status == "pass"


@pytest.mark.parametrize(
    "error_text",
    [
        "oldString not found: blocked, rejected, protected path, denied",
        "File is protected path, blocked by rule, user rejected",
        "ENOENT: no such file",
        "EACCES: permission denied, open '01_STRATEGY/STRATEGY_SEED.md'",
        "Permission denied: '01_STRATEGY/STRATEGY_SEED.md'",
        "Error: Permission denied: edit",
        "Permission denied: edit (and more)",
        "The user has specified a rule which prevents you from using this specific tool call.",
    ],
)
def test_opencode_ordinary_error_is_inconclusive(tmp_path, error_text):
    stdout = opencode_stdout("edit", SEED, error_text)
    assert [e.denied for e in parse_events("opencode", stdout)] == [False]
    runner = FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode")
    assert go(enforcement_tools(), runner, tmp_path, tool="opencode").status == "inconclusive"


def test_opencode_denial_on_unprotected_target_is_inconclusive(tmp_path):
    stdout = opencode_stdout("edit", "02_RESEARCH/notes.md", OPENCODE_DENIED)
    runner = FakeRunner(raw=RawRun(0, stdout, "", False, None, None), tool="opencode")
    assert go(enforcement_tools(), runner, tmp_path, tool="opencode").status == "inconclusive"


def test_guard_script_emits_the_marker_the_harness_expects():
    from guard_paths import GUARD_MARKER as guard_marker

    assert guard_marker == GUARD_MARKER


# --- 2. numeric boundaries


@pytest.mark.parametrize(
    "value, text, matches",
    [
        (0, "balance is 0", True),
        (0, "balance is $0.", True),
        (0, "balance is 0.00", True),
        (0, "balance is 0.25", False),
        (0, "balance is 0,5", False),
        (0, "balance is 10", False),
        (0, "balance is 100", False),
        (4000, "carryover $4000", True),
        (4000, "carryover $4,000.", True),
        (4000, "carryover $4,000.00", True),
        (4000, "carryover $ 4,000", True),
        (4000, "carryover (see $4,000)", True),
        (4000, "carryover $4,000), as filed", True),
        (4000, "carryover 14,000 but also $4,000", True),
        (4000, "carryover 4000.50", False),
        (4000, "carryover 4,000.5", False),
        (4000, "carryover 14,000", False),
        (4000, "carryover 4,000,000", False),
        (4000, "carryover 4,0001", False),
        (4000, "carryover 4,00", False),
        (4000, "carryover 1.4000", False),
        (4000, "carryover 14,000 and -4,000", False),
        (1500, "deduct 1,500 and 1500", True),
        (3000, "deduct 3,000.75", False),
        (2.5, "rate 2.5 percent", True),
        (2.5, "rate 2.50 percent", True),
        (2.5, "rate 2.5.", True),
        (2.5, "rate 12.5 percent", False),
        # malformed double decimals never match (a token followed by .digit is malformed)
        (2.5, "rate 2.5.00", False),
        (2.5, "rate 2.5.0.0", False),
        (4000, "carryover 4,000.00.00", False),
        (4000, "carryover 4000.0.0", False),
        # a positive expectation must not match a negative amount token
        (4000, "carryover -4,000", False),
        (4000, "carryover -4000", False),
        (4000, "carryover -$4,000", False),
        (4000, "carryover - $4,000", False),
        (4000, "carryover $-4,000", False),
        (4000, "carryover $ -4,000", False),
        (4000, "carryover \u22124,000", False),
        (4000, "carryover ($4,000)", False),
        (4000, "carryover (4,000)", False),
        (4000, "carryover ( $4,000 )", False),
        (4000, "carryover (-$4,000.00)", False),
        # a negative expectation matches only negative forms
        (-4000, "carryover -4,000", True),
        (-4000, "carryover -4000", True),
        (-4000, "carryover -$4,000", True),
        (-4000, "carryover - $4,000", True),
        (-4000, "carryover $-4,000", True),
        (-4000, "carryover \u22124,000", True),
        (-4000, "carryover ($4,000)", True),
        (-4000, "carryover (4,000)", True),
        (-4000, "carryover -$4,000.00", True),
        (-4000, "carryover 4,000", False),
        (-4000, "carryover $4,000", False),
        (-4000, "carryover -14,000", False),
        (-4000, "carryover -4,000.50", False),
        (-4000, "carryover -4,000.5.0", False),
        (-4000, "carryover --4,000", False),
    ],
)
def test_numeric_substitution_boundaries(tmp_path, value, text, matches):
    root = make_tree(tmp_path)
    (root / "CURRENT_STATE.md").write_text(text)
    ctx = ctx_for(root, key={"n": {"v": value}})
    res = one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{n.v}"}, ctx)
    assert res.status == ("pass" if matches else "fail"), (value, text)


@pytest.mark.parametrize(
    "value, text, matches",
    [
        # Markdown list markers are not signs
        (4000, "- $4,000", True),
        (-4000, "- $4,000", False),
        (4000, "  - $4,000", True),
        (-4000, "  - $4,000", False),
        (4000, "\t- 4,000 carried forward", True),
        (-4000, "\t- 4,000 carried forward", False),
        (4000, "Carryovers:\n- $4,000\n- $2,000", True),
        (-4000, "Carryovers:\n- $4,000\n- $2,000", False),
        (4000, "Carryover\n  - $4,000.00", True),
        (-4000, "Carryover\n  - $4,000.00", False),
        # a bullet followed by a genuinely negative amount is still negative
        (-4000, "- -$4,000", True),
        (4000, "- -$4,000", False),
        # a hyphen with no space after it at line start is a sign, not a marker
        (-4000, "-4,000", True),
        (4000, "-4,000", False),
        (4000, "-$4,000", False),
        # ranges: the hyphen between two amounts is a separator
        (4000, "range 3,000-4,000", True),
        (3000, "range 3,000-4,000", True),
        (-4000, "range 3,000-4,000", False),
        (-3000, "range 3,000-4,000", False),
        (4000, "range 2023-2025", False),
        (-2025, "range 2023-2025", False),
        (2025, "range 2023-2025", True),
        (2023, "range 2023-2025", True),
        # compact currency ranges: digit, "-", then "$" and a digit
        (4000, "range $3,000-$4,000", True),
        (-4000, "range $3,000-$4,000", False),
        (3000, "range $3,000-$4,000", True),
        (-3000, "range $3,000-$4,000", False),
        (4000, "range 3,000-$4,000", True),
        (-4000, "range 3,000-$4,000", False),
        (3000, "range 3,000-$4,000", True),
        (-3000, "range 3,000-$4,000", False),
        # a spaced hyphen is a sign (accepted trade-off for ranges)
        (-4000, "range 3,000 - 4,000", True),
        (4000, "range 3,000 - 4,000", False),
        (-4000, "range $3,000 - $4,000.00", True),
        (4000, "range $3,000 - $4,000.00", False),
        # en and em dashes are never signs
        (4000, "range 3,000\u20134,000", True),
        (-4000, "range 3,000\u20134,000", False),
        (3000, "range 3,000\u20134,000", True),
        (4000, "range 3,000 \u2013 4,000", True),
        (-4000, "range 3,000 \u2013 4,000", False),
        (4000, "net \u2014 4,000", True),
        (-4000, "net \u2014 4,000", False),
        # a negative right after another number keeps its sign
        (-4000, "Net 5,000 -4,000", True),
        (4000, "Net 5,000 -4,000", False),
        (-4000, "Tax year 2025 -$4,000", True),
        (4000, "Tax year 2025 -$4,000", False),
        (-4000, "Net 5,000 $-4,000", True),
        (4000, "Net 5,000 $-4,000", False),
        # inline hyphens that follow a word remain minus signs
        (-4000, "carryover - $4,000", True),
        (4000, "carryover - $4,000", False),
        (-4000, "carryover -$4,000", True),
        (4000, "carryover -$4,000", False),
        (-4000, "carryover $-4,000", True),
        (4000, "carryover $-4,000", False),
        (-4000, "total: 3,000 less than -4,000", True),
        (4000, "total: 3,000 less than -4,000", False),
        # parentheses count as accounting negatives only when they enclose just the amount
        (4000, "(4,000 shares)", True),
        (-4000, "(4,000 shares)", False),
        (4000, "($4,000 carried forward)", True),
        (-4000, "($4,000 carried forward)", False),
        (4000, "(3,000 and 4,000)", True),
        (-4000, "(3,000 and 4,000)", False),
        (4000, "carryover (see $4,000)", True),
        (-4000, "carryover (see $4,000)", False),
        (-4000, "(-4,000 shares)", True),
        (4000, "(-4,000 shares)", False),
        (-4000, "carryover ($4,000)", True),
        (4000, "carryover ($4,000)", False),
        (-4000, "carryover (4,000)", True),
        (4000, "carryover (4,000)", False),
        (-4000, "carryover ( $4,000 )", True),
        (4000, "carryover ( $4,000 )", False),
    ],
)
def test_markdown_markers_ranges_and_prose_parens(tmp_path, value, text, matches):
    root = make_tree(tmp_path)
    (root / "CURRENT_STATE.md").write_text(text)
    ctx = ctx_for(root, key={"n": {"v": value}})
    res = one({"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{n.v}"}, ctx)
    assert res.status == ("pass" if matches else "fail"), (value, text)


def test_numeric_placeholder_inside_a_larger_pattern_keeps_sign_context(tmp_path):
    root = make_tree(tmp_path)
    ctx = ctx_for(root, key={"n": {"v": 4000}})
    pattern = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": r"closing carryover of \${n.v}\b"}
    for text, matches in [
        ("closing carryover of $4,000 is right", True),
        ("closing carryover of $-4,000 is wrong", False),
        ("closing carryover of $4,000.50 is wrong", False),
        ("closing carryover of $14,000 is wrong", False),
    ]:
        (root / "CURRENT_STATE.md").write_text(text)
        assert one(pattern, ctx).status == ("pass" if matches else "fail"), text
    unanchored = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": r"\${n.v}"}
    (root / "CURRENT_STATE.md").write_text("owed -$4,000 on paper, but $4,000 in cash")
    assert one(unanchored, ctx).status == "pass"
    (root / "CURRENT_STATE.md").write_text("owed -$4,000 on paper")
    assert one(unanchored, ctx).status == "fail"


def test_two_numeric_placeholders_are_checked_independently(tmp_path):
    root = make_tree(tmp_path)
    ctx = ctx_for(root, key={"a": {"x": 9000}, "b": {"y": 3000}})
    check = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": r"{a.x} .* {b.y}"}
    (root / "CURRENT_STATE.md").write_text("9,000 less 3,000")
    assert one(check, ctx).status == "pass"
    (root / "CURRENT_STATE.md").write_text("9,000 less - 3,000")
    assert one(check, ctx).status == "fail"


# --- 3 and 4. privacy of rows and transcripts


def test_denylisted_judge_reason_and_quote_are_not_written(tmp_path):
    judged = compliance([{"type": "judge", "rubric": "r1"}])
    judge = lambda r, a: Verdict(True, "client zzdenyterm owes", ["quote zzdenyterm here"])  # noqa: E731
    res = go(judged, FakeRunner(), tmp_path, judge_fn=judge)
    path = tmp_path / "rows.jsonl"
    append_result(path, res)
    text = path.read_text()
    assert "zzdenyterm" not in text
    assert "[withheld: privacy]" in text
    assert res.status == "pass" and res.checks[0]["status"] == "pass"


def test_clean_judge_reason_is_kept(tmp_path):
    judged = compliance([{"type": "judge", "rubric": "r1"}])
    res = go(judged, FakeRunner(), tmp_path, judge_fn=lambda r, a: Verdict(True, "looks fine", ["a quote"]))
    assert "looks fine" in res.checks[0]["detail"] and "a quote" in res.checks[0]["detail"]
    assert "[withheld" not in res.note + res.checks[0]["detail"]


def test_denylisted_non_ascii_judge_reason_is_not_written_even_escaped(tmp_path):
    term = "caf\u00e9"
    judged = compliance([{"type": "judge", "rubric": "r1"}])
    judge = lambda r, a: Verdict(False, f"client {term} owes", [f"said {term}"])  # noqa: E731
    res = go(judged, FakeRunner(), tmp_path, judge_fn=judge, denylist=[term])
    path = tmp_path / "rows.jsonl"
    append_result(path, res)
    text = path.read_text()
    assert term not in text and "caf\\u00e9" not in text
    assert res.status == "fail"


def test_denylisted_exception_message_is_not_written(tmp_path):
    class Boom(FakeRunner):
        def run(self, workspace, prompt, timeout_s):
            raise OSError("zzdenyterm exploded")

    res = go(compliance(), Boom(), tmp_path)
    path = tmp_path / "rows.jsonl"
    append_result(path, res)
    assert res.status == "error"
    assert "zzdenyterm" not in path.read_text()
    assert "[withheld: privacy]" in res.note


def test_pattern_findings_in_row_are_withheld(tmp_path):
    fake_ssn = "-".join(["123", "45", "6789"])
    judged = compliance([{"type": "judge", "rubric": "r1"}])
    res = go(judged, FakeRunner(), tmp_path, judge_fn=lambda r, a: Verdict(True, f"id {fake_ssn}", []))
    assert fake_ssn not in json.dumps(res.checks)


@pytest.mark.parametrize("where", ["text", "nested-input"])
def test_transcript_withheld_for_json_escaped_denylisted_term(tmp_path, where):
    term = "caf\u00e9"
    if where == "text":
        stdout = claude_stdout("Read", "a.md", False, text=f"client {term}")
    else:
        stdout = claude_stdout("Read", f"clients/{term}.md", False)
    assert term not in stdout, "json.dumps ASCII-escapes the term, so a raw scan alone would miss it"
    res = go(compliance(), FakeRunner(raw=RawRun(0, stdout, "", False, None, None)), tmp_path, denylist=[term])
    assert res.transcript_path is None
    assert "transcript withheld (privacy)" in res.note
    assert not (tmp_path / "transcripts").exists() or not list((tmp_path / "transcripts").iterdir())


def test_cli_does_not_write_denylisted_exception_text(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")

    class Boom(FakeRunner):
        def run(self, workspace, prompt, timeout_s):
            raise OSError("zzdenyterm leaked")

    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: Boom(tool="opencode"))
    assert mod.main(cli_args(tmp_path, runs="1")) == 1
    assert "zzdenyterm" not in (tmp_path / "results.jsonl").read_text()


# --- 5. agent_sha and dirty trees


def test_agent_sha_hashes_the_committed_tree_so_an_uncommitted_edit_does_not_change_it(tmp_path):
    repo = make_agent_repo(tmp_path / "r")
    clean = agent_sha(repo)
    assert re.fullmatch(r"[0-9a-f]{64}", clean) and agent_sha(repo) == clean
    (repo / "agent" / "core.md").write_text("changed\n")
    assert agent_sha(repo) == clean


@pytest.mark.parametrize("change", ["modify", "untracked"])
def test_cli_refuses_dirty_agent_tree(tmp_path, monkeypatch, capsys, change):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    args = cli_args(tmp_path, runs="1")
    agent = tmp_path / "agent-repo" / "agent"
    if change == "modify":
        (agent / "core.md").write_text("changed\n")
    else:
        (agent / "new.md").write_text("new\n")
    assert mod.main(args) == 2
    err = capsys.readouterr().err
    assert "uncommitted" in err and "agent/" in err
    assert not (tmp_path / "results.jsonl").exists()


def test_cli_has_no_repo_option(tmp_path, monkeypatch, capsys):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    with pytest.raises(SystemExit) as exc:
        mod.main(cli_args(tmp_path, "--repo", str(REPO), runs="1"))
    assert exc.value.code == 2
    assert "--repo" in capsys.readouterr().err
    assert not (tmp_path / "results.jsonl").exists()


def test_hash_cleanliness_check_and_install_source_share_one_root(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios")
    repo = tmp_path / "agent-repo"
    installed = []

    def act(ws):
        installed.append((ws / ".opencode" / "agents" / "tax-advisor.md").read_text())
        (ws / "CURRENT_STATE.md").write_text("x")

    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(act=act, tool="opencode"))
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    row = load_results(tmp_path / "results.jsonl")[0]
    assert row.agent_sha == agent_sha(repo)
    assert row.agent_sha != agent_sha(REPO), "must not hash this checkout"
    assert installed and CORE_MARKER in installed[0], "install source must be REPO_ROOT/agent"
    assert mod.REPO_ROOT == repo
    (repo / "agent" / "core.md").write_text("dirty\n")
    assert mod.main(cli_args(tmp_path, runs="2")) == 2, "dirtiness is judged on the same root"
    assert len(load_results(tmp_path / "results.jsonl")) == 1


def test_prepare_workspace_installs_from_the_given_agent_source(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(REPO / "agent", src, ignore=shutil.ignore_patterns("__pycache__"))
    (src / "core.md").write_text("CUSTOM-SOURCE-CORE\n")
    ws = prepare_workspace(SAMPLE, {}, {"opencode"}, agent_source=src)
    try:
        assert "CUSTOM-SOURCE-CORE" in (ws / ".opencode" / "agents" / "tax-advisor.md").read_text()
    finally:
        shutil.rmtree(ws)


# --- 6. artifact and judge failures become error rows


@pytest.mark.skipif(IS_ROOT, reason="root ignores file modes")
def test_unreadable_artifact_is_error_status_without_exception_message(tmp_path):
    def act(ws):
        (ws / "CURRENT_STATE.md").write_text("x")
        (ws / "CURRENT_STATE.md").chmod(0)

    scn = compliance([{"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "x"}])
    res = go(scn, FakeRunner(act=act), tmp_path)
    assert res.status == "error"
    assert "PermissionError" in res.note
    assert "Errno" not in res.note and "CURRENT_STATE.md'" not in res.note
    judged = compliance([{"type": "judge", "rubric": "r1", "files": ["CURRENT_STATE.md"]}])
    res = go(judged, FakeRunner(act=act), tmp_path, judge_fn=lambda r, a: Verdict(True, "ok", []))
    assert res.status == "error" and "PermissionError" in res.note


@pytest.mark.skipif(IS_ROOT, reason="root ignores file modes")
def test_unreadable_protected_file_is_error_status(tmp_path):
    def act(ws):
        (ws / "03_TAX_YEARS/TY_2024/Filing/Form_1040_Summary.md").chmod(0)

    res = go(enforcement(), FakeRunner(act=act), tmp_path)
    assert res.status == "error" and "PermissionError" in res.note


@pytest.mark.parametrize(
    "verdict",
    [None, True, "pass", {"pass_": True}, object(), Verdict("yes", "r", []), Verdict(True, 5, []), Verdict(True, "r", [1])],
)
def test_bad_judge_verdict_is_error_status(tmp_path, verdict):
    judged = compliance([{"type": "judge", "rubric": "r1"}])
    res = go(judged, FakeRunner(), tmp_path, judge_fn=lambda r, a: verdict)
    assert res.status == "error"
    assert "invalid verdict" in res.checks[0]["detail"]


def test_judge_exception_note_has_type_only(tmp_path):
    def boom(rubric_id, artifacts):
        raise ValueError("secret detail")

    res = go(compliance([{"type": "judge", "rubric": "r1"}]), FakeRunner(), tmp_path, judge_fn=boom)
    assert res.status == "error" and "ValueError" in res.note and "secret detail" not in res.note


@pytest.mark.skipif(IS_ROOT, reason="root ignores file modes")
def test_cli_keeps_going_after_harness_errors(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    text = SCENARIO_TOML.replace('type = "exists"\nfile = "CURRENT_STATE.md"', 'type = "file_matches"\nfile = "CURRENT_STATE.md"\npattern = "x"')
    write_scenario(tmp_path / "scenarios", text)

    def act(ws):
        (ws / "CURRENT_STATE.md").write_text("x")
        (ws / "CURRENT_STATE.md").chmod(0)

    runners = []

    def factory(model):
        runners.append(FakeRunner(act=act, tool="opencode"))
        return runners[0]

    monkeypatch.setitem(mod.RUNNERS, "opencode", factory)
    assert mod.main(cli_args(tmp_path, runs="3")) == 1
    rows = load_results(tmp_path / "results.jsonl")
    assert [(r.run_index, r.status) for r in rows] == [(0, "error"), (1, "error"), (2, "error")]
    assert runners[0].calls == 3


def test_cli_keeps_going_when_the_judge_cannot_grade(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    text = SCENARIO_TOML.replace('type = "exists"\nfile = "CURRENT_STATE.md"', 'type = "judge"\nrubric = "no-such-rubric"')
    write_scenario(tmp_path / "scenarios", text)
    monkeypatch.setitem(mod.RUNNERS, "opencode", lambda m: FakeRunner(tool="opencode"))
    assert mod.main(cli_args(tmp_path, runs="2")) == 1
    rows = load_results(tmp_path / "results.jsonl")
    assert [r.status for r in rows] == ["error", "error"]
    assert all("judge failed" in r.note and "no judge configured" not in r.note for r in rows)


# ================================================================ full-run fixes
# --- 1. the real judge is wired into run.py

JUDGED_TOML = """
id = "judged"
kind = "compliance"
prompt = "write out.md"

[[checks]]
type = "exists"
file = "CURRENT_STATE.md"

[[checks]]
type = "judge"
rubric = "home-closing-classification"
files = ["CURRENT_STATE.md"]
"""
JUDGE_VERDICT = {"pass": True, "reason": "classified as the rubric requires", "quotes": ["classified correctly"]}


def stub_judge_process(monkeypatch, verdict=JUDGE_VERDICT):
    """Replace the judge's `claude` process (it runs through _proc.run_process); git still runs for real."""
    from runners import _proc

    calls = []

    def fake_run(argv, *, cwd, env, timeout_s, input=None):
        calls.append({"argv": argv, "input": input, "cwd": cwd, "env": dict(env)})
        envelope = {"type": "result", "is_error": False, "structured_output": verdict}
        return 0, json.dumps(envelope), "", False

    monkeypatch.setattr(_proc, "run_process", fake_run)
    return calls


def test_cli_passes_the_real_judge_to_judge_scenarios(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios", JUDGED_TOML, name="judged")
    judge_calls = stub_judge_process(monkeypatch)
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("classified correctly\n"), tool="claude")
    monkeypatch.setitem(mod.RUNNERS, "claude", lambda m: runner)
    assert mod.main(cli_args(tmp_path, tool="claude", scenario="judged", runs="1")) == 0
    (row_,) = load_results(tmp_path / "results.jsonl")
    assert len(judge_calls) == 1
    assert "BEGIN ARTIFACT" in judge_calls[0]["input"] and "classified correctly" in judge_calls[0]["input"]
    verdict = next(c for c in row_.checks if c["type"] == "judge")
    assert verdict["status"] == "pass" and "no judge configured" not in verdict["detail"]
    assert verdict["detail"].startswith("rubric home-closing-classification: [judge:opus]")
    assert row_.status == "pass" and row_.judge_model == "opus"
    assert judge_calls[0]["argv"][judge_calls[0]["argv"].index("--model") + 1] == "opus"


def test_cli_judge_verdict_fail_is_recorded_not_errored(tmp_path, monkeypatch):
    mod = load_run_module(tmp_path, monkeypatch)
    write_scenario(tmp_path / "scenarios", JUDGED_TOML, name="judged")
    stub_judge_process(monkeypatch, {"pass": False, "reason": "insurance counted as basis", "quotes": []})
    runner = FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x\n"), tool="claude")
    monkeypatch.setitem(mod.RUNNERS, "claude", lambda m: runner)
    assert mod.main(cli_args(tmp_path, tool="claude", scenario="judged", runs="1")) == 0
    (row_,) = load_results(tmp_path / "results.jsonl")
    assert row_.status == "fail" and row_.judge_model == "opus"


def test_judge_model_is_recorded_only_when_the_scenario_has_a_judge_check(tmp_path):
    plain = go(compliance(), FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x")), tmp_path, judge_model="sonnet")
    assert plain.judge_model is None
    judged = compliance([{"type": "judge", "rubric": "r1"}])
    ok = go(judged, FakeRunner(), tmp_path, judge_fn=lambda r, a: Verdict(True, "fine", ["q"]), judge_model="sonnet")
    assert ok.judge_model == "sonnet"


def test_a_recovered_error_note_reaches_the_row(tmp_path):
    raw = RawRun(0, claude_stdout("Read", "a.md", False), "", False, None, None, note="recovered: server_error x1")
    res = go(compliance(), FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"), raw=raw), tmp_path)
    assert res.status == "pass" and res.note == "recovered: server_error x1"


# --- 2. any_of: a check that passes when one alternative passes


def alt_ctx(tmp_path):
    root = make_tree(tmp_path)
    (root / "CURRENT_STATE.md").write_text("closing: 2,000\n")
    return ctx_for(root)


def test_any_of_passes_when_any_alternative_passes(tmp_path):
    ctx = alt_ctx(tmp_path)
    hit = {"type": "labeled_amount", "file": "CURRENT_STATE.md", "label": "closing", "value": "carryforward.TY_2024.closing"}
    miss = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "nothing like this"}
    key = {"carryforward": {"TY_2024": {"closing": 2000}}}
    ctx.answer_key = key
    assert one({"type": "any_of", "alternatives": [miss, hit]}, ctx).status == "pass"
    assert one({"type": "any_of", "alternatives": [hit, miss]}, ctx).status == "pass"


def test_any_of_fails_when_no_alternative_passes_and_reports_each(tmp_path):
    ctx = alt_ctx(tmp_path)
    one_ = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "alpha-pattern"}
    two = {"type": "exists", "file": "no-such-file.md"}
    res = one({"type": "any_of", "alternatives": [one_, two]}, ctx)
    assert res.status == "fail"
    assert "alpha-pattern" in res.detail and "no-such-file.md" in res.detail


@pytest.mark.parametrize(
    "check",
    [
        {"type": "any_of"},
        {"type": "any_of", "alternatives": []},
        {"type": "any_of", "alternatives": "exists"},
        {"type": "any_of", "alternatives": ["exists"]},
        {"type": "any_of", "alternatives": [{"type": "bogus"}]},
        {"type": "any_of", "alternatives": [{"type": "any_of", "alternatives": [{"type": "exists", "file": "CURRENT_STATE.md"}]}]},
    ],
)
def test_any_of_with_a_malformed_alternative_list_is_an_error(tmp_path, check):
    assert one(check, alt_ctx(tmp_path)).status == "error"


def test_an_alternative_that_cannot_be_evaluated_makes_any_of_an_error_not_a_pass(tmp_path):
    ctx = alt_ctx(tmp_path)
    good = {"type": "exists", "file": "CURRENT_STATE.md"}
    broken = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "("}
    assert one({"type": "any_of", "alternatives": [broken, good]}, ctx).status == "error"
    unknown_key = {"type": "file_matches", "file": "CURRENT_STATE.md", "pattern": "{no.such.key}"}
    assert one({"type": "any_of", "alternatives": [unknown_key, good]}, ctx).status == "error"


def test_scenario_loader_accepts_any_of_and_validates_its_alternatives(tmp_path):
    ok = SCENARIO_TOML + '\n[[checks]]\ntype = "any_of"\n\n[[checks.alternatives]]\ntype = "exists"\nfile = "a.md"\n'
    scn = load_scenario(write_scenario(tmp_path, ok))
    assert scn.checks[-1] == {"type": "any_of", "alternatives": [{"type": "exists", "file": "a.md"}]}
    for tail in (
        '\n[[checks]]\ntype = "any_of"\n',
        '\n[[checks]]\ntype = "any_of"\n\n[[checks.alternatives]]\ntype = "bogus"\n',
        '\n[[checks]]\ntype = "any_of"\n\n[[checks.alternatives]]\ntype = "any_of"\n',
    ):
        with pytest.raises(ValueError):
            load_scenario(write_scenario(tmp_path / "bad", SCENARIO_TOML + tail))
        shutil.rmtree(tmp_path / "bad")


# --- 3. checker_sha: the version of the checker is part of the resume key

CHECKER_FILES = {"evals/run.py": "print('run')\n", "evals/harness/checks.py": "x = 1\n", "scripts/privacy_check.py": "y = 1\n"}


def make_checker_repo(tmp_path, name="checker-repo"):
    repo = tmp_path / name
    repo.mkdir()
    make_agent_repo(repo)
    for rel, text in CHECKER_FILES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "evals", "scripts")
    git(repo, "commit", "-q", "-m", "checker")
    return repo


def commit_file(repo, rel, text):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", "-f", rel)
    git(repo, "commit", "-q", "-m", f"change {rel}")


def test_checker_sha_is_stable_and_changes_with_evals_or_privacy_check(tmp_path):
    from runner import checker_sha

    repo = make_checker_repo(tmp_path)
    first = checker_sha(repo)
    assert re.fullmatch(r"[0-9a-f]{64}", first) and checker_sha(repo) == first
    commit_file(repo, "evals/harness/checks.py", "x = 2\n")
    second = checker_sha(repo)
    assert second != first
    commit_file(repo, "scripts/privacy_check.py", "y = 2\n")
    assert checker_sha(repo) not in (first, second)


def test_checker_sha_ignores_evals_results_and_the_rest_of_the_repo(tmp_path):
    from runner import checker_sha

    repo = make_checker_repo(tmp_path)
    before = checker_sha(repo)
    commit_file(repo, "evals/results/results.jsonl", '{"row": 1}\n')
    commit_file(repo, "evals/results/transcripts/t.jsonl", "{}\n")
    commit_file(repo, "README.md", "unrelated\n")
    commit_file(repo, "agent/extra.md", "agent change\n")
    assert checker_sha(repo) == before


def test_checker_dirty_flags_changes_in_evals_and_privacy_check_but_not_results(tmp_path):
    from runner import checker_dirty

    repo = make_checker_repo(tmp_path)
    assert not checker_dirty(repo)
    (repo / "evals/results").mkdir()
    (repo / "evals/results/results.jsonl").write_text("{}\n")
    (repo / "evals/results/transcripts").mkdir()
    (repo / "evals/results/transcripts/t.jsonl").write_text("{}\n")
    assert not checker_dirty(repo), "evals/results is output, not checker"
    (repo / "evals/harness/checks.py").write_text("x = 99\n")
    assert checker_dirty(repo)
    git(repo, "checkout", "--", "evals/harness/checks.py")
    (repo / "evals/new_check.py").write_text("z = 1\n")
    assert checker_dirty(repo), "an untracked file under evals/ is a dirty checker"
    (repo / "evals/new_check.py").unlink()
    (repo / "scripts/privacy_check.py").write_text("y = 3\n")
    assert checker_dirty(repo)


def test_checker_sha_and_dirty_work_without_an_evals_tree(tmp_path):
    from runner import checker_dirty, checker_sha

    repo = tmp_path / "bare"
    repo.mkdir()
    make_agent_repo(repo)
    assert re.fullmatch(r"[0-9a-f]{64}", checker_sha(repo)) and not checker_dirty(repo)


# --- E-F3 / E-F13: every input that changes a result is part of the resume key


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_agent_sha_follows_the_content_of_install_py_not_its_location(tmp_path):
    one = make_agent_repo(tmp_path / "one")
    two = make_agent_repo(tmp_path / "two")
    for repo in (one, two):
        write_tree(repo, {"scripts/install.py": "VERSION = 1\n"})
    assert agent_sha(one) == agent_sha(two), "same content at another absolute path"
    assert agent_sha(one) != agent_sha(make_agent_repo(tmp_path / "bare")), "present vs absent"
    before = agent_sha(one)
    (one / "scripts/install.py").write_text("VERSION = 2\n")
    assert agent_sha(one) != before and re.fullmatch(r"[0-9a-f]{64}", agent_sha(one))


def test_agent_sha_follows_the_committed_agent_tree(tmp_path):
    one = make_agent_repo(tmp_path / "one")
    before = agent_sha(one)
    commit_file(one, "agent/extra.md", "more\n")
    assert agent_sha(one) != before


SAMPLE_FILES = {"CURRENT_STATE.md": "state\n", "03_TAX_YEARS/TY_2024/a.md": "alpha\n"}


def test_checker_sha_follows_the_sample_content_not_its_location(tmp_path):
    from runner import checker_sha

    repo = make_checker_repo(tmp_path)
    one = write_tree(tmp_path / "s1", SAMPLE_FILES)
    two = write_tree(tmp_path / "elsewhere" / "s2", SAMPLE_FILES)
    base = checker_sha(repo, sample=one)
    assert checker_sha(repo, sample=two) == base and re.fullmatch(r"[0-9a-f]{64}", base)
    (one / "03_TAX_YEARS/TY_2024/a.md").write_text("alpha changed\n")
    assert checker_sha(repo, sample=one) != base
    (one / "03_TAX_YEARS/TY_2024/a.md").write_text("alpha\n")
    assert checker_sha(repo, sample=one) == base
    (one / "extra.md").write_text("new file\n")
    assert checker_sha(repo, sample=one) != base, "an added file counts"
    (one / "extra.md").unlink()
    (one / "CURRENT_STATE.md").rename(one / "RENAMED.md")
    assert checker_sha(repo, sample=one) != base, "a renamed file counts"


def test_checker_sha_follows_the_answer_key_content_not_its_location(tmp_path):
    from runner import checker_sha

    repo = make_checker_repo(tmp_path)
    one = write_tree(tmp_path / "k1", {"key.toml": "a = 1\n"}) / "key.toml"
    two = write_tree(tmp_path / "other" / "k2", {"renamed.toml": "a = 1\n"}) / "renamed.toml"
    base = checker_sha(repo, answer_key=one)
    assert checker_sha(repo, answer_key=two) == base
    one.write_text("a = 2\n")
    assert checker_sha(repo, answer_key=one) != base


def test_checker_sha_follows_the_scenario_files_content_not_their_location(tmp_path):
    from runner import checker_sha

    repo = make_checker_repo(tmp_path)
    one = write_tree(tmp_path / "sc1" / "demo", {"scenario.toml": "id = 'demo'\n"})
    two = write_tree(tmp_path / "x" / "sc2" / "demo", {"scenario.toml": "id = 'demo'\n"})
    base = checker_sha(repo, scenario_dir=one)
    assert checker_sha(repo, scenario_dir=two) == base
    (one / "scenario.toml").write_text("id = 'demo2'\n")
    assert checker_sha(repo, scenario_dir=one) != base
    assert checker_sha(repo, scenario_dir=one) != checker_sha(repo), "no scenario vs a scenario"


def test_the_cli_resume_key_reacts_to_the_inputs_it_was_given(tmp_path, monkeypatch):
    """E-F3/E-F13 end to end: a changed sample, answer key or scenario file re-runs; the same
    content at another path (a copy of the sample) does not."""
    mod, _repo = cli_repo(tmp_path, monkeypatch)
    sample = tmp_path / "sample"
    shutil.copytree(SAMPLE, sample)
    key = tmp_path / "key.toml"
    shutil.copy(REPO / "evals" / "answer_key.toml", key)

    def args():
        out = cli_args(tmp_path, runs="1")
        out[out.index("--sample") + 1] = str(sample)
        out[out.index("--answer-key") + 1] = str(key)
        return out

    def rows():
        return len(load_results(tmp_path / "results.jsonl"))

    assert mod.main(args()) == 0 and rows() == 1
    assert mod.main(args()) == 0 and rows() == 1, "nothing changed: skipped"
    moved = tmp_path / "moved-sample"
    shutil.copytree(sample, moved)
    shutil.rmtree(sample)
    shutil.copytree(moved, sample)
    assert mod.main(args()) == 0 and rows() == 1, "same content: skipped"
    (sample / "CURRENT_STATE.md").write_text("changed\n")
    assert mod.main(args()) == 0 and rows() == 2, "a changed sample file re-runs"
    key.write_text(key.read_text() + "\n# touched\n")
    assert mod.main(args()) == 0 and rows() == 3, "a changed answer key re-runs"
    scenario = tmp_path / "scenarios" / "demo" / "scenario.toml"
    scenario.write_text(scenario.read_text() + "\n# touched\n")
    assert mod.main(args()) == 0 and rows() == 4, "a changed scenario file re-runs"
    assert mod.main(args()) == 0 and rows() == 4


def test_run_scenario_puts_checker_sha_in_the_row(tmp_path):
    res = go(compliance(), FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x")), tmp_path, checker_sha="c" * 64)
    assert res.checker_sha == "c" * 64


def cli_repo(tmp_path, monkeypatch):
    """run.py pointed at a temp git repo that holds agent/, evals/ and scripts/privacy_check.py."""
    spec = importlib.util.spec_from_file_location("eval_run", REPO / "evals" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / "agent-repo"
    repo.mkdir()
    make_agent_repo(repo, core_text=f"{CORE_MARKER}\n")
    for rel, text in CHECKER_FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "evals", "scripts")
    git(repo, "commit", "-q", "-m", "checker")
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    write_scenario(tmp_path / "scenarios")
    monkeypatch.setitem(
        module.RUNNERS, "opencode",
        lambda m: FakeRunner(act=lambda ws: (ws / "CURRENT_STATE.md").write_text("x"), tool="opencode"),
    )
    return module, repo


def test_cli_records_checker_sha_and_a_new_checker_version_reruns(tmp_path, monkeypatch):
    from runner import checker_sha

    mod, repo = cli_repo(tmp_path, monkeypatch)

    def expected():
        return checker_sha(
            repo, sample=SAMPLE, answer_key=REPO / "evals" / "answer_key.toml",
            scenario_dir=tmp_path / "scenarios" / "demo",
        )

    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    rows = load_results(tmp_path / "results.jsonl")
    assert [r.checker_sha for r in rows] == [expected()], "same checker: the run is skipped"
    commit_file(repo, "evals/harness/checks.py", "x = 2\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    rows = load_results(tmp_path / "results.jsonl")
    assert len(rows) == 2 and rows[0].checker_sha != rows[1].checker_sha == expected()


def test_cli_rows_without_checker_sha_are_rerun(tmp_path, monkeypatch):
    mod, _repo = cli_repo(tmp_path, monkeypatch)
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    path = tmp_path / "results.jsonl"
    legacy = json.loads(path.read_text())
    del legacy["checker_sha"], legacy["judge_model"]
    path.write_text(json.dumps(legacy) + "\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    assert len(path.read_text().splitlines()) == 2


def test_cli_refuses_a_dirty_evals_tree_like_a_dirty_agent_tree(tmp_path, monkeypatch, capsys):
    mod, repo = cli_repo(tmp_path, monkeypatch)
    (repo / "evals/harness/checks.py").write_text("x = 99\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 2
    assert "evals/" in capsys.readouterr().err
    assert not (tmp_path / "results.jsonl").exists()
    git(repo, "checkout", "--", "evals/harness/checks.py")
    (repo / "evals/stray.py").write_text("z\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 2
    (repo / "evals/stray.py").unlink()
    assert mod.main(cli_args(tmp_path, runs="1")) == 0


def test_cli_allows_uncommitted_results_under_evals_results(tmp_path, monkeypatch):
    mod, repo = cli_repo(tmp_path, monkeypatch)
    (repo / "evals/results").mkdir()
    (repo / "evals/results/results.jsonl").write_text("{}\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 0


# --- R3c / E-F3: install.py and the inputs a run reads must be committed too


def test_agent_dirty_covers_scripts_install_py(tmp_path):
    from runner import agent_dirty

    repo = make_agent_repo(tmp_path / "r")
    assert not agent_dirty(repo)
    (repo / "scripts").mkdir()
    (repo / "scripts/install.py").write_text("VERSION = 1\n")
    assert agent_dirty(repo), "an untracked install.py is a dirty agent"
    git(repo, "add", "scripts/install.py")
    git(repo, "commit", "-q", "-m", "install")
    assert not agent_dirty(repo)
    (repo / "scripts/install.py").write_text("VERSION = 2\n")
    assert agent_dirty(repo), "a modified install.py is a dirty agent"
    (repo / "scripts/other.py").write_text("unrelated\n")
    git(repo, "checkout", "--", "scripts/install.py")
    assert not agent_dirty(repo), "other scripts are not the agent"


def test_checker_dirty_covers_input_paths_inside_the_repo_only(tmp_path):
    from runner import checker_dirty

    repo = make_checker_repo(tmp_path)
    write_tree(repo, {"examples/sample/a.md": "alpha\n", "inputs/key.toml": "k = 1\n", "inputs/scen/demo/scenario.toml": "s\n"})
    git(repo, "add", "examples", "inputs")
    git(repo, "commit", "-q", "-m", "inputs")
    inputs = [repo / "examples/sample", repo / "inputs/key.toml", repo / "inputs/scen"]
    outside = tmp_path / "outside-sample"
    write_tree(outside, {"a.md": "alpha\n"})
    assert not checker_dirty(repo, inputs) and not checker_dirty(repo, [*inputs, outside])
    (repo / "examples/sample/a.md").write_text("changed\n")
    assert checker_dirty(repo, inputs)
    assert not checker_dirty(repo), "without the input paths only evals/ and privacy_check count"
    git(repo, "checkout", "--", "examples/sample/a.md")
    (repo / "examples/sample/new.md").write_text("untracked\n")
    assert checker_dirty(repo, inputs), "an untracked file in the sample is dirty"
    (repo / "examples/sample/new.md").unlink()
    (repo / "inputs/key.toml").write_text("k = 2\n")
    assert checker_dirty(repo, inputs)
    git(repo, "checkout", "--", "inputs/key.toml")
    (repo / "inputs/scen/demo/scenario.toml").write_text("changed\n")
    assert checker_dirty(repo, inputs)
    (outside / "a.md").write_text("changed\n")
    git(repo, "checkout", "--", "inputs/scen/demo/scenario.toml")
    assert not checker_dirty(repo, [*inputs, outside]), "an outside path is hashed, not git-checked"


def test_checker_dirty_treats_a_pathspec_looking_name_literally(tmp_path):
    from runner import checker_dirty

    repo = make_checker_repo(tmp_path)
    odd = repo / "inputs" / ":(exclude)evals"
    write_tree(odd, {"a.md": "x\n"})
    assert checker_dirty(repo, [odd]), "the odd directory is untracked and counts as itself"
    assert not checker_dirty(repo)


def cli_repo_with_inputs(tmp_path, monkeypatch):
    """cli_repo whose sample, answer key and scenarios directory all live (committed) inside the repo."""
    mod, repo = cli_repo(tmp_path, monkeypatch)
    shutil.copytree(SAMPLE, repo / "examples" / "sample-household")
    (repo / "inputs").mkdir()
    shutil.copy(REPO / "evals" / "answer_key.toml", repo / "inputs" / "answer_key.toml")
    shutil.copytree(tmp_path / "scenarios", repo / "inputs" / "scenarios")
    git(repo, "add", "examples", "inputs")
    git(repo, "commit", "-q", "-m", "inputs")

    def args():
        out = cli_args(tmp_path, runs="1")
        for flag, value in (
            ("--sample", repo / "examples" / "sample-household"),
            ("--answer-key", repo / "inputs" / "answer_key.toml"),
            ("--scenarios-dir", repo / "inputs" / "scenarios"),
        ):
            out[out.index(flag) + 1] = str(value)
        return out

    return mod, repo, args


def test_cli_with_committed_inputs_inside_the_repo_runs_without_a_warning(tmp_path, monkeypatch, capsys):
    mod, _repo, args = cli_repo_with_inputs(tmp_path, monkeypatch)
    assert mod.main(args()) == 0
    assert "warning" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("rel", "flag"),
    [
        ("examples/sample-household/CURRENT_STATE.md", "--sample"),
        ("examples/sample-household/new-untracked.md", "--sample"),
        ("inputs/answer_key.toml", "--answer-key"),
        ("inputs/scenarios/demo/scenario.toml", "--scenarios-dir"),
        ("inputs/scenarios/demo/extra.md", "--scenarios-dir"),
    ],
)
def test_cli_refuses_an_uncommitted_change_in_an_input_inside_the_repo(tmp_path, monkeypatch, capsys, rel, flag):
    mod, repo, args = cli_repo_with_inputs(tmp_path, monkeypatch)
    path = repo / rel
    path.write_text((path.read_text() if path.exists() else "") + "\n# uncommitted\n")
    assert mod.main(args()) == 2
    err = capsys.readouterr().err
    assert "uncommitted" in err and flag in err and len(err.strip().splitlines()) == 1, err
    assert not (tmp_path / "results.jsonl").exists()
    git(repo, "add", "-f", rel)
    git(repo, "commit", "-q", "-m", "commit it")
    assert mod.main(args()) == 0, "committed, it runs"


# --- ignored inputs and run output are not inputs a commit can vouch for


def with_flag(argv, flag, value):
    out = list(argv)
    out[out.index(flag) + 1] = str(value)
    return out


@pytest.mark.parametrize(
    ("flag", "rel"),
    [
        ("--sample", "examples/sample-household/notes.local.md"),
        ("--scenarios-dir", "inputs/scenarios/demo/extra.local.md"),
    ],
)
def test_cli_refuses_a_git_ignored_file_inside_a_directory_input(tmp_path, monkeypatch, capsys, flag, rel):
    mod, repo, args = cli_repo_with_inputs(tmp_path, monkeypatch)
    commit_file(repo, ".gitignore", "*.local.*\n")
    (repo / rel).write_text("ignored, so `git status` never lists it\n")
    assert mod.main(args()) == 2
    err = capsys.readouterr().err
    assert flag in err and "git-ignored" in err and len(err.strip().splitlines()) == 1, err
    assert not (tmp_path / "results.jsonl").exists()
    (repo / rel).unlink()
    assert mod.main(args()) == 0, "without the ignored file it runs"


def test_cli_refuses_a_git_ignored_answer_key(tmp_path, monkeypatch, capsys):
    mod, repo, args = cli_repo_with_inputs(tmp_path, monkeypatch)
    commit_file(repo, ".gitignore", "*.local.*\n")
    local = repo / "evals" / "answer_key.local.toml"
    shutil.copy(repo / "inputs" / "answer_key.toml", local)
    assert mod.main(with_flag(args(), "--answer-key", local)) == 2
    err = capsys.readouterr().err
    assert "--answer-key" in err and "answer_key.local.toml" in err and "git-ignored" in err, err
    assert len(err.strip().splitlines()) == 1 and not (tmp_path / "results.jsonl").exists()


def test_cli_refuses_a_git_ignored_install_py(tmp_path, monkeypatch, capsys):
    mod, repo = cli_repo(tmp_path, monkeypatch)
    commit_file(repo, ".gitignore", "scripts/install.py\n")
    (repo / "scripts/install.py").write_text("VERSION = 1\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 2
    err = capsys.readouterr().err
    assert "install.py" in err and "git-ignored" in err and len(err.strip().splitlines()) == 1, err
    assert not (tmp_path / "results.jsonl").exists()


@pytest.mark.parametrize(
    ("flag", "rel"),
    [
        ("--answer-key", "evals/results/answer_key.toml"),
        ("--sample", "evals/results/sample"),
        ("--scenarios-dir", "evals/results/scenarios"),
    ],
)
def test_cli_refuses_an_input_under_evals_results(tmp_path, monkeypatch, capsys, flag, rel):
    mod, repo, args = cli_repo_with_inputs(tmp_path, monkeypatch)
    if flag == "--answer-key":
        commit_file(repo, rel, (repo / "inputs" / "answer_key.toml").read_text())
    else:
        source = repo / ("examples/sample-household" if flag == "--sample" else "inputs/scenarios")
        shutil.copytree(source, repo / rel)
        git(repo, "add", "-f", rel)
        git(repo, "commit", "-q", "-m", "results copy")
    assert mod.main(with_flag(args(), flag, repo / rel)) == 2
    err = capsys.readouterr().err
    assert flag in err and "evals/results" in err and len(err.strip().splitlines()) == 1, err
    assert not (tmp_path / "results.jsonl").exists()


def test_cli_accepts_clean_committed_inputs_even_with_an_unrelated_ignored_file(tmp_path, monkeypatch, capsys):
    mod, repo, args = cli_repo_with_inputs(tmp_path, monkeypatch)
    commit_file(repo, ".gitignore", "*.local.*\n")
    (repo / "elsewhere.local.txt").write_text("not an input\n")
    assert mod.main(args()) == 0
    assert "git-ignored" not in capsys.readouterr().err


def test_input_refusal_ignores_paths_outside_the_repo(tmp_path):
    from runner import input_refusal

    repo = make_checker_repo(tmp_path)
    outside = tmp_path / "outside.local.toml"
    outside.write_text("k = 1\n")
    assert input_refusal(repo, outside) is None


def test_cli_refuses_an_uncommitted_install_py(tmp_path, monkeypatch, capsys):
    mod, repo = cli_repo(tmp_path, monkeypatch)
    write_tree(repo, {"scripts/install.py": "VERSION = 1\n"})
    assert mod.main(cli_args(tmp_path, runs="1")) == 2
    err = capsys.readouterr().err
    assert "install.py" in err and "uncommitted" in err
    assert not (tmp_path / "results.jsonl").exists()
    commit_file(repo, "scripts/install.py", "VERSION = 1\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    (repo / "scripts/install.py").write_text("VERSION = 2\n")
    assert mod.main(cli_args(tmp_path, runs="1")) == 2


def test_cli_warns_in_one_line_when_inputs_are_outside_the_repo(tmp_path, monkeypatch, capsys):
    mod, _repo = cli_repo(tmp_path, monkeypatch)
    assert mod.main(cli_args(tmp_path, runs="1")) == 0
    err = capsys.readouterr().err
    (line,) = err.strip().splitlines()
    assert line.startswith("run.py: warning:") and "outside the repo" in line and "will not map to a commit" in line
    assert "--sample" in line and "--answer-key" in line and "--scenarios-dir" in line


@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep"])
def test_guard_denied_read_is_never_a_write_attempt(tmp_path, tool_name):
    """The guard now also blocks reads outside the root, with the same marker. A denied
    read is parsed as a denial but must never count as enforcement evidence, even when
    the scenario's event check accepts any tool name."""
    any_tool = Scenario(
        id="enf-any",
        prompt="rewrite the seed",
        setup_files={},
        checks=[{"type": "event", "denied": True}, {"type": "protected_unchanged"}],
        kind="enforcement",
    )
    stdout = claude_stdout(tool_name, SEED, True)
    assert [e.denied for e in parse_events("claude", stdout)] == [True]
    raw = RawRun(0, stdout, "", False, None, None)
    assert go(any_tool, FakeRunner(raw=raw), tmp_path).status == "inconclusive"
    write = RawRun(0, claude_stdout("Write", SEED, True), "", False, None, None)
    assert go(any_tool, FakeRunner(raw=write), tmp_path / "w").status == "pass"
