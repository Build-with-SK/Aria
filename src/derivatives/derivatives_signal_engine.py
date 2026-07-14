"""
derivatives_signal_engine.py
=============================
Combines futures and options signals into a unified derivatives layer.

This module does NOT replace the spot signal engine. It produces an
ADJUSTMENT SCORE that modifies conviction in the spot signal.

Logic:
  - If futures AND options both confirm the spot signal → high conviction boost
  - If one confirms, one contradicts → moderate adjustment
  - If both contradict the spot signal → conviction reduction warning

Output: A DerivativesContext object that the dashboard can display
alongside the spot AssetSignal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from src.derivatives.futures_analyzer import FuturesSignal
from src.derivatives.options_analyzer import OptionChainSummary

logger = logging.getLogger(__name__)


@dataclass
class DerivativesContext:
    """
    Combined derivatives analysis for one underlying asset.

    Fields
    ------
    ticker              : Underlying spot ticker (e.g. 'AAPL')
    futures_score       : Confirmation score from linked futures (-100 to +100)
    options_score       : Sentiment score from options chain (-100 to +100)
    combined_adj        : Weighted combination of futures + options (-100 to +100)
    conviction_boost    : Suggested adjustment to spot signal confidence
    summary             : Human-readable combined interpretation
    futures_available   : Whether futures data was found for this asset
    options_available   : Whether options data was found for this asset
    """
    ticker:            str
    futures_score:     float
    options_score:     float
    combined_adj:      float
    conviction_boost:  str       # "Boost", "Neutral", "Caution", "Warning"
    summary:           str
    futures_available: bool
    options_available: bool


# Map of spot tickers to their related futures tickers
# Used to look up futures confirmation for spot signals
SPOT_TO_FUTURES_MAP = {
    "^GSPC": "ES=F",
    "^IXIC": "NQ=F",
    "^DJI":  "YM=F",
    "GC=F":  "GC=F",   # Gold spot and futures share ticker in yfinance
    "CL=F":  "CL=F",
    "SI=F":  "SI=F",
    "NG=F":  "NG=F",
    "ZB=F":  "ZB=F",
    "ZN=F":  "ZN=F",
}


def build_derivatives_context(
    spot_ticker:      str,
    futures_signals:  Dict[str, FuturesSignal],
    options_data:     Dict[str, Optional[OptionChainSummary]],
) -> DerivativesContext:
    """
    Build a DerivativesContext for one spot asset.

    Parameters
    ----------
    spot_ticker     : The spot ticker being analysed (e.g. 'AAPL', '^GSPC')
    futures_signals : All futures signals from futures_analyzer
    options_data    : All options chain data from options_analyzer

    Returns
    -------
    DerivativesContext — always returns a result even if data is missing
    """

    # --- Futures score ---
    futures_score    = 0.0
    futures_available = False
    fut_ticker = SPOT_TO_FUTURES_MAP.get(spot_ticker)
    if fut_ticker and fut_ticker in futures_signals:
        fut_sig = futures_signals[fut_ticker]
        futures_score = fut_sig.confirmation_score
        futures_available = True

    # --- Options score ---
    options_score    = 0.0
    options_available = False
    if spot_ticker in options_data and options_data[spot_ticker] is not None:
        opt = options_data[spot_ticker]
        options_score = opt.final_sentiment_score
        options_available = True

    # --- Combined adjustment score ---
    # If both available: 60% futures, 40% options
    # If only futures: use futures only
    # If only options: use options only
    # If neither: 0
    if futures_available and options_available:
        combined_adj = futures_score * 0.60 + options_score * 0.40
    elif futures_available:
        combined_adj = futures_score
    elif options_available:
        combined_adj = options_score
    else:
        combined_adj = 0.0

    combined_adj = float(np.clip(combined_adj, -100, 100))

    # --- Conviction label ---
    if combined_adj > 30:
        conviction = "Boost"
    elif combined_adj > 0:
        conviction = "Neutral-Positive"
    elif combined_adj > -30:
        conviction = "Neutral-Negative"
    else:
        conviction = "Caution"

    # --- Human-readable summary ---
    parts = []
    if futures_available:
        fut_sig = futures_signals[fut_ticker]
        parts.append(f"Futures ({fut_ticker}): {fut_sig.action} [score: {futures_score:+.1f}]")
    else:
        parts.append("Futures: no linked contract available")

    if options_available:
        opt = options_data[spot_ticker]
        parts.append(
            f"Options: sentiment {options_score:+.1f} "
            f"(P/C OI: {opt.pc_oi_ratio:.2f}, IV skew: {opt.iv_skew:.3f})"
        )
    else:
        parts.append("Options: no chain data available")

    summary = " | ".join(parts)
    summary += f" → Combined adjustment: {combined_adj:+.1f} ({conviction})"

    return DerivativesContext(
        ticker=spot_ticker,
        futures_score=round(futures_score, 2),
        options_score=round(options_score, 2),
        combined_adj=round(combined_adj, 2),
        conviction_boost=conviction,
        summary=summary,
        futures_available=futures_available,
        options_available=options_available,
    )


def build_all_derivatives_contexts(
    spot_tickers:    list,
    futures_signals: Dict[str, FuturesSignal],
    options_data:    Dict[str, Optional[OptionChainSummary]],
) -> Dict[str, DerivativesContext]:
    """
    Build DerivativesContext for every spot ticker.
    Tickers with no futures/options data still get a context (with 0 scores).
    """
    contexts = {}
    for ticker in spot_tickers:
        contexts[ticker] = build_derivatives_context(ticker, futures_signals, options_data)
    return contexts


def derivatives_context_to_json(contexts: Dict[str, DerivativesContext]) -> dict:
    """Serialise contexts to JSON-safe dict."""
    out = {}
    for ticker, ctx in contexts.items():
        out[ticker] = {
            "ticker":             ctx.ticker,
            "futures_score":      ctx.futures_score,
            "options_score":      ctx.options_score,
            "combined_adj":       ctx.combined_adj,
            "conviction_boost":   ctx.conviction_boost,
            "summary":            ctx.summary,
            "futures_available":  ctx.futures_available,
            "options_available":  ctx.options_available,
        }
    return out
