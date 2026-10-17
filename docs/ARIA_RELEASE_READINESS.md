# ARIA release readiness — 2026-10-17

Candidate: branch **`claude/aria-completion`**, worktree
`.claude/worktrees/aria-completion` (HEAD at the commit that adds this file;
code last changed in `e5903f1`). Release owner: Claude Code. Reviewer of record
for the trading path: Codex (WORK_PLAN §2.6).

## Verdict

**Ready as a PAPER-only release candidate for owner review. Not ready to merge
or deploy yet.** Three things stand between this branch and
`aria/integrity-upgrade`. All three are the owner's call or Codex's, not code:

1. **Codex's re-review** of my trading-path commits (`e7282f4`, `f5f2bf7`,
   `c49217c`, `33eb7a0`, and the fixes in `4abcb84`). I reviewed and merged all
   of Codex's commits. The rule is that each side's safety-critical code is
   read by the other before it merges.
2. **The owner's yes** to merge into `aria/integrity-upgrade` and restart the
   backend. The restart stops and starts the desk while the owner's 2-day paper
   test is still running (until 2026-10-18 17:13).
3. **A paper-account validation session** the owner starts (gate G7). Everything
   below was proven against fake brokers driving the real adapter code, not
   against Alpaca's paper venue.

It is **not** a candidate for live money. No trading edge is measured (gate
G12), and live trading stays disabled by design.

## Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| G1 Full test suite | **PASS — 3457 passed, 9 skipped, 0 failed, EXIT 0** (401.9 s) | `venv\Scripts\python.exe -m pytest tests -q -p no:randomly` on `4abcb84` + docs; floor in WORK_PLAN is 3251 |
| G2 Trading-path focus suites | **PASS** — the 930-test desk/reflex/order/risk/execution/session/security/durability group found 4 failures on the merged tree (2 integration defects: audit F-10 and the R1 fix in `4abcb84`); after the fixes every one passes inside G1 | per-group runs in this session |
| G3 Frontend | **PASS** — `verify-launch-routes`, `verify-technical-dashboard`, `verify-execution-status` (7 states; unknowns never shown as fills), staging `vite build`, `verify-shell`: all EXIT 0 | run from the worktree; output in scratch, nothing published, `frontend/dist` untouched |
| G4 API sweep (every GET route, isolation, network refused) | **PASS for the trading path** — 158 routes: 138 × 200, 13 × 422 (required query missing), 4 × 404 and 2 × 400 (literal path templates), **1 timeout: `/api/brain/memories` 25 s** (cold vector store; not trading) — **no 5xx** | `ARIA_LAUNCH_SWEEP=1 pytest tests/test_launch_get_sweep.py`: 3 passed |
| G5 Static analysis / types | **Run; no blocking defect.** ruff: no undefined names or syntax errors; 2 real merge artifacts found and fixed. Remaining: 239 hygiene findings and 139 mypy lines on the order path (typing debt) | audit §5, O-11 |
| G6 Database migrations | **PASS** — run against copies of live `aria_core.db` and `universe.db`: current, idempotent, integrity ok, rows unchanged; copies deleted | audit §5 |
| G7 Paper-trading workflow | **PASS on safe infrastructure; NOT VALIDATED on the paper venue.** End-to-end with fake brokers: approve → claim → send → timeout → lookup → settle; reflex fire → UNKNOWN → no second order; real ASGI server + restart (`test_final_runtime`); desk exit through the queue | `test_order_settlement`, `test_order_safety_review`, `test_final_execution_reliability`, `test_final_runtime`, `test_reduce_only`. **Blocked:** a paper-venue session would place orders during the owner's 2-day test |
| G8 Crash recovery and reconciliation | **PASS in tests** — torn queue/playbooks/day-state/positions/config; crash during save; a killed lock holder; a second process holding the queue or the desk; UNKNOWN surviving restart and never resent; reconciliation by client id, then by broker id | `test_queue_durability`, `test_state_store_durability`, `test_durable_state`, `test_desk_single_instance`, `test_final_execution_reliability` |
| G9 Live trading disabled | **PASS, measured live 20:28** — mode PAPER since 2026-09-23; kill switch off; desk ceiling PAPER in every mode; autonomy needs `ALPACA_PAPER` exactly `true` and a paper endpoint | `launch_check --base-url http://localhost:8000`; `src/trading/access.py` |
| G10 Secrets | **PASS** — no credential in tracked files; `.env`, research keys, kill switch and order records are git-ignored; no key is logged | audit §5 |
| G11 Independent review | **PENDING** — Codex's `09f2158`, `0a1b21a` and `048f47b` reviewed by me (approved with fixes); my trading-path commits await Codex | `docs/ARIA_HANDOFF.md` |
| G12 Measured trading edge | **NOT MET** — paper test $10,008.38 → $10,008.38, 0 desk executions so far | `launch_check` |
| G13 Always-on runtime | **NOT MET** — backend down 08:59→20:13 today; pipeline 1 of 10 runs complete since July; ML predictions from 2026-05-31 | `launch_check`; launch-sprint log |

## What changed in this candidate (vs `aria/integrity-upgrade @ b5b47f2`)

- S1.3 order safety (deterministic client ids; found-only settlement; claim
  before POST; unresolved orders block every lane; owner release) — previously
  unmerged.
- Codex `09f2158`, `048f47b`: durable queue claims and audit, fail-closed
  adapter reads, idempotent protective orders, desk exits through the durable
  queue, IBKR orderRef lookup, terminal partial-fill state, pending-entry DEFER.
- Codex `0a1b21a`: honest execution status UI.
- This mission: `src/core/durable.py` and fail-closed playbooks / day state /
  positions / desk config; single-instance desk; `OrderRefused`; the
  positions-unreadable fix; reconciliation by broker id; 503 for damaged state;
  `.gitignore` for runtime trading records; the merge fixes (sentiment
  fallback, duplicate function); the launch-sprint branch (sentiment
  unavailable ≠ neutral, repo-anchored scans, `launch_check`).

## Behaviour changes the owner should know before deploying

- After a restart, the desk refuses to run in a **second** backend process.
  `/api/desk/status` shows `instance_conflict` there.
- Any working (non-reducing) order resting in the account **defers every new
  entry** until it fills or is cancelled (`09f2158`, O-9).
- A queue, playbook or day-state file that is damaged now **stops** the
  affected lane, with 503 and a `*.corrupt-*` copy, instead of silently
  starting empty. Recovery: restore from the nightly `data/backups/desk_*.zip`.
  From this candidate on it also holds `execution/approval_queue.json`, its
  audit and the reconciliation log. Otherwise repair the file, using the
  append-only `approval_queue.audit.jsonl`. Never delete an UNKNOWN row to
  "unblock" a symbol: check the broker by client id and use the owner release.
- New runtime files: `data/execution/approval_queue.lock`,
  `approval_queue.audit.jsonl`, `order_reconciliation.jsonl`,
  `data/desk/desk_daemon.lock`, `data/desk/playbooks.json.lock` (all
  git-ignored).
- Queue ids for new rows are 32-hex (were 8); old rows still load.

## Deployment, when approved (not done)

1. Codex APPROVE on the trading-path commits, recorded in the handoff or its log.
2. `git merge --no-ff claude/aria-completion` into `aria/integrity-upgrade`
   (main folder). Tag the current HEAD `rollback/pre-completion-2026-10-17`
   first. Run the full suite there (floor 3457).
3. Stop and start the `ARIA Backend` task, preferably after the paper test ends
   on 2026-10-18 17:13. Confirm a single listener.
4. `python -m src.core.launch_check --root <repo> --base-url http://localhost:8000`.
   Expect PASS for runtime, owner refusal, PAPER mode, desk and broker; the
   desk status must show no `instance_conflict`.
5. Rebuild the frontend from the merged source if `0a1b21a` is to go live
   (staging build, verifiers, then copy into `frontend/dist`).
6. Rollback: stop the task, `git checkout` the rollback tag, restart. The
   state files stay readable by the old code (only new fields were added).

## Remaining risks (full list with acceptance criteria: audit §3 "Open")

O-1 no measured edge · O-2 runtime not always-on · O-3 paper venue not
exercised · O-4 IBKR untested · O-5 `live_guard` false UNKNOWN on non-paper OCO
(fenced file) · O-6 halt with unreadable positions cancels protective stops ·
O-7 per-row signal staleness · O-8 id-less UNKNOWN playbook cannot be released
· O-9 pending-entry DEFER is account-wide · O-10 POSIX queue lock has no
timeout · O-11 lint/type debt · O-12 dependency advisories untriaged ·
O-13 sklearn warning noise · O-14 S2.2/S2.3 blocked.
