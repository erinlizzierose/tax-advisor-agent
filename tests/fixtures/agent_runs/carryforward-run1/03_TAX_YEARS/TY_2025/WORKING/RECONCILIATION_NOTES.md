# TY_2025 Reconciliation Notes: capital-loss carryover (provisional, return not filed)

Analysis date: 2026-09-15 (user-stated). No 2025 Form 1040 is in the workspace.

## RSU basis (drives the 2025 result)

- 1099-B (`Investments/1099B_Brightfield.md`): 100 sh Northwind RSU shares, proceeds 16,000, basis 0, basis NOT reported to IRS. Reported gain 16,000.
- 2024 W-2 Avery (`TY_2024/W2s/W2_Avery.md`, Box 14): 100 shares vested 2024-03-01 at FMV 150, 15,000 included in Box 1. That income was already taxed, so basis = 15,000 (IRC §83(a), §1012; Treas. Reg. §1.83-4(b)). Law label: current law, high confidence.
- Reported gain 16,000 vs reconciled gain 1,000. Holding period begins 2024-03-02, so long-term (sold 2025-06-02). Form 8949 Part II, Box E, column (g) adjustment of -15,000 with code B.
- Needs confirmation: that the employer did not already add basis, and the vest FMV (150) matches the employer's records. Treat as BLOCKING for the 2025 return, not for the ledger rows 2022-2023.

## Roll (provisional)

| Item | On filed-chain figures | Reconciled |
|---|---|---|
| Opening carryover | 3,000 | 2,000 (BLOCKING, see TY_2024 notes) |
| 2025 net result | +15,500 as broker reported | +500 (1,000 RSU gain, -500 index fund) |
| Opening absorbed by gain | 15,500 (opening to 0, net gain 12,500) | 500, leaving 1,500 |
| Deduction used | 0 | 1,500 |
| Closing | 0 | 0 |

## Impact (rough, not a tax calculation)

Using broker basis would overstate 2025 net long-term gain by 15,000. At a likely 15% federal LTCG rate, about $2,250 of tax; the rate is unverified for 2025 and needs the 2025 return computation. The carryover ends at 0 in either case, but the 2025 deduction and taxable income differ. The 1,000 opening discrepancy changes the 2025 ordinary-income deduction by 1,000 (about 22 to 24% bracket, roughly $220 to $240, unverified).
