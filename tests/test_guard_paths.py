import ast
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import guard_paths
from guard_paths import is_protected

SCRIPT = Path(__file__).resolve().parent.parent / "agent" / "claude" / "hooks" / "guard_paths.py"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for d in (
        "00_CONTROL",
        "01_STRATEGY",
        "03_TAX_YEARS/TY_2024/W2s",
        "03_TAX_YEARS/TY_2024/K1s",
        "03_TAX_YEARS/TY_2024/WORKING",
        "03_TAX_YEARS/TY_2024/W2s/WORKING",
        "04_LONG_TERM_RECORDS",
        ".claude",
        ".opencode/agents",
    ):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "01_STRATEGY/STRATEGY_SEED.md").write_text("seed")
    (tmp_path / "00_CONTROL/link").symlink_to(tmp_path / "03_TAX_YEARS/TY_2024/W2s")
    return tmp_path


PROTECTED = [
    "01_STRATEGY/STRATEGY_SEED.md",
    "03_TAX_YEARS/TY_2024/W2s/w2.txt",
    "03_TAX_YEARS/TY_2024/K1s/k1.txt",
    "03_TAX_YEARS/TY_2024/notes.txt",
    "03_TAX_YEARS/TY_2024/W2s/WORKING/x.md",
    "03_TAX_YEARS/TY_2024/WORKING/../W2s/w2.txt",
    "/etc/passwd",
    ".claude/settings.json",
    ".opencode/agents/tax-advisor.md",
    "01_strategy/strategy_seed.md",
    "03_TAX_YEARS/TY_ABCD/WORKING/x.md",
    "03_TAX_YEARS/TY_20245/WORKING/x.md",
    "03_TAX_YEARS/TY_202/WORKING/x.md",
    "03_TAX_YEARS/WORKING/x.md",
    "../outside.txt",
    "00_CONTROL/link/w2.txt",
    # Allow-list (R27): everything not listed as writable is protected.
    "opencode.json",
    "opencode.jsonc",
    "AGENTS.md",
    "CLAUDE.md",
    ".mcp.json",
    "notes.md",
    ".git/config",
    ".git/x",
    ".agents/x",
    ".agents/skills/x/SKILL.md",
    ".opencode/opencode.jsonc",
    "05_OTHER/x.md",
    "00_CONTROL",
    "02_RESEARCH",
    "04_LONG_TERM_RECORDS",
    "01_STRATEGY",
    "03_TAX_YEARS/TY_2024/WORKING",  # A-F11: the bare WORKING path is not writable
    "03_TAX_YEARS/TY_2024/WORKING/",
    "03_TAX_YEARS/TY_2024/working",
    # A-F3: a non-ASCII component never matches the allow-list (U+017F long s, U+212A Kelvin).
    "01_STRATEGY/\u017fTRATEGY_SEED.md",
    "01_STRATEGY/STRATEGY_\u017fEED.md",
    "03_TAX_YEARS/TY_2024/WOR\u212aING/x.md",
    "00_CONTROL/\u212aEY.md",
    "\u017f00_CONTROL/x.md",
]

ALLOWED = [
    "03_TAX_YEARS/TY_2024/WORKING/DOCUMENT_INVENTORY.md",
    "03_TAX_YEARS/TY_2024/WORKING/CURRENT_STATE.md",
    "03_TAX_YEARS/TY_2024/WORKING/sub/x.md",
    "00_CONTROL/FACT_REGISTER.md",
    "01_STRATEGY/STRATEGY_MASTER_CURRENT.md",
    "01_STRATEGY/sub/x.md",
    "02_RESEARCH/TAX_POSITION_MEMOS/m.md",
    "04_LONG_TERM_RECORDS/CARRYFORWARD_LEDGER.md",
    "CURRENT_STATE.md",
    # ASCII names compare case-insensitively (OpenCode is stricter: see test_opencode_policy).
    "current_state.md",
    "00_control/FACT_REGISTER.md",
    "03_tax_years/ty_2024/working/x.md",
]


@pytest.mark.parametrize("rel", PROTECTED)
def test_protected_relative(root, rel):
    assert is_protected(rel, str(root)) is True


@pytest.mark.parametrize("rel", PROTECTED)
def test_protected_absolute(root, rel):
    target = rel if rel.startswith("/") else str(root / rel)
    assert is_protected(target, str(root)) is True


@pytest.mark.parametrize("rel", ALLOWED)
def test_allowed(root, rel):
    assert is_protected(rel, str(root)) is False
    assert is_protected(str(root / rel), str(root)) is False


def test_dot_segment_absolute(root):
    assert is_protected(f"{root}/./01_STRATEGY/STRATEGY_SEED.md", str(root)) is True


def test_symlink_to_outside_root_is_protected(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (root / "00_CONTROL/escape").symlink_to(outside)
    assert is_protected("00_CONTROL/escape/new.txt", str(root)) is True


def test_symlinked_root(tmp_path):
    real = tmp_path / "real"
    (real / "01_STRATEGY").mkdir(parents=True)
    (real / "03_TAX_YEARS/TY_2024/WORKING").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    for base in (link, real):
        assert is_protected("01_STRATEGY/STRATEGY_SEED.md", str(link)) is True
        assert is_protected(str(base / "01_STRATEGY/STRATEGY_SEED.md"), str(link)) is True
        assert is_protected("03_TAX_YEARS/TY_2024/WORKING/x.md", str(link)) is False
        assert is_protected(str(base / "03_TAX_YEARS/TY_2024/WORKING/x.md"), str(link)) is False


def _variant_root(root: Path) -> Path:
    variant = root.parent / root.name.upper()
    if variant == root or not variant.exists() or not os.path.samefile(variant, root):
        pytest.skip("case-sensitive filesystem")
    return variant


def test_case_variant_root_allows_working(root):
    variant = _variant_root(root)
    assert is_protected(str(variant / "00_CONTROL/review-probe.md"), str(root)) is False
    assert is_protected(str(variant / "03_TAX_YEARS/TY_2024/WORKING/x.md"), str(root)) is False
    assert is_protected(str(root / "00_CONTROL/review-probe.md"), str(variant)) is False


def test_case_variant_root_still_protects(root):
    variant = _variant_root(root)
    assert is_protected(str(variant / "01_STRATEGY/STRATEGY_SEED.md"), str(root)) is True
    assert is_protected(str(variant / "03_TAX_YEARS/TY_2024/W2s/w2.txt"), str(root)) is True
    assert is_protected(str(variant / ".claude/settings.json"), str(root)) is True
    assert is_protected(str(variant / "../escape.txt"), str(root)) is True


def test_case_variant_root_main(root, monkeypatch, capsys):
    variant = _variant_root(root)
    code, _ = run_main(monkeypatch, capsys, edit(str(variant / "00_CONTROL/a.md")), root)
    assert code == 0
    code, err = run_main(monkeypatch, capsys, edit(str(variant / "01_STRATEGY/STRATEGY_SEED.md")), root)
    assert code == 2
    assert "[tax-advisor-guard] protected path: 01_STRATEGY/STRATEGY_SEED.md" in err


def test_root_symlink_under_claude_is_protected(root):
    (root / ".claude/back").symlink_to(root)
    assert is_protected(str(root / ".claude/back/00_CONTROL/a.md"), str(root)) is True
    assert is_protected(".claude/back/00_CONTROL/a.md", str(root)) is True
    variant = _variant_root(root)
    assert is_protected(str(variant / ".claude/back/00_CONTROL/a.md"), str(root)) is True
    assert is_protected(str(variant / ".claude/back/00_CONTROL/a.md"), str(variant)) is True


def test_external_alias_of_root_is_protected(tmp_path_factory):
    base = tmp_path_factory.mktemp("ws")
    ws = base / "root"
    (ws / "00_CONTROL").mkdir(parents=True)
    (ws / "01_STRATEGY").mkdir()
    alias = base / "alias"
    alias.symlink_to(ws)
    assert is_protected(str(ws / "00_CONTROL/a.md"), str(ws)) is False
    assert is_protected(str(alias / "00_CONTROL/a.md"), str(ws)) is True
    assert is_protected(str(alias / "01_STRATEGY/STRATEGY_SEED.md"), str(ws)) is True
    upper = base / "ALIAS"
    if upper.exists():
        assert is_protected(str(upper / "00_CONTROL/a.md"), str(ws)) is True


def test_sibling_directory_is_outside(root):
    sibling = root.parent / (root.name + "-sibling")
    (sibling / "00_CONTROL").mkdir(parents=True)
    assert is_protected(str(sibling / "00_CONTROL/a.md"), str(root)) is True
    other = root.parent / "unrelated"
    other.mkdir()
    assert is_protected(str(other / "a.md"), str(root)) is True
    assert is_protected(str(other / "a.md").upper(), str(root)) is True


def test_nul_byte_fails_closed(root):
    assert is_protected("00_CONTROL/a\x00b", str(root)) is True


def test_hard_link_to_seed_is_protected(root):
    """A-F3: an existing writable-looking path that is the same file as the seed."""
    os.link(root / "01_STRATEGY/STRATEGY_SEED.md", root / "00_CONTROL/seed-copy.md")
    assert is_protected("00_CONTROL/seed-copy.md", str(root)) is True
    assert is_protected(str(root / "00_CONTROL/seed-copy.md"), str(root)) is True


def test_symlink_to_seed_is_protected(root):
    (root / "00_CONTROL/seed-link.md").symlink_to(root / "01_STRATEGY/STRATEGY_SEED.md")
    assert is_protected("00_CONTROL/seed-link.md", str(root)) is True


def test_unrelated_existing_file_stays_allowed(root):
    (root / "00_CONTROL/FACT_REGISTER.md").write_text("facts")
    assert is_protected("00_CONTROL/FACT_REGISTER.md", str(root)) is False


# --- read tools: only targets outside the root are blocked (A-F4 / E-F9) -----------


def read(tool, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input}


READ_BLOCKED = [
    read("Read", file_path="/etc/hosts"),
    read("Read", file_path="../outside.txt"),
    read("Read", file_path="~/notes.txt"),
    read("Glob", pattern="/etc/*"),
    read("Glob", pattern="*.md", path="/etc"),
    read("Glob", pattern="../../*"),
    read("Glob", pattern="**/../../../etc/hosts"),
    read("Grep", pattern="x", path="/etc"),
    read("Grep", pattern="x", path="../"),
    read("Read", file_path=""),
    read("Read"),
    read("Grep", pattern="x", path=5),
    read("Grep", pattern="x", glob=5),
    read("Grep", pattern="x", glob=None),
    read("Glob", pattern="*.md", path=5),
    read("Glob", pattern=""),
    read("Glob", pattern="   "),
]

# R3: an absolute, home or Windows-rooted brace or extglob alternative could
# make a glob walk start outside the root, whatever the literal prefix says.
ALTERNATIVE_BLOCKED = [
    read("Glob", pattern="{/Users/someone,x}/*"),
    read("Glob", pattern="{x,/etc}/*"),
    read("Glob", pattern="@(/etc|x)/*"),
    read("Glob", pattern="+(~|x)/*"),
    read("Glob", pattern="{~/Documents,a}/*"),
    read("Glob", pattern="!(x|/etc)/*"),
    read("Glob", pattern="*(/etc)"),
    read("Glob", pattern="{a,{b,/etc}}/*"),
    read("Glob", pattern="{C:/Windows,x}/*"),
    read("Glob", pattern="{x,c:\\Windows}/*"),
    read("Glob", pattern="{\\\\server\\share,x}/*"),
    read("Glob", pattern="{\\/etc,x}/*"),
    read("Glob", pattern="{\\~,x}/*"),
    read("Glob", pattern="{[/]etc,x}/*"),
    read("Glob", pattern="00_CONTROL/{x,/etc}/*"),
    read("Grep", pattern="x", glob="{/etc,x}/*"),
    read("Grep", pattern="x", glob="@(~|x)/*"),
    read("Grep", pattern="x", glob="{x,D:/y}/*", path="00_CONTROL"),
]

ALTERNATIVE_ALLOWED = [
    read("Glob", pattern="**/*.md"),
    read("Glob", pattern="{00_CONTROL,02_RESEARCH}/**/*.md"),
    read("Glob", pattern="03_TAX_YEARS/TY_2024/**"),
    read("Glob", pattern="03_TAX_YEARS/{TY_2023,TY_2024}/WORKING/*.md"),
    read("Glob", pattern="@(00_CONTROL|02_RESEARCH)/*"),
    read("Glob", pattern="**/*.{md,txt}"),
    read("Glob", pattern="*.md", path=""),
    read("Glob", pattern="*.md", path="  "),
    read("Grep", pattern="x", glob="*.{md,csv}"),
    read("Grep", pattern="x", glob="", path="00_CONTROL"),
    read("Grep", pattern="x", glob="   ", path="00_CONTROL"),
    read("Grep", pattern="x", glob="*.md", path=""),
    read("Grep", pattern="x", type=""),
]


@pytest.mark.parametrize("payload", ALTERNATIVE_BLOCKED)
def test_read_rooted_alternative_is_blocked(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert code == 2
    assert err.startswith("[tax-advisor-guard] protected path:")


@pytest.mark.parametrize("payload", ALTERNATIVE_ALLOWED)
def test_read_relative_alternatives_are_allowed(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert (code, err) == (0, "")


READ_ALLOWED = [
    read("Read", file_path="01_STRATEGY/STRATEGY_SEED.md"),
    read("Read", file_path="03_TAX_YEARS/TY_2024/W2s/w2.txt"),
    read("Read", file_path=".claude/settings.json"),
    read("Glob", pattern="**/*.md"),
    read("Glob", pattern="03_TAX_YEARS/*/WORKING/*.md"),
    read("Grep", pattern="/etc/hosts"),
    read("Grep", pattern="x", path="03_TAX_YEARS"),
    read("Grep", pattern="x", glob="*.md"),
]


@pytest.mark.parametrize("payload", READ_BLOCKED)
def test_read_outside_root_is_blocked(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert code == 2


@pytest.mark.parametrize("payload", READ_ALLOWED)
def test_read_inside_root_is_allowed(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert (code, err) == (0, "")


# N5: a '..' segment anywhere in a glob, including inside brace or extglob
# alternatives, an escaped '\.\.' or a '[.][.]' class, is blocked with the marker.
DOTDOT_BLOCKED = [
    read("Glob", pattern="{..,x}/../../etc/*"),
    read("Glob", pattern="{../..,a}/*"),
    read("Glob", pattern="{a,../..}/*"),
    read("Glob", pattern="@(..)/*"),
    read("Glob", pattern="+(..)/x"),
    read("Glob", pattern="!(x|..)/*"),
    read("Glob", pattern="*(..)"),
    read("Glob", pattern="\\.\\./*"),
    read("Glob", pattern="[.][.]/*"),
    read("Glob", pattern="00_CONTROL/.."),
    read("Glob", pattern="..."),
    read("Glob", pattern="*.md", path="00_CONTROL/../.."),
    read("Grep", pattern="x", glob="../*.md"),
    read("Grep", pattern="x", glob="{..,x}/*"),
    read("Grep", pattern="x", path="00_CONTROL/.."),
]

DOTDOT_ALLOWED = [
    read("Glob", pattern="**/*.md"),
    read("Glob", pattern="03_TAX_YEARS/TY_2024/**"),
    read("Glob", pattern="{00_CONTROL,02_RESEARCH}/*.md"),
    read("Glob", pattern="@(00_CONTROL)/*"),
    read("Glob", pattern="*.v1..2.md"),
    read("Glob", pattern="00_CONTROL/notes..md"),
    read("Glob", pattern=".*"),
    read("Grep", pattern="..", glob="*.md"),
    read("Grep", pattern="x", glob="**/*.v1..2.md"),
]


@pytest.mark.parametrize("payload", DOTDOT_BLOCKED)
def test_read_dotdot_segment_is_blocked(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert code == 2
    assert err.startswith("[tax-advisor-guard] protected path:")


@pytest.mark.parametrize("payload", DOTDOT_ALLOWED)
def test_read_dots_inside_names_are_allowed(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert (code, err) == (0, "")


def test_read_absolute_paths(root, monkeypatch, capsys):
    code, _ = run_main(monkeypatch, capsys, read("Read", file_path=str(root / "00_CONTROL/x.md")), root)
    assert code == 0
    code, _ = run_main(monkeypatch, capsys, read("Glob", pattern=str(root / "03_TAX_YEARS/**/*.md")), root)
    assert code == 0
    code, _ = run_main(monkeypatch, capsys, read("Grep", pattern="x", path=str(root)), root)
    assert code == 0


def test_read_marker_names_the_target(root, monkeypatch, capsys):
    code, err = run_main(monkeypatch, capsys, read("Read", file_path="/etc/hosts"), root)
    assert code == 2
    assert "[tax-advisor-guard] protected path: /etc/hosts" in err


def test_read_through_symlink_to_outside_is_blocked(root, tmp_path_factory, monkeypatch, capsys):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text("s")
    (root / "00_CONTROL/escape").symlink_to(outside)
    for payload in (
        read("Read", file_path="00_CONTROL/escape/secret.txt"),
        read("Glob", pattern="00_CONTROL/escape/*"),
        read("Grep", pattern="s", path="00_CONTROL/escape"),
    ):
        code, _ = run_main(monkeypatch, capsys, payload, root)
        assert code == 2, payload


def test_read_tools_do_not_get_the_write_policy(root, monkeypatch, capsys):
    """Reading protected evidence is fine; only writing it is blocked."""
    code, _ = run_main(monkeypatch, capsys, read("Read", file_path="01_STRATEGY/STRATEGY_SEED.md"), root)
    assert code == 0
    code, _ = run_main(monkeypatch, capsys, edit("01_STRATEGY/STRATEGY_SEED.md", "Write"), root)
    assert code == 2


def test_subprocess_read_entry_point(root):
    assert run_script(read("Read", file_path="/etc/hosts"), root).returncode == 2
    assert run_script(read("Read", file_path="00_CONTROL/x.md"), root).returncode == 0


# R4f: the CLI may trim path arguments, so a path with surrounding whitespace
# could hide an absolute or '~' path. Such values are refused outright.
PADDED_BLOCKED = [
    read("Read", file_path=" /etc/hosts"),
    read("Read", file_path=" ~/x"),
    read("Glob", pattern=" /etc/*"),
    read("Grep", pattern="x", path=" /etc"),
    read("Read", file_path="00_CONTROL/x.md "),
    read("Read", file_path="x\n"),
    read("Read", file_path="\t00_CONTROL/x.md"),
    read("Read", file_path="\u00a0/etc/hosts"),
    read("Read", file_path="\u2003/etc/hosts"),
    read("Glob", pattern="*.md", path=" /etc"),
    read("Glob", pattern="*.md "),
    read("Grep", pattern="x", glob=" /etc/*"),
    read("Grep", pattern="x", glob="*.md\n"),
    {"tool_name": "Edit", "tool_input": {"file_path": " 00_CONTROL/x.md"}},
    {"tool_name": "Write", "tool_input": {"file_path": "00_CONTROL/x.md "}},
    {"tool_name": "Write", "tool_input": {"file_path": "\u00a000_CONTROL/x.md"}},
    {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "00_CONTROL/n.ipynb\n"}},
]

PADDED_ALLOWED = [
    read("Read", file_path="00_CONTROL/x.md"),
    read("Glob", pattern="**/*.md", path="  "),
    read("Grep", pattern="x", glob="  ", path="00_CONTROL"),
    read("Grep", pattern=" leading and trailing ", path="00_CONTROL"),
    {"tool_name": "Write", "tool_input": {"file_path": "00_CONTROL/x.md"}},
]


@pytest.mark.parametrize("payload", PADDED_BLOCKED)
def test_surrounding_whitespace_is_blocked(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert code == 2
    assert err.startswith("[tax-advisor-guard] protected path:")


@pytest.mark.parametrize("payload", PADDED_ALLOWED)
def test_unpadded_paths_and_blank_optionals_are_allowed(root, monkeypatch, capsys, payload):
    code, err = run_main(monkeypatch, capsys, payload, root)
    assert (code, err) == (0, "")


def test_subprocess_padded_read_is_blocked(root):
    assert run_script(read("Read", file_path=" /etc/hosts"), root).returncode == 2


def run_main(monkeypatch, capsys, payload, project_dir):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    if project_dir is None:
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    code = guard_paths.main()
    return code, capsys.readouterr().err


def edit(path, tool="Edit"):
    return {"tool_name": tool, "tool_input": {"file_path": path}}


def test_main_blocks_seed(root, monkeypatch, capsys):
    code, err = run_main(monkeypatch, capsys, edit("01_STRATEGY/STRATEGY_SEED.md"), root)
    assert code == 2
    assert err.startswith("[tax-advisor-guard] protected path:")
    assert "01_STRATEGY/STRATEGY_SEED.md" in err


def test_main_blocks_absolute_seed(root, monkeypatch, capsys):
    code, err = run_main(monkeypatch, capsys, edit(str(root / "01_STRATEGY/STRATEGY_SEED.md")), root)
    assert code == 2
    assert "[tax-advisor-guard] protected path: 01_STRATEGY/STRATEGY_SEED.md" in err


def test_main_allows_working(root, monkeypatch, capsys):
    code, err = run_main(
        monkeypatch, capsys, edit(str(root / "03_TAX_YEARS/TY_2024/WORKING/CURRENT_STATE.md"), "Write"), root
    )
    assert code == 0
    assert err == ""


def test_main_notebook_path(root, monkeypatch, capsys):
    payload = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "03_TAX_YEARS/TY_2024/K1s/n.ipynb"}}
    code, _ = run_main(monkeypatch, capsys, payload, root)
    assert code == 2
    payload["tool_input"]["notebook_path"] = "00_CONTROL/n.ipynb"
    code, _ = run_main(monkeypatch, capsys, payload, root)
    assert code == 0


def test_main_blocks_if_any_path_key_protected(root, monkeypatch, capsys):
    payload = {"tool_input": {"file_path": "00_CONTROL/ok.md", "notebook_path": ".claude/x.ipynb"}}
    code, _ = run_main(monkeypatch, capsys, payload, root)
    assert code == 2


def test_main_bad_json(root, monkeypatch, capsys):
    code, _ = run_main(monkeypatch, capsys, "{not json", root)
    assert code == 2


def test_main_missing_project_dir(root, monkeypatch, capsys):
    code, _ = run_main(monkeypatch, capsys, edit("00_CONTROL/FACT_REGISTER.md"), None)
    assert code == 2


def test_main_empty_project_dir(root, monkeypatch, capsys):
    code, _ = run_main(monkeypatch, capsys, edit("00_CONTROL/FACT_REGISTER.md"), "")
    assert code == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_input": "oops"},
        {"tool_input": ["a"]},
        {"tool_input": {}},
        {"tool_input": {"file_path": 5}},
        {"tool_input": {"file_path": ""}},
        {"tool_input": {"file_path": None}},
        {"tool_name": "Edit"},
        ["not", "a", "dict"],
        "a string",
        None,
    ],
)
def test_main_malformed_input(root, monkeypatch, capsys, payload):
    code, _ = run_main(monkeypatch, capsys, json.dumps(payload), root)
    assert code == 2


def test_main_empty_stdin(root, monkeypatch, capsys):
    code, _ = run_main(monkeypatch, capsys, "", root)
    assert code == 2


def run_script(payload, project_dir, python="python3"):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        [python, str(SCRIPT)],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_subprocess_entry_point(root):
    blocked = run_script(edit("01_STRATEGY/STRATEGY_SEED.md"), root)
    assert blocked.returncode == 2
    assert blocked.stderr.startswith("[tax-advisor-guard] protected path:")
    allowed = run_script(edit("03_TAX_YEARS/TY_2024/WORKING/x.md", "Write"), root)
    assert allowed.returncode == 0
    missing = run_script(edit("00_CONTROL/x.md"), None)
    assert missing.returncode == 2


# --- A-F1: the hook runs as bare python3 from the user's PATH ---------------------


def test_hook_parses_as_python_38():
    ast.parse(SCRIPT.read_text(), feature_version=(3, 8))


def test_hook_avoids_post_38_apis():
    tree = ast.parse(SCRIPT.read_text())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"removeprefix", "removesuffix", "cache", "is_relative_to"}
    assert not any(isinstance(n, ast.keyword) and n.arg == "strict" for n in ast.walk(tree))
    assert not any(type(n).__name__ in ("Match", "TryStar") for n in ast.walk(tree))


def test_hook_defers_annotations():
    tree = ast.parse(SCRIPT.read_text())
    first = tree.body[1]  # body[0] is the docstring
    assert isinstance(first, ast.ImportFrom) and first.module == "__future__"
    assert [a.name for a in first.names] == ["annotations"]


def test_version_gate_blocks_below_38(root):
    code = (
        "import runpy, sys\n"
        "sys.version_info = (3, 7, 0, 'final', 0)\n"
        f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n"
    )
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(edit("00_CONTROL/x.md")),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stderr.startswith("[tax-advisor-guard] blocked: python3 >= 3.8 required")
    assert "protected path:" not in result.stderr


def _old_interpreters() -> list[str]:
    found = []
    for candidate in (shutil.which("python3.8"), shutil.which("python3.9"), "/usr/bin/python3"):
        if not candidate or candidate in found or not os.path.exists(candidate):
            continue
        probe = subprocess.run(
            [candidate, "-c", "import sys; print(sys.version_info[:2] < (3, 10))"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "True":
            found.append(candidate)
    return found


def test_subprocess_cases_under_old_python(root):
    interpreters = _old_interpreters()
    if not interpreters:
        pytest.skip("no python3.8/python3.9 interpreter found (checked PATH and /usr/bin/python3)")
    cases = [
        (edit("01_STRATEGY/STRATEGY_SEED.md"), 2, True),
        (edit("03_TAX_YEARS/TY_2024/WORKING/x.md", "Write"), 0, False),
        (read("Read", file_path="/etc/hosts"), 2, True),
        (read("Read", file_path="00_CONTROL/x.md"), 0, False),
        (read("Glob", pattern="{../..,a}/*"), 2, True),
    ]
    for python in interpreters:
        for payload, code, marker in cases:
            result = run_script(payload, root, python)
            assert result.returncode == code, (python, payload, result.stderr)
            assert result.stderr.startswith("[tax-advisor-guard] protected path:") is marker
        assert run_script(edit("00_CONTROL/x.md"), None, python).returncode == 2
