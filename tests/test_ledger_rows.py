"""ledger_rows_if_present: a ledger table row for a year, if there is one, must equal the answer key.

Header-driven (ruling R24). For each Markdown table in the file the header row maps the Opening,
Deduction used and Closing columns by name, in any order. In a table that maps all three, a row is
a year row if its identifying cell (the first non-value cell that holds any year, so a leading index
column is skipped) contains the year. A table mapping some but not all of the three, or one twice,
is unrelated only if NO row names the year in ANY cell (fail closed, E-F2). A table mapping none of
them (status, sources) fails on a year row (columns unknown) if ANY of its cells holds an amount-like
token anywhere in its text; a hold-out marker does not exempt such a row (R3c). Header synonyms
(Beginning/Applied/Ending, ...) map, so those tables are value-checked. A missing
row passes; a present row that differs, or cannot be
read, fails. Nothing else in the file (notes, prose, other rows) can rescue it.
"""

import pytest

import checks
from checks import CheckContext, run_checks

KEY = {
    "carryforward": {
        "TY_2024": {"opening": 4000, "deduction_used": 3000, "closing": 2000},
        "TY_2025": {"opening": 2000, "deduction_used": 1500, "closing": 0},
    }
}
CHECK_2024 = {"type": "ledger_rows_if_present", "file": "ledger.md", "year": "2024", "key": "carryforward.TY_2024"}
CHECK_2025 = {"type": "ledger_rows_if_present", "file": "ledger.md", "year": "2025", "key": "carryforward.TY_2025"}
UNKNOWN_COLUMNS = (
    "TY_2024 row with amounts in a table without ledger columns "
    "(columns unknown; amounts in side tables are not accepted)"
)
HEADER = "| Year | Opening | Current-year result | Deduction used | Closing |\n|---|---|---|---|---|\n"
GOOD_2024 = "| 2024 | 4,000 | (1,000) | 3,000 | 2,000 |"
GOOD_2025 = "| 2025 | 2,000 | +500 | 1,500 | 0 |"
WRONG_2024 = "| 2024 | 5,000 | (1,000) | 3,000 | 3,000 |"  # the filed return's figures


def score(tmp_path, text, check=CHECK_2024, key=KEY):
    if text is not None:
        (tmp_path / "ledger.md").write_text(text)
    ctx = CheckContext(tmp_path, {}, {}, [], "", key)
    return run_checks([check], ctx)[0]


def table(header_cells, *rows, outer_pipes=True):
    pipe = "|" if outer_pipes else ""
    lines = [pipe + " | ".join(header_cells) + pipe, pipe + " | ".join("---" for _ in header_cells) + pipe]
    lines += [pipe + " | ".join(row) + pipe for row in rows]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ pass


@pytest.mark.parametrize(
    "text",
    [
        None,  # no ledger file at all: the 2022/2023 checks fail on that, not this one
        "# Ledger\n\nNothing entered yet.\n",
        HEADER + "| 2022 | 0 | (12,000) | 3,000 | 9,000 |\n| 2023 | 9,000 | +2,000 | 3,000 | 4,000 |\n",
        HEADER + "| 2023 | 9,000 | +2,000 | 3,000 | 5,000 |\n",  # other years are not this check's business
        HEADER + GOOD_2024 + "\n",
        HEADER + "| TY_2024 | $4,000 | -1,000 | $3,000 | $2,000 |\n",
        HEADER + "| **2024** | **4,000** | (1,000) | 3,000 | **2,000** |\n",
        HEADER + "| 2024 | 4000 | (1,000) | 3000 | 2000.00 |\n",
        HEADER + "  | 2024 (reconciled) | 4,000 | (1,000) | 3,000 | 2,000\n",  # indented, no closing pipe
        table(["Year", "Opening", "Result", "Deduction", "Closing"], ["2024", "4,000", "(1,000)", "3,000", "2,000"], outer_pipes=False),
        "On 2024 the opening was 5,000 and the closing 3,000 (prose, not a table row).\n",
        "- **2024**: filed opening 5,000 vs reconciled 4,000; Closing 3,000 filed, 2,000 reconciled.\n",
        HEADER + "| 20245 | 1 | 2 | 3 | 4 |\n| 12024 | 1 | 2 | 3 | 4 |\n",  # not the year as a whole number
    ],
)
def test_absent_or_exact_passes(tmp_path, text):
    res = score(tmp_path, text)
    assert res.status == "pass", res.detail


@pytest.mark.parametrize(
    ("header", "row"),
    [
        (["Year", "Closing", "Opening", "Deduction used", "Result"], ["2024", "2,000", "4,000", "3,000", "(1,000)"]),
        (["Year", "Deduction used", "Closing", "Opening"], ["2024", "3,000", "2,000", "4,000"]),
        (["Tax year", "Opening", "Result", "Used", "Carryover out"], ["2024", "4,000", "(1,000)", "3,000", "2,000"]),
        (["Year", "Opening", "Result", "Deduction", "Closing"], ["2024", "4,000", "(1,000)", "3,000", "2,000"]),
        (["Year", "**Opening**", "Result", "_Deduction used_", "`Closing`"], ["2024", "4,000", "(1,000)", "3,000", "2,000"]),
        (["YEAR", "OPENING", "RESULT", "DEDUCTION USED", "CLOSING"], ["2024", "4,000", "(1,000)", "3,000", "2,000"]),
        (["Year", "Opening", "Result", "Deduction used", "Closing", "Source"], ["2024", "4,000", "(1,000)", "3,000", "2,000", "TY_2023 worksheet"]),
        (["Label", "Opening", "Deduction used", "Closing"], ["Tax year 2024", "4,000", "3,000", "2,000"]),  # the first column needn't be named Year
    ],
)
def test_a_correct_row_under_any_named_header_passes(tmp_path, header, row):
    res = score(tmp_path, table(header, row))
    assert res.status == "pass", res.detail


def test_the_header_synonym_lists_are_constants():
    assert set(checks.LEDGER_HEADERS) == {"opening", "deduction_used", "closing"}
    assert set(checks.LEDGER_HEADERS["deduction_used"]) == {
        "deduction used", "deduction", "used", "applied", "deducted", "utilized",
    }
    assert set(checks.LEDGER_HEADERS["closing"]) == {
        "closing", "closing carryover", "ending", "ending balance", "remaining", "closing balance",
        "carryover out", "carryforward out", "carried out", "carried forward",
    }
    assert set(checks.LEDGER_HEADERS["opening"]) == {
        "opening", "opening carryover", "opening carryover in", "beginning", "beginning balance", "starting",
        "starting balance", "opening balance", "carryover in", "carryforward in", "carried in",
        "carried forward in", "available",
    }


@pytest.mark.parametrize(
    "header",
    [
        ["Year", "Carryover in", "Used", "Carryover out"],
        ["Year", "Beginning balance", "Applied", "Ending balance"],
        ["Year", "Starting balance", "Deducted", "Remaining"],
        ["Year", "Carried forward in", "Utilized", "Carried forward"],
        ["Year", "Beginning", "Deduction", "Closing balance"],
    ],
)
def test_symmetric_synonym_headers_map_and_check_the_values(tmp_path, header):
    assert score(tmp_path, table(header, ["2024", "4,000", "3,000", "2,000"])).status == "pass"
    for wrong in (["2024", "5,000", "3,000", "2,000"], ["2024", "4,000", "3,000", "3,000"]):
        res = score(tmp_path, table(header, wrong))
        assert res.status == "fail" and UNKNOWN_COLUMNS not in res.detail and "header" not in res.detail, res.detail


@pytest.mark.parametrize(
    "header",
    [
        ["Year", "Opening carryover", "Current-year result", "Deduction used", "Closing carryover"],
        ["Year", "Opening carryover in", "Result", "Deduction", "Carryover out"],
        ["Year", "**Opening Carryover**", "Result", "Used", "_Closing  Carryover_"],
        ["Year", "Closing carryover", "Deduction used", "Opening carryover in", "Result"],
    ],
)
def test_the_synonym_headers_map_and_check_the_values(tmp_path, header):
    values = {"opening": "4,000", "deduction": "3,000", "closing": "2,000"}

    def row(closing):
        cells = []
        for name in header:
            plain = name.lower()
            if "opening" in plain:
                cells.append(values["opening"])
            elif "closing" in plain or "carryover out" in plain:
                cells.append(closing)
            elif "deduction" in plain or "used" in plain:
                cells.append(values["deduction"])
            elif plain == "year":
                cells.append("2024")
            else:
                cells.append("(1,000)")
        return cells

    assert score(tmp_path, table(header, row("2,000"))).status == "pass"
    res = score(tmp_path, table(header, row("3,000")))
    assert res.status == "fail" and "closing" in res.detail and "TY_2024" in res.detail, res.detail


@pytest.mark.parametrize(
    "header",
    [
        ["Year", "Opening", "Deduction used", "Closing", "Closing carryover"],
        ["Year", "Opening", "Deduction used", "Closing carryover", "Carryover out"],
        ["Year", "Opening", "Opening carryover", "Deduction used", "Closing"],
        ["Year", "Opening carryover", "Opening carryover in", "Deduction used", "Closing"],
    ],
)
def test_a_synonym_cannot_create_a_double_mapping(tmp_path, header):
    res = score(tmp_path, table(header, ["2024"] + ["4,000"] * (len(header) - 1)))
    assert res.status == "fail" and "header" in res.detail and "repeated" in res.detail, res.detail


# ------------------------------------------------------------------ fail: the review's loopholes


def test_a_tax_year_cell_is_recognized(tmp_path):
    res = score(tmp_path, HEADER + "| Tax year 2024 | 5,000 | (1,000) | 3,000 | 3,000 |\n")
    assert res.status == "fail" and "opening" in res.detail and "TY_2024" in res.detail, res.detail


def test_a_linked_year_cell_is_recognized(tmp_path):
    res = score(tmp_path, HEADER + "| [2024](#ty-2024) | 5,000 | (1,000) | 3,000 | 3,000 |\n")
    assert res.status == "fail" and "opening" in res.detail, res.detail


def test_columns_are_read_by_header_name_not_position(tmp_path):
    swapped = table(["Year", "Opening", "Result", "Closing", "Deduction"], ["2024", "4,000", "(1,000)", "3,000", "2,000"])
    res = score(tmp_path, swapped)
    assert res.status == "fail" and "closing" in res.detail and "deduction" in res.detail, res.detail
    right = table(["Year", "Opening", "Result", "Closing", "Deduction"], ["2024", "4,000", "(1,000)", "2,000", "3,000"])
    assert score(tmp_path, right).status == "pass"


@pytest.mark.parametrize(
    "year_cell",
    [
        "2024",
        "**2024**",
        "TY_2024",
        "2024 (pending)",
        "[2024](#x)",
        "[2024][ref]",
        "**[2024](#a)**",
        "_2024_",
        "`2024`",
        "Tax year 2024",
        "FY2024",
        "TY 2024 (filed)",
        '<a href="#x">2024</a>',
        "2024-2026",
        "2024-12-31",
    ],
)
def test_every_form_of_year_cell_is_recognized(tmp_path, year_cell):
    res = score(tmp_path, HEADER + f"| {year_cell} | 5,000 | (1,000) | 3,000 | 3,000 |\n")
    assert res.status == "fail" and "TY_2024" in res.detail, (year_cell, res.detail)
    assert score(tmp_path, HEADER + f"| {year_cell} | 4,000 | (1,000) | 3,000 | 2,000 |\n").status == "pass"


# ------------------------------------------------------------------ fail: a present row that differs


@pytest.mark.parametrize(
    ("row", "column"),
    [
        (WRONG_2024, "opening"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | 3,000 |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 6,000 | 2,000 |", "deduction"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | -2,000 |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | (2,000) |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | 2,000.50 |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | 12,000 |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | 2,000 (est.) |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 |  |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | pending |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 | 2,000 or 3,000 |", "closing"),
        ("| 2024 | 4,000 | (1,000) | 3,000 |", "closing"),  # the Closing column is missing from the row
        ("| 2024 | 3,000 | 2,000 |", "closing"),
        ("| TY_2024 | 5,000 | (1,000) | 3,000 | 3,000 |", "opening"),
        ("| 2024 (filed) | 5,000 | (1,000) | 3,000 | 3,000 |", "opening"),
    ],
)
def test_a_present_row_that_differs_fails(tmp_path, row, column):
    res = score(tmp_path, HEADER + row + "\n")
    assert res.status == "fail" and column in res.detail and "TY_2024" in res.detail, res.detail


def test_notes_prose_or_another_row_in_the_file_cannot_rescue_a_wrong_row(tmp_path):
    text = (
        HEADER
        + WRONG_2024
        + "\n\nCorrect closing carryover: 2,000 (pending amendment).\n- **2024**: Closing 2,000.\n\n"
        + HEADER
        + GOOD_2024
        + "\n"  # a second table with a correct row does not erase the first
    )
    res = score(tmp_path, text)
    assert res.status == "fail" and "opening" in res.detail, res.detail


def test_every_present_row_for_the_year_is_checked(tmp_path):
    assert score(tmp_path, HEADER + GOOD_2024 + "\n| TY_2024 | 4,000 | (1,000) | 3,000 | 3,000 |\n").status == "fail"
    assert score(tmp_path, HEADER + GOOD_2024 + "\n| TY_2024 | $4,000 | (1,000) | 3,000 | 2,000 |\n").status == "pass"


def test_a_2025_row_with_closing_500_fails_and_the_right_row_passes(tmp_path):
    res = score(tmp_path, HEADER + "| 2025 | 2,000 | +500 | 1,500 | 500 |\n", CHECK_2025)
    assert res.status == "fail" and "closing" in res.detail and "TY_2025" in res.detail
    assert score(tmp_path, HEADER + GOOD_2025 + "\n", CHECK_2025).status == "pass"
    assert score(tmp_path, HEADER + "| 2025 | 2,000 | +500 | 1,500 | 0.00 |\n", CHECK_2025).status == "pass"
    assert score(tmp_path, HEADER + "| 2025 | 2,000 | +500 | 1,500 | — |\n", CHECK_2025).status == "fail"


# ------------------------------------------------------------------ tables that are not (fully) the ledger


def test_a_table_with_none_of_the_three_columns_and_no_year_anywhere_is_another_attribute_and_ignored(tmp_path):
    """E-F2: a table is unrelated ONLY if no row names the year in any cell."""
    charity = table(
        ["Year", "Contributions", "AGI limit", "Carried over"],
        ["2022", "9,999", "8,888", "7,777"],
        ["2023", "1,111", "2,222", "3,333"],
    )
    assert score(tmp_path, charity).status == "pass"
    assert score(tmp_path, charity, CHECK_2025).status == "pass"
    assert score(tmp_path, charity + "\n" + HEADER + GOOD_2024 + "\n").status == "pass"
    assert score(tmp_path, charity + "\n" + HEADER + WRONG_2024 + "\n").status == "fail"
    assert score(tmp_path, HEADER + GOOD_2024 + "\n\n" + charity).status == "pass"


def test_a_table_with_none_of_the_three_columns_but_a_year_row_with_amounts_and_no_marker_fails_closed(tmp_path):
    charity = table(["Year", "Contributions", "AGI limit", "Carried over"], ["2024", "9,999", "8,888", "7,777"])
    res = score(tmp_path, charity)
    assert res.status == "fail" and "TY_2024" in res.detail and UNKNOWN_COLUMNS in res.detail, res.detail
    assert score(tmp_path, charity, CHECK_2025).status == "pass"
    assert score(tmp_path, HEADER + GOOD_2024 + "\n\n" + charity).status == "fail", "a good table elsewhere cannot rescue it"


# ------------------------------------------------------------------ N1: a year held out of the ledger

STATUS_TABLE = table(
    ["Year", "Filed", "Reconciled", "Status"],
    ["2022", "9,000", "9,000", "Reconciled"],
    ["2024", "5,000", "4,000", "BLOCKING"],
)
SOURCES_TABLE = table(
    ["Document", "Path"],
    ["Return summary", "03_TAX_YEARS/TY_2024/Filing/Form_1040_Summary.md"],
    ["1099-B", "`03_TAX_YEARS/TY_2024/Investments/1099B_Brightfield.md`"],
)
UNMAPPED = ["Year", "Prior", "Taken", "Next"]  # maps none of the three
SYNONYM_HEADER = ["Year", "Beginning", "Result", "Applied", "Ending"]


@pytest.mark.parametrize(
    "text",
    [
        HEADER + GOOD_2024 + "\n\n" + STATUS_TABLE.replace("5,000", "n/a").replace("4,000", "n/a"),
        table(["Year", "Status"], ["2024", "BLOCKING \u2014 held out"]),
        table(["Year", "Status"], ["2024", "no amounts here"]),
        table(["Year", "Where"], ["2024", "TY_2024/WORKING/RECONCILIATION_NOTES.md"]),
        table(["#", "Year", "Status"], ["1", "2024", "pending"]),  # an index column is not an amount
        table(["#", "Year", "Status"], ["12", "2024", "pending"]),
        table(["Year", "Status"], ["2024", "Q1 open; see 2025 filing 2024-03-01"]),
        table(["Document", "Path", "TY"], ["1099-B", "`03_TAX_YEARS/TY_2024/Investments/1099B_Brightfield.md`", "TY_2024"]),
        table(["Form", "Year"], ["Form 1040", "2024"]),
        HEADER + GOOD_2024 + "\n\n" + SOURCES_TABLE,
        SOURCES_TABLE,
    ],
)
def test_a_year_row_without_amounts_in_an_unmapped_table_passes(tmp_path, text):
    res = score(tmp_path, text)
    assert res.status == "pass", res.detail


def test_a_status_only_row_passes_but_the_same_row_with_amounts_fails_with_the_explicit_note(tmp_path):
    status_only = table(["Year", "Status"], ["2024", "BLOCKING \u2014 held out"])
    assert score(tmp_path, status_only).status == "pass"
    with_amounts = table(["Year", "Filed", "Reconciled", "Status"], ["2024", "5,000", "4,000", "BLOCKING"])
    res = score(tmp_path, with_amounts)
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail


@pytest.mark.parametrize("marker", ["pending", "blocked", "open", "deferred", "held out", "Not entered", "BLOCKING"])
def test_a_hold_out_marker_no_longer_exempts_a_row_that_has_amounts(tmp_path, marker):
    res = score(tmp_path, table(UNMAPPED, ["2024", "5,000", "3,000", marker]))
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail
    res = score(tmp_path, table(["Year", "Reported", "Status"], ["2024", "5,000", marker]))
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail
    assert score(tmp_path, table(UNMAPPED, ["2024 (pending)", "5,000", "3,000", "3,000"])).status == "fail"


@pytest.mark.parametrize(
    "amount_cell",
    ["5,000 ST", "$5,000 (reported)", "5,000 USD", "approx. 5000", "1,250.75 est", "ST 5,000", "(5,000) LT", "500", "$5"],
)
def test_amounts_with_units_or_annotations_are_still_amounts(tmp_path, amount_cell):
    res = score(tmp_path, table(UNMAPPED, ["2024", amount_cell, "pending", "pending"]))
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail


def test_an_annotated_amount_in_a_wrong_off_list_row_fails_even_when_other_cells_are_status_words(tmp_path):
    res = score(tmp_path, table(["Year", "Reported", "Status"], ["2024", "5,000 ST", "reconciled"]))
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail


def test_a_marker_on_one_row_does_not_excuse_another_year_row(tmp_path):
    text = table(UNMAPPED, ["2024", "5,000", "3,000", "BLOCKING"], ["2024", "5,000", "3,000", "3,000"])
    assert score(tmp_path, text).status == "fail"


SIDE_HEADER = "| Year | A | B | C |\n|---|---|---|---|\n"


@pytest.mark.parametrize(
    "row",
    [
        "| 2024 | 5000/3000/2000 |",
        "| 2024 | Opening | 2000 (per TY_2023 close) |",
        "| 2024 | 5k | 3k | 2k |",
        "| 2024 | opening 50 (est) |",
        "| 2024 | 5000/3000k |",
        "| 2024 | 50/30/20 |",
        "| 2024 | 2000 |",  # 2000 is not a plausible tax year: it is the TY_2024 closing amount
        "| 2024 | 1.5M |",
        "| 2024 | used 3 |",
        "| 2024 | 12 | pending |",
        "| 2024 | balance 2,000 | TY_2023 |",
    ],
)
def test_hidden_amounts_in_an_unmapped_table_fail_closed(tmp_path, row):
    res = score(tmp_path, SIDE_HEADER + row + "\n")
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, (row, res.detail)


@pytest.mark.parametrize(
    "row",
    [
        "| 2024 | held out |",
        "| 2024 | see Q1; filed 04/15/2025 |",
        "| 2024 | filed 2025-04-15, TY_2023 closing pending |",
        "| 2024 | Form 1040, Schedule D line 6 |",
        "| 2024 | see 03_TAX_YEARS/TY_2024/WORKING/RECONCILIATION_NOTES.md |",
        "| 2024 | FY2024 Q2 pending |",
        "| 2024 | TY_2024 | TY_2023 |",
    ],
)
def test_status_and_evidence_text_in_an_unmapped_table_stays_clear(tmp_path, row):
    res = score(tmp_path, SIDE_HEADER + row + "\n")
    assert res.status == "pass", (row, res.detail)


def test_a_year_outside_the_plausible_tax_years_is_an_amount_but_the_checked_year_is_not(tmp_path):
    assert score(tmp_path, SIDE_HEADER + "| 1999 | held out |\n").status == "pass"  # not the 2024 row
    assert score(tmp_path, SIDE_HEADER + "| 2024 | 1999 |\n").status == "fail"
    assert score(tmp_path, SIDE_HEADER + "| 2024 | 2023 | 2025 |\n").status == "pass"


def test_a_leading_index_column_is_not_an_amount_but_a_value_after_the_year_is(tmp_path):
    assert score(tmp_path, table(["#", "Year", "Status"], ["2", "2024", "pending"])).status == "pass"
    assert score(tmp_path, table(["Year", "Rank", "Status"], ["2024", "2", "pending"])).status == "fail"
    assert score(tmp_path, table(["Year", "Value", "Status"], ["2024", "2000", "pending"])).status == "fail"


@pytest.mark.parametrize(
    "header",
    [
        ["Year", "Beginning", "Result", "Applied", "Ending"],
        ["Year", "Starting", "Result", "Used", "Remaining"],
        ["Year", "Available", "Result", "Utilized", "Carried out"],
        ["Year", "Carryforward in", "Result", "Deducted", "Carryforward out"],
        ["Year", "**Opening Balance**", "Result", "Deduction", "Closing Balance"],
        ["YEAR", "BEGINNING", "RESULT", "APPLIED", "ENDING"],
    ],
)
def test_synonym_headers_are_mapped_and_value_checked(tmp_path, header):
    wrong = ["2024", "5,000", "(1,000)", "3,000", "3,000"]
    right = ["2024", "4,000", "(1,000)", "3,000", "2,000"]
    res = score(tmp_path, table(header, wrong))
    assert res.status == "fail" and "opening" in res.detail and "closing" in res.detail, res.detail
    assert UNKNOWN_COLUMNS not in res.detail
    assert score(tmp_path, table(header, right)).status == "pass"


def test_beginning_applied_ending_wrong_row_fails_with_pending_status_and_correct_row_passes(tmp_path):
    header = ["Year", "Beginning", "Applied", "Ending", "Status"]
    wrong = score(tmp_path, table(header, ["2024", "5,000", "3,000", "3,000", "pending"]))
    assert wrong.status == "fail" and "opening" in wrong.detail and "closing" in wrong.detail, wrong.detail
    assert score(tmp_path, table(header, ["2024", "4,000", "3,000", "2,000", "pending"])).status == "pass"


@pytest.mark.parametrize("name", ["Reported", "Reconciled", "Status", "Source", "Filed", "Current-year result", "Notes"])
def test_unrelated_headers_do_not_map_to_a_ledger_field(tmp_path, name):
    for field_names in checks.LEDGER_HEADERS.values():
        assert name.lower() not in field_names
    res = score(tmp_path, table(["Year", name], ["2024", "5,000"]))
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail


def test_two_synonyms_for_one_field_fail_closed(tmp_path):
    header = ["Year", "Beginning", "Opening", "Applied", "Ending"]
    res = score(tmp_path, table(header, ["2024", "4,000", "4,000", "3,000", "2,000"]))
    assert res.status == "fail" and "repeated" in res.detail, res.detail


@pytest.mark.parametrize(
    "header",
    [
        ["Year", "Opening", "Result"],
        ["Year", "Result", "Closing"],
        ["Year", "Opening", "Deduction used", "Closing", "Closing"],
    ],
)
def test_a_partial_or_repeated_header_fails_even_when_the_row_is_marked_blocking(tmp_path, header):
    row = ["2024"] + ["4,000"] * (len(header) - 2) + ["BLOCKING"]
    res = score(tmp_path, table(header, row))
    assert res.status == "fail" and "TY_2024" in res.detail and "header" in res.detail, res.detail


@pytest.mark.parametrize("cell", ["$4,000", "4000", "(1,000)", "-2,000.50", "\u22122,000", "**4,000**", "0"])
def test_these_cells_are_amounts(tmp_path, cell):
    res = score(tmp_path, table(["Year", "Value"], ["2024", cell]))
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail


@pytest.mark.parametrize(
    "cell",
    ["03_TAX_YEARS/TY_2024/x.md", "Avery", "see Q1", "2024-03-01", "N/A", "", "Form 1040", "1099-B", "notes.md", "TY_2024"],
)
def test_these_cells_are_not_amounts(tmp_path, cell):
    res = score(tmp_path, table(["Year", "Value"], ["2024", cell]))
    assert res.status == "pass", res.detail


@pytest.mark.parametrize(
    "header",
    [
        ["Year", "Beginning", "Result", "Taken", "Ending"],  # maps two of three: partial header
        ["Year", "Prior", "Result", "Taken", "Next"],
        ["Year", "Open", "Close"],
    ],
)
def test_an_unmappable_or_partial_header_with_a_2024_row_fails_closed(tmp_path, header):
    row = ["2024", "5,000", "(1,000)", "3,000", "3,000"][: len(header)]
    res = score(tmp_path, table(header, row))
    assert res.status == "fail" and "TY_2024" in res.detail, res.detail
    right = ["2024", "4,000", "(1,000)", "3,000", "2,000"][: len(header)]
    assert score(tmp_path, table(header, right)).status == "fail", "even a correct row cannot be verified"


def test_a_header_without_the_year_row_is_ignored(tmp_path):
    header = ["Year", "Prior", "Result", "Taken", "Next"]
    assert score(tmp_path, table(header, ["2022", "0", "(12,000)", "3,000", "9,000"])).status == "pass"


@pytest.mark.parametrize(
    "header, row",
    [
        (["Year", "Prior", "Result", "Taken", "Next"], ["Row 1", "2024", "5,000", "(1,000)", "3,000"]),
        (["Entry", "Period", "Prior", "Taken", "Next"], ["1", "TY_2024", "5,000", "3,000", "3,000"]),
    ],
)
def test_the_year_need_not_be_in_the_first_cell_of_an_unmapped_table(tmp_path, header, row):
    res = score(tmp_path, table(header, row))
    assert res.status == "fail" and UNKNOWN_COLUMNS in res.detail, res.detail


INDEXED = ["#", "Year", "Opening", "Result", "Deduction used", "Closing"]


def test_a_leading_index_column_is_recognized_in_a_mapped_table(tmp_path):
    res = score(tmp_path, table(INDEXED, ["3", "2024", "5,000", "(1,000)", "3,000", "3,000"]))
    assert res.status == "fail" and "opening" in res.detail and "TY_2024" in res.detail, res.detail
    assert score(tmp_path, table(INDEXED, ["3", "2024", "4,000", "(1,000)", "3,000", "2,000"])).status == "pass"
    assert score(tmp_path, table(INDEXED, ["1", "2022", "0", "(12,000)", "3,000", "9,000"])).status == "pass"


def test_a_year_named_in_a_notes_cell_of_another_years_mapped_row_is_not_that_years_row(tmp_path):
    header = ["Year", "Opening", "Result", "Deduction used", "Closing", "Notes"]
    text = table(header, ["2023", "9,000", "+2,000", "3,000", "4,000", "feeds TY_2024 opening"])
    assert score(tmp_path, text).status == "pass"
    assert score(tmp_path, text + "| 2024 | 4,000 | (1,000) | 3,000 | 2,000 | ok |\n").status == "pass"
    assert score(tmp_path, text + "| 2024 | 5,000 | (1,000) | 3,000 | 3,000 | ok |\n").status == "fail"


# A header-named year column (Year / Tax year / TY) identifies the row wherever it sits.
FROM_YEAR_HEADER = ["Note", "Opening", "Deduction used", "Closing", "Year"]


def test_a_year_in_the_last_column_identifies_the_row(tmp_path):
    res = score(tmp_path, table(FROM_YEAR_HEADER, ["From 2023", "5,000", "3,000", "3,000", "2024"]))
    assert res.status == "fail" and "opening" in res.detail and "TY_2024" in res.detail, res.detail
    assert score(tmp_path, table(FROM_YEAR_HEADER, ["From 2023", "4,000", "3,000", "2,000", "2024"])).status == "pass"
    # the same shape with a 2023 year cell is a 2023 row, whatever it says about 2024 elsewhere
    assert score(tmp_path, table(FROM_YEAR_HEADER, ["feeds 2024", "9,000", "3,000", "4,000", "2023"])).status == "pass"


@pytest.mark.parametrize("name", ["Year", "Tax year", "TY", "**Year**"])
def test_every_year_header_name_is_recognized_in_any_position(tmp_path, name):
    header = ["Prior", "Opening", "Deduction used", "Closing", name]
    res = score(tmp_path, table(header, ["2023 close", "5,000", "3,000", "3,000", "2024"]))
    assert res.status == "fail" and "TY_2024" in res.detail, res.detail
    header = [name, "Opening", "Deduction used", "Closing", "Prior"]
    assert score(tmp_path, table(header, ["2024", "5,000", "3,000", "3,000", "2023 close"])).status == "fail"


def test_without_a_year_named_header_the_first_cell_with_a_year_still_identifies_the_row(tmp_path):
    header = ["Period", "Opening", "Deduction used", "Closing", "Source"]
    assert score(tmp_path, table(header, ["2024", "5,000", "3,000", "3,000", "TY_2023"])).status == "fail"
    assert score(tmp_path, table(header, ["2023", "9,000", "3,000", "4,000", "feeds TY_2024"])).status == "pass"


def test_a_year_column_cell_without_a_year_falls_back_to_the_other_cells(tmp_path):
    header = ["Year", "Opening", "Deduction used", "Closing", "Note"]
    assert score(tmp_path, table(header, ["TBD", "5,000", "3,000", "3,000", "TY_2024"])).status == "fail"


def test_a_year_in_a_value_cell_alone_is_not_a_year_row(tmp_path):
    assert score(tmp_path, HEADER + "| 2023 | 2024 | +2,000 | 3,000 | 2024 |\n").status == "pass"


def test_a_headerless_pipe_run_with_the_year_in_any_cell_fails_closed(tmp_path):
    res = score(tmp_path, "Row 3 | 2024 | 5,000 | (1,000) | 3,000 | 3,000\n")
    assert res.status == "fail" and "TY_2024" in res.detail and "header" in res.detail, res.detail


@pytest.mark.parametrize(
    "header",
    [
        ["Year", "Result", "Closing"],  # only Closing
        ["Year", "Opening", "Result"],  # only Opening
        ["Year", "Opening", "Result", "Deduction used"],  # no Closing
        ["Year", "Result", "Deduction used", "Closing"],  # no Opening
        ["Year", "Opening", "Closing amount", "Deduction used"],  # a near-name does not map
        ["Year", "Opening", "Result", "Deduction used", "Closing", "Closing"],  # one name twice
        ["Year", "Opening", "Result", "Deduction", "Deduction used", "Closing"],  # two synonyms of one field
    ],
)
def test_an_ambiguous_header_with_a_year_row_fails_closed(tmp_path, header):
    row = ["2024"] + ["4,000"] * (len(header) - 1)
    res = score(tmp_path, table(header, row))
    assert res.status == "fail" and "TY_2024" in res.detail and "header" in res.detail, res.detail


def test_an_ambiguous_header_without_a_year_row_is_fine(tmp_path):
    assert score(tmp_path, table(["Year", "Result", "Closing"], ["2023", "1", "2"])).status == "pass"


def test_a_year_row_without_a_header_and_separator_fails_closed(tmp_path):
    for text in (
        WRONG_2024 + "\n",
        "2024 | 5,000 | (1,000) | 3,000 | 3,000\n",
        "Year | Opening | Result | Deduction used | Closing\n2024 | 4,000 | (1,000) | 3,000 | 2,000\n",  # no separator row
        "Notes\n\n" + GOOD_2024 + "\n",
    ):
        res = score(tmp_path, text)
        assert res.status == "fail" and "TY_2024" in res.detail and "header" in res.detail, (text, res.detail)


# ------------------------------------------------------------------ T11 fix 1: bullets with pipes, reported / reconciled

LEDGER_TOP = HEADER + "| 2022 | 0 | -12,000 | 3,000 | 9,000 |\n| 2023 | 9,000 | +2,000 | 3,000 | 4,000 |\n\n### BLOCKING\n\n"
# The three Claude layouts of the 2026-11-03 run, reduced to their 2024/2025 lines.
CLAUDE_RUN0_2024 = (
    "- 2024: reported opening 5,000 (filed Sch D line 14) vs reconciled opening 4,000 (2023 closing). "
    "Reported closing 3,000 vs reconciled 2,000. Deduction (3,000) is unchanged either way, so 2024 tax is "
    "unaffected. Provisional reconciled row: 4,000 | -1,000 | 3,000 | 2,000.\n"
)
CLAUDE_RUN0_2025 = (
    "- 2025 (return not in workspace): reported/filed-chain opening 3,000 vs reconciled 2,000. Also the 1099-B "
    "shows 0 basis on RSU shares; W-2 evidence supports 15,000 basis. Provisional reconciled row: 2,000 | +500 | 1,500 | 0.\n"
)
CLAUDE_RUN1_2024 = (
    "- **2024**: Filed Sch D line 14 opening = 5,000 vs reconciled opening = 4,000 (2023 closing). Filed carryover "
    "out = 3,000 vs reconciled 2,000. Unexplained. 2024 tax is unaffected (3,000 deduction allowed either way). "
    "Reconciled-if-confirmed row would be: 2024 | 4,000 | -1,000 | 3,000 | 2,000. Not entered until the user "
    "confirms the 2023 carryover and the source of the 5,000 (OPEN_QUESTIONS Q-CF-1).\n"
)
RR_HEADER = (
    "| Year | Opening reported / reconciled | Result | Deduction | Closing reported / reconciled |\n|---|---|---|---|---|\n"
)
CLAUDE_RUN2_2024 = "| 2024 | 5,000 / 4,000 | -1,000 | 3,000 | 3,000 / 2,000 |\n"
CLAUDE_RUN2_2025 = (
    "| 2025 (draft, not filed in workspace) | 3,000 / 2,000 | +500 (RSU basis 15,000 applied) vs +15,500 as "
    "reported on 1099-B | 1,500 | 0 / 0 |\n"
)
CLAUDE_RUN2 = RR_HEADER + CLAUDE_RUN2_2024 + CLAUDE_RUN2_2025


@pytest.mark.parametrize(
    "text",
    [
        LEDGER_TOP + CLAUDE_RUN0_2024 + CLAUDE_RUN0_2025,
        LEDGER_TOP + CLAUDE_RUN1_2024,
        LEDGER_TOP + "2024 and 2025 are left out.\n\n" + CLAUDE_RUN2,
        "Reconciled-if-confirmed row would be: 2024 | 4,000 | -1,000 | 3,000 | 2,000\n",
        "- Provisional reconciled 2024 row: 4,000 | 3,000 | 2,000\n",  # opening, deduction, closing
        "- 2024 | 4,000 | (1,000) | 3,000 | 2,000 |\n",
        "* **2024**: row: $4,000 | -1,000 | $3,000 | **2,000**; pending\n",
        "1. 2024: 4,000 | -1,000 | 3,000 | 2,000\n",
        "- 2024 | BLOCKING | see TY_2024/WORKING/RECONCILIATION_NOTES.md\n",  # a status bullet without amounts
        "- 2023: 9,000 | +2,000 | 3,000 | 4,000\n",  # another year's bullet
        table(["Year", "Opening (filed / reconciled)", "Deduction used", "Closing (as reported / reconciled)"], ["2024", "5,000 / 4,000", "3,000", "3,000 / 2,000"]),
    ],
)
def test_the_real_claude_hold_out_layouts_pass(tmp_path, text):
    for check in (CHECK_2024, CHECK_2025):
        res = score(tmp_path, text, check)
        assert res.status == "pass", (check["year"], res.detail)


@pytest.mark.parametrize(
    ("text", "check"),
    [
        (LEDGER_TOP + CLAUDE_RUN0_2024.replace("row: 4,000 | -1,000 | 3,000 | 2,000", "row: 5,000 | -1,000 | 3,000 | 3,000"), CHECK_2024),
        (LEDGER_TOP + CLAUDE_RUN0_2024.replace("| 2,000.", "| 3,000."), CHECK_2024),
        (LEDGER_TOP + CLAUDE_RUN0_2024.replace("4,000 | -1,000 | 3,000", "4,000 | -1,000 | 6,000"), CHECK_2024),
        (LEDGER_TOP + CLAUDE_RUN0_2025.replace("row: 2,000 | +500 | 1,500 | 0.", "row: 3,000 | +500 | 1,500 | 500."), CHECK_2025),
        (LEDGER_TOP + CLAUDE_RUN1_2024.replace("2024 | 4,000 | -1,000 | 3,000 | 2,000.", "2024 | 5,000 | -1,000 | 3,000 | 3,000."), CHECK_2024),
        ("Reconciled-if-confirmed row would be: 2024 | 5,000 | -1,000 | 3,000 | 3,000\n", CHECK_2024),
        (RR_HEADER + "| 2024 | 5,000 / 5,000 | -1,000 | 3,000 | 3,000 / 3,000 |\n", CHECK_2024),
        (RR_HEADER + "| 2024 | 4,000 / 5,000 | -1,000 | 3,000 | 2,000 / 3,000 |\n", CHECK_2024),  # the reconciled side is read
        (RR_HEADER + "| 2024 | 5,000 | -1,000 | 3,000 | 3,000 |\n", CHECK_2024),  # only the planted figures
        (RR_HEADER + "| 2024 | 4,000 | -1,000 | 3,000 | 2,000 |\n", CHECK_2024),  # one figure: which side is it?
        (RR_HEADER + "| 2024 | 5,000 / 4,000 / 4,500 | -1,000 | 3,000 | 3,000 / 2,000 |\n", CHECK_2024),
        (RR_HEADER + CLAUDE_RUN2_2025.replace("3,000 / 2,000", "3,000 / 3,000"), CHECK_2025),
        (RR_HEADER + CLAUDE_RUN2_2025.replace("0 / 0", "500 / 500"), CHECK_2025),
        ("- 2024: opening 5,000 | closing 3,000\n", CHECK_2024),
        ("- 2024 | 5,000 | (1,000) | 3,000 | 3,000\n", CHECK_2024),
        ("1. 2024: 5,000 | -1,000 | 3,000 | 3,000\n", CHECK_2024),
        ("- 2024: 4,000 | 3,000\n", CHECK_2024),  # two values: columns unknown
        ("- 2024: 4,000 | -1,000 | 3,000 | 2,000 | 9\n", CHECK_2024),  # five values
        ("- 2024: 4,000 | -1,000 | 3,000 | 2,000 (est)\n", CHECK_2024),
        ("- 2024: 4,000 | pending | 3,000 | 2,000\n", CHECK_2024),
        ("- 2024: 5000/3000/2000 | pending\n", CHECK_2024),
        ("Filed vs reconciled: 5,000 | 4,000 for 2024\n", CHECK_2024),
        # fail closed: no explicit year cell, and the line names 2024 as well as 2025
        ("- 2025 opening depends on the 2024 closing: 2,000 | +500 | 1,500 | 0\n", CHECK_2024),
    ],
)
def test_wrong_figures_in_the_hold_out_layouts_fail(tmp_path, text, check):
    res = score(tmp_path, text, check)
    assert res.status == "fail" and f"TY_{check['year']}" in res.detail, res.detail


@pytest.mark.parametrize(
    "text",
    [
        "- 2025: depends on 2024. Row: 2025 | 2,000 | +500 | 1,500 | 0\n",
        "- 2024 notes: row: 2023 | 9,000 | +2,000 | 3,000 | 4,000\n",
    ],
)
def test_an_explicit_year_cell_names_the_rows_year(tmp_path, text):
    assert score(tmp_path, text, CHECK_2024).status == "pass"


def test_the_agent_run_ledgers_keep_their_verdicts(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parent / "fixtures" / "agent_runs"
    for run in ("carryforward-run0", "carryforward-run1"):
        text = (root / run / "04_LONG_TERM_RECORDS" / "CARRYFORWARD_LEDGER.md").read_text()
        for check in (CHECK_2024, CHECK_2025):
            assert score(tmp_path, text, check).status == "pass", run


# ------------------------------------------------------------------ HTML (ruling R26)


def html_table(*rows, tag="table", head=("Year", "Opening", "Result", "Deduction used", "Closing")):
    cells = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<{tag}>\n<thead><tr>{cells}</tr></thead>\n<tbody>{body}</tbody>\n</{tag}>\n"


WRONG_ROW = ["2024", "5,000", "(1,000)", "3,000", "3,000"]
RIGHT_ROW = ["2024", "4,000", "(1,000)", "3,000", "2,000"]
UNSUPPORTED = "unsupported layout: HTML table with a 2024/2025 row"


def test_an_html_table_with_a_wrong_2024_row_fails_closed(tmp_path):
    res = score(tmp_path, "# Ledger\n\n" + html_table(["2022", "0", "(12,000)", "3,000", "9,000"], WRONG_ROW))
    assert res.status == "fail" and UNSUPPORTED in res.detail and "TY_2024" in res.detail, res.detail


def test_html_tables_are_not_parsed_so_even_a_correct_row_fails_and_nothing_rescues_it(tmp_path):
    assert score(tmp_path, html_table(RIGHT_ROW)).status == "fail"
    assert score(tmp_path, HEADER + GOOD_2024 + "\n\n" + html_table(WRONG_ROW)).status == "fail"
    assert score(tmp_path, html_table(WRONG_ROW) + "\nCorrect closing carryover: 2,000.\n").status == "fail"


@pytest.mark.parametrize(
    "text",
    [
        html_table(WRONG_ROW, tag="TABLE"),
        '<table class="ledger" border=1>\n<tr><td>2024</td><td>5,000</td></tr>\n</table>\n',
        "<table><tr><td>20<b></b>24</td><td>5,000</td></tr></table>",  # a tag splits the year
        "<table><tr><td>20&#50;4</td><td>5,000</td></tr></table>",  # decimal entity
        "<table><tr><td>&#x32;024</td><td>5,000</td></tr></table>",  # hex entity
        "<table><tr><td>20&amp;#50;4</td><td>5,000</td></tr></table>",  # double-encoded
        "<table><tr><td>Tax year <i>2024</i></td></tr></table>",
        "<table><tr><td>2024</td><td>5,000</td></tr>",  # never closed
        "<table\n  id=x><tr><td>2024</td></tr></table>",
    ],
)
def test_every_html_table_form_with_the_year_fails_closed(tmp_path, text):
    res = score(tmp_path, text)
    assert res.status == "fail" and UNSUPPORTED in res.detail, (text, res.detail)


def test_the_html_failure_is_per_year(tmp_path):
    table_2025 = html_table(["2025", "2,000", "+500", "1,500", "500"])
    res = score(tmp_path, table_2025, CHECK_2025)
    assert res.status == "fail" and UNSUPPORTED in res.detail and "TY_2025" in res.detail
    assert score(tmp_path, table_2025, CHECK_2024).status == "pass"


def test_an_html_table_with_only_earlier_years_has_no_effect(tmp_path):
    older = html_table(["2022", "0", "(12,000)", "3,000", "9,000"], ["2023", "9,000", "+2,000", "3,000", "4,000"])
    assert score(tmp_path, older).status == "pass"
    assert score(tmp_path, older, CHECK_2025).status == "pass"
    assert score(tmp_path, older + "\n" + HEADER + GOOD_2024 + "\n").status == "pass"
    assert score(tmp_path, older + "\n" + HEADER + WRONG_2024 + "\n").status == "fail"
    assert score(tmp_path, "<tablet>2024</tablet> and <tabled>2024</tabled>\n").status == "pass"  # not table elements


@pytest.mark.parametrize("cell", ["20&#50;4", "&#50;024", "2&#48;24", "&#x32;&#x30;24", "&#50;&#48;&#50;&#52;", "TY_20&#50;4"])
def test_an_entity_encoded_year_cell_in_markdown_is_recognized(tmp_path, cell):
    res = score(tmp_path, HEADER + f"| {cell} | 5,000 | (1,000) | 3,000 | 3,000 |\n")
    assert res.status == "fail" and "opening" in res.detail, (cell, res.detail)
    assert score(tmp_path, HEADER + f"| {cell} | 4,000 | (1,000) | 3,000 | 2,000 |\n").status == "pass"


def test_entities_in_value_cells_and_headers_are_decoded(tmp_path):
    assert score(tmp_path, HEADER + "| 2024 | 4,&#48;00 | (1,000) | 3,000 | &#50;,000 |\n").status == "pass"
    assert score(tmp_path, HEADER + "| 2024 | 4,000 | (1,000) | 3,000 | &#51;,000 |\n").status == "fail"
    header = "| Year | Open&#105;ng | Result | Deduction&nbsp;used | Clos&#105;ng |\n|---|---|---|---|---|\n"
    assert score(tmp_path, header + GOOD_2024 + "\n").status == "pass"
    assert score(tmp_path, header + WRONG_2024 + "\n").status == "fail"


@pytest.mark.parametrize("cell", ["202&#51;", "2023&#50;", "2023 (see note&#50;)", "&#50;023", "20&#50;3"])
def test_entities_in_a_2023_row_do_not_change_2024_or_2025_handling(tmp_path, cell):
    text = HEADER + f"| {cell} | 9,000 | +2,000 | 3,000 | 4,000 |\n" + GOOD_2024 + "\n" + GOOD_2025 + "\n"
    assert score(tmp_path, text).status == "pass"
    assert score(tmp_path, text, CHECK_2025).status == "pass"
    wrong = HEADER + f"| {cell} | 1 | 2 | 3 | 4 |\n"  # nothing in a 2023 row is read as a 2024 or 2025 row
    assert score(tmp_path, wrong).status == "pass" and score(tmp_path, wrong, CHECK_2025).status == "pass"


def test_correct_markdown_still_passes_alongside_unrelated_html(tmp_path):
    text = "<p>Ledger</p>\n<br>\n" + HEADER + GOOD_2024 + "\n" + GOOD_2025 + "\n"
    assert score(tmp_path, text).status == "pass" and score(tmp_path, text, CHECK_2025).status == "pass"


def test_the_check_reads_every_file_the_glob_matches(tmp_path):
    (tmp_path / "a.md").write_text(HEADER + GOOD_2024 + "\n")
    (tmp_path / "b.md").write_text(HEADER + WRONG_2024 + "\n")
    ctx = CheckContext(tmp_path, {}, {}, [], "", KEY)
    check = {**CHECK_2024, "file": "*.md"}
    assert run_checks([check], ctx)[0].status == "fail"


@pytest.mark.parametrize(
    "check",
    [
        {"type": "ledger_rows_if_present", "year": "2024", "key": "carryforward.TY_2024"},
        {"type": "ledger_rows_if_present", "file": "ledger.md", "key": "carryforward.TY_2024"},
        {"type": "ledger_rows_if_present", "file": "ledger.md", "year": "2024"},
        {"type": "ledger_rows_if_present", "file": "ledger.md", "year": "24", "key": "carryforward.TY_2024"},
        {"type": "ledger_rows_if_present", "file": "ledger.md", "year": "2024", "key": "carryforward.TY_2099"},
        {"type": "ledger_rows_if_present", "file": "../ledger.md", "year": "2024", "key": "carryforward.TY_2024"},
    ],
)
def test_bad_parameters_are_errors_not_passes(tmp_path, check):
    assert score(tmp_path, HEADER + GOOD_2024 + "\n", check).status == "error"


def test_an_answer_key_entry_without_the_three_amounts_is_an_error(tmp_path):
    key = {"carryforward": {"TY_2024": {"opening": 4000, "closing": 2000}}}
    assert score(tmp_path, HEADER + GOOD_2024 + "\n", key=key).status == "error"
