# ARIA architecture

Integrated candidate: `codex/aria-final-integration`, runtime implementation `89f8f4a`.
The historical completion architecture below is retained with these updates: router 7;
legacy quarantine and identity-checked read-only recovery; bounded queue/ML locks;
lazy embedding model; Windows Python 3.14.8 candidate; ML batch persistence and row ages.
See `ARIA_FINAL_INTEGRATION.md` for current evidence and blockers.

How ARIA was put together as of `claude/aria-completion` (2026-10-17), with the
trading path in detail. Deeper references, still current:
`docs/TRADING_ARCHITECTURE.md` (the `src/trading` layers and the road to live),
`docs/PORTFOLIO_RISK.md` (pre-trade gate), `docs/MARKET_SESSIONS.md`,
`docs/DATA_LAYER.md`, `docs/RESEARCH_ENGINE.md`, `docs/MAC_MINI_SERVER.md`.

## 1. Processes and entry points

| Entry point | Started by | What it runs |
| --- | --- | --- |
| `scripts/serve.py` → `backend/main.py` (FastAPI, uvicorn, port 8000) | `ARIA Backend` scheduled task | The API (231 routes), the built frontend at `/app/`, and background threads started a few seconds after boot: the **desk daemon** (12 s), the quant lab, the brain daemon (once Ollama answers). Workers refuse to start under `ARIA_TESTING`. |
| `main.py` | `TradingIntelligenceSystem` scheduled task (visible console) | The data pipeline: download → signals → sentiment → macro → ML (~4 h) → exports to `data/*.json`. Places no orders. |
| `chat.py` | the owner, by hand | CLI chat (a hidden entry point — check it before calling any module dead). |
| `src/dashboard/dashboard.py` | Streamlit, by hand | Legacy dashboard (also a hidden entry point). |
| `frontend/` | `npm run build` → `frontend/dist`, served by the backend | React 18, react-router 7, Vite 8; 19 pages, inline styles; `/app/` with a service worker. |

**Exactly one process may run the desk** (`data/desk/desk_daemon.lock`, held
for the process's life; a second backend stays idle, reports
`instance_conflict` in `/api/desk/status` and retries every 5 min).

## 2. The trading path

```
signals.json ─┐                 ┌─ DELIBERATE lane: hunt cycle (30 min, market hours)
(pipeline)    │                 │    analysts → debate → judge → risk officer
              ├─► desk daemon ──┤    → capital allocator → slate → AutoExecutor
quotes ───────┘                 │
(Alpaca)                        └─ REFLEX lane: armed playbooks, quote poll 3 s
                                     validate → confidence → size → risk → LLM veto
                                     → AutoExecutor._handle (same gates)
owner ── POST /api/execute/approve (session + step-up) ──┐
                                                          ▼
                          ApprovalQueue (data/execution/approval_queue.json)
                                                          ▼
                OrderManager.execute ── session gate (UNKNOWN never OPEN)
                                     ── reduce-only (exits) / pre-trade risk (entries)
                                     ── unresolved-order guard (any lane, any process)
                                     ── claim "submitting" + client_order_id, ON DISK
                                     ── ModeGatedBroker (RESEARCH/PAPER/LIVE, per call)
                                     ── PaperOnlyBroker / live_guard
                                     ── adapter: kill switch, then the POST (no retry)
                                                          ▼
                                     Alpaca (paper) │ IBKR (when TWS runs)
```

Management tick (every 5 min, 24/7): `PositionManager.tick` (adopt broker
positions → prune closed → exposure triage → exit rules → bracket healing →
stale-order cleanup), then `OrderManager.reconcile_unresolved` and
`reflex.reconcile_unknown_playbooks`, then signal-spike arming.

Desk exits (`PositionManager._close`) cancel the position's bracket, reconcile
the queue's claims, re-check reduce-only, and then go through the same
`ApprovalQueue` + `OrderManager.execute` path as entries: durable claim,
client id, timeout settlement (Codex `048f47b`).

### Who may place what

| Caller | Wrapper | Ceiling |
| --- | --- | --- |
| Desk (deliberate, reflex, exits, brackets) | `access.gate_for_desk` | **PAPER in every mode** |
| Owner's approval queue | `access.gate_for_approvals` | LIVE_AUTONOMOUS (needs the out-of-process switch and the hard limits) |
| `src/trading` pipeline | `access.paper_adapter` / `live_adapter` | per function; **no production caller today** |

Autonomous execution also needs, all at once: `ALPACA_PAPER` exactly `true`,
`auto_execute` armed in the desk config, a connected broker that reads as paper
at the endpoint, budget and trades left today, and fresh signals
(`AutoExecutor.gates`). Any red gate queues the trade for the owner.

## 3. Order lifecycle (approval-queue row)

```
pending ──approve──► approved ──claim (client id on disk)──► submitting ──POST──┐
   │                    │                                                       │
   └─cancel/reject──────┘ (only before the claim)                               │
                                                                                 ▼
          ┌────────────── broker answered ──────────────┬───────── no answer ──────────┐
          ▼                                             ▼                              ▼
   executed (order_status: submitted │ partial │ filled)   rejected (broker said no)   unknown
          │  order_terminal marks a cancelled partial                                  │
          ├─ reconcile: later filled / cancelled / expired                              │
          ▼                                                                            ▼
   executed+filled │ rejected (ended unfilled)       reconcile finds it by client id ──┘
                                                    (or by broker id once acknowledged);
                                                    otherwise only the OWNER's release
                                                    (step-up + note) clears it
```

Rules that hold on every path:

- The claim is written **before** the POST, so a crash or a cancel racing the
  send can never leave a row claiming "never sent".
- `submitting` / `unknown` rows, and acknowledged orders still working, block
  any other order that could add to the same instrument — from every lane, in
  every process, across restarts.
- A 404 or "not found" after a timeout settles nothing. Only the broker's own
  answer about that order does.
- A definite refusal (`OrderRefused`, or a 4xx to the single POST) is rejected,
  never UNKNOWN; a timeout is UNKNOWN, never rejected.
- Reflex playbooks mirror this: an UNKNOWN playbook, or one that fired within
  its expiry, blocks re-arming its ticker.

## 4. State stores

All are JSON or SQLite under `data/` (git-ignored). The order-path stores are
written atomically (temp file + fsync + `os.replace`). When damaged they **fail
closed** — they never read as empty. A copy is quarantined beside the file
(`*.corrupt-<UTC>`), the API answers 503 with the file name, and the file is left
for repair.

| Store | Owner module | Damaged → |
| --- | --- | --- |
| `data/execution/approval_queue.json` (+ `.lock`, `.audit.jsonl`) | `approval_queue` | `QueueStateError`: nothing proposed, sent or changed |
| `data/execution/order_reconciliation.jsonl` | `order_manager` | append-only; a write failure is logged, never decides state |
| `data/desk/playbooks.json` (+ `.lock`) | `reflex` | scan fires nothing; arming refused |
| `data/desk/day_state.json` | `day_state` | written today → usage unknown: no budget, no start equity, entries held; earlier day → normal roll-over |
| `data/desk/positions.json` | `position_manager` | owner pushed; broker positions re-adopted with synthetic 1×ATR / 2×ATR levels |
| `data/desk/pending_cancels.json` | `position_manager` | copy kept; the stale-order sweep still covers flat tickers |
| `data/desk_config.json` | `desk.config` | defaults, `auto_execute` **off** |
| `data/kill_switch.json` | `kill_switch` | engaged (fails closed) |
| `data/trading/*` (mode, limits, journal) | `src/trading` | see `TRADING_ARCHITECTURE.md` |
| `data/aria_core.db` (event bus + prediction ledger), `data/universe.db` | `core.bus`/`ledger`, `data.universe` | SQLite; additive `ADD COLUMN` migrations on connect |

Primitives: `src/core/durable.py` (`atomic_write_json`, `read_json_state`,
`ProcessSharedLock`, `SingleInstance`). The queue's own lock and atomic write
are Codex's implementation in `approval_queue.py`; both lock a sidecar file,
never the file they replace.

## 5. Invariants (WORK_PLAN §4, enforced by tests)

- Nothing calls `approve_and_execute` autonomously; the brain proposes, the
  human approves.
- Kill switch, risk profiles, the pre-trade gate and the reduce-only invariant
  stay in force on every order path (`test_session_bypass` inventories every
  send site).
- A market whose session is UNKNOWN is never OPEN.
- The vault is owner-only; `.env` and the vault are never copied, quoted or
  committed.
- No claim of improvement without a measured before/after.

## 6. Ownership

`docs/collab/WORK_PLAN.md` §3 decides. In short: Claude — `backend/`, `src/`
trading, desk, execution, risk, core, auth, markets, intelligence. Codex —
`frontend/`, `src/v5`, data, research, evolution, training, backtesting,
`scripts/`, repo hygiene, and (for this mission, by the owner's brief) execution
reliability, coordinated through `docs/ARIA_HANDOFF.md`.
