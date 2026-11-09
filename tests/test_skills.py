import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "agent" / "skills"
ARCHITECTURE = ROOT / "docs" / "architecture.md"

EXPECTED_SKILLS = {
    "tax-strategy-discovery",
    "tax-law-research",
    "tax-document-inventory",
    "tax-amendment-window",
    "tax-prior-year-review",
    "tax-filing-workflow",
    "tax-annual-rollover",
    "tax-session-close",
}
NON_SKILL_DESTINATIONS = {"core", "docs-only"}

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|")
TEMPLATE_REF = re.compile(r"templates/([A-Za-z0-9_.\-]+\.md)")

LEDGER_HEADER = "| Year | Opening | Current-year result | Deduction used | Closing |"


def skill_text(skill_id: str) -> str:
    return (SKILLS_DIR / skill_id / "SKILL.md").read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "missing frontmatter"
    end = text.index("\n---\n", 4)
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        assert sep, f"bad frontmatter line: {line!r}"
        fields[key.strip()] = value.strip()
    return fields


def routed_sections() -> dict[int, list[str]]:
    rows: dict[int, list[str]] = {}
    for line in ARCHITECTURE.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            rows[int(match.group(1))] = [d.strip() for d in match.group(2).split(",")]
    return rows


def test_exactly_the_eight_skills_exist():
    found = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    assert found == EXPECTED_SKILLS
    for skill_id in EXPECTED_SKILLS:
        assert (SKILLS_DIR / skill_id / "SKILL.md").is_file(), skill_id


def test_frontmatter_valid():
    for skill_id in EXPECTED_SKILLS:
        fields = parse_frontmatter(skill_text(skill_id))
        assert fields.get("name") == skill_id, skill_id
        assert KEBAB.match(fields["name"]), skill_id
        description = fields.get("description", "")
        assert 1 <= len(description) <= 1024, f"{skill_id}: {len(description)} chars"


def test_referenced_templates_exist():
    required = {
        "tax-session-close": {"CURRENT_STATE.md"},
        "tax-law-research": {"SOURCE_LEDGER_ENTRY.md", "TAX_POSITION_MEMO.md"},
        "tax-strategy-discovery": {
            "STRATEGY_AGREEMENT_SUMMARY.md",
            "STRATEGY_MASTER.md",
            "STRATEGY_ONE_PAGER.md",
            "STRATEGY_IMPACT_NOTE.md",
        },
        "tax-amendment-window": {"AMENDMENT_WINDOW.md"},
    }
    for skill_id, names in required.items():
        referenced = set(TEMPLATE_REF.findall(skill_text(skill_id)))
        assert names <= referenced, f"{skill_id} does not reference {names - referenced}"
    for skill_id in EXPECTED_SKILLS:
        for name in set(TEMPLATE_REF.findall(skill_text(skill_id))):
            path = SKILLS_DIR / skill_id / "templates" / name
            assert path.is_file(), f"{skill_id}: missing template {name}"


def test_routed_sections_present():
    checked = 0
    for number, destinations in routed_sections().items():
        for destination in destinations:
            if destination in NON_SKILL_DESTINATIONS:
                continue
            assert destination in EXPECTED_SKILLS, f"section {number}: {destination!r}"
            marker = re.compile(rf"Manual §{number}(?!\d)")
            assert marker.search(skill_text(destination)), (
                f"{destination} lacks 'Manual §{number}'"
            )
            checked += 1
    assert checked >= 20


def test_amendment_skill_rules():
    text = skill_text("tax-amendment-window")
    for phrase in ("Never assume", "§6511", "§6513", "§7503", "D.C."):
        assert phrase in text, phrase


def test_amendment_template_fields():
    template = (
        SKILLS_DIR / "tax-amendment-window" / "templates" / "AMENDMENT_WINDOW.md"
    ).read_text(encoding="utf-8")
    for field in (
        "Actual filing date",
        "Deemed filing date",
        "Deemed payment date(s)",
        "Unadjusted deadline",
        "Holiday/weekend adjustment",
        "Refund-claim deadline",
        "Lookback conclusion",
        "Authority",
        "Confidence",
    ):
        assert field in template, field


def test_amendment_template_has_actual_and_deemed_payment_fields():
    template = (
        SKILLS_DIR / "tax-amendment-window" / "templates" / "AMENDMENT_WINDOW.md"
    ).read_text(encoding="utf-8")
    lines = template.splitlines()
    labels = (
        "Actual payment date(s):",
        "Payment type(s):",
        "Payment amount(s):",
        "Payment evidence path(s):",
        "Deemed payment date(s):",
    )
    positions = []
    for label in labels:
        matches = [i for i, line in enumerate(lines) if line.startswith(label)]
        assert len(matches) == 1, f"{label!r} must be exactly one field line"
        positions.append(matches[0])
    # Actual payment facts come before the deemed dates derived from them.
    assert positions == sorted(positions)


def test_filing_workflow_keeps_brokerage_scope():
    text = skill_text("tax-filing-workflow")
    assert "Never treat proceeds or withdrawals as income" not in text
    assert "gross brokerage withdrawals or sale proceeds" in text
    assert "retirement distributions" in text
    assert "own researched tax treatment" in text


def test_ledger_format_defined():
    assert LEDGER_HEADER in skill_text("tax-prior-year-review")


def test_ledger_sign_convention_and_example():
    text = skill_text("tax-prior-year-review")
    for phrase in (
        "before any carryover",
        "absorbed against ordinary income",
        "carryover out",
        "gains first absorb the opening carryover",
        "RECONCILIATION_NOTES.md",
        "not as hidden changes",
    ):
        assert phrase.lower() in text.lower(), phrase
    assert "plus Current-year result minus Deduction used" not in text
    example = re.search(
        r"^\|\s*Example\s*\|\s*([\d,]+)\s*\|\s*([+-]?[\d,]+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|$",
        text,
        flags=re.MULTILINE,
    )
    assert example, "worked example row missing"
    opening, result, used, closing = (int(g.replace(",", "")) for g in example.groups())
    # Gains absorb the opening carryover first; only a remaining net loss is deducted.
    assert closing == max(0, opening - result) - used
    # For this example the deduction is the lesser of the annual limit and the net loss.
    assert used == min(3000, opening - result)
    assert "assumed limit" not in text
    assert "$1,500 MFS" in text
    assert "Capital Loss Carryover Worksheet" in text
    assert "verify the limit for the specific year and filing status" in text


def test_deduction_used_is_the_1212b2_worksheet_line_4_amount():
    text = skill_text("tax-prior-year-review")
    assert "loss actually deducted" not in text
    assert "§1212(b)(2)" in text
    assert "Capital Loss Carryover Worksheet line 4" in text
    assert "not the Schedule D line 21 deduction" in text
    assert "taxable income before the capital-loss deduction is below" in text


def test_low_taxable_income_does_not_shrink_the_annual_limit():
    text = skill_text("tax-prior-year-review")
    assert "can be smaller when the taxable-income adjustment" not in text
    assert "does not shrink the $3,000 ($1,500 MFS) limit" in text
    assert "more carries forward" in text


def test_gain_absorption_and_deduction_used_are_not_double_counted():
    text = skill_text("tax-prior-year-review")
    assert "Utilization is counted once, in Deduction used." not in text
    assert "current-year gains is reflected in the max(…) term" in text
    assert "recorded once, in Deduction used" in text


def test_capital_loss_character_is_a_note_not_extra_columns():
    text = skill_text("tax-prior-year-review")
    headers = [line for line in text.splitlines() if line.startswith("| Year |")]
    assert headers and all(line == LEDGER_HEADER for line in headers)
    for phrase in (
        "§1212(b)(1)",
        "lines 8 and 13",
        "note line under the ledger table",
        "not as extra table columns",
        "must sum to Closing",
        "Schedule D lines 6 and 14",
    ):
        assert phrase in text, phrase


def test_mismatch_stays_blocking_while_a_question_is_open():
    text = skill_text("tax-prior-year-review")
    assert "no question about it remains open in `00_CONTROL/OPEN_QUESTIONS.md`" in text
    assert "doesn't clear it" in text
    # Only the user can waive a mismatch as immaterial; the agent can't clear it that way.
    assert "the user has marked it immaterial" in text
    assert "until it is explained or explicitly marked immaterial" not in text


def test_unfinished_years_carry_no_ledger_amounts():
    text = skill_text("tax-prior-year-review")
    for phrase in (
        "gets no amounts in that ledger",
        "doesn't hold back another ledger's amounts",
        # Scoped per ledger: an open carryforward item must not freeze an unrelated home basis.
        "a `BLOCKING` item about that ledger's figures is still open",
        "by year and status only",
        "and the year is final (see Long-term ledgers)",
    ):
        assert phrase in text, phrase
    status_line = re.search(r"^\s*- 2025: pending.*$", text, flags=re.MULTILINE)
    assert status_line, "example status line missing"
    # The example lists the year by status only: no amount besides the year itself (paths aside).
    prose = re.sub(r"`[^`]*`", "", status_line.group(0))
    assert re.findall(r"\d[\d,]*", prose) == ["2025"]


def test_election_rule_keeps_default_and_lists_amendable_examples():
    text = skill_text("tax-prior-year-review")
    assert "`Not amendable / election closed` unless authority says otherwise" in text
    for phrase in ("§63(e)(3)", "§6013(b)", "Reg. 1.6013-1(a)(1)", "Reg. 1.901-1(d)", "§179", "Reg. 301.9100-2"):
        assert phrase in text, phrase
    assert "verify per authority" in text


def test_withholding_deemed_paid_date_is_calendar_fixed():
    text = skill_text("tax-amendment-window")
    assert "treated as paid on the original due date" not in text
    assert "15th day of the fourth month following the close of the tax year" in text
    assert "without regard to any extension" in text
    assert "not moved by a weekend, holiday or postponement rule" in text


def test_special_periods_name_the_common_provisions():
    text = skill_text("tax-amendment-window")
    for phrase in (
        "§6511(c)",
        "§6511(d)(1)",
        "§6511(d)(2)",
        "§6511(d)(3)",
        "§6511(d)(4)",
        "§6511(h)",
        "§7508A(d)",
        "§7508A(e)",
    ):
        assert phrase in text, phrase


def test_amendment_template_payment_note_counts_five_fields():
    template = (
        SKILLS_DIR / "tax-amendment-window" / "templates" / "AMENDMENT_WINDOW.md"
    ).read_text(encoding="utf-8")
    assert "four lines" not in template
    assert "repeat all five fields" in template


def test_exact_paths_present():
    expectations = {
        "tax-prior-year-review": (
            "04_LONG_TERM_RECORDS/CARRYFORWARD_LEDGER.md",
            "03_TAX_YEARS/TY_YYYY/WORKING/RECONCILIATION_NOTES.md",
            "reported X vs reconciled Y",
        ),
        "tax-filing-workflow": ("04_LONG_TERM_RECORDS/HOME_BASIS_LEDGER.md",),
        "tax-amendment-window": ("03_TAX_YEARS/TY_YYYY/WORKING/AMENDMENT_WINDOW.md",),
    }
    for skill_id, needles in expectations.items():
        text = skill_text(skill_id)
        for needle in needles:
            assert needle in text, f"{skill_id}: {needle}"


def test_session_close_template_is_current_state():
    template = (
        SKILLS_DIR / "tax-session-close" / "templates" / "CURRENT_STATE.md"
    ).read_text(encoding="utf-8")
    for heading in (
        "# Current State",
        "## Engagement Stage",
        "## Tax Years Present",
        "## Amendment Status",
        "## Key Carryforwards / Basis",
        "## Next Exact Actions",
    ):
        assert heading in template, heading


def test_skills_have_no_home_directory_paths():
    for path in SKILLS_DIR.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text, path
        assert "/home/" not in text, path


def test_skills_respect_write_boundaries():
    # The user places evidence in source folders; skills must say so.
    for skill_id in ("tax-document-inventory", "tax-filing-workflow", "tax-annual-rollover"):
        text = skill_text(skill_id)
        assert "ask the user" in text.lower(), skill_id
        assert "blocked write" in text.lower(), skill_id
