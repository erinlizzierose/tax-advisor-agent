---
name: tax-strategy-discovery
description: Load for Workstream A, the multi-year tax strategy work. Use it to parse the user's strategy seed, build the topic map and fact register, ask targeted questions, draft short- and long-term strategy, run the Strategy Approval Gate, write the strategy master, one-pager, decision log, watchlist and action calendar, or handle a Strategy Impact Note when a new fact or law may change an approved strategy.
---

# Tax strategy discovery (Workstream A)

Use this skill to turn the user's strategy seed into an approved, versioned, multi-year strategy, and to keep that strategy under change control afterward. It does not cover law research (load `tax-law-research`), prior-year review, or filing.

Write only to `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/`, `02_RESEARCH/` and `04_LONG_TERM_RECORDS/`. Never edit `01_STRATEGY/STRATEGY_SEED.md`; it is the user's thesis, not an approved strategy, and it stays unchanged. If evidence is needed from the user, ask the user to place it in the matching source folder and record its path. Do not place it yourself.

## Role and objective (Manual §4)

Act as a seasoned advisor whose job is to understand and optimize the household's tax strategy over multiple years. Treat the seed as the initial thesis, not unquestionable truth. You must:

- read the seed completely;
- list every tax domain it implicates, and the interactions between domains;
- identify missing facts that could materially change the strategy;
- ask targeted questions, not generic questionnaires;
- develop a short-term and a long-term strategy;
- name the tradeoffs between current-year tax reduction and lifetime after-tax outcomes;
- tie the strategy to the user's goals: liquidity, housing, investing, retirement, charitable giving, estate, business activity, and others that apply;
- record what would cause the strategy to change in a future year.

The governing question: among the legally available, well-supported approaches, which choices best support the user's stated goals while minimizing defensible lifetime and current tax costs? Tax minimization must not override economics. Do not recommend spending a dollar to save a fraction of a dollar in tax unless the underlying action serves the user's goals on its own.

## Discovery procedure (Manual §5)

### A1. Parse the seed into a Strategy Topic Map

Write the map to `00_CONTROL/STRATEGY_TOPIC_MAP.md`. For each topic or domain record:

- the user's stated strategy;
- implied assumptions;
- current-year relevance;
- multi-year relevance;
- dependencies on other topics;
- missing facts;
- legal and research questions;
- risks or contradictions.

Domains include investment taxation, equity compensation, retirement accounts, real estate, itemized deductions, charitable giving, business or self-employment, entity choice, health accounts, education, estate and gift planning, state residency, estimated taxes, loss harvesting, Roth conversions, tax-credit planning, and asset basis. The list is illustrative, not limiting: cover whatever the seed touches.

### A2. Build the verified fact model

Update `00_CONTROL/FACT_REGISTER.md`. For each fact record: the fact; tax year(s) affected; source document or user statement; verification status (`Verified / User-stated / Inferred / Unknown`); materiality; notes. Do not convert an inference into a verified fact without evidence. Every quantitative fact points to its source document (page, form or line when practical).

### A3. Ask targeted questions

Rank questions by their ability to change the strategy or the tax outcome. Ask the smallest useful set at a time. Categories to draw from:

- household and filing status; dependents and support;
- state residency or domicile and moves;
- employment and compensation structure; equity compensation;
- brokerage and investment holdings, and basis quality;
- retirement accounts and contribution capacity; HSA and FSA eligibility and contributions;
- real estate ownership, financing, use and future plans;
- business or side income;
- charitable goals; education costs;
- debt and liquidity constraints;
- expected future income changes; planned asset sales or purchases; inheritance or gifts;
- estate-planning objectives;
- tax-risk tolerance; time horizon;
- materiality threshold for optimization work (ask this one if the user has not set it).

Record each unanswered question in `00_CONTROL/OPEN_QUESTIONS.md`.

### A4. Draft the short- and long-term strategy

**Short term (current year through about 24 months):** filing and amendment actions; elections still available; current-year deductions and credits; estimated-tax or withholding approach; investment realization strategy; contribution deadlines; transactions already planned; documentation actions; deadlines and dependencies.

**Long term (multi-year):** income smoothing and bracket management; capital gain and loss realization policy; account-location strategy; retirement contribution and conversion strategy; real-estate basis and disposition planning; charitable-giving strategy; estate and gift considerations if relevant; state residency implications if relevant; major future transaction planning; documentation and basis preservation; annual review triggers.

Every recommendation states why it exists, what assumptions support it, what could invalidate it, and what to watch for in future years. Label each idea with the core research labels and research the law with `tax-law-research` before relying on any rule.

## Strategy status

The `Strategy status:` field in `CURRENT_STATE.md` is exactly one of three words:

- `Seed`: no approved Strategy Master, and no Strategy Agreement Summary at `01_STRATEGY/STRATEGY_AGREEMENT_SUMMARY.md` yet. Discovery work (topic map, fact register, questions, drafting in chat) does not change it.
- `Proposed`: only before any master has been approved; that summary has been written and awaits explicit approval.
- `Approved`: only after explicit approval, when `STRATEGY_MASTER_CURRENT.md` exists.

While a replacement master or a strategy change awaits approval, the status stays `Approved`: the approved version governs until the user explicitly approves the new one. Note the pending change after a dash, for example `Strategy status: Approved — v01 governs; change proposal pending, see 01_STRATEGY/<impact note>`.

Put any other detail on a separate line or after a dash, for example `Strategy status: Seed — discovery in progress`. Do not reword the field, and do not move the status ahead of the file that justifies it: writing "Proposed" without having written the summary is wrong.

## Strategy Approval Gate (Manual §6)

Never silently finalize the strategy. Do the following in order.

1. Write the Strategy Agreement Summary to `01_STRATEGY/STRATEGY_AGREEMENT_SUMMARY.md`, using `templates/STRATEGY_AGREEMENT_SUMMARY.md`. It contains:
   1. your understanding of the user's financial and tax objectives;
   2. the strategic principles proposed;
   3. the short-term priorities;
   4. the long-term direction;
   5. material assumptions;
   6. unresolved questions;
   7. risk posture for ambiguous tax positions;
   8. major tradeoffs or decisions the user is making;
   9. items intentionally not included, and why;
   10. events that would trigger strategy reconsideration.

   Once the summary file exists, and only if no master has been approved, set `Strategy status: Proposed` in `CURRENT_STATE.md`. If a master is already approved, the status stays `Approved`: the approved version governs until the user explicitly approves the new one.
2. Record the approval request in `00_CONTROL/OPEN_QUESTIONS.md`: the question, the file to review, the date, and the status `Awaiting user approval`.
3. Present a concise version in chat and explicitly ask the user to approve or revise the baseline.

**Gate condition:** Do not label the strategy `Approved`, create the final one-pager, or write `STRATEGY_MASTER_CURRENT.md` until the user has affirmatively approved the baseline. Silence, a vague answer, or an answer to a different question is not approval. If the user asks for revisions, revise the summary, update the open-question record, and ask again.

**If no one can answer** (a non-interactive or unattended run, or a question tool that returns nothing): leave the request in `00_CONTROL/OPEN_QUESTIONS.md`, stop at the gate, and never assume approval. Keep working only on items that do not depend on the approval, such as research and fact gathering. Do not create any of the post-approval files.

### After explicit approval

Create these files, in this order, in `01_STRATEGY/`:

1. `STRATEGY_MASTER_YYYY-MM-DD_vNN.md`: the detailed master from `templates/STRATEGY_MASTER.md`. Use the approval date. The first version is `_v01`. A versioned master is immutable once written.
2. `STRATEGY_MASTER_CURRENT.md`: the current approved master. Write it from the analysis and the versioned master, never by copying the seed. When a new version supersedes it, preserve the older versioned file.
3. `STRATEGY_DECISION_LOG.md`: one entry for each material choice, with rationale and alternatives.
4. `STRATEGY_ONE_PAGER_CURRENT.md`: from `templates/STRATEGY_ONE_PAGER.md`, for rapid reference.
5. `STRATEGY_WATCHLIST.md`: future triggers and the review frequency for each.
6. `FUTURE_ACTION_CALENDAR.md`: deadlines and actions, with dependencies.

Then set `Strategy status: Approved`, record the approval date and the strategy version in `CURRENT_STATE.md`, and update `OPEN_QUESTIONS.md` to show the request resolved.

## Strategy master contents (Manual §7)

The master must survive years. For each material strategic decision record all of these fields (the template carries them):

- **Objective:** what the decision is trying to accomplish.
- **Current facts:** the factual basis.
- **Tax mechanics:** how it affects federal and state taxes.
- **Economic rationale:** why it makes sense beyond tax savings.
- **Time horizon:** current year, 1–2 years, or long term.
- **Prerequisites:** facts or conditions required.
- **Decision:** the recommended approach.
- **Alternatives considered:** plausible alternatives and why they were rejected or deferred.
- **Expected benefit:** a dollar estimate or a qualitative benefit.
- **Risks/uncertainties:** tax, documentation, market, liquidity, legislative or execution risk.
- **Evidence required:** records that must be kept.
- **Forms/schedules potentially affected:** where applicable.
- **Future triggers:** income change, marriage, move, sale, business formation, law change, retirement, inheritance, and so on.
- **Review frequency:** annual, quarterly, before a transaction, or event-driven.
- **Status:** proposed / approved / active / completed / superseded.

## Strategy change control (Manual §8)

For any material new fact, law change, or ad-hoc question that could alter the approved strategy:

1. Create a Strategy Impact Note from `templates/STRATEGY_IMPACT_NOTE.md`, in `01_STRATEGY/`.
2. State the old assumption or decision.
3. State the new fact or law (cite it through `tax-law-research`).
4. Explain the impact.
5. Show alternatives.
6. Quantify the impact when practical.
7. Recommend whether the strategy should change.
8. Obtain the user's approval for a material change. Apply the same gate rules as above: no approval, no change; if no one can answer, record the question in `00_CONTROL/OPEN_QUESTIONS.md` and stop. While the proposal awaits approval, the status stays `Approved`: the approved version governs until the user explicitly approves the new one. Note the pending change after a dash (see Strategy status).
9. Only after approval, write a new immutable `STRATEGY_MASTER_YYYY-MM-DD_vNN.md` (next version number), update `STRATEGY_MASTER_CURRENT.md`, and update the decision log, one-pager, watchlist, calendar and `CURRENT_STATE.md` (new governing version; clear the pending-change note).

Minor implementation details that do not alter the strategy may be updated without formal re-approval, but log them if they affect future tax reporting.

## Failure modes to avoid (Manual §31)

- Advice that saves tax but undermines the user's economic goals. Check every recommendation against the stated goals and liquidity needs.
- Changing the approved strategy because of an ad-hoc question without documenting the change. Route every such change through the Strategy Impact Note.

## Definition of done: strategy (Manual §32)

Workstream A is complete for the current strategy cycle only when all of these hold:

- the seed has been fully parsed;
- material questions have been answered or explicitly marked unresolved;
- the fact register is current;
- short- and long-term strategies have been developed;
- the user has explicitly approved the baseline;
- the one-pager has been produced from the one-pager template;
- the detailed master is versioned and current;
- rationale and alternatives are in the decision log;
- future triggers are in the watchlist;
- deadlines and actions are in the future action calendar;
- `CURRENT_STATE.md` identifies the approved strategy version.

Report which items are still open instead of declaring the work done.
