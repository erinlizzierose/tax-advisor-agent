---
name: tax-prior-year-review
description: Load when reconstructing and reviewing a filed prior-year return from source documents, classifying potential issues, building an amendment analysis, or reconciling carryforwards and basis across years. Use it for RETURN_RECONSTRUCTION.md, AMENDMENT_ANALYSIS.md, RECONCILIATION_NOTES.md and the CARRYFORWARD_LEDGER.md format.
---

# Tax prior-year review and cross-year reconciliation

Use this skill to review each in-scope prior year independently before changing anything, and to keep every surviving tax attribute reconciled from year to year. Run `tax-document-inventory` first (the Completeness Gate applies), and calculate the window with `tax-amendment-window`. Review the oldest open year first unless a deadline requires a different order. Start from the source documents upward. Don't assume the filed return is wrong.

Write only to `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/` (never the seed), `02_RESEARCH/`, `04_LONG_TERM_RECORDS/` and `03_TAX_YEARS/TY_YYYY/WORKING/`. Use `03_TAX_YEARS/TY_YYYY/` as the canonical evidence folder and keep your analysis in that year's `WORKING/`. Never edit the filed return, transcripts or source forms. If the user must supply or replace evidence, ask them to place it in the source folder, then record its path. A blocked write is expected: don't retry it and don't work around it.

## Reconstruct the return (Manual §13)

Create or update `03_TAX_YEARS/TY_YYYY/WORKING/RETURN_RECONSTRUCTION.md`. Capture, as applicable:

- filing status and dependents;
- federal and state AGI;
- taxable income;
- ordinary income;
- qualified dividends;
- short- and long-term capital gains and losses;
- itemized versus standard deduction;
- major deductions;
- credits;
- NIIT, AMT and other additional taxes;
- withholding;
- estimated and extension payments;
- refund or balance due;
- overpayment applied forward;
- carryforwards into and out of the year.

Every figure points to its source document (page, form or line when practical). Keep source figures, derived calculations, authority and recommendations separate.

### Search for issues

Focus on:

- missing or incorrect cost basis;
- securities transferred between brokers;
- noncovered securities;
- reinvested distributions affecting basis;
- equity-compensation basis and double taxation;
- long-term versus short-term classification;
- wash-sale handling;
- capital-loss carryforwards;
- qualified dividends;
- foreign tax credits and carryovers;
- investment-interest carryovers;
- nondeductible IRA basis and Form 8606;
- HSA issues;
- charitable carryforwards;
- passive-activity losses, if applicable;
- depreciation and basis schedules, if applicable;
- business and QBI loss carryovers, if applicable;
- itemized deductions;
- mortgage interest and points;
- state and local tax treatment;
- estimated-tax penalties and annualization;
- credits omitted or incorrectly limited;
- state-specific carryovers;
- mismatches against information-return transcripts;
- foreign reporting and cross-border items, when applicable.

Never treat gross sale proceeds or brokerage withdrawals as income. Compute gain or loss. Never accept broker-reported basis without checking it when the facts create basis risk.

### Classify every potential issue

Use exactly one of:

- `Confirmed error`
- `Likely issue`
- `Possible opportunity`
- `No issue found`
- `Insufficient evidence`
- `Not amendable / election closed`

Never recommend a retroactive tax-lot selection or election that legally had to be made earlier. An election that had to be made contemporaneously is `Not amendable / election closed` unless authority says otherwise.

Check each election's own rule before classifying it. Some elections can be made, changed or revoked on an amended return or under relief provisions; verify per authority for the year and facts, for example:

- itemized versus standard deduction (§63(e)(3) context);
- separate to joint filing status (§6013(b)), but not joint to separate after the due date (Reg. 1.6013-1(a)(1));
- foreign tax credit versus deduction (Reg. 1.901-1(d));
- §179 expensing, per its own rules (§179(c)(2) and the regulations for the year);
- the automatic 6-month extension for elections due with the return (Reg. 301.9100-2).

These are leads to verify, not conclusions. When late-election relief would need a ruling request, recommend `Professional review first`.

### Amendment analysis

For every proposed amendment, write a mini tax-position memo (load `tax-law-research` for the memo standard and template) and quantify the estimated federal and state impact. Store year-specific amendment analysis in `03_TAX_YEARS/TY_YYYY/WORKING/AMENDMENT_ANALYSIS.md`. Store a separate reusable or complex memo in `02_RESEARCH/TAX_POSITION_MEMOS/` when preserving the legal analysis has cross-year value. Then take the candidate through the Amendment Decision Gate in `tax-amendment-window`.

Never recommend an amendment without tracing its effect into later years.

## Cross-year reconciliation (Manual §15)

Create a year-to-year ledger whenever any tracked tax attribute exists or a prior-year change may affect later years. Work through the permanent year folders in chronological order (`03_TAX_YEARS/TY_2022/`, `TY_2023/`, and so on). Never move a year between "current" and "prior" locations; reconciliation depends on stable paths.

Reconcile, when applicable:

- capital-loss carryforwards;
- net operating losses;
- passive-activity losses;
- charitable-contribution carryforwards;
- foreign-tax-credit carryovers;
- investment-interest carryforwards;
- nondeductible IRA basis;
- AMT credits;
- credit carryforwards;
- QBI losses;
- depreciation and asset basis;
- home basis;
- securities basis where prior adjustments matter;
- estimated-tax payments;
- refunds applied to the next year;
- state tax credits and carryforwards;
- foreign-asset reporting attributes and basis, where applicable;
- any other attribute that survives year-end.

For every attribute, show:

```text
Beginning balance
+ current-year additions
- current-year utilization
+/- amendment adjustments
= ending balance / next-year opening balance
```

Store the detailed reconciliation for each affected year in `03_TAX_YEARS/TY_YYYY/WORKING/RECONCILIATION_NOTES.md`.

### Mismatches are blocking

Any mismatch is a **blocking issue** until it is explained, or until the user has marked it immaterial. A mismatch counts as explained only when its cause is documented in `RECONCILIATION_NOTES.md` and no question about it remains open in `00_CONTROL/OPEN_QUESTIONS.md`. Your own explanation, however confident, doesn't clear it while you are still asking the user to confirm anything about it. You can't mark a mismatch immaterial yourself, even one with no tax effect for that year: say why you think it is immaterial and ask the user. When a filed or reported figure disagrees with the reconciled figure, record both explicitly in `RECONCILIATION_NOTES.md`, in the form "reported X vs reconciled Y" (with the source of each), and mark the item `BLOCKING` until explained. Don't pick one silently. Don't carry the unexplained number into a later year or into the long-term ledger. Add the open item to `00_CONTROL/OPEN_QUESTIONS.md`.

### Long-term ledgers

Update the permanent ledgers in `04_LONG_TERM_RECORDS/` only after the relevant returns are finalized and the amounts reconcile:

- `CARRYFORWARD_LEDGER.md`
- `HOME_BASIS_LEDGER.md`
- `RENTAL_PROPERTY_BASIS_LEDGER.md`
- `INVESTMENT_BASIS_LEDGER.md`
- `RETIREMENT_BASIS_LEDGER.md`
- `FOREIGN_ASSET_REGISTER.md`

A ledger entry must point back to the tax year and to the source or working evidence that created it. Never drop an existing row or lose a carryforward record when updating.

A year that isn't final for a ledger (its return isn't filed, or a `BLOCKING` item about that ledger's figures is still open) gets no amounts in that ledger: no table row, and no amounts in notes under the table. An open item about one ledger doesn't hold back another ledger's amounts: a blocked capital-loss carryforward doesn't stop you recording a home basis that reconciles. You may list a year that isn't final under the table by year and status only, for example:

```markdown
- 2025: pending, return not filed. Projection in `03_TAX_YEARS/TY_2025/WORKING/RECONCILIATION_NOTES.md`.
```

Keep its projected or unconfirmed amounts in that year's `RECONCILIATION_NOTES.md`.

### CARRYFORWARD_LEDGER.md format

`04_LONG_TERM_RECORDS/CARRYFORWARD_LEDGER.md` has one section per tracked attribute (for example capital loss, charitable, foreign tax credit, passive loss). Each section has one row per year with exactly this header:

```markdown
| Year | Opening | Current-year result | Deduction used | Closing |
|---|---|---|---|---|
| YYYY | 0 | 0 | 0 | 0 |
```

Rules for the rows:

- Opening equals the previous row's Closing. A break in that chain is a mismatch and is blocking.
- Column meanings (capital-loss section):
  - **Opening** is the carryover in, a positive amount.
  - **Current-year result** is the year's own net capital gain (positive) or loss (negative), before any carryover.
  - **Deduction used** is the amount of the carryover and current-year net loss treated as absorbed against ordinary income under §1212(b)(2), a positive amount: the smaller of the Schedule D line 21 loss (as a positive number) and taxable income plus that loss, not below zero. That is the Capital Loss Carryover Worksheet line 4 amount for that year, not the Schedule D line 21 deduction. The two are equal unless taxable income before the capital-loss deduction is below the line 21 amount; when taxable income is low or negative, line 4 is smaller (verify the limit and the worksheet for the year with `tax-law-research`).
  - **Closing** is the carryover out, which becomes the next year's Opening.
- Gains first absorb the opening carryover. So Closing = max(0, Opening − Current-year result) − Deduction used. Carryover absorbed by current-year gains is reflected in the max(…) term; the §1212(b)(2) amount is recorded once, in Deduction used, so no amount is subtracted twice.
- Capital-loss carryovers keep their short-term or long-term character (§1212(b)(1)). Record that year's short-term and long-term carryover (Capital Loss Carryover Worksheet lines 8 and 13, or the filed return's worksheet) in that year's `WORKING/RECONCILIATION_NOTES.md` and as a note line under the ledger table, not as extra table columns. The two must sum to Closing. The character matters because next year's Opening enters Schedule D lines 6 and 14 separately, and short-term carryover offsets short-term gains (taxed at ordinary rates) before long-term ones; the total alone cannot reproduce that split.
- Amendment adjustments go in `RECONCILIATION_NOTES.md` for the affected years, not as hidden changes to these columns. When an amendment changes a row, revise the row's figures to the reconciled amounts and cite the note.
- For another attribute (charitable, credit, passive loss), keep the same header and chain, and state its one-line column meanings under that section's heading before the first row.
- Worked example, illustrative round numbers only, not a ledger entry:

```markdown
| Year | Opening | Current-year result | Deduction used | Closing |
|---|---|---|---|---|
| Example | 10,000 | +4,000 | 3,000 | 3,000 |
```

  The 4,000 gain absorbs 4,000 of the 10,000 opening carryover, leaving a 6,000 net loss. The deduction is the lesser of the annual limit (3,000) and that 6,000 loss, so 3,000 is deducted and 3,000 carries out to next year's Opening. This assumes taxable income before the capital-loss deduction is at least zero, so worksheet line 4 equals 3,000. The Schedule D line 21 deduction is the lesser of the net capital loss and $3,000 ($1,500 MFS) (§1211(b)). Low or negative taxable income does not shrink the $3,000 ($1,500 MFS) limit itself; it reduces the amount treated as used under §1212(b)(2) (the Capital Loss Carryover Worksheet line 4 amount), so more carries forward. Verify the worksheet, and verify the limit for the specific year and filing status.
- Under the table, name the evidence for each row: the `TY_YYYY` source or working file and the figure's page, form or line. Use the reconciled figure, not the software figure, and note the version of the return (original or amended) it came from.
- Add or revise a row only after the figure is reconciled and the year is final (see Long-term ledgers). When an amendment changes an earlier year, update that row and every later row, and log the change in the affected years' `RECONCILIATION_NOTES.md`.

## Failure modes to avoid (Manual §31)

- Amendments not traced into later years. Calculate the dependency chain first.
- Lost carryforward records. Keep the ledger current and the evidence path on every row.
- Basis taken from the broker without checking. Verify it against transfers, noncovered status, reinvested distributions, wash sales and equity-compensation records.
- Software or AI arithmetic standing in for reconciliation. Reconcile each material figure to its source document.
- Applying current thresholds to older years, and treating proposed law as enacted. Use `tax-law-research` for each year's own figures.
- Assuming the state follows the federal treatment. Review state items separately.
