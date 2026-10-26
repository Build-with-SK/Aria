# Claude final architecture review — ARIA

Reviewer: Claude Code, independent release reviewer. Date: 2026-10-18.

## 1. What was reviewed

| Item | Value |
| --- | --- |
| Candidate | `codex/aria-final-integration` @ `edc2796` (= `codex/aria-operational-readiness` at review start). Runtime code `89f8f4a`, tests `a079dff`, then report and whitespace commits. |
| Ancestry | Contains `claude/aria-completion` (`659a75c`) and, rebased, every `codex/aria-final-verification` change (`2dd1f7c`→`b4c9942`, `a5e4043`→`1fc1e05`, …). The ML resume is `4ffda39` cherry-picked as `1fd6426`. 164 commits ahead of `main`, 60 ahead of `aria/integrity-upgrade`. |
| Later Codex work | `edfb5e6` (2026-10-18 15:36) on `codex/aria-operational-readiness`, reviewed in CLAUDE_SECURITY_REVIEW §4 and in ARIA_REVIEW_HANDOFF. Uncommitted Codex docs in its worktree were not reviewed. |
| Review worktree | `.claude/worktrees/aria-final-review`, branch `claude/aria-final-review` from `edc2796`. Review-only commits: one probe test file and these documents. No Codex file was modified. |
| Protected state | Nothing pushed, merged, deployed or restarted. No broker call. No production `data/` content read for review; the runtime venv was only scanned with pip-audit. |

Companion documents: CLAUDE_TRADING_SAFETY_REVIEW (invariants, F-01…F-16),
CLAUDE_ML_PIPELINE_REVIEW (F-04…F-08), CLAUDE_SECURITY_REVIEW (S-01…S-05),
ARIA_REVIEW_HANDOFF (Codex coordination), CLAUDE_RELEASE_RECOMMENDATION (gates).

## 2. System map and review depth

```
pipeline (main.py, scheduled task / POST /api/run)
  data download → features → signals.json, macro, sentiment → ML (batched, ml_store) → strategies
        │ files under data/ (signals.json TRACKED IN GIT, aged by mtime → F-10)
        ▼
backend (FastAPI, one process; role guard → owner/free/anon)
  ├─ approval path: /api/execute/approve (owner + step-up) ─┐
  ├─ desk daemon (SingleInstance) ── hunt cycle → AutoExecutor ─┤
  │      ├─ management tick: PositionManager (exits, healing, adoption) → reconcile_unresolved
  │      └─ reflex lane (playbooks, cross-process lock) → AutoExecutor._handle
  │                                                         ▼
  │                       OrderManager.execute — session gate → unresolved guard →
  │                       pre-trade risk (entries) / reduce-only (exits) →
  │                       durable claim (approval_queue, fsync + audit) → adapter
  │                                                         ▼
  │            ModeGatedBroker / PaperOnlyBroker (reads allowlisted) → Alpaca / IBKR adapters
  │                                                         (kill switch inside each adapter)
  └─ /api/trading/halt → src/trading/killswitch (kill switch, RESEARCH, cancel → F-01)
src/trading TradingPipeline + OrderEngine journal: tested, NO production caller
```

| Component | Depth | State |
| --- | --- | --- |
| Approval queue / durable claims / legacy quarantine | Line by line | Sound. Invariants 1-5 and 11 PASS. |
| Order manager (execute, settle, reconcile, protection) | Line by line | Sound for identity. F-02 (protection after a failed exit). |
| Alpaca adapter | Line by line | Correct timeout/4xx classification and partial-cancel mapping. Missing `client_order_id` in open orders feeds F-01. |
| IBKR adapter | Submit, cancel, lookup, status | F-15. Gateway validation BLOCKED. Session-local order ids are a documented gap. |
| Live gate / mode gate / reduce-only | Line by line | Sound. F-03 (requeue route). |
| Desk exit engine (PositionManager) | Line by line | F-02, F-09, F-16. A damaged state file holds management (correct). |
| Auto-executor / reflex | Fire paths | Sound. Gate 6 defeated by F-10. |
| Desk daemon / single instance | Lock, tick ordering | Single desk per machine PASS. Reconcile runs after the tick (cause of F-02). |
| Kill switch / halt | Line by line | F-01 (cancel step), F-13 (FLATTEN inert). |
| ML store / pipeline ML step | Line by line | F-04, F-05, F-06 (edfb5e6), F-08. |
| Data freshness (world model, gate 6) | Gate path | F-10. |
| Research HTTP / XML | Line by line + `edfb5e6` | S-02 fixed in `edfb5e6`. |
| Auth / role guard / readiness | Middleware, policy, readiness | S-03, F-12. |
| Scanner, sentiment, strategy modules, playbook arming, scheduler, DB migrations, frontend | **Not reviewed in depth.** Relied on the suite. `test_migration_freeze` (3 cases) and the browser acceptance test were **skipped** in my run (no production ledger / no staged build). | NOT TESTED by this review. |

## 3. Architectural inconsistencies

| Id | Severity | Finding | Recommendation |
| --- | --- | --- | --- |
| A-1 | LOW (latent) | **Two idempotency stores for one account.** `approval_queue.json` (claims, UNRESOLVED blocking) and `data/trading/orders.jsonl` (`OrderEngine`, its own halt on unsettled claims). Neither consults the other's in-flight orders. Harmless today because `TradingPipeline` has no production caller (`src/trading/orders.py:58-66`). | Before wiring `TradingPipeline`, make one store authoritative, or make each guard read both. Add a test that fails when a production caller appears without that. |
| A-2 | HIGH → F-10 | **Generated state is tracked in git.** `data/signals.json`, `ml_predictions.json`, `macro_data.json`, `trade_candidates.json`, `futures_signals.json`, `political_signals.json` are versioned, and the main checkout shows them modified. Any git operation rewrites live inputs to the desk and resets the mtime the freshness gate trusts. | Untrack them (owner decision). Age every gate input by row stamps. |
| A-3 | MEDIUM → F-02 | **Management-tick order.** Reconciliation runs after the exit engine and bracket healing, so the engine acts on claims the broker has already settled. | Reconcile at the start of the tick, and again after any self-cancel. |
| A-4 | MEDIUM → F-09 | **Desk state is not derived from the broker.** Tracked qty is a snapshot taken at adoption or entry, and healing trusts it. | Treat broker qty as authoritative every tick. Tracked state keeps intent only (stop, target, thesis). |
| A-5 | LOW | **Two order managers per process.** The backend's (approval ceiling LIVE_AUTONOMOUS) and the desk's (ceiling PAPER) differ only in their gate. They agree because every write meets the same queue lock. Correct, but easy to break by adding a third path. | Keep `test_session_bypass` / the source scans that pin every `submit_order(` call site. |
| A-6 | INFO | The literal invariant "nothing calls `approve_and_execute` autonomously" holds. The desk's armed auto-execute calls `OrderManager.execute` directly, behind gate 1-6, the paper gate and the mode ceiling. The real control is therefore the arming and gates, and F-10 weakens gate 6. | State this explicitly in ARIA_ARCHITECTURE so nobody reads the invariant as "no autonomous orders". |

Dead code: only `TradingPipeline` is confirmed unwired, and that is documented in the code
itself. I make no other dead-code claim: hidden entry points (root `chat.py`, scheduler
generator, Streamlit) were not swept here.

## 4. Operational and deployment review

"Local" = demonstrated on this machine in isolation. "Deployed" = on the target host after
cutover. A local pass is not deployed acceptance.

| Area | Local evidence | Deployed | Notes |
| --- | --- | --- | --- |
| Startup | Suite covers lifespan ordering and the route-guard recheck being fatal at startup (`test_security_execution_auth`). | NOT TESTED | `/ready` is NOT READY without an owner credential (good). |
| Shutdown | Codex: real POSIX process shutdown/restart in WSL (`test_posix_runtime`, skipped on Windows in my run). | NOT TESTED | |
| Process supervision | Desk watchdog (15 min), APScheduler grace 1 h, SingleInstance retry every 300 s. | NOT TESTED | The scheduled task's battery/2 h limits are recorded in memory as owner actions. 7-day uptime unmeasured (Codex blocker). |
| Restart recovery | Queue claims survive restart (tests). The first management tick runs immediately. Unresolved rows block entries until reconciled. | NOT TESTED | Restart with real working orders is part of the paper acceptance plan. |
| Single instance | Desk: OS lock. Pipeline: **none** (F-08). | NOT TESTED | |
| Backups | Desk nightly zip includes `data/execution` (queue, audit, reconciliation). `scripts/backup.py` server backup: `edfb5e6` hardens restore but aborts the whole archive on one failed SQLite snapshot (security review §4). | NOT TESTED | A restore drill on the target host has not been done. |
| Configuration validation | Mode, limits and risk profile read with fail-closed defaults; live requires out-of-process switch + exact `"true"` env + endpoint check. | NOT TESTED | |
| Health / readiness | `/health` (liveness), `/ready` (owner credential, data writable, session store, disk). | NOT TESTED | **F-12 (LOW)**: readiness ignores execution-state integrity: queue readable, desk instance lock held, unresolved count, kill switch state. A backend that cannot read `approval_queue.json` reports ready. |
| Browser / proxy / TLS | Codex: built React app + real ASGI + forwarding proxy + PWA upgrade, synthetic APIs. **Skipped in my run.** | BLOCKED | Hosted TLS and proxy never exercised. |
| Auth / sessions | Role guard, step-up, proven-ownership for execution (tests). S-03 (Host). | NOT TESTED | |
| Logging / alerting | ntfy/Pushover pushes, critical logs on UNKNOWN, reconciliation log. | NOT TESTED | The halt report misstates outcomes (F-01). |
| Failure recovery | Corrupt queue → `QueueStateError` (blocked, file preserved). Corrupt positions/pending-cancels → held with quarantine copy. | NOT TESTED | |
| Environment isolation | `tests/conftest.py` redirects every ARIA data root; metered hosts blocked. | n/a | My runs wrote nothing to production `data/`. |
| Runtime dependencies | **S-01**: the runtime venv has 74 known vulnerabilities in 14 packages. The audited env is not deployed. | BLOCKED | Recreate the venv at cutover. |
| Rollback | Codex documents: never run legacy code over quarantined state; preserve claims and preimages. | NOT TESTED | **F-10 adds a rollback hazard**: checking out any older commit rewrites `signals.json` with a fresh mtime. |

## 5. Overall architectural judgement

The order path is built the right way round: the claim before the send, absence is not
proof, reads allowlisted and writes refused by default, reduce-only at the moment of
sending, single desk per machine. Those properties held under line-by-line review and
under the probes. The defects found sit at the edges: the emergency halt's adapter
translation, the freshness gate's trust in file metadata, and the exit engine acting on
state that reconciliation had not yet settled. They are fixable without redesign. Two of
them (F-01, F-10) must be fixed before any further paper acceptance session.
