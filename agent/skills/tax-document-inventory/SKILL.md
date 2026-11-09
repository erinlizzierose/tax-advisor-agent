---
name: tax-document-inventory
description: Load before analyzing any tax year, and whenever source documents need to be found, classified or checked for completeness. Use it to recursively inventory a TY_YYYY folder, classify documents regardless of folder name, find missing, corrected or superseded forms, compare source forms to the filed or draft return, and write DOCUMENT_INVENTORY.md. Also holds the full tax-year folder rules.
---

# Tax document inventory

Use this skill before any analysis of a tax year, and whenever the question is "what evidence do we have, and what is missing". It produces `03_TAX_YEARS/TY_YYYY/WORKING/DOCUMENT_INVENTORY.md`.

Write only to `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/` (never the seed), `02_RESEARCH/`, `04_LONG_TERM_RECORDS/` and `03_TAX_YEARS/TY_YYYY/WORKING/`. You never create, move, rename or copy source documents, and you never create source-category subfolders. If a document or folder should exist, ask the user to place or create it, then record its actual path. A blocked write is expected: don't retry it and don't work around it.

## Tax-year folder rules (Manual §2)

1. All tax years use the same structure. There is no `CURRENT_YEAR`, `PRIOR_YEARS` or `POST_FILING` tree.
2. The current filing year is a status, not a location. `CURRENT_STATE.md` names the `03_TAX_YEARS/TY_YYYY/` folder being prepared.
3. Tax-year folders are permanent. `TY_2025/` stays `TY_2025/` after filing and in later years. Never move a year because it becomes a prior year.
4. User and source evidence lives outside `WORKING/`. Typical source categories:
   - `W2s/`: W-2 forms and related wage statements.
   - `1099s/`: 1099-INT, 1099-DIV, 1099-NEC/MISC and similar information returns. A brokerage composite 1099 may live in `Investments/` instead.
   - `Investments/`: 1099-B and composite brokerage statements, realized gain/loss reports, basis reports, trade confirmations, equity-compensation records, investment statements.
   - `Real_Estate/`: rental records, depreciation schedules, closing disclosures, mortgage and 1098 records, property tax, settlement documents, improvement records.
   - `Foreign_Assets/`: foreign retirement accounts, foreign brokerage holdings, foreign-account, PFIC, FBAR/FATCA support, foreign tax records.
   - `Retirement/`: 1099-R, 5498, IRA basis support, HSA and retirement records.
   - `Deductions_Credits/`: childcare, charitable, education, medical, energy and other deduction or credit support.
   - `Payments_Notices/`: estimated payments, extension payments, IRS and state notices, payment confirmations.
   - `Filing/`: filed federal and state returns, draft returns under review, tax-software output, e-file acceptance or rejection evidence, filing worksheets.
5. `WORKING/` holds your year-specific analysis. Create files there as needed: `DOCUMENT_INVENTORY.md`, `RETURN_RECONSTRUCTION.md`, `AMENDMENT_WINDOW.md`, `AMENDMENT_ANALYSIS.md`, `RECONCILIATION_NOTES.md`, `TAX_SOFTWARE_INPUT_MAP.md`, `FILING_CHECKLIST.md`, `REVIEW_NOTES.md`, `EXPECTED_CARRYFORWARDS.md`, `FILED_RETURN_SUMMARY.md`, plus other year-specific calculations or memos that don't belong in the global research folder.
6. The listed categories are recommended, not exhaustive. The user may have `K1s/`, `Child_Care/`, `Business/`, `Other/` or similar. Inventory the whole `TY_YYYY/` tree recursively. Never assume only the standard folders exist.
7. Don't require empty folders. A category exists only when documents need it, and the user creates it.
8. Use the tax year the evidence belongs to. A 2025 W-2 belongs in `TY_2025/` even if the return is prepared in 2026.
9. Long-lived original evidence stays with its originating year when practical (for example a rental acquisition closing package in `TY_2017/Real_Estate/`, even if 2017 is not under review). Long-term ledgers may point back to it.
10. Don't duplicate source documents. Reference the canonical file path in inventories, fact registers and ledgers.
11. Don't silently move or rename the user's files to fit the taxonomy. If documents are already sensibly organized, preserve them and record their actual paths. Reorganizing is optional and never a prerequisite for analysis.

Other workspace rules that matter here:

- Every quantitative fact in working notes points to a source document, with page, form or line when practical.
- Keep source documents, derived calculations, research authority and recommendations distinct.
- `04_LONG_TERM_RECORDS/` holds durable derived records, not a duplicate archive of annual source documents.

## Inventory procedure and completeness gate (Manual §11)

Run this before analyzing any tax year.

1. Identify every `03_TAX_YEARS/TY_YYYY/` folder relevant to the engagement.
2. Recursively list every file in each year folder. Exclude AI-generated `WORKING/` files when deciding what source evidence the user supplied.
3. Classify each source document by type and tax relevance, whatever subfolder the user chose.
4. Compare the source inventory with the filed or draft return in `Filing/` and, where available, IRS and state transcripts.
5. Identify missing forms or evidence.
6. Flag duplicated, amended, corrected or superseded forms.
7. Record whether each tax document appears to have been included in the filed or draft return.
8. Identify mismatches between source forms and the return.
9. Create or update `03_TAX_YEARS/TY_YYYY/WORKING/DOCUMENT_INVENTORY.md`.
10. Preserve each document's actual path so later analysis finds the evidence without chat history.

Documents to seek when relevant:

- full federal and state returns, schedules and worksheets;
- W-2s;
- 1099-B, 1099-DIV, 1099-INT, 1099-R, 1099-NEC/MISC;
- 1098 series forms;
- K-1s;
- Forms 5498 and 5498-SA;
- brokerage realized gain/loss and cost-basis reports;
- trade confirmations, when basis or tax-lot identification matters;
- RSU, ESPP and option records;
- prior-year capital-loss and other carryforward worksheets;
- IRS and state transcripts;
- estimated-tax and extension-payment records;
- IRS and state notices;
- home Closing Disclosure or settlement statement;
- property-tax statements;
- mortgage statements and points or origination records;
- charitable substantiation;
- HSA and IRA records;
- child and dependent-care records, when relevant;
- business or rental records, if applicable;
- foreign-account, foreign-asset, PFIC, FBAR/FATCA and foreign-tax records, when applicable.

**Folder flexibility.** A document is not missing merely because it isn't in the recommended subfolder. Search the whole applicable `TY_YYYY/` tree before concluding that evidence is absent.

**Completeness Gate.** You may do provisional analysis with incomplete documents, but never present an unsupported result as final. Every provisional conclusion names the missing evidence that could change it.

## Writing the inventory

For each document record: canonical path; document type; tax year it belongs to; issuer; masked identifiers only (never full SSNs or account numbers); status (original, corrected, amended, superseded, duplicate); included in the filed or draft return (yes, no, unknown); mismatch notes. Add a "Missing or unconfirmed" list, each with the question it blocks, and ask the user to supply the documents. Add anything blocking to `00_CONTROL/OPEN_QUESTIONS.md`, and put a missing document that could invalidate analysis at its place in `00_CONTROL/PRIORITY_CHECKLIST.md`.
