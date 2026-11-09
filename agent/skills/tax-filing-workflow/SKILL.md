---
name: tax-filing-workflow
description: Load when preparing the active year's return. Use it for the phased filing workflow F1 to F13, the live priority checklist, the tax software input map, form-level output review, the independent reasonableness check, the filing-readiness gate, post-filing closeout, and the definition of done for the filing and amendment cycle.
---

# Tax filing workflow

Use this skill to prepare the active filing year identified in `CURRENT_STATE.md`, and to keep the priority checklist live. Align every filing choice with the approved strategy master. Load `tax-document-inventory` for the inventory, `tax-law-research` for any rule you rely on, `tax-amendment-window` and `tax-prior-year-review` for prior-year dependencies.

The active year lives permanently at `03_TAX_YEARS/TY_YYYY/`. Don't create or move it into a separate current-year directory.

Write only to `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/` (never the seed), `02_RESEARCH/`, `04_LONG_TERM_RECORDS/` and `03_TAX_YEARS/TY_YYYY/WORKING/`. You may start a new year by writing `03_TAX_YEARS/TY_YYYY/WORKING/` files. Where this workflow says software output, drafts, filed returns or acceptance evidence are stored in `Filing/` (or any other source folder, including `Real_Estate/`), the user stores them: ask the user to place the file there, then record its canonical path in the `WORKING/` file. A blocked write is expected: don't retry it and don't work around it.

Follow the phases in order unless a deadline requires triage.

## Current-year filing workflow (Manual §16)

### Phase F1: Establish filing facts

Record each of these:

- tax year;
- canonical tax-year path;
- federal and state jurisdictions;
- filing status;
- dependents;
- extension status;
- payment history;
- filing deadline (verify it, including any weekend or legal-holiday adjustment; see `tax-amendment-window`);
- outstanding notices;
- prior-year return version that governs opening balances.

Write these to `CURRENT_STATE.md` and to the active year's `WORKING/` files as appropriate.

### Phase F2: Complete document inventory

Recursively reconcile all source documents in the active `TY_YYYY/` folder and any available transcripts. Identify missing or corrected forms. Update `03_TAX_YEARS/TY_YYYY/WORKING/DOCUMENT_INVENTORY.md` (procedure in `tax-document-inventory`).

### Phase F3: Resolve amendment dependencies

If an open prior-year issue changes a current-year carryforward, basis, credit, depreciation amount, payment or state amount, resolve or model it before finalizing the current return.

### Phase F4: Reconstruct income and basis

Reconcile wages, interest, dividends, brokerage activity, retirement distributions, business or rental income if any, foreign income and assets when applicable, and other reportable income. Never treat gross brokerage withdrawals or sale proceeds as income; compute gain or loss on each securities sale. Retirement distributions and other withdrawals are not covered by that rule: each gets its own researched tax treatment for the year (see `tax-law-research`).

For investments, explicitly reconcile:

- proceeds;
- adjusted basis;
- holding period;
- covered or noncovered status;
- wash sales;
- employee-equity basis;
- capital-loss carryforwards;
- qualified dividends;
- foreign taxes;
- NIIT effects.

Store the reconstruction in `03_TAX_YEARS/TY_YYYY/WORKING/RETURN_RECONSTRUCTION.md`. Reconcile every material figure to a source document yourself. Your own arithmetic or the software total is not reconciliation.

### Phase F5: Review deductions and credits

Analyze all applicable items against the strategy and facts, including itemized versus standard deduction, home-related deductions, retirement and HSA treatment, charitable items, child and dependent care, education credits, energy credits, business deductions, and state-specific opportunities when relevant.

### Phase F6: Review home purchase and real-estate basis

When a home or investment-property transaction exists, analyze the Closing Disclosure and related records. Separate each item into:

- currently deductible;
- capitalized into property basis;
- loan cost or amortizable item, if applicable;
- nondeductible personal cost;
- uncertain / needs authority.

The source evidence stays in the applicable year's `Real_Estate/` folder, which the user maintains; ask the user to place missing records there and record their canonical paths. Once the analysis is reconciled, update the applicable long-term basis ledger: `04_LONG_TERM_RECORDS/HOME_BASIS_LEDGER.md` for a home, or `04_LONG_TERM_RECORDS/RENTAL_PROPERTY_BASIS_LEDGER.md` for investment property. Each ledger row points back to the tax year and evidence that created it. Put anything classified uncertain into a tax position memo (see `tax-law-research`).

### Phase F7: Estimated tax and penalty analysis

Reconcile withholding, estimated payments, extension payments, safe-harbor rules, timing of income, and annualization methods when applicable. Don't accept a software penalty automatically. Evaluate whether timing-based calculations improve the result.

### Phase F8: State return

Perform a separate state-law review. Never assume federal treatment controls state treatment. Reconcile state modifications, credits, capital-gain rules, residency and part-year issues, SALT interactions, property-state filing obligations and amendment dependencies. Review federal and state results separately.

### Phase F9: Tax software entry map

Create `03_TAX_YEARS/TY_YYYY/WORKING/TAX_SOFTWARE_INPUT_MAP.md`. For each material item show:

- source document path;
- amount;
- tax treatment;
- software interview or form location;
- resulting tax form or schedule;
- supporting evidence;
- review status.

### Phase F10: Output-form review

Draft and final tax-software output is stored in `03_TAX_YEARS/TY_YYYY/Filing/`. The user places it there; ask for it, then record its path. Review the actual generated forms, not merely the software interview summary. Verify material figures and the expected forms and schedules.

### Phase F11: Independent reasonableness check

Independently estimate the major tax components and compare them to the software result. Investigate material differences. Record the findings in `03_TAX_YEARS/TY_YYYY/WORKING/REVIEW_NOTES.md`.

### Phase F12: Filing readiness gate

Before the return is considered ready, present to the user:

- expected federal refund or balance due;
- expected state refund or balance due;
- material tax positions taken;
- unresolved low-confidence items;
- amendments still pending;
- documentation to retain;
- carryforwards expected into next year;
- post-filing actions;
- estimated-tax and withholding actions for the next year.

Update `03_TAX_YEARS/TY_YYYY/WORKING/FILING_CHECKLIST.md`.

The user must approve proceeding with the final filing package. Silence is not approval. If no one can answer, record the request in `00_CONTROL/OPEN_QUESTIONS.md` and stop at the gate. Never claim a return has been filed unless actual submission or acceptance evidence exists, and never claim you filed it yourself.

### Phase F13: Post-filing closeout

After filing and acceptance evidence are available:

- ask the user to retain the final filed return and acceptance evidence in `03_TAX_YEARS/TY_YYYY/Filing/`, and record the canonical paths;
- create or update `03_TAX_YEARS/TY_YYYY/WORKING/FILED_RETURN_SUMMARY.md`;
- finalize `EXPECTED_CARRYFORWARDS.md` and the reconciliation notes;
- update the relevant `04_LONG_TERM_RECORDS/` ledgers (formats in `tax-prior-year-review`);
- update `CURRENT_STATE.md`;
- add next-year actions to the strategy calendar and watchlist (`01_STRATEGY/`) and to the priority system.

Don't move the completed `TY_YYYY/` directory. It remains the permanent record for that tax year.

## Priority checklist, default order (Manual §17)

Maintain `00_CONTROL/PRIORITY_CHECKLIST.md` as a live checklist and reorder it when deadlines or facts warrant.

1. **Hard deadlines and open amendment windows.** Identify anything that expires first.
2. **Unfiled return / extension / payment exposure.** Determine filing status, unpaid tax, and penalty and interest issues.
3. **Missing documents and transcripts.** Recursively inventory the applicable `TY_YYYY/` folders and resolve gaps that could invalidate analysis.
4. **High-dollar basis and capital-loss issues.** Verify brokerage basis, employee stock, carryforwards and holding periods.
5. **Cross-year dependencies.** Resolve earlier-year items that alter the active filing year's reporting.
6. **Home and itemized deductions.** Mortgage interest, points, property tax, closing statement, basis.
7. **Retirement, HSA and account-basis issues.** Contributions, distributions, Form 8606, carryovers.
8. **Credits and state-specific opportunities.** Identify valuable credits and modifications, including dependent and childcare items when relevant.
9. **Estimated-tax / penalty optimization.** Safe harbor, timing, annualization, waivers if legally available.
10. **Active-year return construction and review.** Complete the tax software input map and the form-level review in the active `TY_YYYY`.
11. **Future-year actions.** Estimated payments, withholding, loss harvesting, contributions, planned transactions, documentation.
12. **Tax-year closeout.** Have the user preserve the filed return and acceptance evidence in `03_TAX_YEARS/TY_YYYY/Filing/`, finalize `03_TAX_YEARS/TY_YYYY/WORKING/`, and update `04_LONG_TERM_RECORDS/`.

Every checklist item has: `Owner | Status | Due date | Dollar impact | Confidence | Dependency | Evidence needed | Notes`.

## Failure modes to avoid (Manual §31)

- Treating gross brokerage withdrawals or sale proceeds as taxable income instead of computing gain or loss.
- Letting tax software substitute for legal analysis.
- Letting AI arithmetic substitute for reconciliation to source documents.
- Assuming federal treatment carries over to the state return.
- Applying current thresholds to older years, and treating proposed law as enacted.

## Definition of done: filing and amendment cycle (Manual §33)

The cycle for a tax year is complete only when all of these are true:

- applicable law has been refreshed and source-logged;
- the year's source documents have been recursively inventoried in `03_TAX_YEARS/TY_YYYY/`;
- amendment windows have been calculated for the relevant prior returns;
- the document inventory is complete, or the gaps are explicitly documented in `TY_YYYY/WORKING/DOCUMENT_INVENTORY.md`;
- prior-year dependencies are resolved or modeled;
- cross-year carryforwards and basis items reconcile;
- material active-year inputs reconcile to source documents;
- applicable optimization opportunities have been considered;
- federal and state results have been reviewed separately;
- generated tax forms have received a form-level review;
- material positions have appropriate memos and evidence;
- the user has approved the filing-readiness summary;
- the final filed return and actual acceptance evidence, when available, are retained in `03_TAX_YEARS/TY_YYYY/Filing/`;
- `TY_YYYY/WORKING/FILED_RETURN_SUMMARY.md` and the year-specific reconciliation files are updated after filing;
- applicable `04_LONG_TERM_RECORDS/` ledgers hold the verified carryforwards and basis;
- next-year actions and strategy watch items are preserved.

Completing a year does not move or rename its `TY_YYYY/` folder.
