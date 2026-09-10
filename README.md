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

**There is no demonstrated edge.** As of 2026-09-06 the record reads *201 independent resolved events, 52.2% correct (95% CI 45.4%–59.0%) — indistinguishable from chance.* That figure moves as predictions resolve; `GET /api/ledger/investigate` is the live one. That sentence is generated from the ledger, not written by hand, and it appears in the daily report and the track record because a hit rate without its interval reads as a measured edge. Architecture is not evidence. Forty-one modules, Kelly sizing and a risk officer describe how the system is built, not whether it works.

**Module count is not a feature.** Forty-one research modules is a lot of parameters, and more parameters means more ways to fit history without noticing. Every module ships in the `experimental` tier and stays there until its own resolved calls say otherwise.

**The language model does not predict returns.** Its job is synthesis, explanation and retrieval. All 41 research modules are statistical code; none of them is an LLM. Where a language model does produce a score, it is registered with `provenance="narrative"` and the registry mechanically widens its confidence interval so it cannot outvote a module with real observations behind it. Fluent reasoning is not an observation count.

**It is a single-author project** with no community validation, no published live results, and no independent replication. The safety gate is the claim in this README you can most reasonably trust, because it is a code-enforced constraint you can read in one file rather than a performance claim you would have to take on faith.

---

## What ARIA is

A self-hosted finance terminal that runs on your machine. Ten background workers, a FastAPI backend, a React deck, and a local LLM that explains rather than forecasts.

- **Research engine (V5)** — 41 independently-callable modules across 7 families (macro 10, quant 8, price 7, machine 6, fundamental 5, behavioural 3, volatility 2), combined into a documented ensemble, then meta-reasoning, a risk gate, a self-audit and a learning loop. Every module returns bull/bear/neutral summing to 100, a confidence interval, sourced evidence, and its own declared weaknesses — or it abstains. **Confidence means P(direction is correct)**, so it lives in [0.5, 1.0]; the trading bar is 55%.
- **The brain** — one reasoning loop (ORIENT → FOCUS → RECALL → ANALYSE → DECIDE → REFLECT) on a local model via Ollama, with vector memory (ChromaDB), Obsidian vault retrieval and an optional frontier consult that is budget-capped and off by default.
- **Signal engine** — multi-asset composite scores across equities, indices, FX, commodities and crypto, with stop, target and position size attached to every call. Every signal now carries its own provider symbol and quote unit (see [Data integrity](#data-integrity)).
- **Research desk** — 22,000+ symbols across 47 venues: live listings for the US, India (NSE + BSE) and the UK, plus curated constituents for further markets, alongside FX, futures, indices and crypto.
- **The Desk** — a multi-agent paper-trading desk: evidence-cited analyst agents feed a bull-vs-bear debate with a deterministic judge, a code-enforced risk officer, and a quarter-Kelly portfolio manager. Every claim cites a number; every fill links to its debate transcript.
- **Daily report** — one dated intelligence briefing per day, written once and kept forever.
- **Live world feed** — continuous, deduplicated observations with their place on an evidence ladder.
- **Execution with a hard paper gate** — by default **nothing executes without explicit human approval**. You can optionally arm auto-execute **on the paper account only**. A live account **always** routes to the manual approval queue — enforced in code, with no override.

Everything runs locally. Your data, your keys, your machine.

### SENTINEL

SENTINEL is a **separate** general intelligence that runs in its own process with its own memory and permissions. It is not part of this repository and ARIA does not depend on it. The entire coupling is HTTP — `src/consult/sentinel_client.py` is the transport, `src/consult/bridge.py` is the judgement about when a second opinion is worth asking for.

ARIA consults it when the question is genuinely hard: evidence that conflicts, an anomaly that may mean a broken assumption, a problem outside the trading domain, or a high-impact call held with less than firm confidence. `should_consult()` is deterministic — using a language model to decide whether to use a language model is a cost with no matching gain.

Three rules make the bridge safe to have:

- **Silence is not agreement.** If SENTINEL is unreachable the result says so explicitly, and the human approving a trade sees `[No independent review: SENTINEL was unreachable. This is not agreement.]` attached to the thesis. That failure otherwise looks exactly like success.
- **It is advisory.** SENTINEL cannot approve or block a trade. Its objections are attached to the proposal for the human to read. A consultant with a veto would be a second decision maker, and the approval queue exists so there is one.
- **Automatic consultation is opt-in.** Set `ARIA_SENTINEL_CONSULT=true` to enable it on the trading path; it is off by default, because a 120-second timeout inside a fifteen-minute loop turns an enhancement into an outage.

Configure with `SENTINEL_URL` (default `http://127.0.0.1:8300`) and a `SENTINEL_CONSULT_TOKEN`. With neither set, ARIA runs exactly as it does now and says so when asked. Every consultation — and every failure to obtain one — is published to the event bus, so the record shows what was asked, what came back, and whether ARIA took it.

To use it: start the SENTINEL process listening on `SENTINEL_URL`, put the same `SENTINEL_CONSULT_TOKEN` in both systems, and open **SYSTEM** in the deck. The panel there shows whether a second opinion is currently possible and, when it is, lets you ask one directly — `ASK` for an independent read, `RED-TEAM` to hand over a thesis and have it attacked. A consultation runs on a local model and takes roughly 20–45 seconds. Whether you took the advice is worth recording; it is the only signal either system gets about whether the bridge is worth its cost.

---

## Architecture

The layering exists to answer one question in one place. Where two components could plausibly own a fact, exactly one does, and the other reads it.

**Identity and market data** (`src/core/identity.py`, `src/data/currency.py`, `src/data/universe.py`)
A symbol master of 22,000 instruments and the rules for deciding *which security a string refers to*. This layer is load-bearing for everything above it and is the subject of [Data integrity](#data-integrity) below.

**World model** (`src/core/world.py`)
The single answer to "what does ARIA currently believe?" — regime, breadth, volatility, portfolio, record, worker health, and an explicit list of blind spots. Diffing two snapshots answers "what changed?". Every block carries an `as_of` and a staleness flag, because this codebase has already rendered a three-month-old VIX under a heading that said LIVE.

**Macro regime** (`src/macro/regime.py`)
A growth/inflation quadrant over CPI, the 10Y–2Y spread, VIX and unemployment. Deterministic and inspectable: the thresholds are data, the evidence is returned in words, and the classifier reports how much of its answer it actually observed rather than defaulted.

**Research** (`src/research/eye.py`, `quality.py`, `live_news.py`)
A perception loop that watches sources on their own cadence, plus an evidence ladder that places each observation by source tier and claim type. `live_news.py` is a reader over that store, not a second store.

**Cognition and memory** (`src/brain/`)
The reasoning daemon, the ChromaDB memory, the Obsidian vault index, and `aggregate.py` — which composes all of it into the one state the interface reads.

**Ledger, attribution, calibration** (`src/core/ledger.py`, `attribution.py`, `calibration.py`)
Every prediction is recorded with its reference price, horizon and evidence, graded when its horizon elapses, and reported with Wilson intervals over distinct events rather than ledger rows.

**Portfolio and strategies** (`src/desk/`, `src/execution/`, `src/v5/`)
Proposal, debate, risk gate, sizing, approval queue.

**Daily reports** (`src/report/daily.py`) and **workers** (`src/core/workers.py`)
Covered below.

**UI** (`frontend/src/`)
Eight destinations, one front door. Inline styles only; no UI framework.

---

## Core design principles

These are the opinions the code actually enforces. Each one exists because something went wrong without it.

**One instrument → one canonical provider identity.** A display ticker is a label inside some namespace; the provider symbol is the instrument. Any function that resolves identity from a bare ticker without a namespace is guessing, and this system has the bruises to prove it.

**Fail closed.** The identity migration refuses to run if the ledger moved underneath its plan. Grading refuses when identity cannot be established. An unresolvable row is skipped and reported, never filled with a best guess.

**Evidence stays separated from confidence.** Source tier ("how much weight does this source carry?") and claim type ("what kind of statement is this?") are different axes and are never collapsed into one credibility number. A rumour on a high-tier feed is still a rumour.

**Confidence means coverage, not conviction.** The regime classifier reports the fraction of its input weight that came from an observed value. A dramatic reading on two of four inputs reports 50%, not 95%.

**Honest uncertainty beats a confident number.** An unmeasurable quantity is reported as unmeasurable, never as a number with a wide error bar that readers will treat as fact. Nothing is reported below 20 resolved calls.

**Historical records are reproducible.** Predictions carry what ARIA called the thing *and* which instrument was actually evaluated. Migrations are additive; subject, price and outcome digests are verified unchanged.

**One brain aggregate.** `/api/brain` composes status, regime, cognition, memory, knowledge and world. Panels do not each fetch their own slice and then disagree on screen.

**Activity, not chain-of-thought.** The live cognition stream says what ARIA is *doing* — "Comparing technical, fundamental and ML evidence — ARKK" — and what each phase concluded. The model's intermediate narration stays server-side. That boundary is drawn once, in `src/brain/aggregate.phase_activity()`, so a component cannot quietly move it.

**Blind spots are part of the output.** A world model that omits what it cannot see invites the reader to treat absence of evidence as evidence of absence. In this system that has already happened once.

**The human is the judgment layer.** Trade proposals stop at an approval queue. A live account cannot be armed for auto-execute; this is enforced in code with no override.

---

## Data integrity

This is the part worth reading even if you skip the rest, because it is the failure mode that cost the most and the one most likely to exist in your own system.

ARIA holds **two instrument namespaces** that both key on a bare ticker and legitimately disagree:

```
signals namespace    configs/universe.yaml keys ARE provider symbols
                     BA -> Boeing, US, USD      (yfinance("BA") returns Boeing)

master  namespace    universe.db DISPLAY symbols
                     BA -> BA.L, BAE Systems, LSE, GBp
```

Both are correct. The bug was a function that answered without a namespace: `native_currency("BA")` returned `GBp`, so a $214 Boeing price was read as 214 pence and displayed at **$2.92**. A hundredfold error assembled out of two true facts joined on a ticker that means different things on each side.

The fix is not to normalise the pence away. `100 GBp = £1.00`, stored currency `GBP`, `price_unit` `GBp`, multiplier `0.01` — that model is correct and load-bearing, and replacing GBp with GBP would *introduce* the error, because every reader that already divides by 100 would do it twice.

What changed is which symbol the currency resolves from:

- `identity.provider_identity()` and `currency.provider_currency()` never consult the display column. A provider symbol carries its venue in its suffix — `.L` is London in pence, `=X` is an FX cross, `=F` is a future, `^` is an index level with no currency, bare is the US listing — so nothing has to guess.
- `describe()` and `native_currency()` remain the display-symbol readings. They are for search, not for prices.
- `currencies_for()` defaults to the signals namespace; the master reading has to be asked for.
- Signals now carry their own `provider_symbol`, `venue` and `price_unit`, so no downstream reader re-derives anything.

Two related classes of defect came out of the same audit and are worth naming, because both are easy to reproduce elsewhere:

**Half-written identity.** An upsert that claims a column on `INSERT` and abandons it on `CONFLICT` produces a row that is partly one instrument and partly another. It happened twice: the yaml loader refreshed `name` and left the venue (`BA` became Boeing's name on BAE Systems' identity), and the LSE loader refreshed everything except `asset_class` (a gold miner, `EDV.L`, stayed filed as `fixed_income` because a US bond ETF shares the bare ticker and loads first). A conflict clause must refresh every column the loader owns.

**Names are not identity.** Two BSE rows appeared to map different companies onto one scrip code. They did not — the ISINs were identical, and both pairs were one security renamed between bhavcopy loads. The names were the weakest evidence available and the only thing that had changed. Duplicate classification now reads the ISIN, which is why a later refresh that added four more renamed listings needed no code change at all.

`UNIQUE(provider_symbol)` is **deliberately not** added. Ten legitimate aliases share a provider symbol — superseded tickers, `BRK.B` / `BRK-B`, bare FX forms — and a unique index would resolve each by deleting a row rather than reporting a conflict. The invariant that matters (*one provider symbol identifies at most one security*) is checked after every universe refresh and enforced on the live-resolve write path.

---

## Measurement

Three questions, three different answers, deliberately reported separately. Conflating them is how a system with no edge comes to look validated.

### 1. Are the confidence numbers honest? — *calibration*

When ARIA says 62%, does it happen 62% of the time? Reported as Brier score, skill against a coin flip, and expected calibration error, bucketed by stated confidence — over **distinct events**, with traded and non-traded populations kept apart, and a Wilson interval on every rate.

`src/core/calibration.py` · `GET /api/ledger/investigate`

Nothing is reported below **20 resolved calls**, and no individual bucket below **5**. A hit rate on twelve trades is noise wearing a percentage sign.

### 2. Is it better than a napkin? — *baselines*

Calibration is a property of the confidence numbers. Skill is a property of the calls, and they come apart: predict "bull" on every US equity at 55% confidence and, in a market that rises 55% of the time, you are perfectly calibrated and perfectly worthless.

So the naive strategies are re-run over **exactly the calls ARIA made** — same tickers, same dates, same horizons, same realised returns, using only price history from before each call — and compared on the paired sample using **McNemar's test on the discordant pairs**. Two systems that agree 39 times out of 40 are not distinguishable, however good both their hit rates look, and the report says `undetermined` rather than quoting a gap. The headline reports the **weakest** result, not the best.

`src/v5/baselines.py` · `GET /api/v5/baselines`

### 3. Which modules have earned a vote? — *tiers*

| Tier | Meaning |
|---|---|
| `experimental` | Fewer than 25 resolved calls. Unproven. **All 41 start here.** |
| `provisional` | Enough calls to measure; not distinguishable from chance. |
| `core` | Hit rate significantly better than a coin at p<0.05. |
| `benched` | Significantly *worse* than a coin. Excluded from live recommendations. |

Tiers are computed from resolved outcomes and nothing else. There is no list to edit: a module cannot be promoted by its author. With 41 modules tested at p<0.05, **roughly two are expected to clear the bar by chance alone** — so a Benjamini-Hochberg adjusted verdict is carried across the family, and `core_after_correction` is the field to read.

`src/v5/tiers.py` · `GET /api/v5/tiers`

---

## The eight destinations

The rail names workspaces, not implementation. There is no SIGNALS entry because signals are something MARKET knows; no APPROVAL QUEUE because approving is a step PORTFOLIO contains; and no LIVE MIND, because watching ARIA think is ARIA.

| | Destination | What it answers |
|---|---|---|
| ◉ | **Brain** | What does ARIA believe, why, and what does it remember? |
| ◬ | **Research** | Everything known about one symbol |
| ∿ | **Market** | Signals, macro, regime, derivatives, live world feed |
| ▣ | **Portfolio** | What is held, how concentrated, what is queued for approval |
| ⚗ | **Strategies** | What the lab found, backtested and evolved |
| ▤ | **Daily Report** | What happened today, and every day before it |
| ◎ | **Track Record** | Is any of this working? Calibration and baselines |
| ⚙ | **System** | Worker health, data freshness, controls |

**Brain is the front door.** `/` and `/brain` render the same thing. Chat, memory search, the live cognition stream, the daemon controls, alerts, world context and blind spots are sections inside it — not peers of it. There used to be six surfaces onto one daemon ("ARIA", "Live Mind", "Cognitive Brain", a thought stream, a memory browser and a chat), and a user could reasonably ask which one was thinking. `tests/test_ui_architecture.py` fails if that shape returns.

Old paths still resolve: `/thinking`, `/live-mind`, `/memory` and `/chat` redirect into `/brain`; `/report` redirects to `/daily-report`.

Press **Ctrl/⌘+K** anywhere for the command palette — a page name, an old page name, or a ticker.

### Daily Report vs Live News

These are two products over one observation store, and keeping them apart is deliberate:

| | Daily Report | Live News |
|---|---|---|
| cadence | once a day | continuous, cursor-based |
| shape | curated, analytical, historical | event-driven |
| identity | the **date** — write-once, kept forever | whatever arrived since your cursor |
| when nothing happened | says so | shows nothing |

A report is never overwritten. Regenerating a day archives the previous version beside it. A day with no report answers `NOT_GENERATED` and keeps answering that — it never falls back to the most recent report, because showing yesterday's conclusions under today's date is the failure the dated store exists to prevent. The date is also the filename, so anything that is not an ISO date is refused.

The live feed only shows what the research eye actually recorded. It also checks that a story is *about* the instrument it was filed under: a keyword search for `BILL` returns Bill Ackman, congressional bills and one story about a man arrested with a guillotine, and 103 of 197 stored observations were the ticker matching as ordinary English. Association is tested case-sensitively three ways — the ticker as a ticker (`BNO`, `$BILL`, `(NET)`), the company's distinctive multi-word name, or neither — and everything held back is reported with its reason.

---

## Getting started

Windows is the primary development environment; the interpreter path below reflects that.

```bash
# 1. clone & install
git clone <your-repo-url> && cd trading-intelligence-system
python -m venv venv
venv\Scripts\pip.exe install -r requirements.txt
npm install --prefix frontend

# 2. configure — copy the example and add your keys
cp .env.example .env

# 3. optional, for the reasoning layer: install Ollama and pull a model
ollama pull qwen2.5:7b-instruct-q4_K_M

# 4. run
venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
npm run dev --prefix frontend
```

On Linux/macOS the interpreter is `venv/bin/python`.

**The app is served under `/app/`, not `/`.**

```
http://localhost:3000/app/     vite dev server, with hot reload
http://localhost:8000/app/     the backend serving the production build
http://localhost:8000/docs     API reference
```

`frontend/vite.config.js` sets `base: '/app/'` because the backend mounts the built SPA at `/app`, and the two have to agree. `http://localhost:3000/brain` will 404; `http://localhost:3000/app/brain` is the page. This costs everyone a minute exactly once.

The signal engine populates on first run (`venv\Scripts\python.exe main.py`); the reasoning loop wakes automatically if Ollama is running.

### Authentication

Set `ARIA_OWNER_TOKEN` in `.env` and present it as `Authorization: Bearer <token>` or `X-ARIA-Token`, or paste it into the owner field on the sign-in screen. If **no** token is configured, requests from loopback are treated as the owner — which keeps a single-user localhost workflow working untouched. The moment this is reachable from anywhere else, set the token.

Authorization is an **allowlist**, not a blocklist: a role starts with nothing and is granted named path prefixes in `src/auth/policy.py`. A route added tomorrow is invisible to non-owners until somebody deliberately names it, so forgetting costs a 403 rather than a disclosure. The vault, the portfolio and execution are never a tier — no plan or flag reaches them.

---

## Development

```bash
venv\Scripts\python.exe -m pytest tests/ -q -p no:randomly   # 1,556 tests, 0 skipped, 0 xfail
npm run build --prefix frontend                              # production build
```

The test suite writes nothing to `data/`. `tests/conftest.py` redirects every module's data path into a temp directory and raises if a test opens the owner's live record read-write — which is why a few tests that legitimately need to read production state open it with `?mode=ro`.

### API

The surfaces the interface actually reads:

| Endpoint | What it returns |
|---|---|
| `GET /api/brain` | The one brain state: status, regime, cognition, memory, knowledge, world |
| `GET /api/brain/activity` | Live cognition — activity summaries and conclusions |
| `GET /api/brain/memory?q=` | Memory search |
| `GET /api/brain/pulse` | Vital signs only — the one brain endpoint that is not owner-only |
| `GET /api/world` | The world model snapshot |
| `GET /api/regime` | Regime with evidence, input coverage and invalidation conditions |
| `GET /api/news/live?since=` | New observations since a cursor |
| `GET /api/news/sources` | What is feeding the feed, by evidence tier |
| `GET /api/daily-report?date=` | One day's report, or `NOT_GENERATED` |
| `GET /api/daily-report/history` | Every stored report, newest first |
| `POST /api/daily-report/generate` | Build and store a report; refuses to overwrite |
| `GET /api/ledger/investigate` | Calibration over distinct events, with intervals |
| `GET /api/system/health` | Worker health from the registry |
| `GET /api/consult/status` | Whether a second opinion is currently possible |
| `POST /api/consult` | Ask SENTINEL for an independent view |
| `POST /api/consult/red-team` | Ask SENTINEL to attack a thesis |
| `POST /api/consult/outcome` | Record whether ARIA took the advice |

Older endpoints were not removed when these were added — `/api/aria/world`, `/api/report` and the rest still answer exactly as before. Full reference at `/docs`.

### Workers

Ten registered background workers, each with a heartbeat that records success *and* failure:

```
desk  brain  quant_lab  fx_monitor  outcome_resolver
world_model  ledger  research_eye  macro  daily_report
```

`GET /api/system/health` reports `ok` / `late` / `stalled` / `failing` from actual heartbeat state. The rail's status dot reads this rather than `/health`, because `/health` answers `ok` whenever the web server can answer — which is how this system once ran a week with a dead prediction loop and a green dot.

---

## Known limitations

- **No live or paper track record has been published.** As of 2026-09-06 the ledger holds 427 predictions, 201 resolved, and reports the result as indistinguishable from chance. That is the honest state of the project, not an oversight — and the verdict, not the count, is the part that has stayed true.
- **SENTINEL runs only when you start it.** It is a separate process from another project and nothing in ARIA supervises it, starts it, or restarts it when it dies. While it is down every consultation returns `unreachable` — the path most carefully tested, because it is the one most often taken. Its answers come from a local 7B model: useful for producing objections worth reading, not an authority to defer to.
- **The signed-in UI has not been visually verified by an automated pass.** Routes, component loading, API responses and the architecture tests are all checked mechanically, and the frontend builds clean — but nobody has scripted a screenshot of the authenticated deck, because doing so would mean handing an owner token to an automated agent. Open it yourself; that is the intended way in.
- **~22,000 company names are unverified** against an authoritative security master. Four known-wrong names were repaired at source and are now retained as a tripwire in `identity.KNOWN_BAD_NAMES` — if one starts firing again, a loader has reintroduced the defect. Names never reach the execution path, which keys on ticker and prices through the provider symbol.
- **Backtests are in-sample until proven otherwise.** Walk-forward validation exists but has not been run across every module. Treat backtested Sharpe as a hypothesis.
- **41 modules on one 21-day horizon is a lot of correlated tests.** The tier system corrects for it; the family weights in `src/v5/ensemble.py` are reasoned, not fitted, and have not themselves been validated out-of-sample.
- **The observation store is thin** — around 110 items a week, dominated by aggregators and social sources with a handful of primary filings. The evidence ladder makes that visible rather than flattering it.
- **Data quality is vendor-dependent.** yfinance is the primary source. Survivorship bias, adjustment errors and stale caches all flow through. The system falls back to a stale cache rather than abstaining, and labels it when it does.
- **The scope is wide for one maintainer** — equities, FX, crypto, options, multi-agent orchestration. Depth per feature varies, and the parts with the least test coverage are the newest.
- **The LLM layer has not been evaluated for anything.** It is a synthesis and interface layer. Its fluency is not correlated with correctness and should not be read as confidence.

---

## The stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11 · FastAPI · APScheduler |
| Cognition | Ollama (local LLM) · ChromaDB · sentence-transformers · optional frontier consult |
| Data | yfinance · NSE/BSE/LSE/NASDAQ symbol masters · FRED |
| ML | LightGBM ensemble · backtesting engine |
| Frontend | React 19 + Vite · zero UI frameworks, inline styles |
| Brokers (paper) | Alpaca · IBKR (optional) |

## Design

Dark command-deck aesthetic; the theme lives in one design system (`frontend/src/index.css`) and every page inherits it. It is also meant to be usable, which is a separate problem from being legible: hover or focus any dotted term for a plain-English explanation of what the number means for a decision; text size, contrast and motion are adjustable from settings; the rail collapses on narrow screens and dense tables scroll inside themselves rather than pushing the page sideways.

Layout encodes priority rather than giving every panel equal weight. On the Brain page the hierarchy is ARIA → the conversation → everything else, and the market regime appears as *context* in the header. It is a weather reading, not the subject of the page.

## Safety principles (non-negotiable)

1. **The human is the judgment layer.** Trade proposals stop at an approval queue. Nothing irreversible is autonomous. A live account cannot be armed for auto-execute; enforced in code with no override.
2. **Research, not advice.** Every surface carries the disclaimer because it's true.
3. **Local first.** Models, memory and data live on your machine. The only outbound calls are the data sources you configure and the optional, budget-capped frontier consult you explicitly enable.
4. **An unmeasurable quantity is reported as unmeasurable** — never as a number with a wide error bar that readers will treat as fact.
5. **Deny by default.** Authorization is an allowlist. A new endpoint is unreachable until named.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, learn from it.

---

<div align="center">

**One instrument → one canonical provider identity → one correct price unit → one reproducible historical record → one intelligence.**

*ARIA proposes. You decide.*

</div>
