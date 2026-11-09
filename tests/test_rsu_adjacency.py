"""RSU basis/gain checks pair each label with the amount next to it, in either order."""

import itertools
import re

import pytest

from checks import Verdict, pair_labels_with_amounts
from test_scenarios import (  # noqa: F401 - private_tempdir is an autouse fixture
    RSU_REVIEW,
    SCENARIOS,
    evaluate,
    failing,
    private_tempdir,
)

JUDGE_OK = lambda rubric_id, artifacts: Verdict(True, "stand-in", ["x"])  # noqa: E731 - isolates the file checks


def score(text):
    return evaluate("rsu-basis", files={RSU_REVIEW: f"# RSU review\n\n{text}\n"}, judge_fn=JUDGE_OK)


BASIS_FORMS = [
    "basis of $15,000",
    "$15,000 basis",
    "Basis: 15,000",
    "| Basis | $15,000 |",
    "basis at vest 2024-03-01: $15,000",
]
GAIN_FORMS = [
    "gain of $1,000",
    "$1,000 long-term gain",
    "Gain: 1,000",
    "| Gain | $1,000 |",
]


@pytest.mark.parametrize(("basis", "gain"), list(itertools.product(BASIS_FORMS, GAIN_FORMS)))
def test_each_accepted_form_passes_on_its_own_lines(basis, gain):
    status, results = score(f"{basis}\n{gain}")
    assert status == "pass", failing(results)


@pytest.mark.parametrize(
    "line",
    [
        "$15,000 basis; $1,000 gain",
        "$15,000 basis, $1,000 gain",
        "$15,000 cost basis and a $1,000 gain",
        "15,000 of basis and 1,000 of gain",
        "basis of $15,000 and a gain of $1,000",
        "basis $15,000, gain $1,000",
        "Proceeds 16,000, basis 15,000, gain 1,000",
        "| Basis | 15,000 | Gain | 1,000 |",
        "| 15,000 | basis | 1,000 | gain |",
        "Corrected cost basis: $15,000. Long-term gain: $1,000.",
        "Shares sold 100, basis $15,000, gain $1,000",
    ],
)
def test_accepted_forms_on_one_line(line):
    status, results = score(line)
    assert status == "pass", failing(results)


PROBES = [
    "1,000 basis, 15,000 gain",
    "basis $1,000 and gain $15,000",
    "| 1,000 | basis | 15,000 | gain |",
]


@pytest.mark.parametrize("line", PROBES + ["| Gain | 15,000 | Basis | 1,000 |", "gain 15,000, basis 1,000"])
def test_swapped_basis_and_gain_fail_both_checks(line):
    status, results = score(line)
    assert status == "fail"
    text = failing(results).lower()
    assert "rsu.basis" in text and "rsu.gain" in text


@pytest.mark.parametrize(
    ("line", "needle"),
    [
        ("basis 0; gain 16,000", "rsu.basis"),
        ("$0 basis; $16,000 gain", "rsu.basis"),
        ("basis $15,000; gain $16,000", "rsu.gain"),
        ("$1,000 basis; $15,000 gain", "rsu.basis"),
        ("$15,000 gain; $1,000 basis", "rsu.basis"),
        ("$14,000 basis; $1,000 gain", "rsu.basis"),
        ("basis $115,000; gain $1,000", "rsu.basis"),
        ("basis 15,000; gain 1,0000", "rsu.gain"),
        ("15,000 shares; 1,000 gain", "rsu.basis"),
        ("basis at vest 2024-03-01: $14,000; gain $1,000", "rsu.basis"),
        ("basis $1,000 or $15,000; gain $1,000", "rsu.basis"),
        ("basis 2024 and 15,000; gain $1,000", "rsu.basis"),
        ("basis\n$15,000; gain $1,000", "rsu.basis"),
        ("basis (Treas. Reg. 1.83-4(b)(1)): $15,000; gain $1,000", "rsu.basis"),
    ],
)
def test_wrong_or_misattached_amounts_fail(line, needle):
    status, results = score(line)
    assert status == "fail", failing(results)
    assert needle in failing(results).lower()


@pytest.mark.parametrize(
    ("line", "must_fail"),
    [
        # a label with no amount of its own must not inherit the competing label's amount
        ("basis proceeds $15,000; gain $1,000", ["rsu.basis"]),
        ("gain proceeds $1,000; basis $15,000", ["rsu.gain"]),
        ("basis $15,000; gain cost $1,000", ["rsu.gain"]),
        ("basis gain $15,000", ["rsu.basis", "rsu.gain"]),
        ("gain basis $1,000", ["rsu.basis", "rsu.gain"]),
        ("basis | proceeds | $15,000 | gain $1,000", ["rsu.basis"]),
    ],
)
def test_competing_labels_are_barriers_not_one_label(line, must_fail):
    status, results = score(line)
    assert status == "fail", failing(results)
    text = failing(results).lower()
    for key in must_fail:
        assert key in text, (key, text)


def test_the_check_that_owns_the_amount_still_passes():
    # "basis $15,000; gain cost $1,000": basis really has its amount; only gain lacks one.
    _, results = score("basis $15,000; gain cost $1,000")
    assert [r.status for r in results if r.type == "labeled_amount"] == ["pass", "fail"]


@pytest.mark.parametrize(
    "line",
    [
        "adjusted cost basis: $15,000; realized gain: $1,000",
        "adjusted basis of $15,000 and a capital gain of $1,000",
        "$15,000 cost basis and a $1,000 long-term capital gain",
        "$15,000 adjusted cost basis, $1,000 long-term gain",
        "| Cost basis | $15,000 | Long-term gain | $1,000 |",
    ],
)
def test_listed_synonym_phrases_stay_one_label(line):
    status, results = score(line)
    assert status == "pass", failing(results)


def test_only_listed_phrases_merge():
    from decimal import Decimal as D

    from checks import SYNONYM_PHRASES

    assert SYNONYM_PHRASES, "a named constant lists the synonymous phrases"
    for phrase in ("cost basis", "adjusted basis", "adjusted cost basis", "long-term gain", "capital gain", "realized gain"):
        assert any(re.fullmatch(p, phrase, re.I) for p in SYNONYM_PHRASES), phrase
    # unlisted neighbours stay separate labels and block each other
    assert pairs("basis cost $15,000", "basis", "cost") == []
    assert pairs("cost basis $15,000", "basis", "cost") == [D(15000)]
    assert pairs("basis proceeds $15,000") == []
    assert pairs("proceeds basis $15,000", "proceeds", "basis") == []
    assert pairs("gain basis $15,000", "gain", "basis") == []


def test_a_rejected_nearer_amount_does_not_let_a_label_reach_a_farther_one():
    # basis is adjacent to 1,000, not to the 15,000 behind it: no skipping past a rejected amount.
    status, results = score("basis $1,000 then $15,000")
    assert status == "fail" and "rsu.basis" in failing(results).lower()


def pairs(line, mine="basis|cost", others="gain|proceeds"):
    return pair_labels_with_amounts(line, re.compile(mine, re.I), re.compile(others, re.I))


def test_pairing_helper_direct():
    from decimal import Decimal as D

    assert pairs("1,000 basis, 15,000 gain") == [D(1000)]
    assert pairs("1,000 basis, 15,000 gain", "gain", "basis") == [D(15000)]
    assert pairs("basis $15,000 and gain $1,000", "gain", "basis") == [D(1000)]
    assert pairs("| Basis | 15,000 | Gain | 1,000 |", "gain", "basis") == [D(1000)]
    assert pairs("basis at vest 2024-03-01: $15,000") == [D(15000)]
    assert pairs("basis, then 2024 and 15,000") == [D(2024)], "the nearest amount only; 15,000 is out of reach"
    assert pairs("no labels 15,000") == []
    assert pairs("basis (1,000)") == [D(-1000)]


# ------------------------------------------------------------------ equations (T11 fix 2)

CLAUDE_EQUATION = (
    "- Derived: basis 100 × 150.00 = 15,000. Gain = 16,000 − 15,000 = **1,000 long-term** "
    "(held 2024-03-01 → 2025-06-02, more than one year)."
)


def labeled(results, key):
    return next(r for r in results if r.type == "labeled_amount" and key in r.detail)


def test_the_claude_equation_line_passes_both_checks():
    status, results = score(CLAUDE_EQUATION)
    assert status == "pass", failing(results)


@pytest.mark.parametrize(
    ("line", "mine", "others", "expected"),
    [
        ("Gain = 16,000 − 15,000 = **1,000 long-term**", "gain", "basis", [1000]),
        ("gain: 16,000 - 15,000 = 1,000", "gain", "basis", [1000]),
        ("basis: 100 × 150 = 15,000", "basis", "gain", [15000]),
        ("basis 100 x $150 = $15,000", "basis", "gain", [15000]),
        ("gain = 1,000 + 15,000 = 16,000", "gain", "basis", [16000]),  # a false pass before the fix
        ("gain: 1,000 + 15,000 = 16,000", "gain", "basis", [16000]),
        ("gain = 16,000 − 15,000 = 1,000 = 2,000", "gain", "basis", [2000]),
        ("basis 15,000 = 100 × 150", "basis", "gain", [15000]),  # value first, then its derivation
        ("gain 1,000 = 16,000 − 15,000", "gain", "basis", [1000]),
        ("gain = 16,000 − 15,000 = 1,000 + 5", "gain", "basis", []),  # neither side is one amount
        ("16,000 − 15,000 = 1,000 gain", "gain", "basis", [1000]),
        ("gain 5,000 / 1,000", "gain", "basis", [5000]),  # no "=": the nearest amount, as before
        # a label inside the expression is a term of it, not its subject (a real Claude line)
        ("Corrected: proceeds 16,000 - basis 15,000 = 1,000 LT gain", "basis", "gain|proceeds", [15000]),
        ("Corrected: proceeds 16,000 - basis 15,000 = 1,000 LT gain", "gain", "basis|proceeds", [1000]),
        ("- Gain = 16,000 − 15,000 = 1,000", "gain", "basis", [1000]),  # a list marker is not an operator
        ("**Gain** = 16,000 − 15,000 = 1,000", "gain", "basis", [1000]),
        ("RSU gain = 16,000 − 15,000 = 1,000 long-term, not 16,000.", "gain", "basis", [1000]),
    ],
)
def test_an_equation_pairs_the_label_with_the_amount_after_the_last_equals(line, mine, others, expected):
    from decimal import Decimal as D

    assert pairs(line, mine, others) == [D(v) for v in expected]


@pytest.mark.parametrize(
    "line",
    [
        "gain = 1,000 + 15,000 = 16,000",
        "Gain = 16,000 − 0 = 16,000",
        "gain: 15,000 + 1,000 = 16,000",
        "Gain = 1,000 × 16 = 16,000",
    ],
)
def test_an_equation_ending_in_the_wrong_gain_fails(line):
    status, results = score("Corrected cost basis: $15,000\n" + line)
    assert status == "fail" and labeled(results, "rsu.gain").status == "fail", failing(results)


def test_an_equation_ending_in_the_wrong_basis_fails():
    status, results = score("basis: 100 × 150 = 1,000\nGain: 1,000")
    assert status == "fail" and labeled(results, "rsu.basis").status == "fail", failing(results)


# ------------------------------------------------------------------ tables (T11 fix 3)

OPENCODE_TABLE = (
    "| Sale | Proceeds | Supported basis | Gain/(loss) | Treatment |\n"
    "|---|---|---|---|---|\n"
    "| Matching RSU lot | $16,000 | $15,000 = 100 × $150 | $1,000 | Long-term, Form 8949 Part II Box E |\n"
    "| Index fund | $4,500 | $5,000 | $(500) | Long-term |\n"
    "| Total | $20,500 | $20,000 | $500 | |\n"
)


def test_a_gain_cell_under_a_gain_header_passes():
    status, results = score("Corrected cost basis: $15,000\n\n" + OPENCODE_TABLE)
    assert labeled(results, "rsu.gain").status == "pass", failing(results)
    assert status == "pass", failing(results)


@pytest.mark.parametrize(
    "table",
    [
        OPENCODE_TABLE.replace("| $1,000 |", "| $16,000 |"),  # the zero-basis gain
        OPENCODE_TABLE.replace("| $1,000 |", "| $1,000 or $16,000 |"),
        OPENCODE_TABLE.replace("| $1,000 |", "| 1,000 (est.) |"),
        OPENCODE_TABLE.replace("Gain/(loss)", "Basis / Gain"),  # the header names a competing label too
        OPENCODE_TABLE.replace("Gain/(loss)", "Gain if basis were zero"),
        OPENCODE_TABLE.replace("Gain/(loss)", "Result"),  # no gain header at all
        OPENCODE_TABLE.replace("|---|---|---|---|---|\n", ""),  # no separator row: not a table
        OPENCODE_TABLE.replace("| $16,000 | $15,000 = 100 × $150 | $1,000 |", "| $1,000 | $15,000 = 100 × $150 | $16,000 |"),
        # 1,000 sits in another lot's row; the RSU row says 16,000
        OPENCODE_TABLE.replace("| $1,000 |", "| $16,000 |").replace("| $(500) |", "| $1,000 |"),
        OPENCODE_TABLE.replace("Matching RSU lot", "Matching lot"),  # no row names the RSU lot
    ],
)
def test_a_wrong_or_unreadable_gain_table_fails(table):
    status, results = score("Corrected cost basis: $15,000\n\n" + table)
    assert status == "fail" and labeled(results, "rsu.gain").status == "fail", failing(results)


def test_a_basis_cell_under_a_basis_header_passes_and_a_zero_one_fails():
    table = "| Lot | Proceeds | Cost basis | Gain |\n|---|---|---|---|\n| RSU | 16,000 | 15,000 | 1,000 |\n"
    status, results = score(table)
    assert status == "pass", failing(results)
    status, results = score(table.replace("| 15,000 |", "| 0 |").replace("| 1,000 |", "| 16,000 |"))
    assert labeled(results, "rsu.basis").status == "fail" and labeled(results, "rsu.gain").status == "fail"


def test_the_table_column_rule_needs_the_rows_identifying_context(tmp_path):
    from checks import CheckContext, run_checks

    (tmp_path / "ledger.md").write_text(
        "| Item | Amount | Running adjusted basis |\n|---|---|---|\n"
        "| Deed recording fee | 200 | 401,700 |\n| Homeowners insurance | 1,800 | 403,500 |\n"
    )
    key = {"home": {"initial_basis": 401700}}
    check = {"type": "labeled_amount", "file": "ledger.md", "label": r"\bbasis\b", "value": "home.initial_basis"}
    ctx = CheckContext(tmp_path, {}, {}, [], "", key)
    assert run_checks([check], ctx)[0].status == "fail", "no table_row: tables are not read"
    assert run_checks([{**check, "table_row": r"\binsurance\b"}], ctx)[0].status == "fail"
    assert run_checks([{**check, "table_row": r"\brecording\b"}], ctx)[0].status == "pass"
    assert run_checks([{**check, "table_row": "("}], ctx)[0].status == "error"


def test_the_rsu_checks_name_the_rsu_row_context():
    checks = [c for c in SCENARIOS["rsu-basis"].checks if c["type"] == "labeled_amount"]
    for check in checks:
        assert re.search(check["table_row"], "Matching RSU lot") and not re.search(check["table_row"], "Index fund")


def test_scenario_uses_the_adjacency_check_not_a_label_regex():
    checks = [c for c in SCENARIOS["rsu-basis"].checks if c["type"] == "labeled_amount"]
    assert {c["value"] for c in checks} == {"rsu.basis", "rsu.gain"}
    assert not [c for c in SCENARIOS["rsu-basis"].checks if c["type"] == "file_matches"]
