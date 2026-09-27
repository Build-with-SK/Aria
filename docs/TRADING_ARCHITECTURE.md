# ARIA — autonomous trading architecture

*Written 2026-09-23. Describes `src/trading/`, which is the execution path.
Everything here is built, tested and running in PAPER mode. Nothing here has
traded real money, and this document does not claim a strategy is profitable —
no strategy has reached out-of-sample evidence yet.*

---

## 1. The shape of it

```
   strategy  →  Signal  →  RISK ENGINE  →  order generation  →  VALIDATOR
                              │                                    │
                              ↓                                    ↓
                        size / NO TRADE                      ORDER ENGINE
                                                          (idempotent, journalled)
                                                                   ↓
                                                           BROKER ADAPTER
                                                          (mode-gated, always)
```

Each layer is a module with no knowledge of the ones above it, and each is
tested on its own:

| layer | module | what it decides |
|---|---|---|
| execution mode | `modes.py` | whether an order may exist at all |
| hard limits | `limits.py` | the ceilings nothing may exceed |
| market data | `market.py` | is this price fit to trade on, is the market open |
| costs | `costs.py` | fees, spread, slippage |
| signal | `signals.py` | a proposal: side, gross edge, stop, data time |
| account | `account.py` | what there is to trade with |
| risk engine | `risk.py` | **size, or NO TRADE with reasons** |
| order build | `orders.py` | one signal → one order, for ever |
| validation | `validator.py` | is this concrete order sendable now |
| broker | `broker.py` | the eight-method adapter, and a paper venue |
| access | `access.py` | which broker this caller gets, re-checked per call |
| halt | `killswitch.py` | stop everything, model-independently |
| pipeline | `pipeline.py` | the path above, timed |
| latency | `latency.py` | measured speed, honestly labelled |
| lifecycle | `lifecycle.py` | which strategies may trade, and where |
| scorecard | `scorecard.py` | how a strategy is judged |
| drift | `drift.py` | when a strategy stops working |
| outcomes | `outcomes.py` | what happened, as research data |
| arbitrage | `arbitrage.py` | net edge after everything, research-only |

### The model is not in this picture

`src/trading` imports nothing from `src.brain`, `src.inference`, `src.consult`,
`ollama`, `openai` or `anthropic`, and `tests/test_trading_structure.py` fails
the build if that changes. A language model can produce research, a note, a
hypothesis — and a human turns that into a `Signal`, which is a dataclass with a
direction, a gross edge in basis points and a stop. It carries **no size**:
sizing is the risk engine's, so "I am very confident" buys nothing.

---

## 2. Execution modes

| mode | orders | broker |
|---|---|---|
| `RESEARCH` *(default)* | none | reads and cancels only |
| `PAPER` | yes | confirmed paper account only |
| `LIVE_AUTONOMOUS` | yes | real money, no per-trade approval — still under every limit |

**Going up is deliberately awkward:** one step at a time, an exact typed phrase
per target mode, a clear kill switch, a recent step-up on the API route, and —
for `LIVE_AUTONOMOUS` — `ARIA_LIVE_TRADING_ENABLED=true` in the service
environment plus at least one strategy at `LIVE_ELIGIBLE`.

**Going down is instant and needs nothing but a reason.** Making the system do
less must never be hard.

The environment switch lives outside the application (`.env`, the launchd job)
and needs a restart, so nothing running inside ARIA can open it. A stored
`LIVE_AUTONOMOUS` with the switch absent reads as `PAPER` — pulling the variable
out of the job is a way to stop live trading that works even if the state file
says otherwise.

```bash
# the phrases, exactly:
PAPER            → "ENABLE PAPER TRADING"
LIVE_AUTONOMOUS  → "ENABLE LIVE AUTONOMOUS TRADING WITH REAL MONEY"
```

Every change, up or down, is appended to `data/trading/mode_ledger.jsonl` with
who, when and why.

---

## 3. Hard limits

`limits.CEILINGS` is compiled into the source. A limit may be set anywhere at or
below its ceiling; above it, the change is refused and the ceiling is named. A
value edited directly into `data/trading/hard_limits.json` is re-clamped on
every load, so the file is not a way round it.

| limit | ceiling | default |
|---|---|---|
| `risk_per_trade_pct` | 2.0 | 1.0 |
| `max_position_pct` | 10 | 10 |
| `max_instrument_exposure_pct` | 10 | 10 |
| `max_sector_exposure_pct` | 35 | 35 |
| `max_strategy_exposure_pct` | 50 | 50 |
| `max_daily_loss_pct` | 5 | 3 |
| `max_drawdown_pct` | 20 | 15 |
| `max_order_value` | 1000 | 100 |
| `max_leverage` | 1.0 | 1.0 |
| `max_open_positions` | 20 | 5 |
| `max_orders_per_minute` | 6 | 2 |
| `max_orders_per_day` | 20 | 10 |
| `max_quote_age_seconds` | 30 | 15 |
| `max_price_deviation_pct` | 2.0 | 1.0 |
| `max_participation_pct` | 10 | 5 |

**Tightening applies immediately. Loosening waits 24 hours** (`COOLING_OFF`), so
a bad night cannot be answered by raising the limit that stopped it. Setting a
limit again cancels a pending loosening.

The AI cannot override any of this. There is no code path from a model to
`set_limits`, and a structural test asserts the only caller is the step-up
protected API route.

---

## 4. The risk engine

Order of checks, each recorded with its detail in the decision:

1. kill switch clear
2. account state **known** and fresh (≤120 s)
3. strategy allowed by its lifecycle stage in this mode
4. market data fresh (`max_quote_age_seconds`), signal data fresh (≤4×)
5. no short selling — a sell with nothing held is refused
6. *closing sells skip everything below: reducing risk is always allowed*
7. drawdown, daily loss, order frequency (minute + day), open positions
8. **sizing** = the smallest of: risk-per-trade against the stop, position cap,
   order-value cap, available cash, instrument / strategy / sector exposure
   room, and a share of displayed depth
9. **economic viability** — round-trip costs at that size against the gross edge
10. leverage after the trade

> **NO TRADE is an answer.** At a few tens of pounds the most common correct
> outcome is `NO TRADE — at 47.30 notional the round trip costs 63.2 bps against
> a gross edge of 40.0 bps, leaving -23.2 bps (needs 10.0)`. An edge that does
> not survive its costs is not an edge.

---

## 5. Orders: one signal, one order

`client_order_id = "aria-" + sha256(strategy | signal | leg)[:24]`

The same signal always produces the same id, so a retry after a network error
re-sends the same id, and a restart that replays a signal cannot open a second
position. The broker refuses the duplicate; the journal refuses it before the
broker is asked.

`data/trading/order_journal.jsonl` records every state: `INTENT` → `SUBMITTING`
→ `NEW`/`FILLED`/… or `UNKNOWN`.

### Fail closed on unknown state

A timeout, or anything that leaves an order's existence uncertain, records
`UNKNOWN` and **halts the engine**. While halted, `submit()` refuses everything.
`reconcile()` asks the broker by client order id:

| answer | result |
|---|---|
| found | its real state is recorded |
| not found | `NOT_FOUND` — the broker never had it |
| cannot ask | stays `UNKNOWN`, the halt stays on |

The halt lifts only when nothing is unsettled. It is a file, so a restart does
not forget it. `reconcile_positions()` compares ARIA's belief with the broker's
book and halts on any mismatch.

This mattered in practice: `AlpacaBroker.submit_order` used to convert a network
failure into `REJECTED`, which is a duplicate-order hazard — a retry would place
a second order. It now returns `UNKNOWN` whenever a client order id was supplied,
and `get_order_by_client_id()` exists so the unknown can be resolved.

---

## 6. Who gets which broker

`ModeGatedBroker` extends the existing `PaperOnlyBroker`, so everything that gate
guaranteed still holds (reads pass, the raw client is never handed out, unknown
methods are treated as writes). On top, **every write re-reads the mode at the
moment of the call** — a desk holding a cached order manager cannot keep trading
after the mode drops.

| caller | ceiling | consequence |
|---|---|---|
| the legacy desk (`src/desk`) | `PAPER` | never reaches real money in any mode |
| the owner's approval queue | `LIVE_AUTONOMOUS` | a human-approved trade may go live **only** in that mode, and still passes the order-value and position checks |
| the new pipeline | `paper_adapter()` / `live_adapter()` | `live_adapter()` is the only function that can return a live-capable adapter |

Before this, the approval path used the raw broker: an approved trade on a live
account executed in any configuration. It cannot now.

The adapter interface is eight methods — `get_account`, `get_positions`,
`get_balances`, `get_quote`, `submit_order`, `cancel_order`, `get_order`,
`get_open_orders` — addressed by **client** order id, because after a timeout the
broker's own id is exactly what is missing. `SimulatedBroker` is a paper venue
that charges fees and slippage, enforces cash, refuses shorts and can be made to
fail on demand (timeouts, lost acknowledgements, lookup failures) so the
recovery paths are tested rather than assumed.

---

## 7. The halt

`POST /api/trading/halt`, or `killswitch.halt(reason)`:

1. **stop new orders** — `src/execution/kill_switch.engage()`, first and
   unconditionally. A file and an environment variable, read by the broker
   adapters themselves, with no dependency on the API being up.
2. **disable autonomy** — mode forced to `RESEARCH`, desk `auto_execute` false.
3. **cancel pending orders** — every open order, best effort, each result
   recorded.
4. **positions** — `PRESERVE` (default) or `FLATTEN`.

**Position policy** (`data/trading/kill_policy.json`):

- `PRESERVE` — positions are left exactly as they are. Liquidating a book in a
  panic is a trading decision, often a bad one, and a kill switch should not
  make trading decisions by default. With `cancel_protective: false` (also the
  default) a resting sell against a held position is **left in place**, so
  halting does not strip a position of its stop.
- `FLATTEN` — reduce-only market sells for every long position. Off unless
  chosen, documented, and tested.

The halt never raises: a broker that cannot be reached is recorded as a failed
cancellation, and the kill switch is engaged regardless. Reducing risk never
requires a step-up — an emergency stop that asks you to re-authenticate is one
that gets pressed too late.

---

## 8. Arbitrage

Evaluated, never executed automatically. `net edge = gross spread − costs`,
where costs are fees per leg, slippage beyond the touch, transfer cost, borrow
(for a short leg, over the settlement/transfer window) and funding. The spread
is already paid in the leg prices — buys at the ask, sells at the bid — so it is
not charged twice.

Verdicts:

| verdict | meaning |
|---|---|
| `CANDIDATE` | net edge above threshold, liquid, likely to execute — *a research candidate, not an instruction* |
| `NOT_ARBITRAGE` | the net edge is not there (the usual answer), or the structure is not an arbitrage |
| `STALE` | a leg's quote is too old, crossed or unstamped |
| `ILLIQUID` | the size exceeds the participation cap on displayed depth, or depth is unknown |
| `FALSE_ARBITRAGE` | a spread too large to be real (>300 bps): stale feed, different instrument, different settlement, halted symbol |
| `EXECUTION_RISK` | execution probability below 75%, or negative expected value once a one-legged fill is charged for |
| `STATISTICAL_SPREAD` | mean reversion with a real risk of loss. Never given a true-arbitrage verdict |

Execution probability falls with quote age, with latency still to be spent, and
with the order's share of displayed depth; legs that cannot execute
simultaneously (transfer or settlement delay) are halved and charged the cost of
unwinding a half-filled pair.

---

## 9. Strategy lifecycle

```
DRAFT → RESEARCH → BACKTEST → WALK_FORWARD → PAPER → VALIDATED
      → LIVE_ELIGIBLE → LIVE            (and SUSPENDED, aside)
```

Each promotion requires evidence on file, checked as numbers — nothing reads
prose:

| stage | gate |
|---|---|
| `RESEARCH` | a hypothesis and named data sources |
| `BACKTEST` | ≥30 trades, costs modelled, period recorded |
| `WALK_FORWARD` | ≥3 folds, purged/embargoed, ≥30 out-of-sample trades |
| `PAPER` | deflated Sharpe > 0 (survives the number of variants tried) |
| `VALIDATED` | ≥20 paper trades over ≥20 days, realised slippage within 5 bps of the model |
| `LIVE_ELIGIBLE` | paper Sharpe > 0, a capacity estimate, **an owner review on file** |
| `LIVE` | the system's effective mode is already `LIVE_AUTONOMOUS` |

Backwards needs no evidence and no ceremony. A suspended strategy that is
reinstated comes back at `PAPER` **at best** — whatever suspended it invalidated
the evidence that promoted it.

What a stage permits: in `RESEARCH` mode any registered strategy is evaluated
(no orders are placed); in `PAPER` mode a strategy must be at `PAPER` or beyond;
in `LIVE_AUTONOMOUS` it must be at `LIVE` and nothing else. Real money therefore
needs two independent deliberate acts.

**Drift** (`drift.py`) compares a recent window with the baseline across
performance, volatility, correlation, execution, slippage and regime. Two
drifted checks, or one severe, suspends the strategy automatically — suspension
reduces capability, so it needs no approval.

**Scorecards** report trade count, win rate, profit factor, Sharpe, Sortino, max
drawdown, average return, volatility, turnover, fees, slippage, capacity, tail
loss and out-of-sample performance. `rank()` refuses to sort on profit: it uses
risk- and cost-adjusted quality with a tail penalty, and a strategy with fewer
than 20 out-of-sample trades is listed as *insufficient evidence*, which is a
different statement from "bad". Sharpe is annualised only when there is enough
elapsed time to estimate a trade rate.

---

## 10. Research memory — and what is *not* training data

Closed trades are recorded in full: strategy, signal, entry, exit, expected
edge, realised P&L, fees, slippage, holding period, regime, the reasons it was
taken and the risk state at the time. Five memories sit alongside — research,
strategy, regime, outcome, failure.

**Every row is written with `training_eligible: false`.** No training pipeline
reads `data/trading/research_memory/`, and nothing in this system promotes a
trade into a corpus. Making one would be a separate, explicit, human act.
Training is paused in this deployment and was not touched by this work.

Two reasons, both load-bearing: a model trained on its own trades learns to
repeat its own mistakes with more confidence; and these rows contain the owner's
positions and balances, which are private.

---

## 11. Measured latency — and what this system is not

Measured on this machine, 2026-09-23 (Windows dev box, retail connection):

| stage | p50 | method |
|---|---|---|
| market data (Alpaca quote, real network) | **124 ms** (first call 692 ms incl. TLS) | 5 reads of `AAPL` |
| decision (risk engine) | **0.030 ms** | 200 iterations, local |
| order generation + validation | **0.009 ms** | 200 iterations, local |
| broker acknowledgement | **0.003 ms** *(simulated venue — not a real broker)* | 200 submissions |
| broker acknowledgement, real venue | **not measured** | no order has been placed to measure it |
| fill | **not measured** | — |

`latency.classify()` derives the label from these numbers and **cannot return
"HFT"**. High-frequency trading means co-located hardware, kernel-bypass
networking and microsecond decision paths. This is a Python process reaching a
retail broker over the public internet: the decision path is fast (tens of
microseconds), and the round trip to the venue is ~124 ms, which is two to three
orders of magnitude away from HFT. The honest description is **systematic
trading with a fast local decision path**.

Note the measured Alpaca quote came back one-sided (ask 0.0) outside market
hours — and `market.check()` refuses it, because a missing or non-positive ask
is not a price. That is the stale-data protection working on real data.

---

## 12. What is enabled here, and what is not

**Enabled:** owner-token login, remote access, streaming, SENTINEL ↔ ARIA
consultation, research, paper trading, the capture/rating loop, the strategy
research architecture, arbitrage evaluation.

**Disabled:**

- live autonomous execution — `ARIA_LIVE_TRADING_ENABLED` is not set, and no
  strategy has reached `LIVE_ELIGIBLE`
- real-money orders from any autonomous path
- self-modifying production strategies — a strategy's stage changes only through
  the lifecycle, and its code is not rewritten at runtime
- model-driven orders — the pipeline takes `Signal` objects, and no model can
  produce one without a human

Current state: **mode `PAPER`**, kill switch clear, order engine not halted,
hard limits at their tight defaults.

---

## 13. Regulatory position

This is a private, single-owner system trading the owner's own personal account.
It manages no third-party money, accepts no client funds, gives no advice to
anyone else, and publishes no signals. Nothing in it is investment advice. If
that ever changes, this is where the question starts, not an afterthought.

---

## 14. API

All routes are owner-only. ★ = also requires a recent step-up.

| method | path | purpose |
|---|---|---|
| GET | `/api/trading/status` | mode, limits, strategies, halt state, what is disabled |
| POST ★ | `/api/trading/mode` | change mode (typed phrase required going up) |
| GET | `/api/trading/mode/history` | the mode ledger |
| GET | `/api/trading/limits` | in force, ceilings, defaults, pending |
| POST ★ | `/api/trading/limits` | tighten now / loosen after 24 h |
| POST | `/api/trading/halt` | **stop everything** (no step-up, deliberately) |
| GET / POST ★ | `/api/trading/kill-policy` | what a halt does to open positions |
| GET / POST | `/api/trading/strategies` | list / register |
| POST | `/api/trading/strategies/{id}/evidence` | file a research result |
| POST ★ | `/api/trading/strategies/{id}/stage` | promote / demote / suspend / reinstate |
| GET | `/api/trading/scorecard` | risk-adjusted performance and ranking |
| GET | `/api/trading/latency` | measured stage latencies |
| POST | `/api/trading/arbitrage/evaluate` | net edge after everything |
| GET / POST | `/api/trading/capital` | the contribution ledger |
| GET | `/api/trading/orders` | the order journal |
| POST | `/api/trading/orders/reconcile` | settle unknown orders, lift a halt |

`/api/trading` is in `policy.EXECUTION_PREFIXES` and `policy.NEVER_SHARED`: the
SENTINEL service role reaches none of it.

---

## 15. Operating it

```bash
# where things stand
curl -s localhost:8000/api/trading/status -H "Authorization: Bearer $ARIA_OWNER_TOKEN"

# enable paper trading (already done on this deployment)
curl -s -X POST localhost:8000/api/trading/mode \
  -H "Authorization: Bearer $ARIA_OWNER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"mode":"PAPER","reason":"why","confirmation":"ENABLE PAPER TRADING"}'

# stop everything
curl -s -X POST localhost:8000/api/trading/halt \
  -H "Authorization: Bearer $ARIA_OWNER_TOKEN" -d '{"reason":"something looks wrong"}'
```

After a halt caused by an unknown order state: reconcile
(`POST /api/trading/orders/reconcile`), release the kill switch
(`POST /api/execute/kill-switch/release`, step-up), then raise the mode again.

### The road to live, in order

1. Register a strategy and file a hypothesis and data sources.
2. Backtest with costs modelled → record the evidence → promote.
3. Purged walk-forward with out-of-sample trades and a deflated Sharpe.
4. Paper trade it — ≥20 trades over ≥20 days — and check realised slippage
   against the model. That number is the one that decides whether the cost model
   is honest.
5. Read it yourself and record the review.
6. Only then: set `ARIA_LIVE_TRADING_ENABLED=true` outside the app, restart,
   promote the strategy to `LIVE`, and raise the mode with the long phrase.

Build the capability, measure it, validate it — *then* let it trade.

---

## 16. Tests

| file | what it holds to account |
|---|---|
| `tests/test_trading_modes.py` | mode transitions, phrases, ledger, hard limits, cooling-off |
| `tests/test_trading_risk.py` | every refusal the risk engine owes |
| `tests/test_trading_orders.py` | idempotency, timeouts, reconciliation, the validator |
| `tests/test_trading_access.py` | the four claims about real money |
| `tests/test_trading_lifecycle.py` | evidence gates, suspension, scorecards, drift |
| `tests/test_trading_arbitrage.py` | costs, stale, illiquid, false and statistical |
| `tests/test_trading_pipeline.py` | end to end, latency, the halt |
| `tests/test_trading_market_costs.py` | stale-data protection, trading hours, fee models |
| `tests/test_trading_structure.py` | the import graph — no model reaches the broker |
| `tests/test_trading_api.py` | the HTTP surface and who may reach it |
