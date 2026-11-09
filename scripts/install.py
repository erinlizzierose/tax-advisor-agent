"""Install the tax-advisor agent into a tax workspace.

Usage: uv run scripts/install.py TARGET [--tools opencode,claude] [--force]
"""

import argparse
import shutil
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
VALID_TOOLS = {"opencode", "claude"}
UNSAFE_TARGET_CHARS = ('*', '?', '"', "\\", "\n", "\r")


def _wrap(frontmatter: str, core: str) -> str:
    return "---\n" + frontmatter.strip() + "\n---\n\n" + core


def _skill_dirs(source: Path) -> list[Path]:
    skills = source / "skills"
    if not skills.is_dir():
        raise FileNotFoundError(f"no skills directory in {source}")
    found = sorted(p for p in skills.iterdir() if p.is_dir())
    if not found:
        raise FileNotFoundError(f"no skills found in {skills}")
    return found


def _require_file(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"missing source asset (not a regular file): {path}")
    return path


def _read_source(path: Path) -> str:
    return _require_file(path).read_text()


def _check_destination(root: Path, dest: Path) -> None:
    """Reject symlinks and non-directory parents among existing components below root."""
    parts = dest.relative_to(root).parts
    current = root
    for i, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing to write through symlink: {current.relative_to(root)}")
        if not current.exists():
            return
        if i < len(parts) - 1 and not current.is_dir():
            raise ValueError(
                f"expected a directory but found a file: {current.relative_to(root)}"
            )


def _clear(dest: Path) -> None:
    """Remove an existing non-symlink destination of any type (symlinks were rejected earlier)."""
    if dest.is_symlink():
        raise ValueError(f"refusing to remove symlink: {dest}")
    if dest.is_dir():
        shutil.rmtree(dest)
    elif dest.exists():
        dest.unlink()


def install(
    target: Path,
    tools: set[str],
    force: bool = False,
    *,
    source: Path = AGENT_DIR,
) -> list[Path]:
    if not tools or not tools <= VALID_TOOLS:
        raise ValueError(
            f"tools must be a non-empty subset of {sorted(VALID_TOOLS)}, got {sorted(tools)}"
        )
    root = target.resolve()
    if any(ch in str(root) for ch in UNSAFE_TARGET_CHARS):
        raise ValueError(
            f"target path contains an unsupported character (* ? \" \\ or newline): {root!r}"
        )
    if root.exists() and not root.is_dir():
        raise ValueError(f"target is not a directory: {root}")
    skills = _skill_dirs(source)
    core = _read_source(source / "core.md")

    # Plan and validate every output before touching disk.
    files: dict[Path, str | Path] = {}
    if "opencode" in tools:
        tmpl = _read_source(source / "opencode" / "frontmatter.yaml.tmpl")
        if "{ROOT}" in tmpl:
            raise ValueError(
                "OpenCode template contains a {ROOT} placeholder; OpenCode matches edit "
                "resources as workspace-relative paths, so absolute rules never match"
            )
        files[root / ".opencode" / "agents" / "tax-advisor.md"] = _wrap(tmpl, core)
    if "claude" in tools:
        fm = _read_source(source / "claude" / "frontmatter.yaml")
        settings = _require_file(source / "claude" / "settings.json")
        hook = _require_file(source / "claude" / "hooks" / "guard_paths.py")
        files[root / ".claude" / "agents" / "tax-advisor.md"] = _wrap(fm, core)
        files[root / ".claude" / "settings.json"] = settings
        files[root / ".claude" / "hooks" / "guard_paths.py"] = hook
    skill_targets = {root / ".claude" / "skills" / s.name: s for s in skills}

    for dest in [*files, *skill_targets]:
        _check_destination(root, dest)

    conflicts = [p for p in [*files, *skill_targets] if p.exists()]
    if conflicts and not force:
        listed = "\n".join(f"  {p.relative_to(root)}" for p in conflicts)
        raise FileExistsError(
            f"refusing to overwrite existing paths (use --force):\n{listed}"
        )

    written: list[Path] = []
    for dest, content in files.items():
        _clear(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Path):
            shutil.copy2(content, dest)
        else:
            dest.write_text(content)
        written.append(dest)
    for dest, src in skill_targets.items():
        _clear(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        written.append(dest)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the tax-advisor agent.")
    parser.add_argument("target", type=Path, help="tax workspace directory")
    parser.add_argument("--tools", default="opencode,claude", help="comma-separated: opencode,claude")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args(argv)
    tools = {t.strip() for t in args.tools.split(",") if t.strip()}
    try:
        written = install(args.target, tools, args.force)
    except (FileExistsError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    root = args.target.resolve()
    for p in written:
        print(p.relative_to(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
