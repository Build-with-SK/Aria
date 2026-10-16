# ARIA — two-agent work plan (Claude Code + Codex)

Written 2026-10-11. This file is the single source of truth for who does what.
Only the integrator (Claude Code, at the owner's direction) edits it. Codex
proposes changes in `docs/collab/log-codex.md`.

## 1. Seats and branches

| | Claude Code | Codex |
| --- | --- | --- |
| Folder | `Documents\trading-intelligence-system` | `Documents\aria-codex` (git worktree of the same repo) |
| Branch | `claude/work` | `codex/work` |
| Log | `docs/collab/log-claude.md` | `docs/collab/log-codex.md` |
| Role | builder + integrator | builder + reviewer |

`aria/integrity-upgrade` is the integration branch. Nobody builds on it
directly: work reaches it only by merge. Nothing is pushed to `origin` and
nothing merges to `main` without the owner saying so.

Only the main folder has the virtual environment, `.env` and the live `data/`
stores. Codex runs Python with
`..\trading-intelligence-system\venv\Scripts\python.exe` from its own folder
(verified 2026-10-11: imports resolve to the Codex copy, `tests/conftest.py`
redirects data writes away from production).

## 2. Rules that keep the two agents out of each other's way

1. **One owner per area** (section 3). To change a file in the other agent's
   area, write the request in your log; the owner of the area makes the change.
2. **`backend/main.py` is Claude's.** Codex writes the logic as a function in
   its own module and names the endpoint it needs in its log; Claude wires it.
   Endpoints are only ever added, never removed or renamed.
3. **Start of every task:** `git merge aria/integrity-upgrade` into your branch,
   then write one line in your log: task id, files you expect to touch.
4. **End of every task:** tests for the touched area pass, commit, then a log
   entry: what changed, what was measured, what is still open, what the other
   agent must know.
5. **Merge:** Claude merges the finished branch into `aria/integrity-upgrade`
   and runs the full suite there (3,004 passing on 2026-10-10 is the floor).
   A red suite is fixed before any other work starts.
6. **Review:** each merge is read by the other agent before the next one from
   the same author. A disagreement goes to the owner, not to a rewrite.
7. **One runtime.** Only the main folder runs the backend, the pipeline or
   anything that writes the real `data/`. Codex runs tests and scripts against
   test isolation only, and never starts a server on ARIA's ports.
8. **Each agent edits only its own log file.** That is why there are two.

## 3. Ownership

| Claude Code | Codex |
| --- | --- |
| `backend/` | `frontend/` |
| `src/brain`, `src/core`, `src/cognitive` | `src/v5`, `src/data`, `src/research` |
| `src/desk`, `src/execution`, `src/trading`, `src/risk` | `src/evolution`, `src/training`, `src/backtesting` |
| `src/auth`, `src/markets`, `src/scheduler`, `src/inference` | `src/intelligence/evaluation.py`, `scripts/` |
| `src/intelligence` (except `evaluation.py`) | repo hygiene, `docs/` (except `docs/collab/WORK_PLAN.md`) |

Tests follow the code they test. Anything not listed belongs to Claude until
this table says otherwise.

## 4. Invariants neither agent may weaken

- Nothing calls `approve_and_execute` autonomously. The brain proposes, the
  human approves.
- Kill switch, risk profiles, the pre-trade risk gate and the reduce-only
  invariant stay in force on every order path.
- A market whose session is UNKNOWN is never treated as OPEN.
- The vault is owner-only; `.env` and anything under the vault are never
  copied, quoted in a log, or committed.
- No claim of improvement without a measured before/after from
  `scripts/calibration_baseline.py --compare` or `src/intelligence/evaluation.py`.
- Before calling a module dead, check the hidden entry points: root `chat.py`,
  `src/scheduler/scheduler.py`, `src/dashboard/dashboard.py` (Streamlit), tests
  that import it, and frontend components named in planning docs.

## 5. Launch readiness — 2026-10-16, deadline 22:00 (owner's order)

The owner wants ARIA launch-ready tonight and both agents working until it is.
"Ready" means the gates below are MET AND MEASURED — not claimed. A gate that
cannot be met tonight is reported as open with its reason; nothing is lowered.

| Gate | Evidence it is met | Owner |
| --- | --- | --- |
| L1 Live runtime serves the integrated commit | backend restarted from integration HEAD; `/health`, `/ready` 200; process start time after the merge | Claude |
| L2 Every page works | each of the 8 routes (`/brain /research /market /portfolio /strategies /daily-report /track-record /system`) renders with no thrown error and handles empty/withheld/error API states; offline checks in `frontend/scripts/` | Codex |
| L3 Every GET endpoint answers | sweep of all GET routes in `backend/main.py` under test isolation; no 5xx, no unhandled exception | Claude |
| L4 Tests green | full primary suite ≥ 3251 passed on the final integration commit; frontend build + offline verifiers pass | Claude (suite), Codex (frontend) |
| L5 Nothing secret ships | no credential/token/key tracked in git history reachable from HEAD; `.env`, vault, `research_keys.json` untracked | Claude |
| L6 Safety unchanged | PAPER mode, live gate false, kill switch, pre-trade gate, reduce-only, approval queue all in force; protected-path fence clean | Claude |
| L7 Someone new can run it | README "Run ARIA" section: prerequisites, start, `/app/` path, owner sign-in, PAPER default, what is NOT validated (no measured edge) | Codex |
| L8 Repo tidy | S1.6 items marked "remove" are `git rm`'d (reversible); no stray backups tracked | Codex |

Codex task brief for tonight: `docs/collab/LAUNCH_CODEX.md`. Codex works on
`codex/work` in its own folder, first merging `aria/integrity-upgrade` (S0.2 for
its branch), commits per gate, and logs each in `log-codex.md`. Claude reviews
every Codex commit before merging; disagreements go to the owner. Only Claude
restarts the runtime (rule 7), once, at cutover, after both halves pass.

Out of scope tonight, stated so nobody claims it: a public multi-user launch
(accounts, legal/financial-advice review, hosting), any measured trading edge,
live-money trading. ARIA stays owner-only and PAPER.

## 6. Stages

S0.1 is DONE (2026-10-16, `10e0fc8..eb093cc`, suite 3251 green, pushed).

Facts below were measured on 2026-10-10. Re-check each one before acting on
it; part of the uncommitted backlog may already have changed it.

### Stage 0 — clean base (blocks everything; needs the owner's yes)

| Id | Owner | Task |
| --- | --- | --- |
| S0.1 | Claude | Commit the 87-file 2026-10-09 remediation on `aria/integrity-upgrade` in reviewable groups, full suite green. |
| S0.2 | Claude | Fast-forward `claude/work` and `codex/work` to the result. |

Until S0 is done Codex's folder is missing that work, so Codex starts only
with tasks marked *safe before S0*.

### Stage 1 — ARIA tells the truth about its own state

| Id | Owner | Task | Evidence it is done |
| --- | --- | --- | --- |
| S1.1 | Claude | "Market state age" and "signal book age" are the same file mtime in `src/brain/self_state.py`. Report each dataset's own age; ML age from the last real write. | Test with distinct mtimes. |
| S1.2 | Claude | Brain daemon start is a one-shot Ollama check 5 s after boot. Make it retry. | Test: Ollama appears late, daemon starts. |
| S1.3 | Claude | Live order path sets no `client_order_id`, so a submit timeout is recorded REJECTED. Set it and reconcile on timeout. Codex reviews before merge. | Fake-broker test for timeout then fill. |
| S1.4 | Claude | Refresh loops advance only while the laptop is awake (backend active 26% of hours). Add session-aware catch-up on wake. | Test with a simulated gap. |
| S1.5 | Codex | Frontend freshness panel: one row per dataset from `/api/data/freshness`, with age and the `pipeline_run` step status. Inline styles, no new packages. | Screenshot in the log. |
| S1.6 | Codex | Repo hygiene, *safe before S0*: list root-level stray files (`*.cpython-312.pyc`, `main_backup_phase5.py`, `main_phase4_backup.py`, old build prompts and reports) with a keep/remove recommendation each. Report only; removal after the owner agrees. | Table in the log. |
| S1.7 | Codex | `core/bus` has `subscribe()` and no subscriber. Write a short options note: remove, or name the first real subscriber. *Safe before S0.* | Note in the log; decision by owner. |

Owner actions in this stage: re-register the `TradingIntelligenceSystem`
scheduled task (the installed one still has the 2 h kill limit).

### Stage 2 — measurement that can be trusted

| Id | Owner | Task |
| --- | --- | --- |
| S2.1 | Codex | V5 logged 498 calls: 399 neutral, 0 bear. Find out why it never says bear (thresholds, module coverage, data gaps). Diagnosis first, no tuning. |
| S2.2 | Codex | The self-report's hit rate pools ~567 ledger events across sources. Make `src/intelligence/evaluation.py` (cluster-robust) the one number each source is reported with; hand Claude the function to call. |
| S2.3 | Claude | Wire S2.2 into the self-report and the API. |
| S2.4 | Codex | V5 fundamentals and news modules are not point-in-time, so no ensemble replay is possible. Scope what a point-in-time snapshot needs. |
| S2.5 | Claude | Reflex lane: 1,300 playbooks armed, 0 fired. Establish whether that is correct behaviour or a dead lane. |

### Stage 3 — always-on host

| Id | Owner | Task |
| --- | --- | --- |
| S3.1 | Claude | Move the 24/7 loops to the Mac mini per `docs/MAC_MINI_SERVER.md`; close the open owner actions listed there. |
| S3.2 | Codex | Frontend works against a remote backend host (no hardcoded localhost). |

### Stage 4 — search for edge (only after Stage 2 shows a trustworthy baseline)

| Id | Owner | Task |
| --- | --- | --- |
| S4.1 | Codex | Evolution lab runs against the Stage 2 evaluation; deflated-Sharpe gate unchanged. |
| S4.2 | Codex | Fine-tune prerequisites: dataset builder under `src/training`; nothing is promoted without the existing promotion gate. |
| S4.3 | Claude | Capital allocation and the desk consume only signals that passed S4.1. |

### Stage 5 — the bigger vision

Not yet written down. The owner describes it (ChatGPT is a good place to draft
it); Claude turns it into tasks here with an owner for each. No agent starts
Stage 5 work from a guess.

## 7. How this stays fast

- Stages 1 and 2 have Claude and Codex tasks that touch disjoint folders, so
  both run at once with no merge conflicts by construction.
- Small tasks, one commit series each, merged the same day. A branch that
  lives longer than a day re-syncs from integration every morning.
- Diagnosis tasks end in a note, not a refactor. The owner decides, then a
  build task is added.
- Safety-critical changes (order path, auth, risk gate) are the only ones that
  wait for the other agent's review before merge. Everything else is reviewed
  after merge.
