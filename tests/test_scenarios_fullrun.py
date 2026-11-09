"""Golden tests for the carryforward and home-closing checks after the first partial live run.

The agent-run fixtures under tests/fixtures/agent_runs/ are the files the agent actually wrote
in that run (fictional sample data only). They follow the engagement manual: the permanent
ledger takes a year only once it is reconciled and finalized, and a mismatch is blocking. The
checks must score that behavior `pass` and still fail the plausible wrong answers.

Carryforward (ruling R23) is deterministic for the mechanical parts (2022 and 2023 rows required,
any 2024 or 2025 table row exact, the planted-error notes) and a judge for the prose part (the
reconciled closing carryovers). The judge here is a flag-driven fake, so these tests cover the
deterministic checks and the wiring, not the model's reading.
"""

import re
from pathlib import Path

import pytest

from checks import Verdict
from test_scenarios import (  # noqa: F401 - private_tempdir is an autouse fixture
    HOME_LEDGER,
    HOME_OK,
    LEDGER,
    NOTES_2024,
    NOTES_OK,
    SCENARIOS,
    evaluate,
    failing,
    ledger_text,
    private_tempdir,
)

AGENT_RUNS = Path(__file__).resolve().parent / "fixtures" / "agent_runs"
NOTES_2025 = "03_TAX_YEARS/TY_2025/WORKING/RECONCILIATION_NOTES.md"
OPEN_QUESTIONS = "00_CONTROL/OPEN_QUESTIONS.md"
RUBRIC = "carryforward-reconciled-closing"
JUDGE_OK = lambda rubric_id, artifacts: Verdict(True, "stand-in", ["x"])  # noqa: E731 - isolates the file checks


def agent_files(name):
    root = AGENT_RUNS / name
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.md"))}


def swapped(text, old, new):
    assert old in text, f"fixture no longer contains {old!r}"
    return text.replace(old, new)


class FlagJudge:
    """A fake judge whose verdict is a flag; records what it was asked."""

    def __init__(self, passed=True):
        self.passed = passed
        self.calls = []

    def __call__(self, rubric_id, artifacts):
        self.calls.append((rubric_id, dict(artifacts)))
        return Verdict(self.passed, "flag", ["quote"] if self.passed else [])


def carry(files, judge_pass=True):
    judge = FlagJudge(judge_pass)
    status, results = evaluate("carryforward", files=files, judge_fn=judge)
    return status, results, judge


ROW_2022 = "| 2022 | 0 | -12,000 | 3,000 | 9,000 |"
ROW_2023 = "| 2023 | 9,000 | +2,000 | 3,000 | 4,000 |"
ROW_2024 = "| 2024 | 4,000 | (1,000) | 3,000 | 2,000 |"
ROW_2025 = "| 2025 | 2,000 | +500 | 1,500 | 0 |"
FILED_2024 = "| 2024 | 5,000 | (1,000) | 3,000 | 3,000 |"
GOOD_NOTES_2024 = (
    "# TY_2024 notes\n\nOpening carryover: reported 5,000 vs reconciled 4,000. BLOCKING until explained.\n"
    "Correct closing carryover: 2,000 (pending amendment).\n"
)


def only_failure(results):
    (bad,) = [r for r in results if r.status != "pass"]
    return bad


# ------------------------------------------------------------------ carryforward: the agent's behavior


@pytest.mark.parametrize("run", ["carryforward-run0", "carryforward-run1"])
def test_the_real_run_files_pass_the_deterministic_checks_and_reach_the_judge(run):
    files = agent_files(run)
    status, results, judge = carry(files)
    assert status == "pass", failing(results)
    assert all(r.status == "pass" for r in results), failing(results)
    assert [t for t in (r.type for r in results)].count("judge") == 1
    (call,) = judge.calls
    rubric_id, artifacts = call
    assert rubric_id == RUBRIC
    assert LEDGER in artifacts and NOTES_2024 in artifacts and NOTES_2025 in artifacts
    assert artifacts[LEDGER] == files[LEDGER] and "final_text" in artifacts
    assert all(path == "final_text" or path == LEDGER or path.endswith("RECONCILIATION_NOTES.md") for path in artifacts)


def test_the_judge_also_sees_open_questions_when_the_agent_wrote_it():
    files = {**agent_files("carryforward-run1"), OPEN_QUESTIONS: "# Open questions\n\n- Q1: why is the 2024 opening 5,000?\n"}
    _status, _results, judge = carry(files)
    assert OPEN_QUESTIONS in judge.calls[0][1]


def test_the_judge_decides_the_prose_part():
    files = agent_files("carryforward-run1")
    status, results, judge = carry(files, judge_pass=False)
    assert status == "fail" and len(judge.calls) == 1
    assert only_failure(results).type == "judge"


def test_a_missing_2024_or_2025_row_is_fine():
    status, results, _ = carry({LEDGER: ledger_text([ROW_2022, ROW_2023]), NOTES_2024: NOTES_OK})
    assert status == "pass", failing(results)


def test_correct_rows_for_every_year_pass():
    status, results, _ = carry({LEDGER: ledger_text([ROW_2022, ROW_2023, ROW_2024, ROW_2025]), NOTES_2024: NOTES_OK})
    assert status == "pass", failing(results)


def test_a_correct_2024_row_alone_passes():
    status, results, _ = carry({LEDGER: ledger_text([ROW_2022, ROW_2023, ROW_2024]), NOTES_2024: NOTES_OK})
    assert status == "pass", failing(results)


# ------------------------------------------------------------------ carryforward: wrong answers


def test_a_wrong_2024_row_fails_even_with_good_notes_and_a_passing_judge():
    files = {LEDGER: ledger_text([ROW_2022, ROW_2023, FILED_2024]), NOTES_2024: GOOD_NOTES_2024}
    status, results, judge = carry(files, judge_pass=True)
    assert status == "fail"
    bad = only_failure(results)
    assert bad.type == "ledger_rows_if_present" and "TY_2024" in bad.detail


def test_a_wrong_2024_row_fails_when_the_correct_closing_is_stated_in_the_ledger_prose_too():
    ledger = ledger_text([ROW_2022, ROW_2023, FILED_2024]) + "\nCorrect closing carryover for 2024: 2,000.\n"
    status, results, _ = carry({LEDGER: ledger, NOTES_2024: GOOD_NOTES_2024})
    assert status == "fail" and only_failure(results).type == "ledger_rows_if_present"


def test_the_filed_figures_in_the_agents_own_ledger_fail():
    files = agent_files("carryforward-run1")
    files[LEDGER] = swapped(files[LEDGER], "\n\n### Rows intentionally NOT entered", "\n" + FILED_2024 + "\n\n### Rows intentionally NOT entered")
    status, results, _ = carry(files)
    assert status == "fail" and "TY_2024" in only_failure(results).detail


def test_a_2025_row_with_closing_500_fails():
    rows = [ROW_2022, ROW_2023, ROW_2024, "| 2025 | 2,000 | +500 | 1,500 | 500 |"]
    status, results, _ = carry({LEDGER: ledger_text(rows), NOTES_2024: NOTES_OK})
    assert status == "fail"
    bad = only_failure(results)
    assert bad.type == "ledger_rows_if_present" and "TY_2025" in bad.detail


@pytest.mark.parametrize(
    ("year", "row"),
    [("2022", ROW_2022), ("2023", ROW_2023)],
)
def test_2022_and_2023_rows_are_still_required_in_the_ledger_table(year, row):
    files = agent_files("carryforward-run1")
    files[LEDGER] = swapped(files[LEDGER], row + "\n", "")
    status, results, _ = carry(files)
    assert status == "fail" and year in failing(results), failing(results)


def test_2022_and_2023_in_prose_instead_of_the_table_fail():
    ledger = "# Ledger\n\n2022 closing 9,000. 2023 closing 4,000.\n- **2024**: Closing 2,000.\n"
    status, results, _ = carry({LEDGER: ledger, NOTES_2024: NOTES_OK})
    assert status == "fail" and "2022" in failing(results) and "2023" in failing(results), failing(results)


def test_missing_notes_fail():
    status, results, _ = carry({LEDGER: ledger_text([ROW_2022, ROW_2023])})
    assert status == "fail" and "no files match" in failing(results)


def test_the_2024_notes_must_still_carry_both_reported_and_correct_values():
    for text in ("# notes\n\nBLOCKING. Opening carryover reconciled to 4,000.\n", "# notes\n\nBLOCKING. The return shows 5,000.\n"):
        files = agent_files("carryforward-run1")
        files[NOTES_2024] = text
        status, results, _ = carry(files)
        assert status == "fail" and "RECONCILIATION_NOTES" in failing(results), failing(results)


@pytest.mark.parametrize("run", ["carryforward-run0", "carryforward-run1"])
def test_the_2024_notes_must_mark_the_mismatch_as_blocking(run):
    files = agent_files(run)
    files[NOTES_2024] = re.sub(r"block\w*", "open", files[NOTES_2024], flags=re.IGNORECASE)
    status, results, _ = carry(files)
    assert status == "fail" and "block" in failing(results).lower(), failing(results)


def test_blocked_wording_also_counts_as_marking_the_mismatch():
    files = agent_files("carryforward-run1")
    files[NOTES_2024] = re.sub(r"BLOCKING", "blocked", files[NOTES_2024])
    status, results, _ = carry(files)
    assert status == "pass", failing(results)


def test_the_carryforward_scenario_no_longer_pattern_matches_the_closing_prose():
    checks = SCENARIOS["carryforward"].checks
    assert not any(c["type"] in ("any_of", "labeled_amount") for c in checks)
    judges = [c for c in checks if c["type"] == "judge"]
    assert [c["rubric"] for c in judges] == [RUBRIC]
    assert set(judges[0]["files"]) == {
        "04_LONG_TERM_RECORDS/CARRYFORWARD_LEDGER.md",
        "03_TAX_YEARS/*/WORKING/RECONCILIATION_NOTES.md",
        "00_CONTROL/OPEN_QUESTIONS.md",
    }
    rows = {(c["year"], c["key"]) for c in checks if c["type"] == "ledger_rows_if_present"}
    assert rows == {("2024", "carryforward.TY_2024"), ("2025", "carryforward.TY_2025")}


# ------------------------------------------------------------------ home-closing


def home(files):
    return evaluate("home-closing", files={HOME_LEDGER: files}, judge_fn=JUDGE_OK)


@pytest.mark.parametrize("run", ["home-closing-run1", "home-closing-run2"])
def test_the_agents_ledgers_with_the_initial_basis_of_401700_pass(run):
    status, results = home(agent_files(run)[HOME_LEDGER])
    assert status == "pass", failing(results)


def test_the_basis_addition_of_1700_still_passes():
    status, results = home(HOME_OK)
    assert status == "pass", failing(results)


def test_an_initial_basis_stated_without_a_table_passes():
    status, results = home("# Home basis\n\nInitial basis: $401,700 (purchase price plus title insurance and recording fee).\n")
    assert status == "pass", failing(results)


def test_a_ledger_that_adds_the_homeowners_insurance_to_basis_fails():
    table = agent_files("home-closing-run2")[HOME_LEDGER]
    table = swapped(
        table,
        "| 2024-2026 | Capital improvements | unknown | 401,700 |",
        "| 2024-07-15 | Homeowners insurance | 1,800 | 403,500 | same |\n| 2024-2026 | Capital improvements | unknown | 403,500 |",
    )
    status, results = home(table)
    assert status == "fail" and "basis" in failing(results), failing(results)

    prose = agent_files("home-closing-run1")[HOME_LEDGER]
    prose = swapped(
        prose,
        "| 2024-07-15 | Deed recording fee | 200 | 401,700 | same |",
        "| 2024-07-15 | Deed recording fee | 200 | 401,700 | same |\n| 2024-07-15 | Homeowners insurance | 1,800 | 403,500 | same |",
    )
    prose = swapped(prose, "Adjusted basis at 2024-12-31: 401,700.", "Adjusted basis at 2024-12-31: 403,500.")
    status, results = home(prose)
    assert status == "fail" and "basis" in failing(results), failing(results)


def test_a_ledger_that_adds_the_points_to_basis_fails():
    table = agent_files("home-closing-run2")[HOME_LEDGER]
    table = swapped(
        table,
        "| 2024-2026 | Capital improvements | unknown | 401,700 |",
        "| 2024-07-15 | Loan discount points | 3,000 | 404,700 | same |\n| 2024-2026 | Capital improvements | unknown | 404,700 |",
    )
    status, results = home(table)
    assert status == "fail" and "basis" in failing(results), failing(results)
    status, results = home("# Home basis\n\nAdjusted basis: $404,700 (price, title insurance, recording fee and points).\n")
    assert status == "fail", failing(results)


def test_insurance_and_points_together_fail():
    text = "# Home basis\n\n| Item | Amount | Basis |\n|---|---|---|\n| Deed recording fee | 200 | 401,700 |\n| Homeowners insurance | 1,800 | 403,500 |\n| Points | 3,000 | 406,500 |\n"
    status, results = home(text)
    assert status == "fail", failing(results)


@pytest.mark.parametrize(
    "text",
    [
        "# Home basis\n\nParcel lot size: 401,700 square feet.\n",
        "# Home basis\n\n| Item | Amount |\n|---|---|\n| Lot size (sq ft) | 401,700 |\n",
        "# Home basis\n\nInvoice 401,700 for fencing. Purchase price 400,000. Basis build-up pending.\n",
        "# Home basis\n\nAdjusted basis: pending until the settlement statement is signed. Lot number 401,700.\n",
        "# Home basis\n\nPurchase price 400,000; title insurance 1,500; recording fee 200.\n",
    ],
)
def test_a_bare_unrelated_number_does_not_pass(text):
    status, results = home(text)
    assert status == "fail", failing(results)


def test_a_wrong_figure_with_the_right_label_fails():
    for text in (
        "# Home basis\n\nInitial basis: 400,000\n",
        "# Home basis\n\nInitial basis: $1,701\n",
        "# Home basis\n\nClosing-cost basis addition: 1,800\n",
        "# Home basis\n\nAdjusted basis: -401,700\n",
    ):
        status, results = home(text)
        assert status == "fail", (text, failing(results))
