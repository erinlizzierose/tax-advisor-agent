# Carryforward Ledger

Last updated: 2026-09-15 (user-stated analysis date). Only reconciled rows are entered. TY_2024 and TY_2025 are held out (see HOLD below).

## Capital loss (federal; Wyoming has no income tax)

Columns: Opening = carryover in (positive). Current-year result = year's own net capital gain (+) or loss (−) before carryover. Deduction used = loss deducted against ordinary income (limit $3,000 MFJ per return). Closing = max(0, Opening − result) − Deduction used.

| Year | Opening | Current-year result | Deduction used | Closing |
|---|---|---|---|---|
| 2022 | 0 | -12,000 | 3,000 | 9,000 |
| 2023 | 9,000 | +2,000 | 3,000 | 4,000 |

Evidence:
- 2022: `03_TAX_YEARS/TY_2022/Filing/Form_1040_Summary.md` (Sch D lines 8b, 14, 21; worksheet), `Investments/1099B_Brightfield.md`; original return filed 2023-10-12, accepted. Reconciled: `TY_2022/WORKING/RECONCILIATION_NOTES.md`.
- 2023: `03_TAX_YEARS/TY_2023/Filing/Form_1040_Summary.md` (Sch D lines 8b, 14, 21; worksheet), `Investments/1099B_Brightfield.md`; original return filed 2024-04-10. Reconciled: `TY_2023/WORKING/RECONCILIATION_NOTES.md`.

### HOLD: rows not entered (BLOCKING)

- **2024**: opening reported 5,000 vs reconciled 4,000; carryover out reported 3,000 vs reconciled 2,000. Cause unexplained. Reconciled arithmetic would be 4,000 / -1,000 / 3,000 / 2,000. Enter after the user explains the difference. See `TY_2024/WORKING/RECONCILIATION_NOTES.md`.
- **2025**: return not prepared. RSU basis (reported gain 16,000 vs reconciled 1,000) is unresolved. See `TY_2025/WORKING/RECONCILIATION_NOTES.md`. Provisional closing is 0.
- The chain break after 2023 is deliberate. Current-year result for 2025 depends on the filing.
