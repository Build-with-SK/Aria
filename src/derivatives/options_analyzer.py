"""
options_analyzer.py
===================
Fetches and analyses options chain data from yfinance.

For each optionable ticker, this module:
  - Fetches all expiry dates available
  - Downloads calls and puts chains
  - Calculates moneyness, intrinsic/time value, breakeven prices
  - Computes put/call ratios (open interest and volume)
  - Produces a sentiment score from -100 (max bearish) to +100 (max bullish)

IMPORTANT: Options data from yfinance is delayed and may be incomplete.
This module uses try/except at every step so a bad ticker never crashes
the full pipeline. Always check the 'error' field in the returned dict.

Integration: The options sentiment score is ADDITIVE to spot signals.
It does not replace them. Think of it as an additional confirmation layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from src.derivatives.black_scholes import call_price, put_price, full_greeks

logger = logging.getLogger(__name__)


# ===========================================================================
# Output dataclass
# ===========================================================================

@dataclass
class OptionChainSummary:
    """Summarised options analysis for one ticker."""
    ticker:              str
    underlying_price:    float
    analysis_date:       str

    # Nearest expiry selected for analysis
    selected_expiry:     str
    days_to_expiry:      int

    # Put/Call ratios (open interest and volume)
    pc_oi_ratio:         float      # > 1 = more puts = bearish lean
    pc_vol_ratio:        float      # > 1 = more put volume = bearish lean

    # Aggregated IV metrics
    avg_call_iv:         float      # Average implied vol of calls
    avg_put_iv:          float      # Average implied vol of puts
    iv_skew:             float      # put IV - call IV > 0 = bearish skew

    # ATM option metrics (closest strike to current price)
    atm_strike:          float
    atm_call_price:      float
    atm_put_price:       float
    atm_call_delta:      Optional[float]
    atm_put_delta:       Optional[float]
    atm_call_oi:         int
    atm_put_oi:          int

    # Breakeven prices
    call_breakeven:      float      # Strike + call premium
    put_breakeven:       float      # Strike - put premium

    # Sentiment scores
    bullish_options_score: float    # -100 to +100
    bearish_options_score: float    # -100 to +100
    final_sentiment_score: float    # -100 (max bearish) to +100 (max bullish)

    # Top call and put by open interest
    top_call_strike:     float
    top_put_strike:      float

    # Raw chain sizes
    n_calls:             int
    n_puts:              int

    explanation:         str = ""
    error:               Optional[str] = None


# ===========================================================================
# Core analysis logic
# ===========================================================================

def _find_atm_strike(strikes: pd.Series, spot: float) -> float:
    """Return the strike closest to the current spot price."""
    if strikes.empty:
        return spot
    return float(strikes.iloc[(strikes - spot).abs().argsort().iloc[0]])


def _safe_mean(series: pd.Series) -> float:
    """Mean of a series, ignoring NaN, returning 0 if empty."""
    clean = series.dropna()
    return float(clean.mean()) if not clean.empty else 0.0


def _compute_pc_ratio(calls: pd.DataFrame, puts: pd.DataFrame, col: str) -> float:
    """
    Compute put/call ratio for a given column (openInterest or volume).
    Returns 1.0 (neutral) if data is missing or zero.
    """
    try:
        call_total = calls[col].fillna(0).sum()
        put_total  = puts[col].fillna(0).sum()
        if call_total == 0:
            return 1.0
        return float(put_total / call_total)
    except Exception:
        return 1.0


def _compute_iv_skew(calls: pd.DataFrame, puts: pd.DataFrame, spot: float) -> Tuple[float, float, float]:
    """
    Average call IV, average put IV, and skew (put IV - call IV).
    Positive skew = market paying more for downside protection = bearish bias.
    """
    # Filter to reasonable moneyness range (0.80 to 1.20 of spot)
    atm_calls = calls[calls["strike"].between(spot * 0.85, spot * 1.15)]
    atm_puts  = puts[puts["strike"].between(spot * 0.85, spot * 1.15)]

    avg_call_iv = _safe_mean(atm_calls["impliedVolatility"]) if not atm_calls.empty else 0.0
    avg_put_iv  = _safe_mean(atm_puts["impliedVolatility"])  if not atm_puts.empty  else 0.0
    skew        = avg_put_iv - avg_call_iv

    return round(avg_call_iv, 4), round(avg_put_iv, 4), round(skew, 4)


def _compute_sentiment_score(
    pc_oi_ratio:  float,
    pc_vol_ratio: float,
    iv_skew:      float,
    days_to_exp:  int,
    atm_call_oi:  int,
    atm_put_oi:   int,
) -> Tuple[float, float, float]:
    """
    Produce bullish score, bearish score, and net sentiment score.

    Methodology:
    - Low P/C OI ratio  → bullish (more calls bought than puts)
    - Low P/C vol ratio → bullish (more call volume)
    - Negative IV skew  → bullish (calls more expensive than puts)
    - High call OI at ATM → bullish conviction

    All components are normalised and clipped to [-100, +100].
    Returns: (bullish_score, bearish_score, net_score)
    """

    # --- P/C OI contribution ---
    # PC ratio of 1 = neutral, < 1 = bullish, > 1 = bearish
    # Mapped: 0.5 → +40, 1.0 → 0, 2.0 → -40
    pc_oi_score = float(np.clip((1.0 - pc_oi_ratio) * 40, -40, 40))

    # --- P/C Volume contribution ---
    pc_vol_score = float(np.clip((1.0 - pc_vol_ratio) * 30, -30, 30))

    # --- IV skew contribution ---
    # Negative skew (calls > puts) = bullish
    # Positive skew (puts > calls) = bearish
    skew_score = float(np.clip(-iv_skew * 200, -30, 30))

    # --- ATM call vs put open interest ---
    total_atm_oi = atm_call_oi + atm_put_oi
    if total_atm_oi > 0:
        call_pct = atm_call_oi / total_atm_oi
        atm_oi_score = float(np.clip((call_pct - 0.5) * 40, -20, 20))
    else:
        atm_oi_score = 0.0

    # Net score
    net = pc_oi_score + pc_vol_score + skew_score + atm_oi_score
    net = float(np.clip(net, -100, 100))

    bullish_score = float(np.clip(net, 0, 100))
    bearish_score = float(np.clip(-net, 0, 100))

    return round(bullish_score, 2), round(bearish_score, 2), round(net, 2)


# ===========================================================================
# Main analysis function
# ===========================================================================

def analyze_options(
    ticker:    str,
    risk_free_rate: float = 0.045,
    target_dte: int = 30,          # Target days to expiry (nearest to 30)
) -> Optional[OptionChainSummary]:
    """
    Fetch and analyse the options chain for one ticker.

    Parameters
    ----------
    ticker         : yfinance ticker (must have options, e.g. 'AAPL')
    risk_free_rate : Annual risk-free rate for Black-Scholes
    target_dte     : Preferred days-to-expiry for analysis (30 is default)

    Returns
    -------
    OptionChainSummary or None if options data is unavailable
    """
    try:
        tk = yf.Ticker(ticker)

        # --- Get expiry dates ---
        try:
            expiries = tk.options
        except Exception as e:
            logger.warning(f"No options data for {ticker}: {e}")
            return None

        if not expiries:
            logger.warning(f"Empty options expiry list for {ticker}")
            return None

        # --- Current price ---
        try:
            hist = tk.history(period="2d")
            if hist.empty:
                logger.warning(f"No price data for {ticker}")
                return None
            spot = float(hist["Close"].iloc[-1])
        except Exception as e:
            logger.warning(f"Could not fetch spot price for {ticker}: {e}")
            return None

        # --- Select nearest expiry to target_dte ---
        today = date.today()
        best_expiry = None
        best_dte    = 9999

        for exp in expiries:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte > 0 and abs(dte - target_dte) < abs(best_dte - target_dte):
                    best_dte    = dte
                    best_expiry = exp
            except Exception:
                continue

        if best_expiry is None or best_dte <= 0:
            logger.warning(f"No valid upcoming expiry for {ticker}")
            return None

        # --- Fetch chain ---
        try:
            chain = tk.option_chain(best_expiry)
            calls = chain.calls.copy()
            puts  = chain.puts.copy()
        except Exception as e:
            logger.warning(f"Could not fetch chain for {ticker} @ {best_expiry}: {e}")
            return None

        if calls.empty or puts.empty:
            logger.warning(f"Empty chain for {ticker} @ {best_expiry}")
            return None

        # Normalise column names (yfinance can vary)
        for df in [calls, puts]:
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]

        # Ensure required columns exist
        required = ["strike", "lastprice", "bid", "ask", "volume", "openinterest", "impliedvolatility"]
        for col in required:
            for df in [calls, puts]:
                if col not in df.columns:
                    df[col] = np.nan

        # Rename for consistency
        for df in [calls, puts]:
            if "openinterest" in df.columns and "open_interest" not in df.columns:
                df["open_interest"] = df["openinterest"]
            if "impliedvolatility" in df.columns and "impliedVolatility" not in df.columns:
                df["impliedVolatility"] = df["impliedvolatility"]
            if "lastprice" in df.columns and "lastPrice" not in df.columns:
                df["lastPrice"] = df["lastprice"]
            if "openInterest" not in df.columns:
                df["openInterest"] = df.get("open_interest", 0)

        # Fill NaN volumes with 0
        for df in [calls, puts]:
            df["volume"]        = df["volume"].fillna(0)
            df["openInterest"]  = df["openInterest"].fillna(0)
            df["impliedVolatility"] = df["impliedVolatility"].fillna(np.nan)

        # --- ATM strike ---
        atm_strike = _find_atm_strike(calls["strike"], spot)

        atm_calls = calls[calls["strike"] == atm_strike]
        atm_puts  = puts[puts["strike"] == atm_strike]

        atm_call_price_val = float(atm_calls["lastPrice"].iloc[0]) if not atm_calls.empty else np.nan
        atm_put_price_val  = float(atm_puts["lastPrice"].iloc[0])  if not atm_puts.empty  else np.nan
        atm_call_oi        = int(atm_calls["openInterest"].iloc[0]) if not atm_calls.empty else 0
        atm_put_oi         = int(atm_puts["openInterest"].iloc[0])  if not atm_puts.empty  else 0

        # ATM greeks via Black-Scholes
        T = best_dte / 365.0
        atm_call_iv = float(atm_calls["impliedVolatility"].iloc[0]) if not atm_calls.empty else 0.25
        atm_call_iv = atm_call_iv if pd.notna(atm_call_iv) and atm_call_iv > 0 else 0.25

        try:
            gs = full_greeks(spot, atm_strike, T, risk_free_rate, atm_call_iv, "call")
            atm_call_delta = gs.get("delta")
            atm_put_delta  = full_greeks(spot, atm_strike, T, risk_free_rate, atm_call_iv, "put").get("delta")
        except Exception:
            atm_call_delta = None
            atm_put_delta  = None

        # --- Breakeven prices ---
        call_breakeven = round(atm_strike + atm_call_price_val, 4) if pd.notna(atm_call_price_val) else atm_strike
        put_breakeven  = round(atm_strike - atm_put_price_val,  4) if pd.notna(atm_put_price_val)  else atm_strike

        # --- P/C ratios ---
        pc_oi_ratio  = _compute_pc_ratio(calls, puts, "openInterest")
        pc_vol_ratio = _compute_pc_ratio(calls, puts, "volume")

        # --- IV skew ---
        avg_call_iv, avg_put_iv, iv_skew = _compute_iv_skew(calls, puts, spot)

        # --- Top OI strikes ---
        top_call_row = calls.loc[calls["openInterest"].idxmax()] if not calls.empty else None
        top_put_row  = puts.loc[puts["openInterest"].idxmax()]   if not puts.empty  else None
        top_call_strike = float(top_call_row["strike"]) if top_call_row is not None else atm_strike
        top_put_strike  = float(top_put_row["strike"])  if top_put_row  is not None else atm_strike

        # --- Sentiment scores ---
        bull_score, bear_score, net_score = _compute_sentiment_score(
            pc_oi_ratio, pc_vol_ratio, iv_skew, best_dte, atm_call_oi, atm_put_oi
        )

        # --- Human-readable explanation ---
        sentiment_label = "bullish" if net_score > 10 else "bearish" if net_score < -10 else "neutral"
        explanation = (
            f"{ticker} options sentiment: {net_score:+.1f} ({sentiment_label}). "
            f"Expiry: {best_expiry} ({best_dte} days). "
            f"P/C OI ratio: {pc_oi_ratio:.2f} ({'bearish bias' if pc_oi_ratio > 1.1 else 'bullish bias' if pc_oi_ratio < 0.9 else 'neutral'}). "
            f"IV skew: {iv_skew:.3f} ({'bearish skew' if iv_skew > 0.02 else 'bullish skew' if iv_skew < -0.02 else 'neutral'}). "
            f"Max call OI at ${top_call_strike:,.2f}, max put OI at ${top_put_strike:,.2f}."
        )

        return OptionChainSummary(
            ticker=ticker,
            underlying_price=round(spot, 4),
            analysis_date=str(today),
            selected_expiry=best_expiry,
            days_to_expiry=best_dte,
            pc_oi_ratio=round(pc_oi_ratio, 3),
            pc_vol_ratio=round(pc_vol_ratio, 3),
            avg_call_iv=avg_call_iv,
            avg_put_iv=avg_put_iv,
            iv_skew=iv_skew,
            atm_strike=atm_strike,
            atm_call_price=round(atm_call_price_val, 4) if pd.notna(atm_call_price_val) else 0.0,
            atm_put_price=round(atm_put_price_val, 4) if pd.notna(atm_put_price_val) else 0.0,
            atm_call_delta=atm_call_delta,
            atm_put_delta=atm_put_delta,
            atm_call_oi=atm_call_oi,
            atm_put_oi=atm_put_oi,
            call_breakeven=call_breakeven,
            put_breakeven=put_breakeven,
            bullish_options_score=bull_score,
            bearish_options_score=bear_score,
            final_sentiment_score=net_score,
            top_call_strike=top_call_strike,
            top_put_strike=top_put_strike,
            n_calls=len(calls),
            n_puts=len(puts),
            explanation=explanation,
        )

    except Exception as e:
        logger.error(f"Options analysis failed for {ticker}: {e}", exc_info=True)
        return None


def analyze_all_options(
    config: dict,
    risk_free_rate: float = 0.045,
) -> Dict[str, Optional[OptionChainSummary]]:
    """
    Run options analysis for all tickers in options_universe config section.

    Returns dict: { "AAPL": OptionChainSummary, "MSFT": None, ... }
    None values indicate unavailable/failed data — handle gracefully.
    """
    results: Dict[str, Optional[OptionChainSummary]] = {}
    options_cfg = config.get("options_universe", [])

    for item in options_cfg:
        ticker = item["ticker"]
        logger.info(f"Analyzing options: {ticker}")
        result = analyze_options(ticker, risk_free_rate=risk_free_rate)
        results[ticker] = result
        if result is None:
            logger.warning(f"Options analysis returned None for {ticker}")

    logger.info(f"Options analysis complete: {sum(1 for v in results.values() if v)} / {len(results)} successful")
    return results


def options_to_json(options_data: Dict[str, Optional[OptionChainSummary]]) -> dict:
    """Serialise options analysis results to a JSON-safe dict."""
    out = {}
    for ticker, data in options_data.items():
        if data is None:
            out[ticker] = {"ticker": ticker, "error": "No data available"}
            continue
        out[ticker] = {
            "ticker":               data.ticker,
            "underlying_price":     data.underlying_price,
            "analysis_date":        data.analysis_date,
            "selected_expiry":      data.selected_expiry,
            "days_to_expiry":       data.days_to_expiry,
            "pc_oi_ratio":          data.pc_oi_ratio,
            "pc_vol_ratio":         data.pc_vol_ratio,
            "avg_call_iv":          data.avg_call_iv,
            "avg_put_iv":           data.avg_put_iv,
            "iv_skew":              data.iv_skew,
            "atm_strike":           data.atm_strike,
            "atm_call_price":       data.atm_call_price,
            "atm_put_price":        data.atm_put_price,
            "atm_call_delta":       data.atm_call_delta,
            "atm_put_delta":        data.atm_put_delta,
            "atm_call_oi":          data.atm_call_oi,
            "atm_put_oi":           data.atm_put_oi,
            "call_breakeven":       data.call_breakeven,
            "put_breakeven":        data.put_breakeven,
            "bullish_options_score": data.bullish_options_score,
            "bearish_options_score": data.bearish_options_score,
            "final_sentiment_score": data.final_sentiment_score,
            "top_call_strike":      data.top_call_strike,
            "top_put_strike":       data.top_put_strike,
            "n_calls":              data.n_calls,
            "n_puts":               data.n_puts,
            "explanation":          data.explanation,
        }
    return out
