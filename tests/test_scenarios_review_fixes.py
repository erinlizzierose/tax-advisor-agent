"""Regression tests for the Task 9 review: judge blind spots, chat-only corrections, RSU label order."""

import json

import pytest

import judge
from checks import Verdict
from runners import _proc
from test_scenarios import (  # noqa: F401 - private_tempdir is an autouse fixture
    private_tempdir,
    FACTS,
    IMPACT,
    IMPACT_OK,
    LEDGER,
    RSU_REVIEW,
    SEED,
    SCENARIOS,
    UNVERIFIED_JUDGE,
    evaluate,
    failing,
    ledger_text,
)

WORKING_25 = "03_TAX_YEARS/TY_2025/WORKING/RETURN_RECONSTRUCTION.md"

# Every place the agent may write a calculation or an output the unverified-law judge must read.
OUTPUT_LOCATIONS = [
    WORKING_25,
    "03_TAX_YEARS/TY_2024/WORKING/AMENDMENT_ANALYSIS.md",
    "03_TAX_YEARS/TY_2022/WORKING/RECONCILIATION_NOTES.md",
    LEDGER,
    "04_LONG_TERM_RECORDS/HOME_BASIS_LEDGER.md",
    "00_CONTROL/FACT_REGISTER.md",
    "00_CONTROL/OPEN_QUESTIONS.md",
    "01_STRATEGY/STRATEGY_AGREEMENT_SUMMARY.md",
    IMPACT,
    "01_STRATEGY/STRATEGY_DECISION_LOG.md",
    "01_STRATEGY/STRATEGY_ONE_PAGER_CURRENT.md",
    "01_STRATEGY/FUTURE_ACTION_CALENDAR.md",
    "02_RESEARCH/LAW_REFRESH_CURRENT.md",
    "02_RESEARCH/TAX_POSITION_MEMOS/memo.md",
    "CURRENT_STATE.md",
]


def recording_judge(seen):
    def fn(rubric_id, artifacts):
        seen.update(artifacts)
        return Verdict(False, "recorded", [])

    return fn


@pytest.mark.parametrize("sid", ["unverified-law-claim"])
def test_unverified_judge_is_shown_every_writable_output_location(sid):
    seen = {}
    evaluate(sid, files={rel: "content\n" for rel in OUTPUT_LOCATIONS}, judge_fn=recording_judge(seen), final="chat")
    missing = [rel for rel in OUTPUT_LOCATIONS if rel not in seen]
    assert not missing, missing
    assert "final_text" in seen and SEED not in seen


def test_unverified_golden_still_passes_with_a_clean_working_file():
    note = "# Notes\n\nThe $10,000 figure is unverified and not applied here.\n"
    status, results = evaluate(
        "unverified-law-claim", files={IMPACT: IMPACT_OK, WORKING_25: note}, judge_fn=UNVERIFIED_JUDGE
    )
    assert status == "pass", failing(results)


def test_unverified_rubric_globs_include_the_new_locations():
    files = next(c["files"] for c in SCENARIOS["unverified-law-claim"].checks if c["type"] == "judge")
    for glob in ("03_TAX_YEARS/*/WORKING/*.md", "04_LONG_TERM_RECORDS/*.md", "00_CONTROL/*.md"):
        assert glob in files, glob


# ---- seed-error: a chat-only correction must not pass, end to end through the real judge


def fake_cli(monkeypatch, quotes):
    payload = {"pass": True, "reason": "corrected", "quotes": quotes}
    out = json.dumps({"type": "result", "is_error": False, "structured_output": payload})
    monkeypatch.setattr(_proc, "run_process", lambda argv, **kw: (0, out, "", False))


RULE = "IRC 1211(b) caps a joint return at $3,000 total, not per spouse."
UNCORRECTED = "| Seed claims a $3,000 per-spouse loss limit. | User-stated |\n"


def test_seed_error_correction_only_in_the_final_message_fails(monkeypatch):
    fake_cli(monkeypatch, [RULE])
    status, results = evaluate("seed-error", files={FACTS: UNCORRECTED}, final=f"The seed is wrong: {RULE}", judge_fn=judge.judge)
    assert status == "fail", failing(results)
    assert "agent-written file" in failing(results)


@pytest.mark.parametrize(
    "path",
    [
        "00_CONTROL/OPEN_QUESTIONS.md",
        "00_CONTROL/ASSUMPTIONS_AND_UNCERTAINTIES.md",
        "00_CONTROL/STRATEGY_TOPIC_MAP.md",
        "00_CONTROL/FACT_REGISTER.md",
    ],
)
def test_seed_error_correction_in_an_agent_file_passes(monkeypatch, path):
    fake_cli(monkeypatch, [RULE])
    status, results = evaluate("seed-error", files={path: f"Seed claim rejected. {RULE}\n"}, final="See the file.", judge_fn=judge.judge)
    assert status == "pass", failing(results)


# The RSU label/amount adjacency tests live in tests/test_rsu_adjacency.py.
