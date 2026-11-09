---
name: tax-amendment-window
description: Load when working out the actual federal and state amendment or refund-claim deadline for a prior tax year, or when deciding whether and in what order to amend. Use it to compute deemed filing and payment dates, weekend and holiday adjustments, and the refund lookback; to write AMENDMENT_WINDOW.md; and to run the Amendment Decision Gate and the user approval of the amendment plan.
---

# Tax amendment window and decision gate

Use this skill for every prior return that may be in scope for an amendment or refund claim, and before recommending any amendment. For return reconstruction and issue classification use `tax-prior-year-review`. For the authority behind a rule use `tax-law-research`.

Write only to `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/` (never the seed), `02_RESEARCH/`, `04_LONG_TERM_RECORDS/` and `03_TAX_YEARS/TY_YYYY/WORKING/`. The filed return, e-file evidence and payment confirmations live in the user's source folders (`Filing/`, `Payments_Notices/` and the like). Ask the user to place any missing one there, then record its canonical path in the working file. A blocked write is expected: don't retry it and don't work around it.

## Actual amendment-deadline procedure (Manual §12)

**Never assume "three years".** Never assume a generic window, a Monday-to-Friday deadline, or that the actual filing date is the operative date. Calculate the actual federal and state window from that return's own facts.

The rules below are to **verify with primary authority for the specific tax year and claim**. They are not conclusions about any return. Research each one using `tax-law-research`, label it, and log it in `00_CONTROL/SOURCE_LEDGER.md`.

- **Refund-claim limitation, §6511.** Verify the §6511(a) filing rule (the period measured from the return's filing or from payment of the tax, as the statute provides) and the §6511(b)(2) lookback limits on how much may be credited or refunded. Verify which lookback limit applies to the specific claim, including any extension period that changes it.
- **Early return, §6513(a).** Verify that a return filed before its due date is treated as filed on the original due date, and that extensions are disregarded when applying that rule.
- **Withholding, §6513(b).** Verify that tax withheld is deemed paid on the 15th day of the fourth month following the close of the tax year (April 15 for a calendar-year individual), not on the date of withholding (§6513(b)(1)). That date is fixed by the calendar: it is determined without regard to any extension and is not moved by a weekend, holiday or postponement rule unless specific authority says so. Also verify the deemed-payment date for estimated tax (the last day prescribed for filing the return, determined without regard to any extension, §6513(b)(2)) and for any other payment type present.
- **Weekend and legal-holiday rollover, §7503.** Verify that a deadline falling on a Saturday, Sunday or legal holiday moves to the next business day, and verify what counts as a legal holiday, including D.C. legal holidays, for the specific year and place of filing. Do the calculation on the actual calendar for that date.
- **Timely mailing, §7502.** Verify whether and how a mailed claim is timely, and the evidence it requires.
- **Special periods.** Verify any special limitation for the specific issue (for example certain losses, credits, carrybacks, or tolling provisions), and disaster relief, special postponements and combat-zone rules. Check at least:
  - §6511(c): extension of the period by agreement with the IRS (Form 872);
  - §6511(d)(1): the longer period for bad debts and worthless securities;
  - §6511(d)(2): net operating loss and capital loss carrybacks;
  - §6511(d)(3): the longer period for the foreign tax credit;
  - §6511(d)(4): credit carrybacks;
  - §6511(h): suspension while an individual is financially disabled (not while a spouse or other person is authorized to act; proof required);
  - §7508: combat-zone and contingency-operation postponements;
  - §7508A: disaster, significant-fire and terrorist or military-action postponements, including the §7508A(a)(3) rule on the amount of any credit or refund, the §7508A(d) overpayment rules, the mandatory §7508A(e) period, and whether a disregarded filing period counts as an extension for the §6511(b)(2)(A) lookback. The subsection letters and the length of the mandatory period changed in 2025, so verify the version in effect for the declaration and the claim.
- **State.** Research the state refund deadline and any duty to notify the state of a federal change separately. Never assume the state follows the federal period.

### Procedure for each year

1. Use the corresponding `03_TAX_YEARS/TY_YYYY/` folder as the canonical year record.
2. Determine the original statutory due date.
3. Determine whether a valid extension was filed and what it changed.
4. Determine the actual filing date from the filed return or e-file evidence when available.
5. Determine the relevant actual payment dates, types and amounts, with the evidence path for each (the template's `Payments` section), then derive the deemed payment dates under the rules you verified. Keep actual and deemed dates in separate fields.
6. Check for disaster relief, special postponements, combat-zone rules or other applicable extensions.
7. Research the federal refund-claim limitation that applies to that return and the specific claim.
8. Check for special limitation periods relevant to the issue, such as specific losses, credits or other statutory exceptions.
9. Calculate the unadjusted deadline, apply any weekend or holiday adjustment, and state the last practical filing date for the claim.
10. Separately research the state amendment or refund deadline and any obligation to notify the state of a federal change.
11. Record authority and calculation in `03_TAX_YEARS/TY_YYYY/WORKING/AMENDMENT_WINDOW.md`, built from `templates/AMENDMENT_WINDOW.md`.

### Required output for each year

- return year;
- jurisdiction;
- original due date;
- extension status;
- actual filing date;
- relevant payment date(s);
- general limitation rule;
- special rule if applicable;
- calculated deadline;
- days remaining as of the analysis date;
- confidence (High, Medium or Low);
- source citation;
- source document paths supporting the filing and payment dates.

The template adds the deemed dates and the lookback conclusion so the calculation can be audited. If any date rests on missing evidence, mark the deadline provisional and say what is missing. If a deadline is close, move it to the top of `00_CONTROL/PRIORITY_CHECKLIST.md` immediately and update the amendment status in `CURRENT_STATE.md`.

## Amendment Decision Gate (Manual §14)

Do not amend a return merely because a different number is possible. For each candidate amendment present:

- issue;
- tax year and canonical `TY_YYYY` folder;
- factual basis;
- legal basis;
- source document paths;
- documents required;
- expected federal impact;
- expected state impact;
- interest and penalty impact;
- future carryforward impact;
- later years affected;
- amendment deadline (from `AMENDMENT_WINDOW.md`);
- audit and documentation considerations;
- confidence;
- recommendation: `Amend now` / `Amend after dependency` / `Do not amend` / `Professional review first`.

### User approval gate

Before preparing a final amendment sequence, present the proposed amendment plan in priority order and ask the user to approve it. Do not treat silence as approval. If no one can answer, record the request in `00_CONTROL/OPEN_QUESTIONS.md` and stop at the gate.

If amending an earlier year changes a carryforward, basis, payment, credit, depreciation amount or other tax attribute that flows into later years, calculate the dependency chain before finalizing later-year amendments or the active-year return.

Persist:

- the decision and calculations in `03_TAX_YEARS/TY_YYYY/WORKING/AMENDMENT_ANALYSIS.md`;
- year-to-year effects in the applicable `RECONCILIATION_NOTES.md` files;
- durable final carryforward and basis effects in `04_LONG_TERM_RECORDS/`, only after they are reconciled.

When a position is uncertain or the window turns on special facts, recommend `Professional review first` and use the `tax-law-research` guidance on the precise question and documents to bring.
