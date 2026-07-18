# ARIA v3 — THE AUTONOMOUS TRADING DESK
## Build prompt (for Claude, or any capable coding agent)

> Reference to surpass: https://github.com/TauricResearch/TradingAgents
> TradingAgents is a multi-agent LLM trading framework: an analyst team
> (fundamentals / sentiment / news / technical) feeds a bull-vs-bear researcher
> debate, a trader agent sizes the call, risk management assesses it, a
> portfolio manager approves it, and a reflection loop feeds realized P&L back
> into future prompts. It runs on LangGraph, supports many LLM vendors, and
> executes only in simulation.
>
> ARIA already has most of this DNA — and beats it on infrastructure (real
> signal engine, quant bridge, persistent vector memory, cinematic UI,
> local-first models, multi-market ₹/£/$ universe). v3 unifies the pieces into
> one autonomous desk and then goes past TradingAgents on two axes it lacks:
> **grounded evidence** (every claim cites a real ARIA number, not LLM recall)
> and **autonomous paper execution with the human informed, not asked**.

---

## WHAT ARIA ALREADY HAS (do not rebuild — orchestrate)

| TradingAgents concept | ARIA equivalent today | File |
|---|---|---|
| Technical analyst | Signal engine — 40+ features, composite score, ML ensemble | `src/signals/signal_engine.py`, `data/signals.json` |
| Sentiment analyst | Sentiment module + on-demand enrichment | `src/sentiment/`, `src/data/enrichment.py` |
| News analyst | NewsAPI enrichment + political engine | `src/data/enrichment.py`, `src/political/` |
| Fundamentals analyst | Universe dossiers (5y history + fundamentals, cached) | `src/data/universe.py` |
| Bull/bear debate | 5-perspective debate + contradiction engine | `src/cognitive/aria_core.py`, `contradiction_engine.py` |
| Trader / sizing | Quarter-Kelly planner | `src/brain/cognitive/planner.py` |
| Risk management | Kelly veto + quant scenarios | `planner.py`, `src/gs_quant_bridge/scenario_engine.py` |
| Portfolio manager approval | Approval queue + executor | `src/execution/approval_queue.py`, `src/brain/cognitive/executor.py` |
| Reflection / memory | Learner + ChromaDB long-term memory | `src/brain/cognitive/learner.py`, `long_term_memory.py` |
| Multi-vendor LLM | Local Ollama + budget-capped frontier consult | `reasoner.py` (`_frontier`), `data/brain_consult.json` |
| Simulated execution | Alpaca **paper** broker + order manager | `src/execution/alpaca_broker.py`, `order_manager.py` |

**Two real gaps to close:**
1. The pieces above run as *separate* subsystems. v3 wires them into **one desk
   pipeline**: analysts → debate → risk → slate → execution → reflection.
2. Execution today always waits for a human. The user wants **autonomous paper
   execution**: approved trades fire on their own; the human is *informed after*,
   never asked first. (Safely — see Module 4.)

---

## MODULE 1 — ANALYST AGENTS  (`src/desk/analysts/`)

Thin agents that turn ARIA's existing data into typed, scored, **evidence-cited**
opinions. This is where ARIA beats TradingAgents: opinions ground in real numbers.

- `technical_agent.py`  ← `signals.json` (composite, trend, momentum, vol, ML horizons)
- `fundamental_agent.py` ← universe dossier (P/E, ROE, growth, margins, 52w return)
- `sentiment_agent.py`  ← `enrichment.enrich()` (news + sentiment + political flag)
- `macro_agent.py`      ← `macro_data.json` (regime, VIX, DXY, yields) → market-wide conditioner

Common return type:
```python
@dataclass
class Opinion:
    agent: str; ticker: str
    view: str            # "bull" | "bear" | "neutral"
    conviction: int      # 0-100
    thesis: str          # one paragraph
    evidence: list       # [{claim, value, source}]  ← provenance, always
```
Cheap agents run on the local model; only the final synthesis may consult the
frontier (switch + budget already exist).

## MODULE 2 — THE DEBATE  (`src/desk/debate.py`)

Reuse / elevate the existing `src/cognitive/aria_core.py` debate. Per focus ticker:
1. **Bull researcher** builds the long case from bullish evidence.
2. **Bear researcher** rebuts using bearish evidence + the macro conditioner.
3. Up to **N rounds** (config, default 2); each round must *answer the other's
   strongest point*, not restate its own.
4. **Judge** synthesises → `conviction`, `key_risk`, `invalidation_level`, verdict.
Persist the full transcript to `data/desk/debates/<id>.json` — it powers the UI
and becomes training data. Feed the `contradiction_engine` in as an anti-drift
guardrail (flag when bull & bear both cite the same number to opposite ends).

## MODULE 3 — RISK OFFICER + PORTFOLIO MANAGER  (`src/desk/`)

`risk_officer.py` — enforces hard rules **in code** (an LLM never overrides these):
- ≤5% equity per name, ≤25% per sector
- portfolio heat cap (max simultaneous open risk)
- regime gate (bearish/crisis → higher conviction bar, smaller size)
- correlation check (no stacking 5 correlated longs)
- daily trade-count cap + **drawdown circuit-breaker** (halt new entries if the
  paper account is down > X% on the day)

`portfolio_manager.py` — ranks survivors into a **trade slate**:
`[{ticker, side, qty, conviction, thesis, stop, target, sector, debate_id}]`,
sized by the quarter-Kelly planner, capped by the risk officer.

## MODULE 4 — AUTONOMOUS PAPER EXECUTION  ⚠ paper-only, hard-gated  (`src/desk/auto_executor.py`)

The one genuinely new behaviour: **approved trades execute automatically; the human
is informed after, never asked first.**

```
SAFETY CONTRACT — do not weaken, ever:
  1. Read ALPACA_PAPER. If it is not exactly "true", auto-exec is DISABLED and
     every trade falls back to the manual approval queue. There is no override.
  2. Gate on data/desk_config.json {"auto_execute": false} — default OFF; the user
     arms it explicitly in the UI.
  3. Per-day notional budget + max-trades/day cap. Breach → stop, notify.
  4. Every auto-fill is (a) logged to data/desk/executions.jsonl, (b) pushed to the
     alerts feed, (c) optionally pushed to the user's phone (ntfy/Pushover). The
     user is INFORMED of every fill: ticker, side, qty, fill price, one-line thesis.
  5. A LIVE account ALWAYS routes to the manual approval queue — auto-exec can only
     ever touch the paper account.
```

Cycle: analysts → debate → slate → risk officer →
**if `auto_execute` AND account is paper:** `OrderManager.execute()` immediately
(no approval wait) → record fill → notify. **else:** push to the approval queue.

Endpoints (additive, `backend/main.py`, lazy imports):
```
GET  /api/desk/status         desk state, auto_execute flag, day budget used, account=paper|live
POST /api/desk/auto-execute   {enabled}   ← 409/refuse if account is live
POST /api/desk/run-now        run one analysts→debate→slate→(exec) cycle
GET  /api/desk/slate          latest ranked slate + debate transcripts
GET  /api/desk/executions     auto-execution log (what ARIA bought, when, why)
GET  /api/desk/pnl            paper equity curve + open positions + day/total P&L
```
Schedule a desk cycle every N minutes on the existing APScheduler daemon.

## MODULE 5 — REFLECTION (beat TradingAgents here)

Extend `learner.py`: when a paper position closes, write the outcome into ChromaDB
**tagged with its `debate_id`**, so a future debate on a similar setup can RECALL
"last time this bull thesis fired, it closed −3% in 4 days." Semantic, per-thesis
memory — richer than TradingAgents' flat markdown log.

## MODULE 6 — THE DESK UI  (`frontend/src/pages/Desk.jsx`, route `/desk`, OPERATIONS group)

The showpiece, in the crimson command-deck aesthetic (`index.css` tokens):
- **Debate theatre** — bull (green) vs bear (red) columns, arguments typing round
  by round, judge verdict landing on a conviction gauge.
- **The slate** — ranked cards (ticker, side, size, conviction, thesis, stop/target).
- **AUTO-EXECUTE toggle** — big, with the guard state shown:
  `● PAPER — auto-exec armed` (green) / `○ LIVE — manual only` (locked).
- **Fills feed** — every auto-execution live: "▲ BOUGHT 12 AAPL @ $312 · thesis: …"
- **Paper P&L** — equity curve, open positions, day/total return, win rate.

---

## CONSTRAINTS
1. Windows / `pathlib` / venv python; additive endpoints only; lazy imports (see CLAUDE.md).
2. **Auto-execution is paper-only and OFF by default.** Module 4's contract is non-negotiable. Real money always goes through human approval.
3. **Hard risk rules live in code.** The debate *informs* sizing; it never overrides the risk officer's caps or the circuit-breaker.
4. **Reuse, don't duplicate** — bus, router, planner, executor, memory, enrichment, order manager, design system already exist. v3 orchestrates them.
5. **Every fill is explainable** — links to its debate transcript and the evidence that drove it. No black-box trades.

## BUILD ORDER
1. Analyst agents (typed, evidence-cited Opinions). 2. Debate engine + transcript
persistence + contradiction guard. 3. Risk officer + portfolio manager (code-enforced).
4. Auto-executor with the full safety contract + notifications. 5. Desk endpoints +
scheduler. 6. Reflection tagging. 7. The Desk UI. 8. Verify end-to-end on paper:
watch a full autonomous cycle, confirm you're informed of the fill, confirm it
**refuses to arm on a live account.**

**Success test:** arm AUTO-EXECUTE, walk away, come back to find ARIA has run a full
analysts → debate → risk → slate → paper-fill cycle on its own — and your feed/phone
tells you exactly what it bought and why, with zero prompts asked of you and zero
possibility it touched real money.
