<div align="center">

# ◉ ARIA

### Open Finance Intelligence

**The open-source AI finance terminal — signals, cognition, and quant analytics in one living command deck.**

*Coding has its AI. Finance gets ARIA.*

Created by **Ariyan** · built with Claude

</div>

---

> ⚠️ **Read this first — it matters.**
> ARIA is a **research and education** tool. It analyses markets, surfaces signals, stress-tests portfolios, and explains its reasoning — but it is **not a licensed financial adviser and does not give personalised investment advice**. Signals are probabilistic, backtests are simulations of the past, and no model predicts markets reliably. Nothing ARIA outputs is a recommendation to buy or sell anything. You are responsible for your own decisions — never risk money you cannot afford to lose.

---

## What ARIA is

ARIA is a self-hosted AI finance terminal that runs entirely on your machine:

- **🧠 A cognitive engine** — an autonomous reasoning loop (ORIENT → FOCUS → RECALL → ANALYSE → DECIDE → REFLECT) running on a **local LLM** via Ollama, with long-term vector memory (ChromaDB), a learning loop that records outcomes, and an optional "frontier consult" switch that routes hard reasoning steps to a frontier model — budget-capped and off by default.
- **✦ Live Mind** — watch it think: every reasoning step streams into a cinematic chain-of-thought view with an animated core.
- **∿ A signal engine** — multi-asset composite scores (technicals + regime + ML ensemble) across equities, indices, FX, commodities and crypto, with risk parameters (stop, target, position size) attached to every call.
- **◉ Signal Map** — the whole market as a force-directed living graph (Obsidian-style), nodes sized by conviction, coloured by direction.
- **⌕ Explorer** — 3,300+ symbols across **India (₹ NSE), the UK (£ LSE), the US ($), and crypto**, loaded on demand: live quote, fundamentals, fresh news, sentiment, political-exposure flag, TradingView chart.
- **💱 Remittance watch** — GBP/INR monitored continuously with direction-framed alerts ("pound strong → good window to send UK→India") and user-set target levels.
- **⚗ Quant Lab** — a self-learning researcher: reads new arXiv q-fin papers, maps them to strategy templates with the local LLM, backtests on real data, and ranks by out-of-sample Sharpe.
- **ƒ Quant analytics** — Black-Scholes greeks, vol surfaces, multi-leg option strategies with payoff curves, and macro stress scenarios (rate shock, crash, vol spike, stagflation…) against your live book.
- **▶ Execution with a human gate** — the engine can *propose* trades to paper brokers, but **nothing executes without explicit human approval**. The brain proposes; you decide. Always.

Everything runs locally. Your data, your keys, your machine.

## The stack

| Layer | Tech |
|---|---|
| Backend | Python · FastAPI · APScheduler |
| Cognition | Ollama (local LLM) · ChromaDB · sentence-transformers · optional Anthropic API consult |
| Data | yfinance · NSE/LSE symbol universes · NewsAPI · FRED |
| ML | LightGBM ensemble · backtesting engine |
| Frontend | React + Vite · canvas animation · zero UI frameworks |
| Brokers (paper) | Alpaca · IBKR (optional) |

## Quick start

```bash
# 1. clone & install
git clone <your-repo-url> && cd trading-intelligence-system
python -m venv venv && venv/Scripts/pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. configure — copy the example and add your keys (all optional except none)
cp .env.example .env

# 3. (optional, for the AI brain) install Ollama and pull a model
ollama pull qwen2.5-coder:7b

# 4. run
venv/Scripts/python -m uvicorn backend.main:app --port 8000   # engine
cd frontend && npm run dev                                     # deck → http://localhost:3000
```

The signal engine populates on first run (`python main.py`); the brain wakes automatically if Ollama is running.

## Design

Dark crimson command-deck aesthetic: canvas particle cores, force-directed graphs, CRT scanlines, animated chain-of-thought. The entire theme lives in one design system (`frontend/src/index.css`) — every page inherits it.

## Safety principles (non-negotiable)

1. **The human is the judgment layer.** Trade proposals stop at an approval queue. Nothing irreversible is autonomous.
2. **Research, not advice.** Every surface carries the disclaimer because it's true.
3. **Local first.** Models, memory and data live on your machine. The only outbound calls are the data sources you configure and the optional, budget-capped frontier consult you explicitly enable.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, learn from it.

---

<div align="center">

*ARIA proposes. You decide.*

</div>
