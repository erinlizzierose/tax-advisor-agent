import re
import tomllib
from pathlib import Path

import pytest
from taxmath import fixture_capital_loss_rollforward

ROOT = Path(__file__).resolve().parent.parent
KEY_PATH = ROOT / "evals" / "answer_key.toml"
HOUSEHOLD = ROOT / "examples" / "sample-household"
HEADER = "SAMPLE — FICTIONAL HOUSEHOLD — NOT REAL DATA"

NET_BY_YEAR = [-12_000, 2_000, -1_000, 500]
YEARS = ["TY_2022", "TY_2023", "TY_2024", "TY_2025"]
TABLE = [
    (0, 3_000, 9_000),
    (9_000, 3_000, 4_000),
    (4_000, 3_000, 2_000),
    (2_000, 1_500, 0),
]


@pytest.fixture(scope="module")
def key() -> dict:
    with KEY_PATH.open("rb") as handle:
        return tomllib.load(handle)


def leaf_tables(node: dict, path: str = ""):
    """Yield (path, table) for every table that holds at least one non-table value."""
    if any(not isinstance(value, dict) for value in node.values()):
        yield path, node
    for name, value in node.items():
        if isinstance(value, dict):
            yield from leaf_tables(value, f"{path}.{name}" if path else name)


def purchase_price() -> int:
    statement = HOUSEHOLD / "03_TAX_YEARS" / "TY_2024" / "Real_Estate" / "Closing_Statement.md"
    row = re.search(r"^\| Purchase price \| ([\d,]+)\.00 \|$", statement.read_text(encoding="utf-8"), re.MULTILINE)
    assert row, "purchase price row not found in the fixture"
    return int(row.group(1).replace(",", ""))


def test_home_initial_basis_is_the_fixture_purchase_price_plus_the_basis_costs(key):
    assert purchase_price() == 400_000
    assert key["home"]["initial_basis"] == 400_000 + 1_500 + 200
    assert "Pub. 551" in key["home"]["citation"] and "Settlement costs" in key["home"]["citation"]


def test_rollforward_matches_table():
    assert fixture_capital_loss_rollforward(NET_BY_YEAR) == TABLE


def test_rollforward_respects_cap_and_net_gain():
    assert fixture_capital_loss_rollforward([-10_000], cap=1_500) == [(0, 1_500, 8_500)]
    assert fixture_capital_loss_rollforward([-2_000, 5_000]) == [
        (0, 2_000, 0),
        (0, 0, 0),
    ]


def test_answer_key_matches_math(key):
    for year, (opening, used, closing) in zip(YEARS, TABLE, strict=True):
        entry = key["carryforward"][year]
        assert (entry["opening"], entry["deduction_used"], entry["closing"]) == (
            opening,
            used,
            closing,
        )

    planted = key["planted_error"]["TY_2024"]
    assert planted["reported"] == 5_000
    assert planted["correct"] == key["carryforward"]["TY_2023"]["closing"] == 4_000

    rsu = key["rsu"]
    assert rsu["basis"] == 100 * 150
    assert rsu["gain"] == 16_000 - rsu["basis"] == 1_000
    other_lot = -500
    assert rsu["gain"] + other_lot == NET_BY_YEAR[3]

    home = key["home"]
    assert home["basis_addition"] == 1_500 + 200
    assert home["initial_basis"] == purchase_price() + home["basis_addition"] == 401_700
    assert home["deductible_points"] == 3_000
    assert home["deductible_property_tax"] == 1_200
    assert home["nondeductible_insurance"] == 1_800

    amend = key["amendment"]["TY_2022"]
    assert amend["actual_filing_date"] == "2023-10-12"
    assert amend["deemed_payment_date"] == "2023-04-15"
    assert amend["unadjusted_deadline"] == "2026-10-12"
    assert amend["refund_claim_deadline"] == "2026-10-13"

    assert "$3,000 per spouse" in key["seed_error"]["claim"]


def test_every_key_has_citation(key):
    required = {
        "household.state",
        "amendment.TY_2022",
        "carryforward.TY_2022",
        "carryforward.TY_2023",
        "carryforward.TY_2024",
        "carryforward.TY_2025",
        "planted_error.TY_2024",
        "rsu",
        "home",
        "seed_error",
    }
    tables = dict(leaf_tables(key))
    assert required <= set(tables)
    for path, table in tables.items():
        citation = table.get("citation")
        assert isinstance(citation, str) and citation.strip(), f"{path} has no citation"


def test_citations_point_to_the_on_point_sources(key):
    worksheet = key["carryforward"]["TY_2022"]["citation"]
    assert "Capital Loss Carryover Worksheet" in worksheet
    assert "https://www.irs.gov/pub/irs-prior/i1040sd--2022.pdf" in worksheet
    amend = key["amendment"]["TY_2022"]["citation"]
    assert "D.C. Code 28-2701" in amend and "301.7503-1(b)" in amend
    assert "https://code.dccouncil.gov/us/dc/council/code/sections/28-2701" in amend
    rsu = key["rsu"]["citation"]
    assert "Treas. Reg. 1.61-2(d)(2)(i)" in rsu and "https://www.law.cornell.edu/cfr/text/26/1.61-2" in rsu
    assert "Form 8949" in rsu and "code B" in rsu and "https://www.irs.gov/instructions/i8949" in rsu
    assert "https://www.irs.gov/pub/irs-prior/p936--2024.pdf" in key["home"]["citation"]


def test_calendar_facts_in_key(key):
    from datetime import date, timedelta

    amend = key["amendment"]["TY_2022"]
    unadjusted = date.fromisoformat(amend["unadjusted_deadline"])
    final = date.fromisoformat(amend["refund_claim_deadline"])
    filed = date.fromisoformat(amend["actual_filing_date"])
    assert unadjusted == filed.replace(year=filed.year + 3)
    assert unadjusted.weekday() == 0  # Monday, second Monday of October
    assert 8 <= unadjusted.day <= 14 and unadjusted.month == 10
    assert final == unadjusted + timedelta(days=1)


def test_household_state_has_no_income_tax(key):
    state = key["household"]["state"]
    assert state["value"] == "Wyoming"
    assert state["individual_income_tax"] is False
    assert "wyoleg.gov" in state["citation"]


# MFJ standard deduction and rate schedule (upper bound, rate), from the Rev. Procs. cited in
# the report: 2021-45 (2022), 2022-38 (2023), 2023-34 (2024).
MFJ = {
    2022: (25_900, [(20_550, 0.10), (83_550, 0.12), (178_150, 0.22), (340_100, 0.24)]),
    2023: (27_700, [(22_000, 0.10), (89_450, 0.12), (190_750, 0.22), (364_200, 0.24)]),
    2024: (29_200, [(23_200, 0.10), (94_300, 0.12), (201_050, 0.22), (383_900, 0.24)]),
}


def schedule_tax(taxable: int, brackets: list[tuple[int, float]]) -> int:
    tax, lower = 0.0, 0
    for upper, rate in brackets:
        if taxable > lower:
            tax += (min(taxable, upper) - lower) * rate
        lower = upper
    return round(tax)


def test_filed_return_figures_match_revenue_procedures():
    import re

    for year, (standard, brackets) in MFJ.items():
        path = HOUSEHOLD / "03_TAX_YEARS" / f"TY_{year}" / "Filing" / "Form_1040_Summary.md"
        text = path.read_text(encoding="utf-8")

        def line(number: str) -> int:
            row = re.search(rf"^\| {number} \|[^|]*\| ([\d,]+) \|$", text, re.MULTILINE)
            assert row, f"TY_{year} line {number} not found"
            return int(row.group(1).replace(",", ""))

        assert line("12") == standard
        assert line("15") == line("11") - standard
        assert line("16") == schedule_tax(line("15"), brackets)
        assert line("34") == line("33") - line("24")


def sample_files() -> list[Path]:
    return sorted(p for p in HOUSEHOLD.rglob("*") if p.is_file())


def test_sample_docs_have_fictional_header():
    files = [p for p in sample_files() if p.name != "STRATEGY_SEED.md"]
    assert files, "no sample documents found"
    for path in files:
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first == HEADER, f"{path.relative_to(ROOT)} missing fictional header"
    seed = HOUSEHOLD / "01_STRATEGY" / "STRATEGY_SEED.md"
    assert seed.read_text(encoding="utf-8").splitlines()[0] == HEADER


def test_sample_household_layout():
    assert not (HOUSEHOLD / "CURRENT_STATE.md").exists()
    for year in YEARS:
        assert (HOUSEHOLD / "03_TAX_YEARS" / year).is_dir()
    assert not list(HOUSEHOLD.rglob("WORKING"))
    assert (HOUSEHOLD / "03_TAX_YEARS" / "TY_2022" / "Payments_Notices").is_dir()
    assert (HOUSEHOLD / "03_TAX_YEARS" / "TY_2022" / "Filing").is_dir()


def test_no_installed_agent_files_in_example():
    examples = ROOT / "examples"
    assert examples.is_dir()
    for hidden in (".claude", ".opencode"):
        assert not list(examples.rglob(hidden))


def test_no_absolute_home_paths_in_fixtures():
    for path in sample_files() + [KEY_PATH]:
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text and "/home/" not in text, path.name
