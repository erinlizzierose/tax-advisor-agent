---
name: tax-annual-rollover
description: Load when starting a new filing cycle, before work on the next tax year begins. Use it to confirm the prior year is complete, finalize long-term ledgers, carry forward only verified opening balances, review the strategy watchlist, run the annual law refresh, re-affirm or re-approve the strategy, and move the active-year pointer in CURRENT_STATE.md without moving any year folder.
---

# Tax annual rollover

Use this skill at the start of every new filing cycle. Its job is to make the new year start from verified numbers and to leave the prior year in place.

Write only to `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/` (never the seed), `02_RESEARCH/`, `04_LONG_TERM_RECORDS/` and `03_TAX_YEARS/TY_YYYY/WORKING/`. You can start the new year by writing `03_TAX_YEARS/TY_<new year>/WORKING/` files. You cannot create the year's source-category folders or put documents in them. The user creates those and places evidence; ask the user, then record the canonical paths in the `WORKING/` files. A blocked write is expected: don't retry it and don't work around it.

## Annual rollover procedure (Manual §22)

Do these in order.

1. Confirm that the prior tax year's final filed return and acceptance evidence remain in `03_TAX_YEARS/TY_<prior year>/Filing/`. If they are missing, ask the user to place them there. Don't proceed on the assumption that the year was filed.
2. Confirm that the prior year's `WORKING/FILED_RETURN_SUMMARY.md`, reconciliation notes and expected carryforwards are complete. Any unexplained mismatch in `RECONCILIATION_NOTES.md` is blocking (see `tax-prior-year-review`).
3. Update and finalize the applicable records in `04_LONG_TERM_RECORDS/`, including carryforwards and the home, rental, investment, retirement and foreign-asset basis ledgers and registers. Use the formats in `tax-prior-year-review`. Record the rollover in `04_LONG_TERM_RECORDS/ANNUAL_ROLLOVER_LOG.md`: date, years involved, ledgers updated, balances carried forward, and open items.
4. Copy forward only verified opening balances into the new year's analysis. Never copy an unexplained number merely because it appeared in prior software. A figure that disagrees with the reconciled one is recorded as "reported X vs reconciled Y" and stays blocking.
5. Review `01_STRATEGY/STRATEGY_WATCHLIST.md` for triggered events.
6. Ask the user what material household, employment, state, investment, property, business, charitable, retirement, foreign-asset or estate facts changed. Record the answers in `00_CONTROL/FACT_REGISTER.md`, with the verification status of each.
7. Run the annual law refresh (see `tax-law-research`).
8. Create a Strategy Impact Note for any material change (see `tax-strategy-discovery`).
9. Re-approve the strategy only if the changes are material. Otherwise document an annual reaffirmation in `01_STRATEGY/STRATEGY_DECISION_LOG.md`. Re-approval comes from the user; never assume it. While a re-approval request is pending, the status in `CURRENT_STATE.md` stays `Approved`: the existing version governs until the user explicitly approves the new one. Note the pending change after a dash (status rule in `tax-strategy-discovery`).
10. Start `03_TAX_YEARS/TY_<new year>/` by writing its `WORKING/` files. If the year folder or its source-category subfolders don't exist, ask the user to create only the ones actually needed.
11. Update `CURRENT_STATE.md` so `Current filing tax year` and `Current tax-year directory` point to the new permanent year folder, and set `Latest completed filing tax year`.
12. Create or update the new year's `WORKING/DOCUMENT_INVENTORY.md` and `FILING_CHECKLIST.md`, and other year-specific working files as they become necessary (see `tax-document-inventory` and `tax-filing-workflow`).

**Do not archive by moving the old year.** The prior year remains permanently under `03_TAX_YEARS/TY_YYYY/`. Changing the active year means changing the pointer in `CURRENT_STATE.md`, nothing else.

## Test of a good rollover

You must be able to reconstruct why every opening balance exists by tracing it through the long-term ledgers and the originating year's files, without relying on memory from the prior year. If you can't trace a balance, don't carry it forward. Mark it unverified, add the question to `00_CONTROL/OPEN_QUESTIONS.md`, and say which evidence would resolve it.
