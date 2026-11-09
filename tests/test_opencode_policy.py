"""Evaluate the rendered OpenCode permission rules with the documented V2 semantics.

OpenCode V2 (opencode.ai/v2/docs/permissions): rules are ordered and the last match wins;
`*` matches zero or more characters including `/`, `?` exactly one character, anything
else is literal; the pattern matches the whole normalized resource; matching is
case-sensitive except on Windows. `edit` resources are workspace-relative paths
(verified on 2.1.4); `webfetch` resources are the requested URL. No matching rule means
`ask`, which this agent never relies on (the first rule is a deny-all).

The path table is the guard's own PROTECTED and ALLOWED lists plus case variants and root
configs, so the two tools are checked against one policy. Where OpenCode's wildcard rules
cannot express the guard's exact policy, the difference is listed in DIVERGENCES with the
reason; every other path must get the guard's answer.
"""

import posixpath
import re

import pytest

from test_agent_core import opencode_rules
from test_guard_paths import ALLOWED, PROTECTED


def _regex(pattern: str) -> re.Pattern:
    out = []
    for ch in pattern:
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
    return re.compile("".join(out), re.DOTALL)


def decide(action: str, resource: str, rules=None) -> str:
    effect = "ask"
    for rule in rules if rules is not None else opencode_rules():
        if _regex(rule["action"]).fullmatch(action) and _regex(rule["resource"]).fullmatch(resource):
            effect = rule["effect"]
    return effect


def edit_effect(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    return decide("edit", normalized)


def test_evaluator_semantics():
    rules = [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": "a/*", "effect": "allow"},
        {"action": "edit", "resource": "a/?.md", "effect": "deny"},
    ]
    assert decide("edit", "a/b/c.md", rules) == "allow"  # `*` crosses `/`
    assert decide("edit", "a/x.md", rules) == "deny"  # last match wins
    assert decide("edit", "A/b.txt", rules) == "deny"  # case-sensitive
    assert decide("read", "a/b", rules) == "deny"
    assert decide("edit", "x", []) == "ask"


# Paths where OpenCode's answer differs from the guard's, accepted on purpose.
DIVERGENCES = {
    # `TY_????` matches any four characters, not only digits.
    "03_TAX_YEARS/TY_ABCD/WORKING/x.md": "allow",
    # Rule matching is lexical; a symlink a user placed inside an allowed folder is not
    # resolved by the rules (the agent has no shell, so it cannot create one itself).
    "00_CONTROL/link/w2.txt": "allow",
    # Non-ASCII names inside an allowed folder are writable in OpenCode; only the seed's
    # case-blind deny looks at name shape. Harmless: the folder itself is writable.
    "00_CONTROL/\u212aEY.md": "allow",
    # Fail-closed: OpenCode's allows are exact-case, so a case variant of a writable path
    # is denied (the guard allows it).
    "current_state.md": "deny",
    "00_control/FACT_REGISTER.md": "deny",
    "03_tax_years/ty_2024/working/x.md": "deny",
    # Fail-closed: the nested-WORKING deny also catches a WORKING folder below WORKING.
    "03_TAX_YEARS/TY_2024/WORKING/a/WORKING/b.md": "deny",
}

EXTRA_PROTECTED = [
    "01_STRATEGY/strategy_seed.md",
    "01_STRATEGY/Strategy_Seed.MD",
    "01_strategy/STRATEGY_SEED.md",
    "03_tax_years/TY_2024/W2s/w2.txt",
    "03_TAX_YEARS/ty_2024/W2s/w2.txt",
    ".opencode/agents/x.md",
    ".claude/hooks/guard_paths.py",
    ".git/hooks/pre-commit",
    "README.md",
    # `?` also matches `/`, so the `TY_????` allow alone would take this two-segment "year";
    # the `03_TAX_YEARS/*/*/WORKING/*` deny after it closes the gap.
    "03_TAX_YEARS/TY_a/bc/WORKING/x.md",
    "03_TAX_YEARS/TY_a/bc/WORKING/sub/x.md",
    "03_TAX_YEARS/TY_2024/Filing/WORKING/x.md",
]

EXTRA_ALLOWED = [
    "03_TAX_YEARS/TY_2024/WORKING/x.md",
    "03_TAX_YEARS/TY_2025/WORKING/x",
    "03_TAX_YEARS/TY_2024/WORKING/a/WORKING/b.md",
    "03_TAX_YEARS/TY_2025/WORKING/RECONCILIATION_NOTES.md",
    "01_STRATEGY/STRATEGY_AGREEMENT_SUMMARY.md",
    "01_STRATEGY/STRATEGY_DECISION_LOG.md",
    "01_STRATEGY/STRATEGY_MASTER_2026-09-15_v01.md",
    "01_STRATEGY/STRATEGY_IMPACT_NOTE_x.md",
    "01_STRATEGY/FUTURE_ACTION_CALENDAR.md",
    "01_STRATEGY/STRATEGY_WATCHLIST.md",
    "01_STRATEGY/STRATEGY_ONE_PAGER_CURRENT.md",
]

TABLE = (
    [(p, "deny") for p in [*PROTECTED, *EXTRA_PROTECTED]]
    + [(p, "allow") for p in [*ALLOWED, *EXTRA_ALLOWED]]
    + [(p, "deny") for p in DIVERGENCES if DIVERGENCES[p] == "allow" and p not in PROTECTED]
)


@pytest.mark.parametrize(("path", "guard"), TABLE, ids=[p for p, _ in TABLE])
def test_opencode_edit_matches_the_guard_policy(path, guard):
    expected = DIVERGENCES.get(path, guard)
    assert edit_effect(path) == expected


def test_divergences_really_diverge_from_the_guard():
    """Each listed divergence is a real difference, so none hides a regression."""
    for path, opencode in DIVERGENCES.items():
        guard_says = "allow" if path in ALLOWED or path in EXTRA_ALLOWED else "deny"
        assert opencode != guard_says, path
        assert edit_effect(path) == opencode, path


@pytest.mark.parametrize("path", ["opencode.json", "opencode.jsonc", "AGENTS.md", "CLAUDE.md", ".mcp.json", ".opencode/agents/x.md", ".agents/x", ".git/x"])
def test_root_configs_are_not_writable(path):
    assert edit_effect(path) == "deny"


def test_other_actions_stay_denied_by_default():
    for action in ("shell", "subagent", "external_directory", "execute", "github_create_issue"):
        assert decide(action, "anything") == "deny", action


WEB_ALLOWED = [
    "https://www.irs.gov/pub/irs-pdf/p550.pdf",
    "https://irs.gov/",
    "https://www.law.cornell.edu/uscode/text/26/1211",
    "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26",
    "https://www.ecfr.gov/current/title-26",
    "https://www.federalregister.gov/documents/2026/01/01/x",
    "https://www.govinfo.gov/app/collection/uscode",
    "https://www.congress.gov/bill/119th-congress/house-bill/1",
    "https://www.ustaxcourt.gov/",
    "https://www.supremecourt.gov/opinions/",
    "https://home.treasury.gov/policy-issues/tax-policy",
]

WEB_DENIED = [
    "https://evil.example/x",
    "http://www.irs.gov/",
    "https://www.irs.gov.evil.example/",
    "https://www.irs.gov@evil.example/",
    "https://evil.example/?u=https://www.irs.gov/",
    "https://evil.example/www.irs.gov/x",
    "https://WWW.IRS.GOV/",
    "https://www.irs.gov:8443/",
    "https://ftb.ca.gov/",  # state sites are not listed; see docs/architecture.md
]


@pytest.mark.parametrize("url", WEB_ALLOWED)
def test_webfetch_allows_authority_hosts(url):
    assert decide("webfetch", url) == "allow"


@pytest.mark.parametrize("url", WEB_DENIED)
def test_webfetch_denies_everything_else(url):
    assert decide("webfetch", url) == "deny"


WEB_HOSTS = [
    "irs.gov",
    "www.irs.gov",
    "apps.irs.gov",
    "law.cornell.edu",
    "www.law.cornell.edu",
    "uscode.house.gov",
    "ecfr.gov",
    "www.ecfr.gov",
    "federalregister.gov",
    "www.federalregister.gov",
    "govinfo.gov",
    "www.govinfo.gov",
    "congress.gov",
    "www.congress.gov",
    "ustaxcourt.gov",
    "www.ustaxcourt.gov",
    "supremecourt.gov",
    "www.supremecourt.gov",
    "treasury.gov",
    "www.treasury.gov",
    "home.treasury.gov",
]


@pytest.mark.parametrize("host", WEB_HOSTS)
def test_webfetch_allows_each_host_bare_and_with_a_path(host):
    """`https://www.irs.gov` (no path) was denied live, so each host also has a bare rule."""
    assert decide("webfetch", f"https://{host}") == "allow"
    assert decide("webfetch", f"https://{host}/") == "allow"
    assert decide("webfetch", f"https://{host}/x") == "allow"
    assert decide("webfetch", f"http://{host}") == "deny"
    assert decide("webfetch", f"http://{host}/x") == "deny"


@pytest.mark.parametrize("url", ["https://www.irs.gov.evil.example", "https://www.irs.gov@evil.example", "https://evil.example/", "http://www.irs.gov"])
def test_webfetch_bare_hosts_do_not_widen_the_scope(url):
    assert decide("webfetch", url) == "deny"


def test_webfetch_rules_are_exact_https_hosts():
    """`*` crosses `/` and `.`, so a wildcard anywhere before the path would let a
    hostile host through (e.g. `https://*.irs.gov/*` matches `https://evil/x.irs.gov/`).
    Each host appears twice: `https://<host>/*` and the bare `https://<host>`."""
    resources = [r["resource"] for r in opencode_rules() if r["action"] == "webfetch" and r["effect"] == "allow"]
    assert resources
    host = re.compile(r"https://[a-z0-9]+(?:[.-][a-z0-9]+)*\.(?:gov|edu)(?:/\*)?\Z")
    assert all(host.match(r) for r in resources), resources
    assert len(resources) == len(set(resources))
    with_path = {r[len("https://"):-2] for r in resources if r.endswith("/*")}
    bare = {r[len("https://"):] for r in resources if not r.endswith("/*")}
    assert with_path == bare == set(WEB_HOSTS)


def test_websearch_is_denied_for_any_query():
    """OpenCode's first web search asks the user to pick a provider; a non-interactive run
    cancels that question and exits 1, so the agent has no web search."""
    for query in ("section 1211 capital loss limit", "", "*", "https://www.irs.gov"):
        assert decide("websearch", query) == "deny", query
    assert not [r for r in opencode_rules() if r["action"] == "websearch" and r["effect"] != "deny"]
