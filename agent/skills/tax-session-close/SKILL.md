---
name: tax-session-close
description: Load when ending a session, before context compaction, on handoff to another agent, or whenever the workspace must be left restart-ready. Use it to write or refresh CURRENT_STATE.md from its template and to run the end-of-session persistence checklist so that a different agent with no access to the conversation can resume accurately.
---

# Tax session close and CURRENT_STATE.md

Use this skill when a session is ending or being handed off, and any time `CURRENT_STATE.md` is missing or stale. The goal: a different agent with no access to this conversation can resume accurately from the workspace alone.

Write only to `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/` (never the seed), `02_RESEARCH/`, `04_LONG_TERM_RECORDS/` and `03_TAX_YEARS/TY_YYYY/WORKING/`. Preserve source documents in their existing tax-year evidence folders. Don't move or rename them. A blocked write is expected: don't retry it and don't work around it. If you can't write at all, say so now and give the exact content to save elsewhere.

## CURRENT_STATE.md (Manual §20)

`CURRENT_STATE.md` is the compaction-proof restart file and the pointer that tells a fresh session which permanent tax-year folder is active. Build it from `templates/CURRENT_STATE.md`. Keep it short enough to read at the start of every session. Put detail in the files it points to.

Update it whenever the engagement stage, governing strategy version, active filing tax year, open deadline, blocking issue or next actions materially change.

Changing the active filing year updates the pointer in `CURRENT_STATE.md`. It never requires moving or renaming the underlying `TY_YYYY/` folder.

Fill the fields from the files, not from memory:

- the research-as-of date from `02_RESEARCH/LAW_REFRESH_CURRENT.md`;
- the strategy status (exactly one of `Seed`, `Proposed` or `Approved`, by the status rule in `tax-strategy-discovery`) and the approved version from `01_STRATEGY/`;
- the amendment status and deadlines from each year's `WORKING/AMENDMENT_WINDOW.md`;
- the carryforwards and basis from `04_LONG_TERM_RECORDS/`, using reconciled figures only;
- the next exact actions as concrete steps someone else can start without asking you anything.

## End-of-session persistence checklist (Manual §28)

Before ending the session, make the workspace sufficient for a different agent to resume.

1. Update `CURRENT_STATE.md`.
2. Update `00_CONTROL/FACT_REGISTER.md`, `OPEN_QUESTIONS.md`, `PRIORITY_CHECKLIST.md` and `SOURCE_LEDGER.md`, plus any affected strategy or research files.
3. For every tax year worked on, update the appropriate files under `03_TAX_YEARS/TY_YYYY/WORKING/`.
4. Leave source documents where they are, in their existing tax-year evidence folders. Don't silently move or rename them. If a document needs to be placed or moved, ask the user.
5. If durable carryforward, basis or foreign-asset information changed, update the applicable `04_LONG_TERM_RECORDS/` ledger only after reconciliation. Leave an unreconciled figure in the year's `RECONCILIATION_NOTES.md`, marked blocking.
6. Record new decisions and why they were made.
7. Do not leave a durable fact, deadline, carryforward, assumption, strategy change, source path or unresolved material question only in chat history.

Then verify. Re-read each file you say you updated and confirm it exists and holds the change. Never tell the user something was saved without that evidence. State exactly which files changed.

## Persistence caveat

A file existing is not proof that it will survive to the next session. If the environment may not preserve the workspace (a temporary or sandboxed environment), or if you don't know whether it does, say so in your closing message and require the user to export or store the workspace externally before the session ends.

## Closing message

Tell the user briefly:

- what you changed (files);
- the engagement stage and the active year;
- the top unresolved items and any deadline that is close;
- any approval you are waiting for (strategy baseline, amendment plan, filing readiness);
- the next exact actions;
- the export reminder, if it applies.
