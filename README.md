<div align="center">

# ◉ ARIA

### Open Finance Intelligence

**The open-source AI finance terminal — signals, cognition, and quant analytics in one living command deck.**

*Coding has its AI. Finance gets ARIA.*

Created by **Soundariyan Karunakaran** · built with Claude

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
- **◆ ARIA V5** — 41 independently-callable research modules across 7 families, combined into an ensemble, then meta-reasoning, a risk gate, a self-audit and a learning loop. Every module returns bull/bear/neutral summing to 100, a real confidence interval, sourced evidence, and its own declared weaknesses. Most price and quant modules state a condition and ask the instrument's own history how often that condition preceded a positive return, so the interval is Wilson on a genuine observation count rather than a stated prior. **Confidence means P(direction is correct)**, so it lives in [0.5, 1.0]; the trading bar is 55%.
- **◎ Track Record** — the honesty page. Merges every labelling loop and reports **calibration**, not just accuracy: Brier score, skill against a coin flip, and expected calibration error. It refuses to report at all below 20 resolved calls, because a hit rate on twelve trades is noise wearing a percentage sign.
- **◬ Research** — 3,300+ symbols across **India (₹ NSE), the UK (£ LSE), the US ($), and crypto**, loaded on demand: live quote, fundamentals, fresh news, sentiment, political-exposure flag, TradingView chart, plus a multi-timeframe indicator scan and ATR-based entry/stop/target.
- **💱 Remittance watch** — GBP/INR monitored continuously with direction-framed alerts ("pound strong → good window to send UK→India") and user-set target levels.
- **⚗ Quant Lab** — a self-learning researcher: reads new arXiv q-fin papers, maps them to strategy templates with the local LLM, backtests on real data, and ranks by out-of-sample Sharpe.
- **ƒ Quant analytics** — Black-Scholes greeks, vol surfaces, multi-leg option strategies with payoff curves, and macro stress scenarios (rate shock, crash, vol spike, stagflation…) against your live book.
- **▦ The Desk (v3)** — an autonomous multi-agent trading desk: evidence-cited analyst agents (technical / fundamental / sentiment / macro) feed a bull-vs-bear debate with a deterministic judge, a code-enforced risk officer (name/sector caps, portfolio heat, regime gate, drawdown circuit-breaker), and a quarter-Kelly portfolio manager. Every claim cites a real number; every fill links back to its debate transcript.
- **▶ Execution with a hard paper gate** — by default **nothing executes without explicit human approval**. You can optionally arm **auto-execute on the paper account only**: fills happen on their own and you are *informed after* (feed + phone push), never asked. A live account **always** routes to the manual approval queue — this is enforced in code with no override.

Everything runs locally. Your data, your keys, your machine.

## The twelve destinations

The deck used to have twenty-two pages. Four of them answered "should I buy this?"
and five were variations on "what is the AI thinking", so the merged ones became
tabs inside the page they belong to. Every old path still redirects, and nothing
was dropped.

| | Destination | What it answers |
|---|---|---|
| ◉ | **ARIA Chat** | Ask it anything, in words |
| ◆ | **ARIA V5** | What do 41 independent engines make of this, and how sure are they? |
| ◬ | **Research** | Everything known about one symbol (Overview and Deep tabs) |
| ⚗ | **Quant Lab** | What has the automated researcher found and backtested? |
| ◈ | **Brain** | What is it thinking, and what does it remember? |
| ⌂ | **Command** | What matters right now — overview, alerts, the daily report |
| ∿ | **Markets** | The board: signals, macro, futures, options |
| ★ | **Recommend** | Where the technical consensus actually landed |
| ▣ | **Portfolio** | What is held, and how concentrated is it? |
| ƒ | **Stress** | What breaks the book — scenarios, greeks, payoffs |
| ◎ | **Track Record** | Is any of this working? Calibration, not vibes |
| ▦ | **The Desk** | The autonomous desk: debate, risk, execution queue |

Press **Ctrl/⌘+K** anywhere for the command palette — type a page name, an old
page name, or just a ticker to go straight to its dossier.

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

It is also meant to be *usable*, which is a separate problem from being legible:
hover or focus any dotted term for a plain-English explanation of what the number
means for a decision; text size, contrast and motion are adjustable from the
settings button; the rail collapses off-canvas on narrow screens and dense tables
scroll inside themselves rather than pushing the page sideways.

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
