# Opus model, pending ledger years, and blocking mismatches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Claude Code agent on Opus by default, keep unfinished years' amounts out of permanent ledgers, and keep a mismatch blocking while any question about it is open.

**Architecture:** Instruction and configuration changes only: one key in `agent/claude/settings.json`, two rule changes in the `tax-prior-year-review` skill, a docs note, and tests that pin the wording.

**Tech Stack:** Markdown skills, JSON settings, pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-11-07-opus-ledger-blocking-design.md`

## Global Constraints

- Don't touch `agent/core.md` (1,995 of the 2,000-word limit).
- No change to the eval checker, answer key or scenarios (`evals/`).
- No change to the OpenCode agent (`agent/opencode/`).
- `agent/claude/frontmatter.yaml` stays unchanged.

## Review Focus

- A year whose return is filed but has an open `BLOCKING` item: it counts as not final, so no amounts in the ledger.
- A ledger other than carryforward (home basis, investment basis): the pending-year rule applies to every permanent ledger.
- An eval run with `--model sonnet`: still runs Sonnet, because the flag outranks the setting.
- A mismatch the agent explains with high confidence while it still asks the user something about it: stays `BLOCKING`.
- Existing ledger rules (header, chain, ST/LT note line, evidence under the table): unchanged, existing tests still pass.

---

### Task 1: Opus as the default model

**Files:**
- Modify: `agent/claude/settings.json`
- Modify: `docs/architecture.md` ("Verified against docs" section)
- Test: `tests/test_agent_core.py::test_claude_settings`

- [ ] **Step 1:** In `test_claude_settings`, add `assert settings["model"] == "opus"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_agent_core.py::test_claude_settings -q`. Expected: FAIL (KeyError `model`).
- [ ] **Step 3:** Add `"model": "opus"` after `$schema` in `agent/claude/settings.json`. In `docs/architecture.md`, add a "Model" bullet under "Verified against docs": settings set `model: opus`; the `--model` flag outranks a `model` setting (model-config docs, checked 2026-11-07), so eval runs keep the model they name; frontmatter `model` was avoided because `--agent` makes the main thread take the agent's model and the docs don't rank it against `--model`.
- [ ] **Step 4:** Run the same test. Expected: PASS.

### Task 2: Pending years and blocking mismatches in the prior-year review skill

**Files:**
- Modify: `agent/skills/tax-prior-year-review/SKILL.md` ("Mismatches are blocking", "Long-term ledgers", carryforward row rules)
- Test: `tests/test_skills.py`

- [ ] **Step 1:** Add two tests:
  - `test_mismatch_stays_blocking_while_a_question_is_open`: the skill text contains `no question about it remains open in \`00_CONTROL/OPEN_QUESTIONS.md\`` and `doesn't clear it`.
  - `test_unfinished_years_carry_no_ledger_amounts`: the skill text contains `gets no amounts in a permanent ledger`, `by year and status only`, and `a \`BLOCKING\` item about it is still open`.
- [ ] **Step 2:** Run `uv run pytest tests/test_skills.py -q -k "blocking_while or no_ledger_amounts"`. Expected: 2 FAIL.
- [ ] **Step 3:** Edit the skill:
  - "Mismatches are blocking": a mismatch counts as explained only when its cause is documented in `RECONCILIATION_NOTES.md` and no question about it remains open in `00_CONTROL/OPEN_QUESTIONS.md`. Your own explanation, however confident, doesn't clear it while you are still asking the user to confirm anything about it.
  - "Long-term ledgers": a year that isn't final (its return isn't filed, or a `BLOCKING` item about it is still open) gets no amounts in a permanent ledger. List it under the table by year and status only, with an example line; its projected or unconfirmed amounts live in that year's `RECONCILIATION_NOTES.md`.
  - Carryforward row rules, "Add or revise a row only after the figure is reconciled": add "and the year is final (see Long-term ledgers)".
- [ ] **Step 4:** Run the two tests, then `uv run pytest tests/test_skills.py -q`. Expected: all PASS.

### Task 3: Full verification

- [ ] **Step 1:** Run `uv run pytest -q -m "not opencode_probe"`. Expected: all pass.
- [ ] **Step 2:** Run `uv run python scripts/privacy_check.py` on the changed files (see its `--help`). Expected: clean.
- [ ] **Step 3:** Show Erin the exact commit (files, message, summary) and commit only on her yes.
- [ ] **Step 4:** After the commit, with Erin's OK: `uv run python evals/run.py --tool claude --model opus --scenario carryforward --runs 3`.
