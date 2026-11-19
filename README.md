# Tax advisor agent

An AI tax advisor that works in a household's multi-year U.S. tax files, packaged as an agent for Claude Code and OpenCode. The coding tools block writes to files it must not change, and a live eval harness grades its work. Every failure is published with its cause.

## Where it comes from

My family didn't want to pay a company to do our taxes, and I thought I could build an agent to do them. The first version was a single prompt: an 8,400-word instruction file that the AI read at the start of every session, plus copy-paste prompts for each task. It told the AI to keep long-term strategy apart from the annual filing, check the law again for each tax year, write every fact to a file instead of leaving it in chat, and never edit the source documents.

This repo is the expanded version, built so other people can use it too. The prompt became an agent you can install into a tax folder: a short core plus eight skills. The coding tools block the writes the prompt forbids, so that rule no longer depends on the model obeying, and an eval harness and tests check whether the agent follows the rest. None of my family's data is here. Everything uses a fictional household, Avery and Jordan Sample, in `examples/sample-household/`. The original prompt, with our details taken out, is `docs/operating-manual.md`.

## Results

Each tool ran 11 scenarios, 3 times each. Every run starts from a fresh copy of the sample household, with no one to answer questions.

| Run | Agent model | Pass | Fail | Inconclusive |
|---|---|---|---|---|
| Claude Code 2.1.319 | Opus 5.5 (default) | **24 / 33** | 3 | 6 |
| Claude Code 2.1.317 | Sonnet 5.5 | **26 / 33** | 4 | 3 |
| OpenCode 2.1.4 | GPT 6.1 Sol | **26 / 33** | 3 | 4 |

The Opus run is at commit `61b8936`, the shipped agent. The Sonnet and OpenCode runs are at `4009d6e`, which differs only in one skill's ledger rules and the default Claude model.

What the 23 non-passes were:

- **Agent mistakes (5):** OpenCode never concluded that the $3,000 of mortgage points was deductible (3 runs). Sonnet carried 1,500 into 2026 instead of 0. Sonnet's engagement charter contradicted its own open approval request. Opus made none.
- **Check limitations (5):** right answers that a deterministic check could not read, all 3 of Opus's failures among them. They stay unfixed because the project allowed one fix round, and it was already used.
- **Inconclusive (13):** asked to edit a protected file, the agent refused, as its rules say, so the lock had nothing to block.

Every run and its cause is in [`evals/results/SUMMARY.md`](evals/results/SUMMARY.md), next to the rows and transcripts.

## How the evals work

- **Checks grade the files the agent wrote.** What it says in chat doesn't count. Deterministic checks match values and dates against an answer key, which was checked against the Internal Revenue Code, regulations and IRS publications. An Opus judge grades meaning, and a pass must quote a file the agent wrote.
- **Nothing protected may change.** Every file outside the write allow-list is hashed before and after each run. Any change fails the run.
- **Results are tied to versions.** Each row records hashes of the agent, installer, checker and sample data. A run is reused only if all of them match.
- **One bounded fix round.** Failures from the first full runs were classified as agent mistakes or check problems. Check problems were fixed once, and the final runs were published as they came out.
- **Transcripts are scanned before saving.** Paths, timestamps and IDs are normalized. A transcript with anything that could be personal is withheld, and only its row is kept.

Before publishing, several agents reviewed the code adversarially, followed by two rounds of rechecks.

## What is enforced and what is only instructed

**Enforced by the tools:**

- The agent may write only `CURRENT_STATE.md`, `00_CONTROL/`, `01_STRATEGY/` (never the seed), `02_RESEARCH/`, `04_LONG_TERM_RECORDS/` and each year's `WORKING/` folder. Source evidence, the strategy seed, root config files and the agent's own configuration are protected.
- No shell, sub-agents or MCP tools.
- No reads outside the workspace (a Claude Code hook; OpenCode's `external_directory` is denied).
- Web fetches only to listed official sites. Web search is off in OpenCode.
- The Claude Code hook fails closed: any error blocks the call.

**Instruction only:** the approval gates, research labels, treating web pages and documents as evidence rather than instructions, keeping household identifiers out of search queries, and not claiming a save or filing without evidence.

## How the guardrails were tested

- Unit tests run the Claude hook against tables of protected and allowed paths: case variants, Unicode look-alikes, hard links and symlinks to the seed, `..` and absolute patterns in globs, and padded paths.
- A small evaluator applies OpenCode's documented rule order to the rendered rules over the same tables.
- In the live evals, the lock blocked all 5 writes the agents attempted in the final runs, and all 3 in an earlier baseline run.

## Before you use real documents

Everything the agent reads goes to your model provider. Before you put a W-2, 1099 or brokerage statement in the folder, black out SSNs, ITINs, account numbers, dates of birth and PINs; the last four digits are enough for the agent to tell documents apart. Redact the text itself, not just the picture: a black box drawn over a PDF often leaves the text underneath readable. Then check your plan's model-training setting (ChatGPT's is on by default), and don't use free OpenCode models that may keep data for training.

## Quick start

Needs `uv` (Python 3.12 or later) and Claude Code 2.1.295 or later, or OpenCode (tested on 2.1.4).

Tested with GPT 6.1 Sol (`openai/gpt-6.1-sol`) in OpenCode, and with Claude Opus 5.5 (the agent's default) and Claude Sonnet 5.5 in Claude Code. The OpenCode agent uses whatever model the session has selected, and other models are untested.

```sh
cp -R examples/sample-household ~/tax-sample
uv run scripts/install.py ~/tax-sample
cd ~/tax-sample
claude --agent tax-advisor
```

For OpenCode, start `opencode` in that folder and switch to the `tax-advisor` agent.

To run the evals, put a denylist of your own private terms in `~/.config/tax-agent/denylist.txt`; the privacy scan refuses to run without one. Then:

```sh
uv run evals/run.py --tool claude --model sonnet --scenario all --runs 3
```

## Limitations

- Three runs per scenario is a small sample: 2/3 and 3/3 are not a reliable difference.
- OpenCode research is fetch-only. IRS site search and congress.gov returned HTTP 403, and PDFs cannot be read.
- Claude Code's web search accepts any query, so only an instruction keeps household data out of searches.
- The Claude hook catches a hard link only to the seed. OpenCode's rules see paths, not links. Neither catches a path swapped between the check and the write.
- OpenCode's `TY_????` rule can't require digits.
- The Claude hook needs `python3` 3.8 or later on `PATH`.

The full list is in `docs/architecture.md`.

## Not tax advice

This is a software project tested on a fictional household. It is not tax, legal or financial advice.
