import re
from pathlib import Path

import pytest
from privacy_check import DEFAULT_DENYLIST, load_denylist

DOCS = Path(__file__).resolve().parent.parent / "docs"
DENYLIST_PATH = DEFAULT_DENYLIST.expanduser()

SKILLS = {
    "tax-strategy-discovery",
    "tax-law-research",
    "tax-document-inventory",
    "tax-amendment-window",
    "tax-prior-year-review",
    "tax-filing-workflow",
    "tax-annual-rollover",
    "tax-session-close",
}
VALID_DESTINATIONS = SKILLS | {"core", "docs-only"}

ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|")


def routing_table() -> dict[int, list[str]]:
    rows: dict[int, list[str]] = {}
    for line in (DOCS / "architecture.md").read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            number = int(match.group(1))
            assert number not in rows, f"section {number} routed twice"
            rows[number] = [item.strip() for item in match.group(2).split(",")]
    return rows


def test_every_manual_section_is_routed():
    rows = routing_table()
    assert set(rows) == set(range(1, 36))
    for number, destinations in rows.items():
        assert destinations, f"section {number} has no destination"
        for destination in destinations:
            assert destination in VALID_DESTINATIONS, f"section {number}: {destination!r}"


def test_docs_only_is_not_mixed_with_other_destinations():
    for number, destinations in routing_table().items():
        if "docs-only" in destinations:
            assert destinations == ["docs-only"], f"section {number}"


def test_every_skill_is_used_by_some_section():
    used = {d for destinations in routing_table().values() for d in destinations}
    assert SKILLS <= used


def test_manual_has_all_numbered_sections():
    manual = (DOCS / "operating-manual.md").read_text(encoding="utf-8")
    numbers = [int(n) for n in re.findall(r"^## (\d+)\. ", manual, flags=re.MULTILINE)]
    assert numbers == list(range(1, 36))


def test_manual_is_sanitized():
    manual = (DOCS / "operating-manual.md").read_text(encoding="utf-8")
    assert "foreign retirement accounts, foreign brokerage holdings" in manual
    assert "STRATEGY_ONE_PAGER" in manual
    assert manual.startswith("Sanitized public version of the manual the agent was built from.")


@pytest.mark.skipif(
    not DENYLIST_PATH.is_file(),
    reason=f"owner denylist not found at {DENYLIST_PATH}; cannot check the manual against it",
)
def test_manual_contains_no_denylist_term():
    lowered = (DOCS / "operating-manual.md").read_text(encoding="utf-8").lower()
    hits = [n for n, term in enumerate(load_denylist(DENYLIST_PATH), start=1) if term.lower() in lowered]
    # Report positions only: the matched terms are personal and this repo is public.
    assert not hits, f"operating-manual.md matches denylist entries at positions {hits}"


def architecture_text() -> str:
    return (DOCS / "architecture.md").read_text(encoding="utf-8")


def test_architecture_states_exact_protected_path_boundary():
    text = architecture_text()
    for phrase in (
        "01_STRATEGY/STRATEGY_SEED.md",
        "03_TAX_YEARS/TY_<4 digits>/WORKING/**",
        "TY_2024/W2s/WORKING/",
        "`.claude/**` and `.opencode/**`",
        "outside the workspace root",
        "external_directory",
        "TY_????",
        "TY_ABCD/WORKING/",
        "approximates the digit rule",
    ):
        assert phrase in text, phrase


def test_architecture_states_mcp_denied_in_both_tools():
    text = architecture_text()
    for phrase in (
        "MCP access is denied in both tools",
        "starts from deny-all",
        "`<server>_<tool>`",
        "`execute` (Code Mode)",
        "`mcp__*` is denied in `settings.json`",
        "explicit `tools` allowlist",
    ):
        assert phrase in text, phrase


def test_architecture_describes_the_write_allow_list():
    """R27: the doc states the allow-list both tools enforce, not the old deny-list."""
    text = " ".join(architecture_text().split())
    for phrase in (
        "allow-list",
        "`CURRENT_STATE.md`",
        "`00_CONTROL/**`",
        "`01_STRATEGY/**` except `01_STRATEGY/STRATEGY_SEED.md`",
        "`02_RESEARCH/**`",
        "`04_LONG_TERM_RECORDS/**`",
        "strictly below",
        "`opencode.json`",
        "`AGENTS.md`",
        "`.git/`",
        "non-ASCII",
        "01_STRATEGY/????????_????.??",
        "Edit|Write|NotebookEdit|Read|Glob|Grep",
    ):
        assert phrase in text, phrase
    assert "Web fetch and web search are allowed, because law research needs them." not in text


def test_architecture_says_opencode_cannot_search_and_claude_can():
    text = " ".join(architecture_text().split())
    assert "web search stays open" not in text
    assert "OpenCode web search is denied by the agent's permissions" in text
    assert "Claude Code keeps its built-in WebSearch" in text
    assert "the two tools differ" in text
    assert "`https://<host>`" in text


def test_architecture_has_known_limitations():
    parts = re.split(r"^## ", architecture_text(), flags=re.MULTILINE)
    found = [p for p in parts if p.startswith("Known limitations")]
    assert len(found) == 1
    text = " ".join(found[0].split())
    for phrase in (
        "instruction-only",
        "OpenCode web search is denied",
        "Claude Code web search is unrestricted",
        "search query",
        "fetch-only",
        "2026-11-03",
        "Web search cancelled",
        "WebFetch(domain:*.gov)",
        "state tax sites",
        "TY_????",
        "hard link",
        "check-then-write",
        "2.1.295",
    ):
        assert phrase in text, phrase


def test_docs_contain_no_home_directory_paths():
    for name in ("operating-manual.md", "architecture.md"):
        text = (DOCS / name).read_text(encoding="utf-8")
        assert "/Users/" not in text, name
        assert "/home/" not in text, name
