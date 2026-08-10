<div align="center">

# ◉ ARIA

### Open Finance Intelligence

**A self-hosted research terminal for markets: signal generation, portfolio risk, and calibration tracking, with a hard human-approval gate on execution.**

Created by **Soundariyan Karunakaran** · built with Claude

</div>

---

> ⚠️ **Read this first — it matters.**
> ARIA is a **research and education** tool. It analyses markets, surfaces signals, stress-tests portfolios, and explains its reasoning — but it is **not a licensed financial adviser and does not give personalised investment advice**. Signals are probabilistic, backtests are simulations of the past, and no model predicts markets reliably. Nothing ARIA outputs is a recommendation to buy or sell anything. You are responsible for your own decisions — never risk money you cannot afford to lose.

---

## What ARIA does not claim

This section is first because it is the one most projects in this category leave out.

**There is no demonstrated edge.** ARIA has not been shown to beat buy-and-hold, or a moving-average cross, or a coin. It ships with the machinery to *measure* whether it does — see [Measurement](#measurement) — and that machinery currently reports `measurable: false`, because not enough predictions have resolved. Architecture is not evidence. Forty-one modules, Kelly sizing and a risk officer describe how the system is built, not whether it works.

**Module count is not a feature.** Forty-one research modules is a lot of parameters, and more parameters means more ways to fit history without noticing. Every module ships in the `experimental` tier and stays there until its own resolved calls say otherwise. See [Module tiers](#3-which-modules-have-earned-a-vote--tiers).

**The language model does not predict returns.** Its job is synthesis, explanation and retrieval. All 41 research modules are statistical or fitted-model code; none of them is an LLM. Where a language model does produce a score, it is registered with `provenance="narrative"` and the registry mechanically widens its confidence interval so it cannot outvote a module with real observations behind it. Fluent reasoning is not an observation count.

**It is a single-author project** with no community validation, no published live results, and no independent replication. The safety gate is the claim in this README you can most reasonably trust, because it is a code-enforced constraint you can read in one file rather than a performance claim you would have to take on faith.

---

## What ARIA is

A self-hosted finance terminal that runs on your machine:

- **Research engine (V5)** — 41 independently-callable modules across 7 families, combined into a documented ensemble, then meta-reasoning, a risk gate, a self-audit and a learning loop. Every module returns bull/bear/neutral summing to 100, a confidence interval, sourced evidence, and its own declared weaknesses — or it abstains. Most price and quant modules state a condition and ask the instrument's own history how often that condition preceded a positive return, so the interval is Wilson on a genuine observation count rather than a stated prior. **Confidence means P(direction is correct)**, so it lives in [0.5, 1.0]; the trading bar is 55%.
- **Track Record** — calibration, not just accuracy: Brier score, skill against a coin flip, skill against the base rate, expected calibration error, and the paired comparison against naive baselines. It refuses to report below 20 resolved calls.
- **Cognitive engine** — a reasoning loop (ORIENT → FOCUS → RECALL → ANALYSE → DECIDE → REFLECT) on a local LLM via Ollama, with vector memory (ChromaDB) and an optional frontier-model consult, budget-capped and off by default. This layer explains and retrieves; it does not forecast.
- **Signal engine** — multi-asset composite scores (technicals + regime + ML ensemble) across equities, indices, FX, commodities and crypto, with stop, target and position size attached to every call.
- **Research desk** — **21,000+ symbols across 40 exchanges**: full live listings for the US (NASDAQ Trader), India (NSE + BSE bhavcopies) and the UK (LSE), plus curated index constituents for 34 further markets — Europe, Japan, Hong Kong, China A-shares, Canada, Australia, Korea, Taiwan, Brazil, Mexico, South Africa, Singapore, Saudi Arabia, Indonesia, Thailand, Malaysia, New Zealand, Turkey and Poland — alongside FX, futures, indices and crypto. Each symbol carries quote, fundamentals, news, sentiment, political-exposure flag, charts, multi-timeframe indicator scan and ATR-based entry/stop/target. Every curated symbol is price-validated against the vendor by `scripts/validate_world_symbols.py`; unpriceable names are removed rather than left to abstain silently.
- **Quant analytics** — Black-Scholes greeks, vol surfaces, multi-leg option strategies with payoff curves, and macro stress scenarios (rate shock, crash, vol spike, stagflation) against your live book.
- **Quant Lab** — reads new arXiv q-fin papers, maps them to strategy templates, backtests on real data, and ranks by out-of-sample Sharpe.
- **The Desk** — a multi-agent paper-trading desk: evidence-cited analyst agents feed a bull-vs-bear debate with a deterministic judge, a code-enforced risk officer (name/sector caps, portfolio heat, regime gate, drawdown circuit-breaker), and a quarter-Kelly portfolio manager. Every claim cites a number; every fill links to its debate transcript.
- **Remittance watch** — GBP/INR monitored continuously with direction-framed alerts and user-set targets.
- **Execution with a hard paper gate** — by default **nothing executes without explicit human approval**. You can optionally arm auto-execute **on the paper account only**. A live account **always** routes to the manual approval queue — enforced in code, with no override.

Everything runs locally. Your data, your keys, your machine.

---

## Measurement

Three questions, three different answers, deliberately reported separately. Conflating them is how a system with no edge comes to look validated.

### 1. Are the confidence numbers honest? — *calibration*

When ARIA says 62%, does it happen 62% of the time? Reported as Brier score, skill against a coin flip, and expected calibration error, bucketed by stated confidence.

`src/v5/track_record.py` · `GET /api/v5/track-record`

Nothing is reported below **20 resolved calls**, and no individual bucket below **5**. A hit rate on twelve trades is noise wearing a percentage sign.

### 2. Is it better than a napkin? — *baselines*

Calibration is a property of the confidence numbers. Skill is a property of the calls, and they come apart: predict "bull" on every US equity at 55% confidence and, in a market that rises 55% of the time, you are perfectly calibrated and perfectly worthless.

So the naive strategies are re-run over **exactly the calls ARIA made** — same tickers, same dates, same horizons, same realised returns, using only price history from before each call — and compared on the paired sample:

| Baseline | Rule |
|---|---|
| `buy_and_hold` | Always bullish. What the money was doing anyway. |
| `sma_20_50` | 20-day SMA above 50-day SMA. |
| `momentum_60d` | Sign of the trailing 60-day return. |

The comparison uses **McNemar's test on the discordant pairs** — only the calls where ARIA and the baseline disagreed carry information about which is better. Two systems that agree 39 times out of 40 are not distinguishable, however good both their hit rates look, and the report says `undetermined` rather than quoting a gap. The headline reports the **weakest** result, not the best: beating one baseline out of three is not edge.

Also reported: Brier against a **constant base-rate forecaster**. Beating a coin flip is easy in a market with drift. Beating the base rate means the inputs did something.

`src/v5/baselines.py` · `GET /api/v5/baselines`

### 3. Which modules have earned a vote? — *tiers*

| Tier | Meaning |
|---|---|
| `experimental` | Fewer than 25 resolved calls. Unproven. **All 41 start here.** |
| `provisional` | Enough calls to measure; not distinguishable from chance. |
| `core` | Hit rate significantly better than a coin at p<0.05. |
| `benched` | Significantly *worse* than a coin. Excluded from live recommendations. |

Tiers are computed from `learning.module_scorecard()` — resolved outcomes and nothing else. There is no list to edit: a module cannot be promoted by its author.

With 41 modules tested at p<0.05, **roughly two are expected to clear the bar by chance alone.** The report therefore also carries a Benjamini-Hochberg adjusted verdict across the whole family of tests, and `core_after_correction` is the field to read when asking whether a promotion is real.

Only `benched` changes the arithmetic. Experimental and provisional modules still vote — excluding everything unproven would empty the ensemble on day one — and the ensemble already discounts weak modules through learned reliability multipliers that move only after a significant sample.

`src/v5/tiers.py` · `GET /api/v5/tiers`

### Provenance

Every module declares what kind of process produced its number, and the registry stamps it from the registration rather than trusting the module's own claim:

| Provenance | What it means | What it's worth |
|---|---|---|
| `statistical` | A condition tested against the instrument's own history. Wilson interval on a real observation count. | Confidence is a measured frequency. |
| `model` | A fitted estimator (LightGBM, HMM, Kalman, PCA). | Trustworthy in proportion to out-of-sample validation — a separate question from its interval. |
| `narrative` | A language model produced or shaped the score. | Not evidence. Interval mechanically widened; capped below the weight of a measured module. |

All 41 shipped modules are `statistical` or `model`. The floor exists so that adding a `narrative` one later cannot quietly launder LLM fluency into statistical confidence.

`src/v5/registry.py`

---

## Known limitations

- **No live or paper track record has been published.** Every measurement surface above currently reports `measurable: false`. This is the honest state of the project, not an oversight.
- **Backtests are in-sample until proven otherwise.** Walk-forward validation exists (`src/models/walk_forward.py`) but has not been run across every module. Treat backtested Sharpe as a hypothesis.
- **41 modules on one 21-day horizon is a lot of correlated tests.** The tier system corrects for it; the family weights in `src/v5/ensemble.py` are reasoned, not fitted, and have not themselves been validated out-of-sample.
- **Data quality is vendor-dependent.** yfinance is the primary source. Survivorship bias, split/dividend adjustment errors and stale caches all flow through to results. The system falls back to a stale cache rather than abstaining, and labels it when it does.
- **The scope is wide for one maintainer** — equities, FX, crypto, options, multi-agent orchestration. Depth per feature varies, and the parts with the least test coverage are the newest.
- **The LLM layer has not been evaluated for anything.** It is a synthesis and interface layer. Its fluency is not correlated with correctness and should not be read as confidence.

---

## The twelve destinations

| | Destination | What it answers |
|---|---|---|
| ◉ | **ARIA Chat** | Ask it anything, in words |
| ◆ | **ARIA V5** | What do 41 engines make of this, and how sure are they? |
| ◬ | **Research** | Everything known about one symbol |
| ⚗ | **Quant Lab** | What has the automated researcher found and backtested? |
| ◈ | **Brain** | What is it thinking, and what does it remember? |
| ⌂ | **Command** | What matters right now — overview, alerts, daily report |
| ∿ | **Markets** | Signals, macro, futures, options |
| ★ | **Recommend** | Where the technical consensus landed |
| ▣ | **Portfolio** | What is held, and how concentrated is it? |
| ƒ | **Stress** | What breaks the book — scenarios, greeks, payoffs |
| ◎ | **Track Record** | Is any of this working? Calibration and baselines |
| ▦ | **The Desk** | Debate, risk, execution queue |

Press **Ctrl/⌘+K** anywhere for the command palette — a page name, an old page name, or a ticker.

## The stack

| Layer | Tech |
|---|---|
| Backend | Python · FastAPI · APScheduler |
| Cognition | Ollama (local LLM) · ChromaDB · sentence-transformers · optional Anthropic API consult |
| Data | yfinance · NSE/LSE symbol universes · NewsAPI · FRED |
| ML | LightGBM ensemble · backtesting engine |
| Frontend | React + Vite · zero UI frameworks |
| Brokers (paper) | Alpaca · IBKR (optional) |

## Quick start

```bash
# 1. clone & install
git clone <your-repo-url> && cd trading-intelligence-system
python -m venv venv && venv/bin/pip install -r requirements.txt
cd frontend && npm install && cd ..

# 2. configure — copy the example and add your keys
cp .env.example .env

# 3. (optional, for the reasoning layer) install Ollama and pull a model
ollama pull qwen2.5-coder:7b

# 4. run
venv/bin/python -m uvicorn backend.main:app --port 8000   # engine
cd frontend && npm run dev                                 # deck → http://localhost:3000
```

On Windows the interpreter is `venv\Scripts\python.exe`.

The signal engine populates on first run (`python main.py`); the reasoning loop wakes automatically if Ollama is running. Run the tests with `venv/bin/python -m pytest tests -q`.

## Design

Dark command-deck aesthetic; the theme lives in one design system (`frontend/src/index.css`) and every page inherits it. It is also meant to be usable, which is a separate problem from being legible: hover or focus any dotted term for a plain-English explanation of what the number means for a decision; text size, contrast and motion are adjustable from settings; the rail collapses on narrow screens and dense tables scroll inside themselves rather than pushing the page sideways.

## Safety principles (non-negotiable)

1. **The human is the judgment layer.** Trade proposals stop at an approval queue. Nothing irreversible is autonomous. A live account cannot be armed for auto-execute; this is enforced in code with no override.
2. **Research, not advice.** Every surface carries the disclaimer because it's true.
3. **Local first.** Models, memory and data live on your machine. The only outbound calls are the data sources you configure and the optional, budget-capped frontier consult you explicitly enable.
4. **An unmeasurable quantity is reported as unmeasurable** — never as a number with a wide error bar that readers will treat as fact.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, learn from it.

---

<div align="center">

*ARIA proposes. You decide.*

</div>
