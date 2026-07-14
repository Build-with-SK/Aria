"""
src/political/__init__.py
=========================
Political Portfolio Intelligence Layer — Public API

Trading Intelligence System — Phase 4
Research and educational purposes only.
Uses only publicly available, legally accessible data.

Quick start:
    from src.political import run_political_pipeline

    # Full pipeline (uses manual CSV if present, otherwise empty watchlist)
    candidates = run_political_pipeline()

    # With external signals from your signal_engine.py:
    candidates = run_political_pipeline(external_signals={
        "NVDA": {"technical_score": 68, "risk_score": 63},
        "MSFT": {"technical_score": 55, "macro_score": 60},
    })
"""

from src.political.political_data_schema import (
    PoliticalTradeRecord,
    PoliticalWatchlistEntry,
    TradeCandidateRecord,
    FINAL_SCORE_WEIGHTS,
    WATCHLIST_SCORE_WEIGHTS,
    TRADE_STATUS_THRESHOLDS,
    STOCK_ACT_RANGES,
)

from src.political.political_sources import (
    load_all_sources,
    list_sources_status,
    create_sample_csv,
    ManualCSVSource,
    APISourcePlaceholder,
    SOURCE_REGISTRY,
)

from src.political.political_watchlist import (
    PoliticalWatchlist,
    run_political_watchlist,
)

from src.political.political_signal_engine import (
    PoliticalSignalEngine,
    run_political_signal_engine,
)


def run_political_pipeline(
    external_signals=None,
    config=None,
    save=True,
    verbose=True,
):
    """
    Run the full Political Intelligence pipeline:
      1. Load political trade records (CSV / API placeholders)
      2. Score and aggregate into watchlist
      3. Combine with external signals
      4. Classify trade candidates
      5. Save output files

    Args:
        external_signals: Optional dict {ticker: {technical_score, options_score, ...}}
        config: Optional config overrides
        save: Write output files (default True)
        verbose: Print summary table (default True)

    Returns:
        List of TradeCandidateRecord

    Output files:
        data/political/watchlist.json
        data/political/political_signals.json
        data/trade_candidates.json
    """
    import logging
    logging.getLogger(__name__).info("Political Intelligence Pipeline starting...")

    # Step 1: Build watchlist
    wl = PoliticalWatchlist(config=(config or {}).get("watchlist_rules"))
    watchlist_entries = wl.run(save=save)

    # Step 2: Run signal engine
    engine = PoliticalSignalEngine(config=config or {})
    candidates = engine.run(
        watchlist=watchlist_entries,
        external_signals=external_signals,
        save=save,
    )

    if verbose:
        engine.print_summary()

    return candidates


__all__ = [
    # Schema
    "PoliticalTradeRecord",
    "PoliticalWatchlistEntry",
    "TradeCandidateRecord",
    "FINAL_SCORE_WEIGHTS",
    "WATCHLIST_SCORE_WEIGHTS",
    "TRADE_STATUS_THRESHOLDS",
    "STOCK_ACT_RANGES",
    # Sources
    "load_all_sources",
    "list_sources_status",
    "create_sample_csv",
    "ManualCSVSource",
    "APISourcePlaceholder",
    "SOURCE_REGISTRY",
    # Watchlist
    "PoliticalWatchlist",
    "run_political_watchlist",
    # Signal engine
    "PoliticalSignalEngine",
    "run_political_signal_engine",
    # Pipeline
    "run_political_pipeline",
]
