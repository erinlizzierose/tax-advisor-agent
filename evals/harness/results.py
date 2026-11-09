"""JSONL result rows and the resume rule.

Only pass, fail and inconclusive count as completed. error and timeout rows stay in the
file as attempts and their key is run again.

The resume key includes `checker_sha`, the version of the checker that produced the row
(see runner.checker_sha). A row from another checker version, or a row written before the
field existed, never counts as completed, so a result is always for the checks now in the repo.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, NamedTuple

COMPLETED = frozenset({"pass", "fail", "inconclusive"})


class ResultKey(NamedTuple):
    """The resume key. A plain tuple of the same values compares and hashes equal to it, and
    nothing about the stored rows (the JSON format) depends on it."""

    tool: str
    model: str
    agent_sha: str
    scenario: str
    run_index: int
    checker_sha: str


# Fields a row may lack: a row written before they existed still loads, with these values.
OPTIONAL_DEFAULTS: dict[str, Any] = {"checker_sha": "", "judge_model": None}


@dataclass(frozen=True)
class RunResult:
    status: str
    tool: str
    model: str
    cli_version: str
    agent_sha: str
    scenario: str
    run_index: int
    started: str
    duration_s: float
    tokens: int | None
    checks: list[dict[str, Any]]
    transcript_path: str | None
    note: str
    checker_sha: str = ""
    judge_model: str | None = None


def resume_key(
    *, tool: str, model: str, agent_sha: str, scenario: str, run_index: int, checker_sha: str
) -> ResultKey:
    """The identity of a run: the same key means the same run, so a stored row can stand for it.
    Keyword-only, so the two 64-hex shas can never be swapped by position."""
    return ResultKey(tool, model, agent_sha, scenario, run_index, checker_sha)


def result_key(result: RunResult) -> ResultKey:
    return resume_key(
        tool=result.tool,
        model=result.model,
        agent_sha=result.agent_sha,
        scenario=result.scenario,
        run_index=result.run_index,
        checker_sha=result.checker_sha,
    )


def append_result(path: Path, result: RunResult) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(result), sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()


def load_results(path: Path) -> list[RunResult]:
    """Read all rows; a missing file is empty and torn or foreign lines are skipped. A row
    without the optional fields (checker_sha, judge_model) loads with their defaults."""
    path = Path(path)
    if not path.is_file():
        return []
    names = {f.name for f in fields(RunResult)}
    required = names - OPTIONAL_DEFAULTS.keys()
    rows: list[RunResult] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict) or not required <= data.keys():
            continue
        rows.append(RunResult(**{k: data[k] for k in names if k in data}))
    return rows


def completed_keys(rows: list[RunResult]) -> set[ResultKey]:
    return {result_key(r) for r in rows if r.status in COMPLETED and r.checker_sha}
