# Opus model, pending ledger years, and when a mismatch stops blocking

Date: 2026-11-07. Status: approved by Erin 2026-11-07 (model location changed during planning, see Approaches).

## Why

The 2026-11-07 Opus eval (5 scenarios, 3 runs each) made no clear tax mistakes, while Sonnet made two in the same scenarios. All three Opus carryforward runs still failed:

- Two failed because Opus wrote projected amounts for unfinished years (2024 blocked, 2025 unfiled) as notes under the ledger table, with explanations inside the amounts. The ledger check could not read them.
- One failed because Opus explained the planted 2024 error itself, posted the corrected row to the permanent ledger and dropped `BLOCKING`, while a confirmation question to the user was still open.

## Goals

1. The Claude Code agent runs on Opus in real use.
2. The permanent carryforward ledger holds amounts only for years that are final and reconciled. An unfinished year appears by name and status only.
3. A mismatch stays `BLOCKING`, and out of the permanent ledger, until it is explained **and** no open question about it remains.

## Non-goals

- No change to the eval checker, the answer key or the scenarios. The carryforward scenario already allows a held-out 2024 and 2025.
- No change to the OpenCode agent's model.
- The lock tests (seed-enforcement, evidence-enforcement), which come back inconclusive when Opus refuses the write, are handled separately.

## Anti-goals

- Loosening the checker to accept prose rows. It was hardened over several review rounds against false passes.
- The agent treating its own inference as a substitute for the user's confirmation on a permanent record.

## Approaches considered

- **Ledger false fails.** (a) Agent rule: unfinished years appear by name and status only, with their amounts kept in that year's `RECONCILIATION_NOTES.md`. (b) Make the checker parse prose rows. **Picked (a):** it keeps the permanent record clean, matches the existing "finalized only" rule, and leaves the checker untouched.
- **Blocking.** (a) Follow the manual literally, so the agent's own explanation clears the block, and relax the test. (b) "Explained" means the cause is documented and no open question about it remains; until then the item stays `BLOCKING` and out of the ledger. **Picked (b):** it's the safest in practice. A missing document (an unseen 2023 trade, an amended 2023 return) could make the agent's explanation wrong, and waiting costs one user answer before the next return is filed. It is a clarification of the manual's "until explained", and the test already matches it.
- **Model.** (a) `model: opus` in the agent frontmatter. (b) `"model": "opus"` in `agent/claude/settings.json`, which the installer copies to the workspace's `.claude/settings.json`. **Picked (b), changed from (a) during planning:** the Claude Code docs say `--agent` makes the main thread take on the agent's model, and don't say whether a frontmatter `model` or the `--model` flag wins. The model-config docs do rank the `--model` flag above a `model` setting, so with (b) eval runs keep the model they name.

## Decisions

- `agent/claude/settings.json` gains `"model": "opus"`. The eval runner still passes `--model` explicitly, so evals keep testing whichever model is named. Interactive use in an installed workspace starts on Opus unless the user passes `--model`, sets `ANTHROPIC_MODEL`, or switches with `/model`.
- In `agent/skills/tax-prior-year-review/SKILL.md`:
  - Under "Mismatches are blocking": a mismatch counts as explained only when its cause is documented in `RECONCILIATION_NOTES.md` and no question about it remains open in `00_CONTROL/OPEN_QUESTIONS.md`. While a question is open, keep `BLOCKING` and keep the year's amounts out of the permanent ledger.
  - Under the ledger format: a year that isn't final (return not filed, or a blocking item open) may be listed below the table by year and status only, with no amounts; its projected amounts live in that year's `RECONCILIATION_NOTES.md`.
- Tests in `tests/test_skills.py` and `tests/test_agent_core.py` pin the new skill wording and the `model` setting. `docs/architecture.md` records the model choice and why.

## Left to the builder

- Exact wording, kept consistent with the skill's existing style.
- Whether `tax-annual-rollover` and `tax-session-close`, which already defer to `tax-prior-year-review` on blocking, need a cross-reference tweak.

## Verification

- `pytest` (focused tests first, then the full suite).
- After the user approves a commit: Opus re-run of `carryforward` (3 runs).
