# The market intelligence & research engine

Last revised 2026-10-09. Describes `src/intelligence/research/`, the research
API and panel, and the test-data isolation hardening made with it.

The engine answers, for one instrument at one instant T:

1. **What is happening?** price, returns, volume, volatility, liquidity, regime
2. **Why might it be happening?** market / sector / factor / currency attribution,
   fundamentals, macro, events, news, cross-asset relationships
3. **Is the move unusual?** the UNEXPLAINED_RESIDUAL against its own residual
   volatility, the move against the instrument's volatility, correlation breaks
4. **Does independent evidence agree?** agreement counted over independent
   evidence GROUPS, with the dependence inside the price group measured
5. **What could invalidate the view?** price levels, trend and regime flips,
   scheduled events, contradicting groups, liquidity
6. **How strong is the evidence?** a scorecard of explicit states — never one
   confidence number

It explains a view. It cannot authorise, size or send a trade.

## 1. The research stack, audited (2026-10-08)

The full table is data — `src/intelligence/research/sources.py`, served at
`GET /api/intelligence/research/sources` — so the evidence graph reads its groups
from the audit rather than from a code path that might forget it.

| Source | Input | Timestamp | PIT-safe? | Provenance | Independent? | Used by |
|---|---|---|---|---|---|---|
| OHLCV / adjusted prices / volume / volatility | vendor chain (yfinance → alphavantage → stooq), split+dividend adjusted | bar date, known once its session closed | by construction (`MarketData.history(sym, at)`) | vendor, served_from, adjustment | the ROOT of every price-derived item | everything |
| Technical indicators | SMA/EMA, RSI, MACD, stochastic, CCI, ADX … | as bars | by construction | computed | **no** — transforms of one series | technical model, signal engine, desk, V5 |
| V5 price / quant / volatility modules | 14+ modules | as bars | by construction | module reports | **no** — 0.58 of V5's weight on one root, not adjusted for | V5 ensemble |
| Fundamentals | yfinance `info` via the universe dossier | FETCH time only (filing unknown) | **live only** | vendor + fetch time | yes of price (ratios embed price) | V5 fundamental, desk |
| Earnings | yfinance earnings dates, EPS estimate / reported / surprise | event time; no schedule publication time | verified (first-observed archive) | vendor + observed_at | yes | this engine (new) |
| Dividends / splits | yfinance actions | ex-date only | verified (known no earlier than ex-date) | vendor | yes | this engine (new) |
| SEC filings | EDGAR via the research eye | filed + seen_at | verified | url, form | yes | live news, this engine |
| Macro releases | BLS CPI / unemployment, Treasury, NY Fed, FRED | observation, release (none for BLS), retrieval | verified | three dates | yes; only the latest value is kept | macro regime, desk |
| Rates, FX, commodities, index, sector, factor tapes | ETF / index / FX series | daily | by construction | vendor | yes of the instrument; factor spreads partly | risk, attribution, cross-asset |
| News | research-eye observations + NewsAPI | published and seen | verified | url, backend, tier | per STORY, not per copy | live news, desk |
| Sentiment | keyword lexicon over headlines | as news | verified | lexicon hits | **no** — the same headlines | desk sentiment analyst |
| Desk views | debate verdicts | debate time | verified (ledger from 2026-10-08) | ledger row | **no** — a vote of price, fundamental, sentiment | desk execution |
| Model predictions | ledger: technical, V5, ML, desk | created / resolved | verified | ledger provenance | technical and ML are price-derived | model intelligence |
| Market regime | SPY trend/vol; macro quadrant | as inputs | by construction / verified | inputs, coverage | context | evaluation, desk |
| Portfolio state | broker AccountState | as_of | live only | broker, as_of | not evidence about the instrument | portfolio risk |

**Duplicated information found:** every technical indicator and three V5 families
share the price root; sentiment restates news; the desk view is a vote of
inputs that are themselves in this table; the technical and ML models are
price-derived. None of these may count as independent confirmation.

## 2. The canonical snapshot (`snapshot.py`, `components.py`, `pit_data.py`)

`ResearchSnapshot`: instrument, asset class, venue, information time, market
session; price, returns, volume, volatility, liquidity, technical;
fundamentals, macro, factors, news, sentiment, cross_asset, regime,
event_risk; provenance, freshness, missingness.

Every component carries `as_of`, `source`, `version`, `freshness`
(FRESH / STALE / MISSING / CONFLICTED / NO_DATA / UNKNOWN), `pit`
(PIT_BY_CONSTRUCTION / PIT_VERIFIED / LIVE_ONLY / UNAVAILABLE), `provenance`
and, when absent, `missing` with the reason.

* A failed provider is MISSING with its message in `provenance.provider_failures`
  — never filled from another source, a neighbour, a default or today's value.
* NO_DATA (a source that covers the instrument found nothing) is distinct from
  MISSING (no source covers it, or it failed).
* Conflicting sources (two fundamentals vendors disagreeing by > 5%) are
  CONFLICTED with both values shown and none chosen.
* A known split inside the history across which the price jumps by the split
  ratio marks the series unadjusted: returns and factors become CONFLICTED and
  price-derived evidence and analogues are withheld.

## 3. Point-in-time guarantees (`pit_data.py`)

The visibility rules live in one place and are applied by the builder, so a
provider that returns a future record still cannot leak it:

| Input | Known at T when |
|---|---|
| bars | its venue's session had closed (`sessions.bar_known_by`); today's open session is future |
| fundamentals | the snapshot was taken at or before T (`available_at`); before the first snapshot: nothing |
| events | it occurred by T (occurrence is publication); or its schedule's publication time, else ARIA's first observation, is at or before T; a withdrawn schedule vanishes from its withdrawal (and reappears from a relisting); outcomes only once it occurred |
| news | max(published, seen) ≤ T — public AND in ARIA's hands |
| macro | release time, else retrieval time (BLS publishes none) |
| model records | made by T; an outcome only if graded by T |
| model reliability | the evidence snapshot used is built at or before T, else built as of T |

A LIVE query fixes T after its providers have been fetched (`prefetch`), so a
schedule fetched for the query is known at its instant.

Live data also ARCHIVES what it sees with the time it was first observed —
fundamentals snapshots (`data/intelligence/research/fundamentals/`) and events
(`.../events/`) — so a point-in-time history accumulates from 2026-10-09 on.
Before that, historical fundamentals and event schedules are unavailable, and
reported as such.

**Adversarial tests** (`tests/test_research_pit_adversarial.py`): price, volume,
fundamentals, news, macro, FX, factor returns, events, corporate actions,
regime, the analogue history and the models' own performance record are each
rewritten AFTER T — the report must be identical — and BEFORE T — it must
change. Plus duplicate news, provider failure, the session boundary (15:59 vs
16:01 New York), a required timezone, holidays, and the split boundary.

## 4. Factor & relative-return attribution (`attribution.py`)

Base-currency log returns (each price converted at its own date's FX rate),
cut at T. For each window (1, 5, 21 trading days) one OLS on the 252 periods
STRICTLY BEFORE the window:

`r = a + b_mkt·MKT + b_sec·(SECTOR − SPY) + b_ctry·(COUNTRY − ACWI) + b_ccy·FX + Σ b_k·FACTOR_k + e`

Contributions are beta × the factor's move in the window; `explained` is their
sum; the rest is **UNEXPLAINED_RESIDUAL** — never called alpha — with a z-score
against the regression's residual volatility (UNUSUAL at |z| ≥ 2,
descriptively). Market proxy SPY for US equities (the style spreads are
SPY-relative), ACWI otherwise; sector = SPDR ETF of the symbol master's sector
(today's classification, not point in time — stated); country = iShares ETF of
a non-US venue's country. Non-US, crypto and FX instruments are estimated
weekly and their 1-day window is NOT_APPLICABLE.

## 5. Cross-asset (`cross_asset.py`)

Pairs by asset class and sector (equities: SPY, TLT, ^VIX, UUP, DBC, HYG;
energy adds USO; gold: TIP, UUP; crypto: BTC, QQQ, UUP, GLD …). Long-run (252)
versus recent (63) correlation and beta, without overlap. States: CORRELATED,
UNCORRELATED, DIVERGING (the recent move departs from the long-run beta by
> 2 residual sd), BREAKDOWN (a meaningful long-run correlation contradicted at
p < 0.01 with a sign flip or |Δ| ≥ 0.4), INSUFFICIENT_DATA, NOT_APPLICABLE.
Lead/lag only for pairs on the same clock, only when it survives Bonferroni
over the lags tested, and labelled an association. Sector dispersion against
its trailing year is available on request.

## 6. Events (`events.py`) and news (`news.py`)

Events carry `event_time`, `publication_time`, `effective_time`, `observed_at`,
`withdrawn_at`, `relisted_at`, source, importance (with its basis) and scope.
Event risk over the horizon: HIGH (instrument earnings), ELEVATED (a split, a
market-wide release), LOW, or **UNKNOWN when the provider failed** — no known
event is not no event. Macro observations enter as ECONOMIC_RELEASE events at
their known time.

News: each item gets an event type (KEYWORD_RULES), a direction (LEXICON),
a claim type and a source tier (src/research/quality), and an association
check (does the headline name the ticker or company?). Items cluster into
STORIES (Jaccard ≥ 0.5 on normalised headline words with ≥ 3 shared, or the
same URL, within 72 h); novelty is ORIGINAL / SYNDICATED / REPEATED per item,
FRESH / STALE and ORIGINAL_TOPIC / NEW_DEVELOPMENT per story. **Only stories
become evidence** — fifteen outlets are one item. No language model is used;
every classification names its rule. Measured on NET (a watched ticker):
75 items → 29 stories → 17 relevant independent stories; 46 syndicated copies
collapsed.

## 7. Evidence independence (`evidence_graph.py`)

Every item names its ROOT (`PRICE:AAPL`, `PRICE:SPY`, `FUNDAMENTALS:AAPL`,
`NEWS:<story>`, `MACRO:…`, `EVENTS:AAPL`, `MODEL:v5` …) and its GROUP
(PRICE_DERIVED, MARKET, FUNDAMENTAL, MACRO, NEWS, CROSS_ASSET, EVENT, MODEL).
Recorded model calls are grouped by their INPUTS: technical and ML are
PRICE_DERIVED; V5 and the desk are MODEL.

Agreement is reported over raw items AND over groups. A group's direction
needs a two-thirds majority of its directional items; otherwise it is MIXED
and does not vote. Inside PRICE_DERIVED the dependence is MEASURED: the daily
direction of each indicator over the last 252 days, their correlation matrix,
and its participation ratio — the effective number of independent signals.
Measured live on AAPL: 8 price-derived items, mean pairwise correlation 0.38,
≈ 2.9 effective independent signals, reported as one group.

## 8. The thesis and its scorecard (`thesis.py`)

Direction from independent groups: BULLISH / BEARISH if at least one group
points that way and none the other (basis MULTIPLE_INDEPENDENT_GROUPS or
SINGLE_EVIDENCE_GROUP); NEUTRAL if none is directional or groups conflict
(CONFLICTED). Supporting and contradicting items with their explanations;
the unexplained residual; regime; event risk; data quality and freshness;
invalidators (20-day low/high, the 200-day average, scheduled events, the
benchmark's 200-day average, a vol-regime change, contradicting groups
strengthening, liquidity halving); historical analogues; recorded model calls
and routing.

Scorecard: DATA_QUALITY, EVIDENCE_DIVERSITY, EVIDENCE_INDEPENDENCE,
HISTORICAL_SUPPORT, REGIME_MATCH, EVENT_RISK, FRESHNESS, CONTRADICTION,
PIT_INTEGRITY — each an explicit state with its basis — and a sentence such as
"Bullish view (multiple independent groups): moderate price derived evidence,
strong market evidence, strong fundamental evidence … high event risk;
historical analogues with no demonstrated edge." `confidence` is None by
design; `executable` and `may_size` are False.

## 9. Historical analogues (`analogues.py`)

State at each past date from bars up to that date only (trend, 21/63-day
momentum, vol percentile, relative return, benchmark trend, volume). A date is
a candidate only if its forward outcome was already known at T; standardised
on the candidates; k nearest taken greedily with no overlap of forward
windows. Reported with count, method, window, forward-return mean/median,
dispersion, win rate, the unconditional base rate over non-overlapping
windows, and an exact binomial test against it. Even a significant difference
is "DIFFERS_FROM_BASE_RATE (in-sample, not validated out of sample)"; otherwise
NOT_DEMONSTRATED; below 504 bars or 10 analogues ANALOGUE_INSUFFICIENT_DATA.

## 10. Prediction integration

```
MARKET DATA -> PIT RESEARCH SNAPSHOT -> FEATURES / FACTORS / EVENTS
  -> INDEPENDENT EVIDENCE -> RESEARCH THESIS -> MODEL PREDICTIONS
  -> MODEL ROUTING -> CONVICTION -> PORTFOLIO RISK -> SESSION -> EXECUTION
```

The engine runs the first five stages and READS the next two (recorded calls,
their reliability as of T, and routing). It never calls the last four — a test
disables `pretrade.assess` and research still runs, and no module in the
package imports trading, execution, the pre-trade gate, the brain, the
inference router or an LLM provider.

The only route toward a trade: a live request saves a compact thesis
(`data/intelligence/research/latest/<SYM>.json`); the conviction for a desk
trade reads it — a local file, no network — as `research_context`: AGREES,
CONTRADICTS, NEUTRAL or UNKNOWN (no thesis, built after the instant, or older
than 24 h). It is recorded with the risk decision and shown as a label
("RESEARCH CONTRADICTS"). **It changes no verdict and no size**; a test proves
a contradicting thesis leaves the pre-trade decision identical. Whether a
contradicting thesis should ever defer a trade is an owner decision, not made.

## 11. API and UI

Owner-only: `GET /api/intelligence/research/sources` and
`GET /api/intelligence/research/{symbol}?horizon_days=&at=&analogues=&dispersion=`
(`at` must carry a timezone; symbols are validated before they can reach a
file name). The Research page has a RESEARCH THESIS panel, run on demand:
direction and basis, the scorecard, evidence groups with the measured
price-signal independence, supporting and contradicting evidence, the
attribution sentence, cross-asset states, events, news stories, analogues,
recorded model calls and routing, invalidators, and every component's
freshness. No confidence number appears anywhere.

## 12. Production-data isolation (tests)

* `pytest.ini`: `testpaths = tests` — a bare `pytest` no longer collects
  root-level scripts (the 2026-10-08 incident).
* The conftest guard refuses every write path into the real data directory:
  `open` for writing, `Path.write_text/bytes/touch/rename/replace/unlink`,
  `os.replace/rename/remove/unlink`, `shutil.move/rmtree`, a writable sqlite
  connection — including a `file:` URI, which previously slipped past.
* Every refused attempt is recorded, and the run FAILS if one happened outside
  a deliberate probe — even if production code swallowed the AssertionError.
* `tests/test_production_data_isolation.py` probes each path against the
  protected files (signals, macro, political, trade candidates, the ledger,
  trading mode, kill switch, desk config) non-destructively and fingerprints
  them before and after.

## 13. Performance (this machine, warm, p50 / p95)

| stage | p50 | p95 |
|---|---|---|
| snapshot (fixture world) | 173 ms | 187 ms |
| factor attribution | 32 ms | 34 ms |
| cross-asset (6 pairs) | 32 ms | 34 ms |
| news clustering, 180 items | 21 ms | 27 ms |
| analogue search | 14 ms | 15 ms |
| thesis generation | 0.4 ms | 0.6 ms |
| complete request, fixture | 158 ms | 194 ms |
| complete request, live AAPL (vendor series cached) | 1.02 s | 1.09 s |
| live cache hit | 0.01 ms | 0.01 ms |

A cold live request took 9.0 s on AAPL (5.1 s prefetch, 1.9 s snapshot, 1.9 s
analogues including the one-time scipy import). `scripts/bench_research.py`
reproduces these. None of it is on the order path, which reads one small
local file.

## 14. Known limitations

* Fundamentals are a vendor snapshot without a filing date; history exists
  only from the first archived snapshot (2026-10-09).
* Earnings / dividend / split schedules carry no publication time; before
  ARIA first observed them they are invisible to a historical query.
* The macro store keeps only the latest value per series: historical macro
  context is unavailable; BLS gives no release time (retrieval time is used).
* No quote history: the spread at T is unknown (liquidity reports it missing).
* News coverage is whatever the research eye watches (5 tickers today) plus
  NewsAPI when a key is configured; otherwise news is MISSING.
* The sector classification is today's, not point in time.
* Direction rules for individual items are descriptive heuristics; none has
  been validated as a predictor. The evidence-group directions inherit that.
* No language model is used; there is therefore no semantic news
  understanding — keyword rules only, labelled as such.
* Analogues use the instrument's own history only.
