#!/usr/bin/env python3
"""Run eval scenarios against a coding-agent CLI.

Usage: run.py --tool {opencode,claude} --model M --scenario {id|all} --runs N

Exit codes: 0 all runs finished or were already complete, 1 some run ended in error or
timeout (re-run to retry it), 2 bad arguments or missing inputs, or uncommitted changes in
agent/ or scripts/install.py, or in the checker (evals/ outside evals/results/,
scripts/privacy_check.py, and the --sample, --answer-key and --scenarios-dir paths when they
are inside the repo). Also exit 2: any of those inputs, or scripts/install.py, that holds
git-ignored files (`git status` does not list them) or lies under evals/results/. Such a path
outside the repo is allowed (its content is hashed) with a one-line warning that rows will not
map to a commit.

A run is "already complete" only for the same agent version (runner.agent_sha, which covers
agent/ and scripts/install.py) AND the same checker version (runner.checker_sha, which covers
evals/, the privacy scanner and the content of the --sample tree, the --answer-key file and the
scenario's files as passed), so rows scored by older checks or other inputs are run again.

Bad input (unknown tool, a model name the tool rejects, a missing --sample, --answer-key,
--scenarios-dir or scenario) exits 2 with a one-line message before anything runs or is written.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Callable

_HERE = Path(__file__).resolve().parent.parent
for _extra in (_HERE / "scripts", _HERE / "evals" / "harness", _HERE / "evals"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from judge import JUDGE_MODEL, judge  # noqa: E402
from privacy_check import DEFAULT_DENYLIST, load_denylist  # noqa: E402
from results import append_result, completed_keys, load_results, resume_key  # noqa: E402
from runner import (  # noqa: E402
    INSTALL_SCRIPT,
    Runner,
    agent_dirty,
    agent_sha,
    checker_dirty,
    checker_sha,
    input_refusal,
    repo_relative,
    run_scenario,
    validate_transcripts_dir,
)
from runners.claude import ClaudeRunner  # noqa: E402
from runners.opencode import OpenCodeRunner  # noqa: E402
from scenario import load_scenarios  # noqa: E402

# The one checkout under evaluation: the agent/ hash recorded in every row, the cleanliness
# check on agent/, and the agent/ directory installed into each workspace all come from
# here. There is deliberately no command-line override.
REPO_ROOT = _HERE

# Tool name -> factory(model) -> Runner.
RUNNERS: dict[str, Callable[[str], Runner]] = {"claude": ClaudeRunner, "opencode": OpenCodeRunner}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run eval scenarios.")
    p.add_argument("--tool", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--scenario", required=True, help="scenario id or 'all'")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--scenarios-dir", type=Path, default=REPO_ROOT / "evals" / "scenarios")
    p.add_argument("--sample", type=Path, default=REPO_ROOT / "examples" / "sample-household")
    p.add_argument("--answer-key", type=Path, default=REPO_ROOT / "evals" / "answer_key.toml")
    p.add_argument(
        "--results", type=Path, default=REPO_ROOT / "evals" / "results" / "results.jsonl"
    )
    p.add_argument(
        "--transcripts-dir", type=Path, default=REPO_ROOT / "evals" / "results" / "transcripts"
    )
    p.add_argument("--denylist", type=Path, default=DEFAULT_DENYLIST)
    p.add_argument("--keep-workspaces", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def fail(message: str) -> int:
        print(f"run.py: {message}", file=sys.stderr)
        return 2

    if args.tool not in RUNNERS:
        known = ", ".join(sorted(RUNNERS)) or "none registered"
        return fail(f"unknown tool {args.tool!r} (available runners: {known})")
    if args.runs < 1:
        return fail("--runs must be at least 1")
    validate_model = getattr(RUNNERS[args.tool], "validate_model", None)
    if validate_model is not None:
        try:
            validate_model(args.model)
        except ValueError as exc:
            return fail(str(exc))
    for label, path, is_dir in (
        ("--sample", args.sample, True),
        ("--answer-key", args.answer_key, False),
        ("--scenarios-dir", args.scenarios_dir, True),
    ):
        if not (path.is_dir() if is_dir else path.is_file()):
            return fail(f"{label} {'directory' if is_dir else 'file'} not found: {path}")
    try:
        validate_transcripts_dir(args.transcripts_dir, REPO_ROOT)
    except ValueError as exc:
        return fail(str(exc))
    denylist_path = args.denylist.expanduser()
    if not denylist_path.is_file():
        return fail(f"denylist not found: {denylist_path} (transcripts are saved only after a privacy scan)")
    inputs = (("--sample", args.sample), ("--answer-key", args.answer_key), ("--scenarios-dir", args.scenarios_dir))
    try:
        scenarios = load_scenarios(args.scenarios_dir, args.scenario)
        answer_key = tomllib.loads(args.answer_key.read_text(encoding="utf-8"))
        if agent_dirty(REPO_ROOT):
            return fail(
                "agent/ or scripts/install.py has uncommitted changes; commit or stash them so "
                "every result maps to exactly one agent version"
            )
        if checker_dirty(REPO_ROOT):
            return fail(
                "evals/ (outside evals/results/) or scripts/privacy_check.py has uncommitted "
                "changes; commit or stash them so every result maps to exactly one checker version"
            )
        for flag, path in inputs:
            if checker_dirty(REPO_ROOT, [path]):
                return fail(
                    f"{flag} {path} has uncommitted changes; commit or stash them so every "
                    "result maps to exactly one checker version"
                )
        for flag, path in (*inputs, (INSTALL_SCRIPT, REPO_ROOT / INSTALL_SCRIPT)):
            reason = input_refusal(REPO_ROOT, path)
            if reason:
                return fail(f"{flag} {path} {reason}")
        sha = agent_sha(REPO_ROOT)
        # Per scenario, from the paths actually passed: a run is the same only if the agent, the
        # checker and the sample, answer key and scenario files it reads all have the same content.
        checkers = {
            s.id: checker_sha(
                REPO_ROOT, sample=args.sample, answer_key=args.answer_key, scenario_dir=args.scenarios_dir / s.id
            )
            for s in scenarios
        }
    except (OSError, ValueError, RuntimeError) as exc:
        return fail(str(exc))
    if not scenarios:
        return fail(f"no scenarios found in {args.scenarios_dir}")

    denylist = load_denylist(denylist_path)
    if not denylist:
        return fail(
            f"denylist has no terms: {denylist_path} (with none, the privacy gate would check "
            "only the ssn/ein/long-digit patterns)"
        )
    done = completed_keys(load_results(args.results))
    try:
        runner = RUNNERS[args.tool](args.model)
    except ValueError as exc:
        return fail(str(exc))
    outside = [flag for flag, path in inputs if repo_relative(REPO_ROOT, path) is None]
    if outside:
        print(
            f"run.py: warning: {', '.join(outside)} outside the repo: its content is hashed, "
            "but rows will not map to a commit",
            file=sys.stderr,
        )
    bad = 0
    for scenario in scenarios:
        checker = checkers[scenario.id]
        for run_index in range(args.runs):
            key = resume_key(
                tool=args.tool,
                model=args.model,
                agent_sha=sha,
                scenario=scenario.id,
                run_index=run_index,
                checker_sha=checker,
            )
            if key in done:
                print(f"skip {scenario.id} #{run_index} (already complete)")
                continue
            result = run_scenario(
                scenario,
                tool=args.tool,
                model=args.model,
                runner=runner,
                run_index=run_index,
                sample=args.sample,
                answer_key=answer_key,
                agent_sha=sha,
                denylist=denylist,
                transcripts_dir=args.transcripts_dir,
                judge_fn=judge,
                judge_model=JUDGE_MODEL,
                checker_sha=checker,
                keep_workspace=args.keep_workspaces,
                agent_source=REPO_ROOT / "agent",
                repo_root=REPO_ROOT,
            )
            append_result(args.results, result)
            print(f"{result.status:12} {scenario.id} #{run_index} {result.note}".rstrip())
            bad += result.status in ("error", "timeout")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
