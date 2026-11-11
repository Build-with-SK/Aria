"""
src/intelligence/research/sources.py
====================================
The audit of every research / evidence source ARIA has, as data.

Written from the code on 2026-10-08, not from documentation. Each entry says
what the input is, what its timestamp means, whether a historical query can
use it without seeing the future, what provenance it carries, which
independent evidence group it belongs to, what it duplicates, and what uses it.

The point of keeping this as data: the evidence graph reads `group` and
`root` from here, so "ten indicators of one price series" cannot be counted
as ten confirmations by a code path that forgot the audit.

PIT status vocabulary:
    PIT_BY_CONSTRUCTION  computed from bars cut at T by the session engine
    PIT_VERIFIED         each record carries a time it became knowable
    LIVE_ONLY            a current snapshot whose publication time is unknown:
                         usable only for T at/after the snapshot was taken
    NOT_PIT              cannot be replayed historically
    UNAVAILABLE          not reachable in this installation
"""
from __future__ import annotations

GROUPS = ("PRICE_DERIVED", "MARKET", "FUNDAMENTAL", "MACRO", "NEWS",
          "CROSS_ASSET", "EVENT", "MODEL")

GROUP_MEANING = {
    "PRICE_DERIVED": "transforms of the instrument's own price/volume tape — one root",
    "MARKET": "the market / sector / factor proxies' tapes, and their attribution",
    "FUNDAMENTAL": "company accounts and valuation from a vendor snapshot",
    "MACRO": "published economic statistics and the macro regime built on them",
    "NEWS": "news clusters — one per story, however many outlets carried it",
    "CROSS_ASSET": "other asset classes' tapes (bonds, USD, commodities, vol)",
    "EVENT": "scheduled or reported corporate and economic events",
    "MODEL": "model predictions whose inputs span several groups",
}

#: name -> audit row. `root` is the raw input every derived item hangs off.
SOURCES: dict[str, dict] = {
    "ohlcv": {
        "input": "daily OHLCV bars (src/v5/marketdata vendor chain: yfinance -> "
                 "alphavantage -> stooq; src/risk/market_data.MarketData)",
        "timestamp": "bar date; a bar is known once its venue's session closed "
                     "(src/markets/sessions.bar_known_by)",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "vendor, served_from, adjustment, "
                                                    "quality (marketdata provenance)",
        "group": "PRICE_DERIVED", "root": "PRICE:{symbol}",
        "independent": "the root of every price-derived item",
        "used_by": "every model, the desk, risk, this engine"},
    "adjusted_prices": {
        "input": "split + dividend adjusted closes (same vendor series)",
        "timestamp": "as ohlcv; adjustments made after T rescale earlier prices by "
                     "one constant, so return RATIOS are unaffected",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "adjustment='split+dividend'",
        "group": "PRICE_DERIVED", "root": "PRICE:{symbol}",
        "independent": "no — the same tape", "used_by": "returns, grading, risk"},
    "volume": {
        "input": "daily volume (same bars)", "timestamp": "as ohlcv",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "as ohlcv",
        "group": "PRICE_DERIVED", "root": "PRICE:{symbol}",
        "independent": "no — same tape, same session; counted inside PRICE_DERIVED",
        "used_by": "liquidity, V5 liquidity/breadth, signal engine"},
    "volatility": {
        "input": "realised vol from closes", "timestamp": "as ohlcv",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "computed", "group": "PRICE_DERIVED",
        "root": "PRICE:{symbol}", "independent": "no", "used_by": "V5 volatility, risk"},
    "technical_indicators": {
        "input": "SMA/EMA, RSI, MACD, stochastic, CCI, ADX, momentum "
                 "(src/data/technical_summary, src/features/indicators)",
        "timestamp": "as ohlcv", "pit": "PIT_BY_CONSTRUCTION", "provenance": "computed",
        "group": "PRICE_DERIVED", "root": "PRICE:{symbol}",
        "independent": "NO — every indicator is a transform of the same closes; "
                       "measured effective independence is reported per query",
        "used_by": "technical model, signal engine, desk technical analyst, V5 price"},
    "v5_price_quant_volatility_modules": {
        "input": "momentum, trend, mean reversion, technical, relative strength, "
                 "stat-arb, cointegration, PCA, clustering, HMM, Kalman, Bayesian, "
                 "time series, volatility (src/v5/modules price/quant/volatility)",
        "timestamp": "as ohlcv", "pit": "PIT_BY_CONSTRUCTION",
        "provenance": "module reports", "group": "PRICE_DERIVED",
        "root": "PRICE:{symbol}",
        "independent": "NO — three V5 families (0.58 of its weight) share one root; "
                       "the V5 ensemble does not adjust for that",
        "used_by": "V5 ensemble"},
    "fundamentals": {
        "input": "vendor fundamentals (yfinance info via src/data/universe dossier): "
                 "P/E, growth, margins, ROE, debt",
        "timestamp": "the time the snapshot was FETCHED; the filing it reflects is "
                     "not stated", "pit": "LIVE_ONLY",
        "provenance": "vendor + fetch time only", "group": "FUNDAMENTAL",
        "root": "FUNDAMENTALS:{symbol}",
        "independent": "yes, of price (accounts), though valuation ratios embed price",
        "used_by": "V5 fundamental family, desk fundamental analyst"},
    "earnings": {
        "input": "earnings dates, EPS estimate / reported / surprise "
                 "(yfinance get_earnings_dates)",
        "timestamp": "event time; schedule publication time NOT provided",
        "pit": "PIT_VERIFIED", "provenance": "vendor + first-observed time "
                                             "(data/intelligence/research/events)",
        "group": "EVENT", "root": "EVENTS:{symbol}",
        "independent": "yes", "used_by": "this engine (new)"},
    "corporate_actions": {
        "input": "dividends and splits (yfinance actions)",
        "timestamp": "ex-date only; announcement date not provided",
        "pit": "PIT_VERIFIED", "provenance": "vendor; known no earlier than ex-date",
        "group": "EVENT", "root": "EVENTS:{symbol}", "independent": "yes",
        "used_by": "this engine (new)"},
    "sec_filings": {
        "input": "SEC EDGAR filings seen by the research eye (8-K, 10-Q …)",
        "timestamp": "filed date + seen_at", "pit": "PIT_VERIFIED",
        "provenance": "url, form, filed, seen_at (data/research/observations.jsonl)",
        "group": "NEWS", "root": "NEWS:{cluster}", "independent": "yes",
        "used_by": "live news feed, this engine"},
    "macro_releases": {
        "input": "CPI YoY, unemployment (BLS), Treasury yields, EFFR (NY Fed), FRED "
                 "(src/macro/feeds)",
        "timestamp": "observation_date, release_date (None for BLS), retrieved_at",
        "pit": "PIT_VERIFIED", "provenance": "source, url, three dates",
        "group": "MACRO", "root": "MACRO:{series}",
        "independent": "yes; only the latest value is kept, so history is "
                       "unavailable", "used_by": "macro regime, desk macro conditioner"},
    "rates_yields": {
        "input": "Treasury par curve (feeds) and rate ETF tapes (IEF, TLT, ^TNX)",
        "timestamp": "daily", "pit": "PIT_BY_CONSTRUCTION (tapes) / PIT_VERIFIED (feeds)",
        "provenance": "as above", "group": "CROSS_ASSET", "root": "PRICE:{proxy}",
        "independent": "yes of the instrument", "used_by": "risk factors, cross-asset"},
    "fx": {
        "input": "FX closes (src/risk/fx, vendor '=X' series)", "timestamp": "daily",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "FxRate with as_of",
        "group": "CROSS_ASSET", "root": "PRICE:{pair}", "independent": "yes",
        "used_by": "risk valuation, attribution currency term"},
    "commodities": {
        "input": "commodity tapes (DBC, USO, GLD, futures)", "timestamp": "daily",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "vendor", "group": "CROSS_ASSET",
        "root": "PRICE:{proxy}", "independent": "yes", "used_by": "risk factors, cross-asset"},
    "index_returns": {
        "input": "market proxies (SPY, ACWI, QQQ)", "timestamp": "daily",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "vendor", "group": "MARKET",
        "root": "PRICE:{proxy}", "independent": "yes of the instrument's residual; "
                                                 "not of its beta-driven move",
        "used_by": "regime, attribution, risk beta"},
    "sector_returns": {
        "input": "SPDR sector ETFs (XLK, XLF …) mapped from the symbol master's sector",
        "timestamp": "daily; the sector CLASSIFICATION is today's, not point in time",
        "pit": "PIT_BY_CONSTRUCTION", "provenance": "vendor + universe.db sector",
        "group": "MARKET", "root": "PRICE:{proxy}", "independent": "partly",
        "used_by": "attribution, dispersion"},
    "factor_returns": {
        "input": "long-short ETF spreads (src/risk/factors: size, value, growth, "
                 "momentum, quality, volatility, rates, credit, fx, commodities)",
        "timestamp": "daily", "pit": "PIT_BY_CONSTRUCTION", "provenance": "proxy legs",
        "group": "MARKET", "root": "PRICE:{proxy}", "independent": "partly",
        "used_by": "risk exposures, attribution"},
    "news": {
        "input": "research eye observations (reddit, stocktwits, google news, EDGAR, "
                 "rss …) and NewsAPI headlines (src/data/enrichment)",
        "timestamp": "published (when the source gives it) and seen_at",
        "pit": "PIT_VERIFIED", "provenance": "url, backend, source tier, seen_at",
        "group": "NEWS", "root": "NEWS:{cluster}",
        "independent": "per STORY: syndicated copies of one story are one item",
        "used_by": "live news, desk sentiment analyst"},
    "sentiment": {
        "input": "keyword lexicon over headlines (src/data/enrichment, "
                 "src/sentiment); StockTwits bull/bear counts",
        "timestamp": "as the headlines", "pit": "PIT_VERIFIED",
        "provenance": "lexicon hits", "group": "NEWS", "root": "NEWS:{cluster}",
        "independent": "NO — derived from the same headlines as news",
        "used_by": "desk sentiment analyst"},
    "analyst_desk_views": {
        "input": "desk debate verdicts (src/desk)", "timestamp": "debate time",
        "pit": "PIT_VERIFIED (ledger, from 2026-10-08)", "provenance": "ledger row",
        "group": "MODEL", "root": "MODEL:desk",
        "independent": "NO — a weighted vote of the technical (price), fundamental "
                       "and sentiment analysts", "used_by": "desk execution"},
    "model_predictions": {
        "input": "ledger predictions: technical, V5, ML ensemble, desk",
        "timestamp": "created_at; outcome at resolved_at", "pit": "PIT_VERIFIED",
        "provenance": "ledger provenance columns", "group": "MODEL",
        "root": "MODEL:{source}",
        "independent": "technical and ML are PRICE_DERIVED; V5 and desk span groups",
        "used_by": "model intelligence, conviction"},
    "market_regime": {
        "input": "SPY trend/vol (src/intelligence/regime) and the macro quadrant "
                 "(src/macro/regime)", "timestamp": "as their inputs",
        "pit": "PIT_BY_CONSTRUCTION (market) / PIT_VERIFIED (macro)",
        "provenance": "inputs + coverage", "group": "MARKET",
        "root": "PRICE:{benchmark}", "independent": "context, not direction",
        "used_by": "evaluation slices, desk conditioner"},
    "portfolio_state": {
        "input": "AccountState from the broker (src/trading/account)",
        "timestamp": "as_of of the snapshot", "pit": "LIVE_ONLY",
        "provenance": "broker name, as_of", "group": "—", "root": "BOOK",
        "independent": "not evidence about the instrument", "used_by": "portfolio risk"},
}

#: Model sources -> the group their INPUTS put them in.
MODEL_GROUP = {"technical": "PRICE_DERIVED", "ml-ensemble": "PRICE_DERIVED",
               "v5": "MODEL", "desk": "MODEL", "signal-engine": "PRICE_DERIVED"}


def table() -> list[dict]:
    """The audit, one row per source, for the API and the documentation."""
    return [{"source": k, **v} for k, v in SOURCES.items()]


def duplicates() -> list[dict]:
    """Sources that restate another source's information."""
    return [{"source": k, "root": v["root"], "why": v["independent"]}
            for k, v in SOURCES.items() if str(v["independent"]).upper().startswith("NO")]
