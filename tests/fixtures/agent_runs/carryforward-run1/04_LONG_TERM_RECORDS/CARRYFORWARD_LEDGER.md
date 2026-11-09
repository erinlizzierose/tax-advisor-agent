# Carryforward Ledger

Last updated: 2026-09-15 (user-stated analysis date; system clock read 2026-10-28)
Sample household, fictional data. Masked identifiers only.

## Capital loss carryover (all long-term; no short-term activity in any year)

Columns: Opening = carryover in (positive). Current-year result = the year's own net capital gain (+) or loss (−) before carryover. Deduction used = loss deducted against ordinary income (limit $3,000 MFJ / $1,500 MFS, IRC §1211(b), not indexed). Closing = max(0, Opening − result) − Deduction used.

| Year | Opening | Current-year result | Deduction used | Closing |
|---|---|---|---|---|
| 2022 | 0 | -12,000 | 3,000 | 9,000 |
| 2023 | 9,000 | +2,000 | 3,000 | 4,000 |

Evidence:
- 2022: 1099-B (`03_TAX_YEARS/TY_2022/Investments/1099B_Brightfield.md`, 400 sh, proceeds 18,000, basis 30,000, basis reported); filed 1040 Sch D lines 8b, 14, 21 and carryover worksheet (`03_TAX_YEARS/TY_2022/Filing/Form_1040_Summary.md`). Original return, accepted 2023-10-12 (`Filing/Efile_Acceptance.md`). Recomputed independently: reconciles.
- 2023: 1099-B (`TY_2023/Investments/1099B_Brightfield.md`, net +2,000); filed Sch D line 14 = 9,000 and worksheet carryover 4,000 (`TY_2023/Filing/Form_1040_Summary.md`). Original return. Recomputed: reconciles. No e-file acceptance record is in the workspace for 2023, so "filed" rests on the return summary only.

### Rows intentionally NOT entered (BLOCKING, see `00_CONTROL/OPEN_QUESTIONS.md` Q1 to Q3)

- **2024**: filed Sch D line 14 opening = 5,000 vs reconciled 4,000 (2023 closing). Filed closing 3,000 vs reconciled 2,000. Unexplained, so no row until resolved. Reconciled figures if the 2023 closing stands: Opening 4,000; result -1,000; deduction 3,000; Closing 2,000. Deduction is 3,000 either way, so no 2024 tax effect; only the carryover record is overstated by 1,000.
- **2025**: return not prepared or filed. Provisional only: opening 2,000 (reconciled) vs 3,000 (filed 2024 worksheet); 2025 result +500 only if RSU basis is corrected to 15,000 (broker reported 0). Deduction used 1,500, closing 0. See `03_TAX_YEARS/TY_2025/WORKING/RECONCILIATION_NOTES.md`.

Chain check: 2022 Closing 9,000 = 2023 Opening 9,000 (ok). 2023 Closing 4,000 vs 2024 filed Opening 5,000 (BREAK, blocking).
