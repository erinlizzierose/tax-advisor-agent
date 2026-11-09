import subprocess
import sys
from pathlib import Path

import pytest

from install import install

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install.py"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "opencode").mkdir(parents=True)
    (src / "claude" / "hooks").mkdir(parents=True)
    (src / "skills" / "demo").mkdir(parents=True)
    (src / "skills" / "demo" / "SKILL.md").write_text("demo skill\n")
    (src / "core.md").write_text("CORE BODY\n")
    (src / "opencode" / "frontmatter.yaml.tmpl").write_text("description: oc\nresource: 01_STRATEGY/x\n")
    (src / "claude" / "frontmatter.yaml").write_text("name: tax-advisor\n")
    (src / "claude" / "settings.json").write_text('{"hooks": {}}\n')
    (src / "claude" / "hooks" / "guard_paths.py").write_text("print('guard')\n")
    return src


@pytest.fixture
def target(tmp_path: Path) -> Path:
    t = tmp_path / "workspace"
    t.mkdir()
    return t


def test_installs_both_tools(source, target):
    written = install(target, {"opencode", "claude"}, source=source)
    assert (target / ".opencode/agents/tax-advisor.md").is_file()
    assert (target / ".claude/agents/tax-advisor.md").is_file()
    assert (target / ".claude/settings.json").read_text() == '{"hooks": {}}\n'
    assert (target / ".claude/hooks/guard_paths.py").read_text() == "print('guard')\n"
    assert (target / ".claude/skills/demo/SKILL.md").read_text() == "demo skill\n"
    claude = (target / ".claude/agents/tax-advisor.md").read_text()
    assert claude == "---\nname: tax-advisor\n---\n\nCORE BODY\n"
    assert target / ".claude/skills/demo" in written
    assert target / ".opencode/agents/tax-advisor.md" in written


def test_opencode_agent_is_written_without_embedding_the_target_path(source, target):
    install(target, {"opencode"}, source=source)
    text = (target / ".opencode/agents/tax-advisor.md").read_text()
    assert text == "---\ndescription: oc\nresource: 01_STRATEGY/x\n---\n\nCORE BODY\n"
    assert str(target.resolve()) not in text


def test_opencode_template_with_a_root_placeholder_is_rejected(source, target):
    # OpenCode matches edit resources as workspace-relative paths, so absolute {ROOT}
    # rules can never match. A template that still has one must fail, not ship it literally.
    (source / "opencode" / "frontmatter.yaml.tmpl").write_text("description: oc\nresource: {ROOT}/x\n")
    with pytest.raises(ValueError, match="ROOT"):
        install(target, {"opencode", "claude"}, source=source)
    assert not (target / ".opencode").exists() and not (target / ".claude").exists()


def test_refuses_to_overwrite_existing_settings(source, target):
    settings = target / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("mine")
    with pytest.raises(FileExistsError) as exc:
        install(target, {"claude"}, source=source)
    assert "settings.json" in str(exc.value)
    assert settings.read_text() == "mine"


def test_nothing_written_when_conflicts_found(source, target):
    (target / ".claude/skills/demo").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        install(target, {"opencode", "claude"}, source=source)
    assert not (target / ".opencode").exists()
    assert not (target / ".claude/agents").exists()
    assert not (target / ".claude/settings.json").exists()
    assert list((target / ".claude/skills/demo").iterdir()) == []


def test_force_overwrites(source, target):
    settings = target / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("mine")
    stale = target / ".claude/skills/demo/stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    other = target / ".claude/skills/other/keep.txt"
    other.parent.mkdir(parents=True)
    other.write_text("keep")
    install(target, {"claude"}, force=True, source=source)
    assert settings.read_text() == '{"hooks": {}}\n'
    assert not stale.exists()
    assert (target / ".claude/skills/demo/SKILL.md").is_file()
    assert other.read_text() == "keep"


def test_opencode_only_still_installs_skills(source, target):
    install(target, {"opencode"}, source=source)
    assert (target / ".claude/skills/demo/SKILL.md").is_file()
    assert (target / ".opencode/agents/tax-advisor.md").is_file()
    assert not (target / ".claude/agents").exists()
    assert not (target / ".claude/settings.json").exists()


def test_claude_only_skips_opencode(source, target):
    install(target, {"claude"}, source=source)
    assert not (target / ".opencode").exists()


def test_missing_skills_raises(source, target):
    (source / "skills" / "demo" / "SKILL.md").unlink()
    (source / "skills" / "demo").rmdir()
    with pytest.raises(FileNotFoundError):
        install(target, {"opencode", "claude"}, source=source)
    assert list(target.iterdir()) == []


def test_missing_skills_dir_raises(source, target):
    import shutil

    shutil.rmtree(source / "skills")
    with pytest.raises(FileNotFoundError):
        install(target, {"claude"}, source=source)


@pytest.mark.parametrize("tools", [set(), {"vim"}, {"claude", "vim"}])
def test_invalid_tools_raise(source, target, tools):
    with pytest.raises(ValueError):
        install(target, tools, source=source)


@pytest.mark.parametrize("bad", ["a*b", 'a"b', "a?b", "a\\b", "a\nb"])
def test_rejects_unsafe_target_names(source, tmp_path, bad):
    t = tmp_path / bad
    t.mkdir()
    with pytest.raises(ValueError):
        install(t, {"opencode"}, source=source)
    assert list(t.iterdir()) == []


def test_cli_invalid_tools_exits_1(target):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(target), "--tools", "bogus"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert result.stderr.strip()
    assert list(target.iterdir()) == []


def snapshot(root: Path) -> dict[str, tuple]:
    snap = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            snap[rel] = ("link", str(p.readlink()))
        elif p.is_dir():
            snap[rel] = ("dir",)
        else:
            snap[rel] = ("file", p.read_bytes())
    return snap


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    out = tmp_path / "outside"
    out.mkdir()
    (out / "sentinel.txt").write_text("keep")
    return out


@pytest.mark.parametrize("force", [False, True])
def test_symlinked_claude_dir_rejected(source, target, outside, force):
    (target / ".claude").symlink_to(outside)
    before = snapshot(outside)
    with pytest.raises(ValueError, match=r"\.claude"):
        install(target, {"opencode", "claude"}, force=force, source=source)
    assert snapshot(outside) == before
    assert not (target / ".opencode").exists()


def test_symlinked_opencode_dir_rejected(source, target, outside):
    (target / ".opencode").symlink_to(outside)
    before = snapshot(outside)
    with pytest.raises(ValueError, match=r"\.opencode"):
        install(target, {"opencode"}, source=source)
    assert snapshot(outside) == before
    assert not (target / ".claude").exists()


def test_force_symlinked_skill_dir_rejected(source, target, outside):
    skills = target / ".claude/skills"
    skills.mkdir(parents=True)
    (skills / "demo").symlink_to(outside)
    before_out = snapshot(outside)
    before = snapshot(target)
    with pytest.raises(ValueError, match="demo"):
        install(target, {"opencode", "claude"}, force=True, source=source)
    assert snapshot(outside) == before_out
    assert snapshot(target) == before


def test_force_symlinked_skills_dir_rejected(source, target, outside):
    (target / ".claude").mkdir()
    (target / ".claude/skills").symlink_to(outside)
    before_out = snapshot(outside)
    with pytest.raises(ValueError, match="skills"):
        install(target, {"opencode"}, force=True, source=source)
    assert snapshot(outside) == before_out
    assert not (target / ".opencode").exists()


@pytest.mark.parametrize("force", [False, True])
def test_symlinked_settings_rejected(source, target, outside, force):
    (target / ".claude").mkdir()
    (target / ".claude/settings.json").symlink_to(outside / "sentinel.txt")
    before_out = snapshot(outside)
    with pytest.raises(ValueError, match=r"settings\.json"):
        install(target, {"claude"}, force=force, source=source)
    assert snapshot(outside) == before_out
    assert (target / ".claude/settings.json").is_symlink()
    assert not (target / ".claude/agents").exists()


@pytest.mark.parametrize(
    "missing",
    ["core.md", "claude/frontmatter.yaml", "claude/settings.json", "claude/hooks/guard_paths.py"],
)
def test_missing_source_asset_changes_nothing(source, target, missing):
    (source / missing).unlink()
    before = snapshot(target)
    with pytest.raises(FileNotFoundError):
        install(target, {"opencode", "claude"}, source=source)
    assert snapshot(target) == before


def test_missing_opencode_template_changes_nothing(source, target):
    (source / "opencode/frontmatter.yaml.tmpl").unlink()
    before = snapshot(target)
    with pytest.raises(FileNotFoundError):
        install(target, {"opencode", "claude"}, source=source)
    assert snapshot(target) == before


def test_source_asset_that_is_a_directory_changes_nothing(source, target):
    (source / "claude/settings.json").unlink()
    (source / "claude/settings.json").mkdir()
    before = snapshot(target)
    with pytest.raises(FileNotFoundError):
        install(target, {"claude"}, source=source)
    assert snapshot(target) == before


@pytest.mark.parametrize("force", [False, True])
def test_file_where_directory_needed_changes_nothing(source, target, force):
    (target / ".claude").write_text("i am a file")
    before = snapshot(target)
    with pytest.raises(ValueError, match=r"\.claude"):
        install(target, {"opencode", "claude"}, force=force, source=source)
    assert snapshot(target) == before


def test_file_at_skills_dir_changes_nothing(source, target):
    (target / ".claude").mkdir()
    (target / ".claude/skills").write_text("file")
    before = snapshot(target)
    with pytest.raises(ValueError, match="skills"):
        install(target, {"opencode"}, force=True, source=source)
    assert snapshot(target) == before


@pytest.mark.parametrize(
    "rel",
    [".opencode/agents/tax-advisor.md", ".claude/settings.json", ".claude/hooks/guard_paths.py"],
)
def test_force_replaces_directory_at_file_path(source, target, rel):
    d = target / rel
    (d / "nested").mkdir(parents=True)
    (d / "nested/x.txt").write_text("x")
    install(target, {"opencode", "claude"}, force=True, source=source)
    assert d.is_file() and not d.is_symlink()
    expected = {
        ".claude/settings.json": '{"hooks": {}}\n',
        ".claude/hooks/guard_paths.py": "print('guard')\n",
    }
    if rel in expected:
        assert d.read_text() == expected[rel]
    else:
        assert d.read_text().endswith("\n---\n\nCORE BODY\n")


def test_force_replaces_file_at_skill_path(source, target):
    skill = target / ".claude/skills/demo"
    skill.parent.mkdir(parents=True)
    skill.write_text("not a dir")
    install(target, {"claude"}, force=True, source=source)
    assert skill.is_dir()
    assert (skill / "SKILL.md").read_text() == "demo skill\n"


def test_file_at_skill_path_conflicts_without_force(source, target):
    skill = target / ".claude/skills/demo"
    skill.parent.mkdir(parents=True)
    skill.write_text("not a dir")
    with pytest.raises(FileExistsError):
        install(target, {"claude"}, source=source)
    assert skill.read_text() == "not a dir"
