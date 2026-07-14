"""
futures_analyzer.py
====================
Analyzes futures contracts using yfinance data.

For each futures contract this module computes:
- Trend, momentum, volatility, and ATR scores (same logic as spot)
- Correlation with its underlying asset
- A confirmation score (-100 to +100): does the futures market
  confirm or contradict the spot signal?

Integration: futures signals are ADDITIVE to spot signals.
They do not replace them. A bullish spot signal confirmed by a
bullish futures market increases conviction; divergence reduces it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ===========================================================================
# Output dataclass
# ===========================================================================

@dataclass
class FuturesSignal:
    ticker:             str
    name:               str
    category:           str
    underlying:         str

    current_price:      float
    price_change_pct:   float      # 1-day % change

    # Sub-scores (each -100 to +100)
    trend_score:        float
    momentum_score:     float
    volatility_score:   float
    correlation_score:  float      # Alignment with underlying

    # Final confirmation score
    confirmation_score: float      # -100 to +100

    # Technical values
    atr:                float
    atr_pct:            float
    realised_vol:       float
    drawdown:           float
    roll_ret_5d:        float
    roll_ret_20d:       float

    regime:             str        # Bull / Bear / Sideways
    action:             str        # confirms / contradicts / neutral
    explanation:        str = ""


# ===========================================================================
# Core calculation
# ===========================================================================

def _compute_futures_sub_scores(df: pd.DataFrame) -> dict:
    """
    Compute sub-scores for a futures DataFrame.
    Returns a dict of score values.
    """
    if df is None or df.empty or len(df) < 30:
        return {}

    close = df["Close"].squeeze()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    # --- Trend score ---
    sma20  = close.rolling(20).mean()
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200, min_periods=50).mean()

    last = close.iloc[-1]
    trend = 0.0
    if pd.notna(sma20.iloc[-1]):
        trend += np.clip((last - sma20.iloc[-1]) / sma20.iloc[-1] * 300, -30, 30)
    if pd.notna(sma50.iloc[-1]):
        trend += np.clip((last - sma50.iloc[-1]) / sma50.iloc[-1] * 200, -25, 25)
    if pd.notna(sma200.iloc[-1]):
        trend += np.clip((last - sma200.iloc[-1]) / sma200.iloc[-1] * 150, -20, 20)
    trend = float(np.clip(trend, -100, 100))

    # --- Momentum score ---
    ret_1d = float(close.pct_change().iloc[-1]) if len(close) > 1 else 0.0
    ret_5d = float(close.pct_change(5).iloc[-1]) if len(close) > 5 else 0.0
    ret_20d = float(close.pct_change(20).iloc[-1]) if len(close) > 20 else 0.0

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=13, min_periods=14).mean()
    loss = (-delta).clip(lower=0).ewm(com=13, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1])

    momentum = float(np.clip((rsi - 50) * 1.2, -35, 35))
    momentum += float(np.clip(ret_5d * 200, -30, 30))
    momentum += float(np.clip(ret_20d * 100, -25, 25))
    momentum = float(np.clip(momentum, -100, 100))

    # --- Volatility score ---
    log_ret = np.log(close / close.shift(1)).dropna()
    vol = float(log_ret.rolling(20).std().iloc[-1]) * np.sqrt(252) if len(log_ret) >= 20 else 0.20
    if vol < 0.10:       vol_score = 20
    elif vol < 0.20:     vol_score = 5
    elif vol < 0.35:     vol_score = -15
    elif vol < 0.60:     vol_score = -35
    else:                vol_score = -60

    # --- ATR ---
    high = df["High"].squeeze() if "High" in df.columns else close
    low  = df["Low"].squeeze()  if "Low"  in df.columns else close
    if isinstance(high, pd.DataFrame): high = high.iloc[:, 0]
    if isinstance(low,  pd.DataFrame): low  = low.iloc[:,  0]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(com=13, min_periods=14).mean().iloc[-1])
    atr_pct = atr / last if last > 0 else 0.0

    # --- Drawdown ---
    rolling_high = close.rolling(252, min_periods=1).max()
    drawdown = float((last - rolling_high.iloc[-1]) / rolling_high.iloc[-1]) if rolling_high.iloc[-1] > 0 else 0.0

    # --- Regime ---
    if pd.notna(sma200.iloc[-1]) and last > sma200.iloc[-1] and \
       pd.notna(sma50.iloc[-1]) and sma50.iloc[-1] > sma200.iloc[-1]:
        regime = "Bull"
    elif pd.notna(sma200.iloc[-1]) and last < sma200.iloc[-1]:
        regime = "Bear"
    else:
        regime = "Sideways"

    return {
        "trend_score":    trend,
        "momentum_score": momentum,
        "vol_score":      vol_score,
        "atr":            round(atr, 4),
        "atr_pct":        round(atr_pct, 5),
        "realised_vol":   round(vol, 4),
        "drawdown":       round(drawdown, 4),
        "ret_1d":         round(ret_1d, 5),
        "ret_5d":         round(ret_5d, 5),
        "ret_20d":        round(ret_20d, 5),
        "regime":         regime,
        "last_price":     round(float(last), 4),
    }


def analyze_futures(
    ticker: str,
    meta:   dict,
    period: str = "2y",
    underlying_data: Optional[pd.DataFrame] = None,
) -> Optional[FuturesSignal]:
    """
    Download and analyze a single futures contract.

    Parameters
    ----------
    ticker          : yfinance futures ticker, e.g. "ES=F"
    meta            : Dict with keys: name, category, underlying
    period          : yfinance period string
    underlying_data : Optional DataFrame of underlying asset (for correlation)

    Returns
    -------
    FuturesSignal or None if download fails
    """
    try:
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df.empty or len(df) < 30:
            logger.warning(f"Insufficient data for futures {ticker}")
            return None

        # Flatten multi-level columns if present
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)

        scores = _compute_futures_sub_scores(df)
        if not scores:
            return None

        # --- Correlation with underlying ---
        correlation_score = 0.0
        if underlying_data is not None and not underlying_data.empty:
            try:
                fut_ret = df["Close"].squeeze().pct_change().dropna()
                if isinstance(fut_ret, pd.DataFrame):
                    fut_ret = fut_ret.iloc[:, 0]
                und_ret = underlying_data["Close"].squeeze().pct_change().dropna()
                if isinstance(und_ret, pd.DataFrame):
                    und_ret = und_ret.iloc[:, 0]
                aligned_f, aligned_u = fut_ret.align(und_ret, join="inner")
                if len(aligned_f) > 20:
                    corr = float(aligned_f.rolling(60).corr(aligned_u).iloc[-1])
                    if not np.isnan(corr):
                        # High positive correlation = futures confirm spot
                        # Weighted by trend direction
                        correlation_score = float(np.clip(corr * scores["trend_score"], -50, 50))
            except Exception as e:
                logger.debug(f"Correlation calculation failed for {ticker}: {e}")

        # --- Composite confirmation score ---
        confirmation = (
            scores["trend_score"]    * 0.35
            + scores["momentum_score"] * 0.30
            + scores["vol_score"]      * 0.10
            + correlation_score        * 0.25
        )
        confirmation = float(np.clip(confirmation, -100, 100))

        # --- Action label ---
        if confirmation > 20:   action = "confirms bullish"
        elif confirmation < -20: action = "confirms bearish"
        else:                   action = "neutral / inconclusive"

        explanation = (
            f"{meta.get('name', ticker)} futures score: {confirmation:.1f}. "
            f"Regime: {scores['regime']}. "
            f"Trend: {scores['trend_score']:.1f}, "
            f"Momentum: {scores['momentum_score']:.1f}. "
            f"ATR: {scores['atr_pct']:.2%} of price. "
            f"20-day return: {scores['ret_20d']:.2%}."
        )

        return FuturesSignal(
            ticker=ticker,
            name=meta.get("name", ticker),
            category=meta.get("category", ""),
            underlying=meta.get("underlying", ticker),
            current_price=scores["last_price"],
            price_change_pct=scores["ret_1d"],
            trend_score=round(scores["trend_score"], 2),
            momentum_score=round(scores["momentum_score"], 2),
            volatility_score=round(scores["vol_score"], 2),
            correlation_score=round(correlation_score, 2),
            confirmation_score=round(confirmation, 2),
            atr=scores["atr"],
            atr_pct=scores["atr_pct"],
            realised_vol=scores["realised_vol"],
            drawdown=scores["drawdown"],
            roll_ret_5d=scores["ret_5d"],
            roll_ret_20d=scores["ret_20d"],
            regime=scores["regime"],
            action=action,
            explanation=explanation,
        )

    except Exception as e:
        logger.error(f"Futures analysis failed for {ticker}: {e}", exc_info=True)
        return None


def analyze_all_futures(
    config: dict,
    raw_spot_data: Optional[Dict[str, pd.DataFrame]] = None,
) -> Dict[str, FuturesSignal]:
    """
    Run analysis for all futures tickers in the config.

    Parameters
    ----------
    config        : Full universe config dict
    raw_spot_data : Optional dict of spot DataFrames for correlation

    Returns
    -------
    Dict: { "ES=F": FuturesSignal, ... }
    """
    futures_cfg = config.get("futures", {})
    results: Dict[str, FuturesSignal] = {}

    for category_key, contracts in futures_cfg.items():
        for contract in contracts:
            ticker = contract["ticker"]
            underlying = contract.get("underlying", ticker)
            underlying_data = (raw_spot_data or {}).get(underlying)

            logger.info(f"Analyzing futures: {ticker}")
            sig = analyze_futures(
                ticker=ticker,
                meta=contract,
                underlying_data=underlying_data,
            )
            if sig:
                results[ticker] = sig

    logger.info(f"Futures analysis complete: {len(results)} contracts")
    return results


def futures_to_json(futures_signals: Dict[str, "FuturesSignal"]) -> dict:
    """Serialize futures signals to a JSON-safe dict."""
    out = {}
    for ticker, sig in futures_signals.items():
        out[ticker] = {
            "ticker":             sig.ticker,
            "name":               sig.name,
            "category":           sig.category,
            "underlying":         sig.underlying,
            "current_price":      sig.current_price,
            "price_change_pct":   sig.price_change_pct,
            "trend_score":        sig.trend_score,
            "momentum_score":     sig.momentum_score,
            "volatility_score":   sig.volatility_score,
            "correlation_score":  sig.correlation_score,
            "confirmation_score": sig.confirmation_score,
            "atr":                sig.atr,
            "atr_pct":            sig.atr_pct,
            "realised_vol":       sig.realised_vol,
            "drawdown":           sig.drawdown,
            "roll_ret_5d":        sig.roll_ret_5d,
            "roll_ret_20d":       sig.roll_ret_20d,
            "regime":             sig.regime,
            "action":             sig.action,
            "explanation":        sig.explanation,
        }
    return out
