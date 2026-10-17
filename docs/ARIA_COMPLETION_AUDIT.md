# ARIA completion audit — 2026-10-17

Lead: Claude Code (architecture, integration, release decisions). Codex worked in
parallel on reliability and CI (`codex/aria-final-verification`); its audit is
`docs/CODEX_ARIA_AUDIT.md` on that branch and agrees with this one where they
overlap. Every claim below cites a command, a test, a file or a commit. Where
something was not measured, this document says so.

**Scope.** ARIA's trading path end to end — market data and freshness, signals,
desk and reflex lanes, approval queue, order manager, broker adapters, risk,
kill switch, reconciliation, persistence, API and UI integration — plus the
repo-wide checks (tests, static analysis, migrations, secrets). The research,
brain, V5 and SENTINEL subsystems were inventoried only; they place no orders.

**Branch audited.** `claude/aria-completion` (worktree
`.claude/worktrees/aria-completion`). Base: `aria/integrity-upgrade @ b5b47f2`,
plus the unmerged `claude/s1.3-followup` and `claude/launch-sprint`, Codex's
`09f2158` / `0a1b21a` / `048f47b`, and this mission's commits. Nothing was
pushed, merged into `aria/integrity-upgrade` or `main`, or deployed.

## 1. Where things stood at the start (measured 2026-10-17 ~19:45)

| Fact | Evidence |
| --- | --- |
| Integration branch `aria/integrity-upgrade @ b5b47f2`; working tree has only runtime data changes (`data/*.json`) | `git status` |
| Order-safety work S1.3 was **unmerged**: `claude/s1.3-followup` answered Codex's CHANGES REQUIRED (8 findings) and was waiting for re-review | `docs/collab/log-claude.md` 2026-10-17 00:50; Codex review in `aria-codex/docs/collab/reviews/` |
| Integration HEAD still recorded a submit timeout as REJECTED (no client order id) | S1.3 log; `src/execution/order_manager.py @ b5b47f2` |
| Live runtime: PAPER, kill switch off, broker connected ($10,008.38), 0 desk executions since the 2-day paper test began (2026-10-16 17:13) | `python -m src.core.launch_check --root <main> --base-url http://localhost:8000`, 20:28 |
| Backend **down 08:59 → 20:13 today** (equity-mark gap); the `ARIA Backend` task started 19:41 | same check, "paper-test continuity" |
| ML predictions last written **2026-05-31**; the 19:49 pipeline run is in its ~4 h ML step (the console window the owner saw) | same check; `Win32_Process` (pid 39660, parent Task Scheduler) |
| Baseline suite on the completion branch: **3390 passed, 9 skipped, EXIT 0** — after fixing one clock-dependent test | `9c5894c`; scratch `baseline_suite2.txt` |

## 2. Architecture inventory

Fuller description: `docs/ARIA_ARCHITECTURE.md`.

| Component | State | Notes |
| --- | --- | --- |
| `backend/main.py` (FastAPI, 231 routes: 158 GET, 69 POST) | **Implemented** | Owner auth by session + step-up on money-moving routes (`src/auth`). Lazy-imports heavy modules. |
| Approval queue `src/execution/approval_queue.py` | **Implemented, hardened this mission** | Durable claim before the POST; process-wide lock; append-only audit; corrupt file fails closed (Codex `09f2158`). |
| Order manager `src/execution/order_manager.py` | **Implemented, hardened** | Session gate, reduce-only, pre-trade risk and unresolved-order guard at send time; deterministic `client_order_id`; UNKNOWN is settled only by a found broker answer or the owner's release. |
| Alpaca adapter | **Implemented**; paper only by default | Orders go through a no-retry client. Reads raise on failure (`09f2158`). |
| IBKR adapter | **Partial** | Only when TWS/Gateway is running. `orderRef` lookup added (`09f2158`). **Never exercised against a gateway in this mission.** |
| Live gate `src/execution/live_guard.py` + mode gate `src/trading/access.py` | **Implemented** | Desk ceiling PAPER in every mode; LIVE_AUTONOMOUS needs the out-of-process switch. `live_guard.py` is a fenced path. |
| Kill switch `src/execution/kill_switch.py` | **Implemented** | File + `ARIA_KILL_SWITCH`; checked inside each adapter below every gate; now also covers OCO (`09f2158`). |
| Halt / flatten `src/trading/killswitch.py` | **Implemented** | A different thing from the kill switch: a policy-driven halt. See finding O-6. |
| `src/trading` pipeline (`TradingPipeline`, `OrderEngine`) | **Implemented but has no production caller** | Only tests run it (`grep "TradingPipeline(" src backend` finds none). Modes, limits, access and halt in `src/trading` ARE live. |
| Desk `src/desk` (hunt cycle 30 min, management tick 5 min, reflex lane 3 s) | **Implemented** | Paper autonomy behind six gates; desk exits now go through the durable queue (`048f47b`). |
| Risk `src/risk` (pre-trade gate, profiles, FX, covariance) | **Implemented** | ACCEPT/REDUCE/DEFER/REJECT at send time; pending working entries now DEFER new exposure (`09f2158`). |
| Market sessions `src/markets` | **Implemented** | UNKNOWN is never OPEN; checked at send. |
| Data pipeline `main.py` (scheduled task) | **Implemented, not durable** | 10 runs since 2026-07-22, 1 complete (launch-sprint log); ML writes only at the end. |
| Frontend (React/Vite, 19 pages, served at `/app/`) | **Implemented** | Execution status is honest about UNKNOWN / working / fills since Codex `0a1b21a` (F8). |
| Mac mini 24/7 host | **Not done** | `docs/MAC_MINI_SERVER.md`; owner actions open. |

## 3. Findings and what happened to each

Priority: **P0** = can send a duplicate or unintended order, or misstate one;
**P1** = wrong state, availability or safety-adjacent; **P2** = hygiene.
"Fixed" means a test reproduced the defect and passes after the change.

### Fixed in this mission

| # | P | Finding | Fix / evidence |
| --- | --- | --- | --- |
| F-1 | P0 | Submit timeout recorded REJECTED; a 404 after a timeout treated as "never placed"; SDK retries hid an accepted POST; unresolved orders did not block other lanes (Codex's 8-finding review of S1.3) | `claude/s1.3-followup` (`4f3b18e`, `d2f5e80`); Codex's 12 probes pass; merged here |
| F-2 | P0 | **Approval queue truncated or empty on a crash mid-write**: `write_text` + `except: return []` erased every submitting/unknown row, and with them the block on a second order | Codex `09f2158` (atomic replace, fail-closed load, OS lock); verified here by `tests/test_queue_durability.py` (torn file, crash during save, second process) |
| F-3 | P0 | Same truncate-then-empty pattern in **reflex playbooks** (fired/unknown playbooks block re-arming), **desk positions**, **pending sibling cancels** and **day state** (daily notional/trade caps reset) | `src/core/durable.py`, `f5f2bf7`, `33eb7a0`; `tests/test_state_store_durability.py`, `tests/test_durable_state.py` |
| F-4 | P0 | Two backend processes (orphaned uvicorn) each ran the desk and reflex loops; every lock was per process | `c49217c` single-instance desk lock; `tests/test_desk_single_instance.py` (real second process) |
| F-5 | P0 | Protective OCO used the retrying client with no client id; an ambiguous OCO fell back to a stop | Codex `09f2158`; merged |
| F-6 | P0 | **The exit engine's stop-loss exit was refused right after it cancelled its own bracket** (introduced by `09f2158`'s durable-claim check). The position then had no bracket and no exit for a tick. | Codex `048f47b` (reconcile after cancel) + `4abcb84` (reconciliation falls back to the order's own status by broker id when the client-id lookup is down); `test_the_exit_goes_out_after_its_own_bracket_is_cancelled`, `..._when_the_client_id_lookup_is_down` |
| F-7 | P1 | **A definitely refused OCO** (40310000 shares reserved; the mode gate in RESEARCH) became UNKNOWN and blocked the symbol until a manual release | `e7282f4` `OrderRefused`; `tests/test_protective_order_outcomes.py` |
| F-8 | P0 | **"Connected, holding nothing."** `account_snapshot` set connected before reading positions; a failed read made the management tick record every tracked position as closed and cancel its protective orders | `e7282f4`; `tests/test_snapshot_positions_unreadable.py` |
| F-9 | P1 | Desk exits bypassed the durable queue (no client id; a timeout read as rejected) | Codex `048f47b`; merged |
| F-10 | P1 | Merge-induced: the yf.Search news fallback read a module-level `yf` that a lazy-import change removed — sentiment silently back to "no news" | `4abcb84`; `tests/test_pipeline_sentiment.py` |
| F-11 | P1 | Merge-induced: two identical `reconcile_unknown_playbooks` in `reflex.py` | `dde257b` (found by ruff F811) |
| F-12 | P1 | Damaged state answered 500 with no explanation | `aba32e3`: `StateUnreadable` / `QueueStateError` / `StateLockTimeout` → 503 with the file name only |
| F-13 | P1 | `/api/execute/positions` turned an unreadable book into a 500 (now that adapters raise) | `e7282f4`: 503 "unreadable — not an empty book" |
| F-14 | P2 | Runtime trading records (`data/execution/*.lock`, `*.audit.jsonl`, reconciliation log) not git-ignored | `edf818b` |
| F-15 | P2 | `test_launch_check` failed once the clock passed its fixture time | `9c5894c` (`--now`) |
| F-16 | P1 | Desk config saved non-atomically (a crash reset every owner setting to defaults) | `33eb7a0` |
| F-17 | P1 | Execution UI showed UNKNOWN as CANCELLED and unconfirmed fills as AUTO-FILLED (Codex review F8) | Codex `0a1b21a`; `verify-execution-status.mjs` passes (7 states) |
| F-18 | P1 | The nightly backup zipped `data/desk/` only — the approval queue, which now stops its lane when damaged, had no restorable copy | `c133e28`; `test_the_nightly_backup_includes_the_order_records` |

### Open — not fixed, with acceptance criteria

| # | P | Finding | Why open | Done when |
| --- | --- | --- | --- | --- |
| O-1 | P0 | **No measured trading edge.** Two-day paper test: $10,008.38 → $10,008.38, 0 desk executions so far | Not a code defect: it needs time and trades | `scripts/paper_test_report.py` over ≥ 30 closed trades shows a confidence interval that excludes zero after costs |
| O-2 | P1 | **Runtime is not always-on.** Backend down 08:59→20:13 today; the pipeline completed 1 of 10 runs since July; ML predictions date from 2026-05-31 | Host and scheduling decisions are the owner's (battery settings, InteractiveToken, no WakeToRun; Mac mini not set up) | 7 consecutive days with no equity-mark gap > 1 h, a pipeline `RUN COMPLETE` every day, and ML predictions < 48 h old (`launch_check` PASS) |
| O-3 | P1 | Paper-account validation **not exercised in this mission**: delayed acknowledgement, partial cancel, gateway restart, real fees. Every claim above rests on fake brokers driving the real adapter code | Would place paper orders during the owner's 2-day test, which this mission was told not to disturb | A scripted paper session (`scripts/bench_reflex.py`-style, owner-started) reconciles every order by client id with zero UNKNOWN left unexplained |
| O-4 | P1 | IBKR path untested against a gateway; `orderRef` is not broker-enforced uniqueness | No TWS/Gateway here | Paper TWS session: submit, timeout injection, `get_order_by_client_id` finds it |
| O-5 | P1 | `live_guard._refuse` returns None for a refused OCO on a **non-paper** account; `place_protective_orders` records that UNKNOWN (a false block, not a duplicate) | `live_guard.py` is a fenced path; owner decision | `live_guard` raises `OrderRefused` for `submit_oco_exit` (owner-approved edit), with a test |
| O-6 | P1 | `src/trading/killswitch.halt` treats unreadable positions as "nothing held" and cancels rather than keeps — **including protective stops** of positions it cannot see | Documented existing halt policy; changing it is an owner decision | Halt with unreadable positions keeps protective orders; test |
| O-7 | P1 | Signal freshness gates the file's age, not each row's: LBRDA's last bar is 2026-08-21 in today's book | Owner decision (launch-sprint log) | Desk refuses entries on a row whose own bar is > N sessions old; test |
| O-8 | P1 | A reflex playbook retired as UNKNOWN while the queue was unreadable carries no ids, so neither reconciliation nor the owner's release can clear it | Rare (needs a damaged queue during a fire) | Release endpoint accepts a playbook ticker with a note; test |
| O-9 | P1 | Pre-trade gate DEFERs every new entry while **any** working (non-reducing) order rests anywhere in the account (`09f2158`) | Deliberately conservative; owner should know | Owner accepts, or the gate scopes the deferral; test either way |
| O-10 | P1 | Codex's queue lock blocks without a timeout on POSIX (`fcntl.flock` LOCK_EX); on Windows `msvcrt` gives up after ~10 s | Mac mini only | Bounded wait with `StateLockTimeout` on both platforms |
| O-11 | P2 | Static analysis debt (§5): 239 ruff findings outside the fixed ones, 139 mypy lines in the order path | Mostly unused imports and Optional looseness; no defect found among those inspected | ruff F401/F541/F841 cleared in owned areas; mypy clean on `src/execution` with `--check-untyped-defs` |
| O-12 | P2 | Dependency advisories (Codex, measured): npm 9 (5 high dev-chain, 4 moderate); pip-audit 119 across 14 installed distributions — **not triaged** | Upgrades need their own tested change | Triage table; production-path advisories fixed or waived with reason |
| O-13 | P2 | sklearn 1.8 `penalty` FutureWarning floods the pipeline console | Cosmetic | `src/models` passes `l1_ratio`/`C`, no warnings |
| O-14 | P2 | S2.2/S2.3 (cluster-robust per-source hit rates) blocked on Codex's uncommitted S2.2 | Codex's area | S2.2 committed and reviewed; S2.3 merged |

## 4. Mission 3 checklist — trading safety and state integrity

| Requirement | Status | Evidence |
| --- | --- | --- |
| 1 Duplicate order prevention | **Met in tests** | Deterministic client id; claim before POST; unresolved/working orders block every lane (`test_order_safety_review.py` F4); single desk instance (F-4) |
| 2 Timeout after submission ≠ rejection | **Met** | `test_order_settlement.py`; adapters classify only a 4xx to the single POST, or a connect timeout, as refused |
| 3 UNKNOWN never auto-retried or re-armed | **Met** | `_arm_blocker`; `execute()` refuses UNKNOWN rows; release needs owner + step-up + note |
| 4 Crash recovery | **Met in tests** | Atomic stores (F-2, F-3, F-16); a claimed row survives a crash as "submitting" and is never resent |
| 5 Broker ↔ local reconciliation | **Met in tests** | `reconcile_unresolved` (client id, then broker id for acknowledged orders) each management tick; append-only `order_reconciliation.jsonl` |
| 6 Restart-safe persistence | **Met in tests** | as 4; corrupt state fails closed with a quarantined copy |
| 7 Concurrent approval/execution | **Met in tests** | OS-level queue lock + claim re-check; 3-process test (Codex), 2-process tests here |
| 8 Stale data, disconnected broker | **Partly** | Quote age ≤ limit and skew-bounded at send; positions-unreadable = disconnected (F-8); per-row signal staleness open (O-7) |
| 9 Rejected / partial / cancelled / expired | **Met in tests** | `order_terminal`; partial cancel keeps filled quantity; acknowledged-then-ended is not success |
| 10 Risk limits before and during execution | **Met** | Pre-trade gate at send; reduce-only re-read after the exclusive claim (`048f47b`); day-state damage holds entries (F-3) |
| Live disabled by default | **Met, measured live** | mode PAPER; `ALPACA_PAPER` gate; desk ceiling PAPER; `launch_check` 20:28 |

## 5. Repo-wide checks

| Check | Result |
| --- | --- |
| Tests | Baseline 3390 passed / 9 skipped. Final run: see `docs/ARIA_RELEASE_READINESS.md` |
| Frontend | launch-routes, technical-dashboard, execution-status, staging build and shell verifiers all EXIT 0 on the integrated branch (`node_modules` linked read-only from the main folder; output in scratch, nothing published) |
| ruff 0.8.4 (`F`, `E9`, `B006`, `B008`) on `src backend main.py chat.py` | 239 findings: F401 108, F541 87, F841 24, B008 16, F811 2, F601 1, B006 1. **No undefined names, no syntax errors.** F811 in `reflex.py` was a real merge artifact (fixed, F-11); F811 in `sessions.py` is an enum member shadowing an import (benign); F601 duplicate `"coinbase"` key in `src/political/fetchers/ticker_mapper.py` (both entries map to `COIN` — harmless) |
| mypy 1.13 on the order path (`--ignore-missing-imports`) | 139 lines: union-attr 25, arg-type 25, attr-defined 22, index 17, … Inspected: Optional client ids that are always set at runtime, `fcntl` unknown on Windows, `hasattr`-guarded `submit_oco_exit`, a dynamically set `RiskOfficer.allocation`. No runtime defect found among them; typing debt (O-11) |
| Database migrations | Additive `ALTER TABLE … ADD COLUMN` on connect (`src/core/ledger.py`, `src/data/universe.py`); no migration framework. Run against **copies** of the live `aria_core.db` and `universe.db`: already current, idempotent on re-run, `integrity_check ok`, no row counts changed. Copies deleted |
| Secrets | No credential-shaped strings in tracked files (only `sk-ant-your-key-here` placeholders in `chat.py`, `.env.example`). `.env`, `data/research_keys.json`, the kill-switch file and the order records are ignored. No logger call prints a key |
| Fence | `scripts/check_protected_paths.py --require-git`: **clean** over all history and over `b5b47f2..HEAD`. `approval_queue.py` and `live_guard.py` are fenced against ARIA-authored commits; these are Claude's and Codex's. `live_guard.py` was not modified |

## 6. Things checked and found working

Kill switch below every gate, including OCO (`test_kill_switch_and_profiles.py`,
`test_protective_order_outcomes.py`). Owner endpoints refuse without a session
(live: 6 × 401). Step-up on approve / release / kill-switch release. Session
gate at send (`test_session_bypass.py`, every send site inventoried).
Reduce-only on every exit path. Live UI shell served for 8/8 routes. Signals
fresh (717 rows, 0.5 h).
