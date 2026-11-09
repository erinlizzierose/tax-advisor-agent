"""Scenario definitions loaded from evals/scenarios/<id>/scenario.toml."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

LEAF_CHECK_TYPES = {
    "exists",
    "absent",
    "protected_unchanged",
    "file_matches",
    "labeled_amount",
    "ledger_rows_if_present",
    "strategy_status_consistent",
    "event",
    "judge",
}
CHECK_TYPES = LEAF_CHECK_TYPES | {"any_of"}
KINDS = ("compliance", "enforcement")
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass(frozen=True)
class Scenario:
    id: str
    prompt: str
    setup_files: dict[str, str]
    checks: list[dict[str, Any]]
    kind: Literal["compliance", "enforcement"]
    timeout_s: int = 900


def _validate(data: dict[str, Any], source: str) -> Scenario:
    def bad(msg: str) -> ValueError:
        return ValueError(f"{source}: {msg}")

    scenario_id = data.get("id")
    if not isinstance(scenario_id, str) or not ID_PATTERN.fullmatch(scenario_id):
        raise bad("id must match [a-z0-9][a-z0-9_-]*")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise bad("prompt must be a non-empty string")
    kind = data.get("kind")
    if kind not in KINDS:
        raise bad(f"kind must be one of {KINDS}")
    timeout_s = data.get("timeout_s", 900)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
        raise bad("timeout_s must be a positive integer")
    setup_files = data.get("setup_files", {})
    if not isinstance(setup_files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in setup_files.items()
    ):
        raise bad("setup_files must be a table of path = content strings")
    checks = data.get("checks", [])
    if not isinstance(checks, list) or not checks:
        raise bad("checks must be a non-empty array of tables")
    for check in checks:
        if not isinstance(check, dict) or check.get("type") not in CHECK_TYPES:
            raise bad(f"each check needs a type in {sorted(CHECK_TYPES)}")
        if check["type"] == "any_of":
            alternatives = check.get("alternatives")
            if not isinstance(alternatives, list) or not alternatives or not all(
                isinstance(a, dict) and a.get("type") in LEAF_CHECK_TYPES for a in alternatives
            ):
                raise bad(
                    f"an any_of check needs a non-empty 'alternatives' array of checks with a type in {sorted(LEAF_CHECK_TYPES)}"
                )
    if kind == "enforcement" and not any(
        c["type"] == "event" and c.get("denied") is True for c in checks
    ):
        raise bad("an enforcement scenario needs an event check with denied = true")
    return Scenario(
        id=scenario_id,
        prompt=prompt,
        setup_files=dict(setup_files),
        checks=[dict(c) for c in checks],
        kind=kind,
        timeout_s=timeout_s,
    )


def load_scenario(path: Path) -> Scenario:
    with Path(path).open("rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{path}: invalid TOML: {exc}") from exc
    return _validate(data, str(path))


def load_scenarios(root: Path, which: str) -> list[Scenario]:
    """Load one scenario by id, or every scenario under root when which is 'all'."""
    if which == "all":
        paths = sorted(root.glob("*/scenario.toml"))
    else:
        if not ID_PATTERN.fullmatch(which):
            raise ValueError(f"invalid scenario id: {which!r}")
        paths = [root / which / "scenario.toml"]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"scenario not found: {missing[0]}")
    scenarios = [load_scenario(p) for p in paths]
    for scenario, path in zip(scenarios, paths):
        if scenario.id != path.parent.name:
            raise ValueError(f"{path}: id {scenario.id!r} must match its directory name")
    return scenarios
