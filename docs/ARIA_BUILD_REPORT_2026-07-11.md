# ARIA Build Report — 2026-07-11

Three phases shipped in one session, inspired by three reels: a self-learning
quant researcher, an evidence-linked research terminal, and a GTA-style
streaming data architecture.

---

## 1. What we built

### Phase 1 — Streaming data foundation (`src/data/universe.py`)
The "open-world" layer: ARIA knows the whole market but never loads it all.

- **Tier 0 — symbol index**: `data/universe.db` (SQLite), 3,143 symbols —
  all 2,384 NSE equities (official NSE master list) + the 770-ticker global
  universe from `configs/universe.yaml` (US stocks, ETFs, crypto, FX,
  futures, indices). Search is instant and offline.
- **Tier 1 — quote cards**: price + day change, fetched in 50-symbol chunks,
  cached 15 min.
- **Tier 2 — dossiers**: 5y weekly prices + ~25 fundamentals for one symbol
  at a time; 2.4s cold / 0.02s cached (24h TTL on disk).
- **Background prefetch**: opening a dossier silently warms same-sector
  peers; sector/mcap metadata fills in progressively as symbols are explored.
- **F&O coverage**: 210 NSE derivative symbols marked with real lot sizes,
  parsed from the official NSE F&O bhavcopy (UDiFF). The old
  `fo_mktlots.csv` is dead — it now returns a PDF.
- **Options**: `/api/universe/options/{symbol}` — expirations + near-the-money
  strikes with IV/OI for US names; lot-size info for NSE names.
- Endpoints: `/api/universe/{status,refresh,search,quote,dossier,peers,options}`.

### Phase 2 — NEXUS research terminal (`src/data/nexus.py`, `Nexus.jsx`)
The "thought process" page: deterministic verdicts, cited evidence.

- **Red-flag engine**: 7 numeric rules → PASS/FAIL/NA chips with the actual
  value shown. No LLM anywhere in a verdict. Thresholds live in
  `configs/nexus_rules.yaml`.
- **Score /10**: 5 components × 2 points (profitability, growth, valuation,
  balance sheet, momentum), evidence string under each bar. Missing data = 0
  points and an honest N/A, never a guess.
- **Evidence-linked SWOT**: every bullet computed from a number and tagged
  with its source chip (`Yahoo Finance` / `Computed locally` / `Rules engine`).
- **Peers panel**: same sector, top by mcap, base-100 comparison chart that
  gains lines as peer dossiers stream in.
- **TradingView embed**: official advanced-chart widget — live global
  coverage (all exchanges, currencies, futures) streamed by TradingView, not
  stored by ARIA. NSE names load via their BSE dual listing (NSE realtime is
  licence-locked in free embeds).

### Phase 3 — Quant Lab (`src/brain/quant_lab.py`, `QuantLab.jsx`)
The self-learning researcher. Fully automated, zero-attention; auto-starts
with the backend and cycles every 12h:

1. **READ** — newest arXiv q-fin papers (official API) → embedded into
   ChromaDB `quant_research` so chat/brain can cite them.
2. **THINK** — each paper mapped to one of 5 templates (momentum_topn,
   ma_cross, mean_reversion_z, rsi_reversal, breakout) by the local Ollama
   LLM, with a deterministic keyword fallback so the loop never stalls.
3. **TEST** — vectorized long-only backtest, 20-stock NSE large-cap basket,
   3y daily bars, 0.1%/side costs, last 252 days held out as out-of-sample.
4. **RECORD** — library ranked by OOS Sharpe, deduplicated (extra papers get
   cited on the existing entry), report auto-written to the Obsidian vault
   (`01 - Trading/TIS/Quant Lab Report.md`).

**Current library (honest numbers)**: 20 papers read, 3 unique strategies.
All three are OOS-negative over the held-out year (momentum best at −0.38
Sharpe OOS but +0.43 over the full 3y). The basket itself fell heavily over
that year; long-only templates can't escape that. The lab reports reality.

**The rule we kept**: execution stays behind the human-approval queue.
CLAUDE.md: *the brain proposes, the human approves.* Automating research is
safe; automating order flow is how accounts die.

---

## 2. What we could have done better

- **Backtest rigor**: single train/test split, no walk-forward, no parameter
  sensitivity, no regime awareness. A strategy that looks bad in one falling
  year may be fine across regimes — and vice versa. Survivorship: the basket
  is today's large caps, which flatters history.
- **Long-only limitation**: in a down year every long-only template loses;
  without shorts/hedges the lab mostly measures the market, not the alpha.
- **Paper→strategy mapping is shallow**: abstracts get mapped to 5 canned
  templates. Real papers propose signals we can't express yet (order flow,
  factors, options surfaces). The citation is real; the fidelity is loose.
- **Yahoo-only fundamentals**: some fields missing for NSE names (e.g. TCS
  ROE); no Screener.in-grade promoter-holding history or interest coverage,
  so several NEXUS rules return N/A more often than they should.
- **No tests**: none of the new modules have unit tests; regressions will be
  silent.
- **Frontend state**: NEXUS/QuantLab reset on HMR/navigation; selected symbol
  isn't in the URL, so reports aren't shareable/bookmarkable.
- **Environment debt**: no real Node.js on the machine — the frontend runs on
  Playwright's bundled node via `.claude/launch.json`. Install Node properly.

## 3. What we can do next (ranked by value ÷ effort)

1. **Walk-forward evaluation** in the lab (rolling 6-month OOS windows,
   parameter grids per template) — makes rankings meaningful. ~1 session.
2. **Regime filter** (NIFTY above/below 200DMA → risk-on/off) as an overlay
   on every template; would likely flip several OOS numbers. Small.
3. **URL routing for NEXUS** (`/nexus/TCS`) + persistent last report. Small.
4. **Screener.in-style fundamentals for NSE** via published annual-report
   figures (or a licensed API) to unlock the dormant NEXUS rules.
5. **Options analytics on the existing chain endpoint**: IV rank, put/call
   OI walls, max-pain — pure computation on data we already fetch.
6. **Chat integration**: let ARIA chat call NEXUS/QuantLab endpoints as tools
   so you can ask "research TCS" in conversation.
7. **Portfolio paper-trading loop**: let the lab paper-trade its champion in
   Alpaca's sandbox and track live-vs-backtest drift — the honest bridge
   between research and any future real order, still behind approval.
8. **Index futures/options for NSE** (NIFTY/BANKNIFTY) in the universe index.

---

*Research and education only. Backtests are simulations of the past, not
predictions. Nothing in ARIA is investment advice, and nothing here should
be traded with money one cannot afford to lose.*
