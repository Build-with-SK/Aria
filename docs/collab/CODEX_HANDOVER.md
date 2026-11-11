# Handover to Codex

From Claude Code, 2026-10-11. Read this, then `docs/collab/WORK_PLAN.md`, then
`AGENTS.md`. The plan is the authority; this file is how to get started.

## Where you work

- Your folder is `C:\Users\sound\Documents\aria-codex`, on branch `codex/work`.
  It is a git worktree of the ARIA repo. Do not open or edit
  `Documents\trading-intelligence-system`; that is Claude Code's folder and it
  holds 87 uncommitted files that are not yours.
- Your folder has no virtual environment, no `.env` and no live data. Run
  Python with the main folder's interpreter, from your folder:

  ```
  ..\trading-intelligence-system\venv\Scripts\python.exe -m pytest tests/test_v5.py -q -p no:cacheprovider
  ```

  Imports resolve to your copy of the code and `tests/conftest.py` redirects
  data writes away from production. Do not install packages into that
  environment; ask in your log.
- Do not start the backend, the pipeline or the frontend dev server. One
  runtime exists and it lives in the main folder.
- Do not push, and do not touch `main` or `aria/integrity-upgrade`.

## What ARIA is, in five lines

- A trading intelligence system: FastAPI backend (`backend/main.py`, ~4,900
  lines), React frontend (`frontend/`), Python packages under `src/`.
- A prediction ledger records every call; calibration and evaluation grade
  them. Measured so far: no source shows an edge over always-long.
- A paper desk proposes trades; a human approves. Live execution exists behind
  a kill switch, risk profiles and a pre-trade risk gate.
- V5 is a 41-module research engine (`src/v5`). The brain (`src/brain`) is a
  local-model daemon with a ChromaDB memory and read access to the owner's
  private notes.
- The machine is a Windows laptop that sleeps most of the day, which is why
  much of ARIA's data is stale. Stage 1 exists to fix that honestly.

## Your areas

`frontend/`, `src/v5`, `src/data`, `src/research`, `src/evolution`,
`src/training`, `src/backtesting`, `src/intelligence/evaluation.py`, `scripts/`,
repo hygiene and docs. Everything else, including `backend/main.py`, is
Claude's: request changes in your log.

## Your first tasks

Stage 0 (committing the backlog) is not done yet, so your branch is behind the
main folder. Start with the two tasks that do not depend on it:

1. **S1.6 — repo hygiene report.** List stray root-level files with a
   keep/remove recommendation. Report only. Check the hidden entry points in
   the plan's section 4 before calling anything dead.
2. **S1.7 — `core/bus` options note.** `subscribe()` exists and nothing
   subscribes. Say what you would do and why, in under a page.

When the plan shows S0 done, run `git merge aria/integrity-upgrade`, then take
S1.5 and the Stage 2 diagnosis tasks.

## Every task

1. `git merge aria/integrity-upgrade`
2. Log line in `docs/collab/log-codex.md`: task id and the files you expect to
   touch.
3. Do the work. Stay inside your areas.
4. Run the tests for what you touched.
5. Commit on `codex/work` with a message that says what is now true.
6. Log entry: what changed, what you measured, what is open, what Claude needs
   to know or wire up.

## Things that have bitten before

- Dead-code claims from an import graph were wrong three times. See the hidden
  entry points list.
- A bare ticker has no meaning without its namespace: in the master namespace
  "BA" is BAE Systems, not Boeing.
- A reported accuracy number means nothing without its sample and source.
  Never state an improvement without the before/after measurement.
- Frontend: inline styles only, the existing CSS variables, no new npm
  packages.
- Ollama calls use `urllib.request` only.

## When unsure

Write the question in your log and move to another task. Do not guess on
anything that touches orders, auth, risk limits or the owner's private notes.
