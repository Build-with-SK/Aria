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
> ARIA already has half of this DNA — and beats it on infrastructure (real
> signal engine, quant bridge, persistent vector memory, live UI, local-first
> models). v3 closes the agent-team gap and then goes past it.

---

## WHAT ARIA ALREADY HAS (do not rebuild — extend)

| TradingAgents concept | ARIA equivalent today |
|---|---|
| Technical analyst | `src/signals/signal_engine.py` — 40+ features, composite score |
| Sentiment analyst | `src/sentiment/` + on-demand `src/data/enrichment.py` |
| News analyst | NewsAPI enrichment + political engine (`src/political/`) |
| Fundamentals analyst | universe dossiers (5y history + fundamentals, cached) |
| Bull/bear debate | `src/cognitive/aria_core.py` 5-perspective debate (Bull/Bear/Risk/Quant/Macro) + `contradiction_engine.py` |
| Trader agent | `src/brain/cognitive/planner.py` (quarter-Kelly sizing) |
| Risk management | Kelly veto + `src/gs_quant_bridge/` scenarios |
| Portfolio manager | approval queue (now: autopilot in