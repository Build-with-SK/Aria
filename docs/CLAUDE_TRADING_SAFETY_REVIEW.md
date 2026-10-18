# Claude trading safety review — ARIA final candidate

Reviewer: Claude Code (independent of the integrator's reasoning; Codex performed the
integration and its own second pass). Date: 2026-10-18.
Candidate: `codex/aria-final-integration` @ `edc2796` (runtime code `89f8f4a`, tests `a079dff`).
Review branch: `claude/aria-final-review` (worktree `.claude/worktrees/aria-final-review`).
Later Codex commit reviewed separately: `edfb5e6` on `codex/aria-operational-readiness`.

No order was placed, cancelled or looked up at any broker. Every broker in this review is a
fake or the real adapter class driving a fake SDK client. No production file was read for
its content or written.

## 1. Evidence base

| Evidence | Result |
| --- | --- |
| Full suite on `edc2796`, Windows, the existing runtime venv (CPython 3.11.8) | **3,523 passed, 13 skipped, exit 0**, 321.9 s. Codex reported 3,524/12 on a private 3.14.8 venv; the runtimes differ, so this is a second measurement, not a rerun of theirs. |
| Skips in that run | opt-in profile and GET sweep; `test_integrated_web_acceptance` (needs a staged build, so browser/proxy acceptance is **not** independently re-verified here); production ledger/symbol-master fixtures absent; 2 POSIX-only. |
| Review probes | `tests/test_claude_final_review_probes.py` (commits `b67a2f2`, `34df97e`): 8 strict-xfail reproductions + 2 controls. Each probe fails today for the reason in its marker. Run with `--runxfail` to see the raw failures. |
| Code read line by line | `approval_queue.py`, `order_manager.py`, `alpaca_broker.py`, `ibkr_broker.py` (submit, cancel, lookup, status), `broker_base.py`, `live_guard.py`, `reduce_only.py`, `trading/access.py`, `trading/killswitch.py`, `trading/broker.py` (legacy adapter), `trading/orders.py` (submit/reconcile), `desk/position_manager.py`, `desk/auto_executor.py`, `desk/reflex.py` (scan/fire), `desk/desk_daemon.py` (instance lock, management tick), `desk/opinion.py` (gate 6), `backend/main.py` execution routes and middleware. |

Reproduce everything below from the review worktree:

```
venv\Scripts\python.exe -m pytest tests/test_claude_final_review_probes.py -q -p no:randomly -rxX
venv\Scripts\python.exe -m pytest tests/test_claude_final_review_probes.py -q -p no:randomly --runxfail
```

## 2. The twelve invariants

| # | Invariant | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | Every order has a durable identity | **PASS** | Queue rows get a uuid at push; `claim_for_submit` writes `status=submitting` + deterministic `client_order_id` (`order_manager.queue_client_order_id`) and fsyncs **before** `submit_order` (`approval_queue.py:337-371`, `order_manager.py:240-261`). Desk exits (`position_manager.py:640`) and protective orders (`order_manager.py:693-696`) go through the same claim. |
| 2 | Duplicate approvals cannot create duplicate orders | **PASS** | The claim is a compare-and-set under `ProcessSharedLock` (cross-process). A second `execute()` for the same id gets `"trade … is submitting, not sendable"` and changes nothing (`order_manager.py:243-248`; "unresolved" is not in that text, so the row is not rejected). Covered by `test_queue_durability.py`, `test_order_safety_review.py`. |
| 3 | UNKNOWN cannot trigger another submission | **PASS** | `execute()` accepts only `pending/approved` (`:171`). An `unknown`/`submitting` row blocks every non-exit order for the instrument on that broker, from every lane, across restarts (`unresolved_for`, `claim_for_submit` busy check). Reflex retires the playbook as `unknown` (`reflex.py`, `_execute_raised`). |
| 4 | Broker timeouts do not imply rejection | **PASS** | Alpaca: `_classify_submit_failure` returns REJECTED only for a 4xx answer to the one POST (orders use a no-retry client) or a `ConnectTimeout`; a 4xx mentioning the client id is UNKNOWN (`alpaca_broker.py:165-203`). Any adapter exception becomes UNKNOWN in `execute()` (`:262-266`). IBKR `placeOrder` exceptions are UNKNOWN (`ibkr_broker.py:474-479`). |
| 5 | Missing broker records do not prove absence | **PASS** | 404 → `None` → row unchanged (`alpaca_broker.py:454-469`, `order_manager.py:487-488`). Only a found broker answer or the owner's step-up release with a note settles UNKNOWN (`approval_queue.release_unresolved`, `/api/execute/unresolved/{id}/release`). |
| 6 | Partial fills stay consistent after restart | **PASS (queue) / FAIL (desk protection)** | Queue: `mark_executed` is monotonic, refuses fills above order qty or non-finite prices, never reopens a terminal order, and a stale zero-fill cancel cannot erase a fill (`approval_queue.py:373-443`). Desk: tracked qty is never re-synced from the broker and sizes bracket healing — **F-09**. |
| 7 | Unreadable broker positions fail closed | **PASS** | `account_snapshot` is `connected` only after positions are read (`auto_executor.py:100-113`, R3); `get_positions` raises (`alpaca_broker.py:339-358`); `reduce_only.at_broker` refuses on any read failure; a damaged `positions.json` or `pending_cancels.json` holds the tick (`position_manager.py:332-354`). |
| 8 | Protective orders are not accidentally removed | **FAIL** | **F-02**: a desk exit that does not fill cancels the bracket, and the same tick cannot re-place it. **F-09**: healing protects only the tracked qty. (The halt correctly keeps protective orders — but see F-01, it also keeps everything else.) |
| 9 | Risk limits cannot be bypassed by alternate paths | **PASS** | Every production send reaches the broker through `OrderManager.execute` (pre-trade portfolio risk for entries, reduce-only for exits) or `place_protective_orders` (reduce-only). The kill switch sits in both adapters. Desk and reflex use `AutoExecutor._handle` → the same `execute`. `TradingPipeline` has no production caller (`src/trading/orders.py:58-66`). Exits and protection skip the pre-trade gate by design: reduce-only is enforced instead. |
| 10 | Concurrent backends cannot independently control one account | **PASS (trading) / note** | The desk, its management tick and the reflex lane run only under the OS `SingleInstance` lock (`desk_daemon.py:31-52,144-156`). Approval-path sends from a second backend still meet the same cross-process queue claim. Not covered: the pipeline (`main.py`) has no cross-process single-writer lock — **F-08** (data integrity, not order control). |
| 11 | Legacy orders stay quarantined without evidence | **PASS** | `_load_safe` turns any no-key historical row that may have been sent into `unknown` with its preimage (`approval_queue.py:200-225`). Legacy lookup is by broker id only, and the found order must match symbol, side and qty (`order_manager.py:466-496`). `bind_legacy_evidence` binds but never settles. |
| 12 | Important state transitions are auditable | **PARTIAL** | Queue: snapshot + fsynced append-only audit of every changed row. Reconciliation lookups: append-only log, but a write failure is only logged. Not audited: desk `positions.json` changes (snapshot only). The halt report files failed cancels under `kept_protective` — **F-01**. |

R1/R2/R3 integration: **retained and consistent**. R1 (identity-checked bracket recovery): the broker-id status fallback in `reconcile_unresolved` is limited to non-legacy rows and checks identity, qty and price (`order_manager.py:514-538`). R2 (`OrderRefused`): used by `place_protective_orders`, the mode gate and `submit_oco_exit`, so a definite refusal never becomes a blocking UNKNOWN. R3: as in invariant 7.

## 3. Findings

### F-01 — HIGH — The emergency halt cannot cancel any resting Alpaca order

- **Component**: `src/trading/broker.py` `LegacyBrokerAdapter.get_open_orders` / `cancel_order` (≈ lines 330-345); `src/execution/alpaca_broker.py` `get_open_orders` (`:589-624`); reached from `POST /api/trading/halt` → `killswitch.halt(broker=access.paper_adapter())`.
- **Evidence**: The adapter keys each open order by `o.get("client_order_id") or o.get("id")`. `AlpacaBroker.get_open_orders()` never returns a `client_order_id`, so the key is Alpaca's broker id. `cancel_order(key)` then calls `get_order(key)` → `get_order_by_client_id(<broker id>)` → Alpaca 404 → `None` → `False`. The halt appends every failed cancel to `kept_protective`. Probe output with the real `AlpacaBroker` class and a fake SDK: `{'cancelled': [], 'kept_protective': ['9f1c-broker-uuid'], 'failed': {}}`. The order was a BUY limit on a flat account.
- **Impact**: After the owner presses STOP, resting entry orders (limit or GTC, from approvals or manual work) stay live at the broker and can fill. The kill switch blocks only new sends. The report tells the owner those orders were deliberately kept as protection. Existing tests miss it because `FakeLegacyBroker.get_open_orders()` returns `[]` and has no client-id lookup.
- **Reproduction**: `test_f01_halt_cancels_a_resting_alpaca_entry`.
- **Fix**: Return `client_order_id` from `AlpacaBroker.get_open_orders` (alpaca-py exposes it), or have `LegacyBrokerAdapter.cancel_order` cancel by broker id when given one. Report a failed cancel under `failed`, never under `kept_protective`. Add the probe as a regression test, with a fake that returns real-shaped orders.
- **Owner**: Codex (implementation), Claude (re-review). **Status**: OPEN.

### F-10 — HIGH — The auto-execution freshness gate trusts file mtime, and `signals.json` is tracked in git

- **Component**: `src/desk/opinion.py:45-64` (`signals_freshness`, gate 6 of `AutoExecutor.gates`), `src/core/world.py:99-121`; `data/signals.json` (tracked: `git ls-files data`).
- **Evidence**: Signals are ticker-keyed, so `dataset_freshness` finds no top-level `data_date` and ages the file by mtime with a 30 h budget. In this review worktree, created today, gate 6 returned `{'fresh': True, 'age_hours': 0.3}`. The committed `data/signals.json` in the candidate (and in `edfb5e6`) was last changed in `54ad07d` on **2026-09-27**, and its 718 rows carry no `data_as_of`.
- **Impact**: Any checkout, merge, reset or restore that rewrites the file makes weeks-old composites, stops and targets pass the one gate meant to stop unattended trading on stale data. That includes the owner-approved cutover of this candidate into the runtime folder. The reflex lane also arms playbooks from signals (`arm_from_signal`). The gate's own docstring describes the 2026-10-06 incident it exists to prevent.
- **Reproduction**: `test_f10_freshly_written_old_signals_do_not_pass_gate_6` (rows dated 21 days ago, file written now → `fresh: True`); the control test documents today's contract.
- **Fix**: Age signals for gate 6 by the rows' own `data_as_of`/`generated_at`, which the pipeline has stamped since 2026-10-10. Treat unstamped rows as UNKNOWN, i.e. not fresh. Stop tracking generated data files (`data/signals.json`, `ml_predictions.json`, `macro_data.json`, `trade_candidates.json`, `futures_signals.json`, `political_signals.json`) — that one is an owner decision. Until fixed, add a cutover step: keep auto-execute disarmed until a pipeline run has rewritten `signals.json`.
- **Owner**: Codex (gate code), Owner (untracking generated data; cutover step). **Status**: OPEN.

### F-02 — MEDIUM — A desk exit that does not fill leaves the position without protection for at least one tick

- **Component**: `src/desk/position_manager.py` `_close` (`:600-663`) and `tick` bracket healing (`:437-443`); `src/desk/desk_daemon.py:350-367` (reconcile runs after `PositionManager.tick()`); `src/execution/reduce_only.py:160-170`.
- **Evidence**: `_close` cancels every open order on the ticker, sends a market exit through the queue, and if no fill arrives in the poll window cancels the exit and returns `None`. The exit row stays `executed/submitted` and non-terminal. The same tick's healing then calls `place_protective_orders`, and the reduce-only check refuses: *"a durable exit claim is not visible at the broker; reconcile it before sending another exit"* (captured in the probe log). Reconciliation, which would settle the cancelled exit, runs only after `tick()` returns.
- **Impact**: During a halt, an auction or any no-fill window, a held position sits with no stop for at least the 5-minute tick interval, and for longer if the exit keeps failing (each tick repeats cancel → exit → cancel). The control test shows protection comes back as soon as `reconcile_unresolved()` runs.
- **Reproduction**: `test_f02_failed_exit_does_not_leave_the_position_naked`; control `test_f02_control_reconcile_first_restores_protection`.
- **Fix**: Settle the desk's own cancelled exit immediately: after a successful `cancel_order`, record the broker's answer for that row (`get_order_status` → `mark_ended_unfilled`), or call `reconcile_unresolved()` before bracket healing in `tick()`. Re-place protection in `_close` itself when the exit is abandoned.
- **Owner**: Codex. **Status**: OPEN.

### F-09 — MEDIUM — Bracket healing sizes protection from the tracked qty, not the broker position

- **Component**: `src/desk/position_manager.py` `_heal_brackets` (`:877-909`), `track_entry` (`:489-516`), `_adopt` (`:462-487`).
- **Evidence**: Tracked `qty` is written at adoption, at `track_entry` (the filled part when the fill window closed on a partial) and at a scale-out, and is never re-read from the broker. `_heal_brackets` passes it to `place_protective_orders`, and reduce-only clips only downwards. Once any stop exists, `has_stop` is True and healing never looks at it again. Probe: 10 held, 4 tracked → *"stop covers 4.0 of 10 shares held"*.
- **Impact**: When a partial entry or an adopted still-filling order completes later, part of the position stays permanently unprotected at the broker. The software exit rules still use broker qty, but only while the backend is up.
- **Reproduction**: `test_f09_healing_protects_the_whole_broker_position`.
- **Fix**: Re-sync tracked qty from the broker on every tick. Compare the resting stop qty with the broker qty, not just whether a stop exists, and heal the shortfall (cancel and re-place as one OCO).
- **Owner**: Codex. **Status**: OPEN.

### F-03 — LOW — The live gate re-queues a refused IBKR order against Alpaca

- **Component**: `src/execution/live_guard.py:291-305` (`_queue_for_human` hardcodes `broker="alpaca"`).
- **Evidence**: The probe re-queues a refused futures order as `'alpaca'`. If the owner approves it, it routes to the wrong venue (where it will most likely be refused).
- **Fix**: Queue with the wrapped broker's route (`"ibkr"` for `IBKRBroker`). **Owner**: Codex. **Status**: OPEN.

### F-13 — LOW — The FLATTEN kill policy is offered but inert in production

- **Component**: `src/trading/killswitch.py` `_flatten`; `GET /api/trading/kill-policy` lists `FLATTEN`.
- **Evidence**: After step 1 engages the kill switch and step 2 drops to RESEARCH, every flatten send is refused. `tests/test_reduce_only.py::test_the_production_flatten_is_paper_only_and_refused_below_the_kill_switch` asserts this deliberately.
- **Impact**: The behaviour is safe, but the owner can choose FLATTEN expecting positions to close, and they will not.
- **Fix**: Remove FLATTEN from the production options, or label it "not available: positions must be closed by the owner at the broker". **Owner**: Codex (UI/API text). **Status**: OPEN.

### F-16 — LOW — A partially filled desk exit is missing from the closed-trade ledger

- **Component**: `position_manager.py:654-663`.
- **Evidence**: If the exit ends PARTIAL, the remainder is cancelled and `_close` returns `None` before any record is written. The next close records only the remaining qty.
- **Impact**: Realised P&L, and the track record and teacher lessons built on it, miss the partial fill. This sits inside the already-BLOCKED fees and partial-exit P&L gate.
- **Fix**: Record a partial exit record (with `fraction` set to the filled share) before cancelling the remainder. **Owner**: Codex. **Status**: OPEN.

### F-15 — INFO — IBKR `get_order_status` reports SUBMITTED for unknown or unreadable orders

- **Component**: `ibkr_broker.py:549-579`.
- **Evidence**: A disconnected gateway or an id missing from `ib.trades()` reads as SUBMITTED. `reconcile_unresolved` accepts any non-UNKNOWN status as a broker answer.
- **Impact**: Mitigated for rows without a broker id. IBKR is BLOCKED anyway, and session-local order ids are a documented gap.
- **Fix**: Return UNKNOWN in both cases. **Owner**: Codex, before IBKR validation. **Status**: OPEN.

## 4. Positive observations (verified, not assumed)

- The claim is made before the POST, so a crash in any window leaves a durable `submitting` row that is reconciled by lookup and never re-sent.
- `mark_executed` refuses identity changes and invalid fill evidence instead of overwriting them.
- The live gate allowlists **reads**, so an unknown or future write method is refused, not passed through. Non-callable client objects are not handed out.
- The pre-trade gate's DEFER does not reject the row, so it can be retried. REJECT does reject it.
- The reflex lane saves playbooks in `finally` under a cross-process lock, and an exception after the execute stage is treated as uncertain, never as "expired".

## 5. Verdict for this area

The core order-identity and UNKNOWN discipline is sound and well tested. Two HIGH defects
must be fixed before controlled paper acceptance: the halt that cannot cancel resting
orders (F-01) and the freshness gate that a git checkout defeats (F-10). Two MEDIUM
protection-loss paths (F-02, F-09) should be fixed before any unattended paper session.
