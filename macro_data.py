"""
macro_data.py
=============
Phase 3 — Macro data integration via FRED (Federal Reserve Economic Data).

Fetches key macro indicators:
  - Fed Funds Rate (FEDFUNDS)
  - CPI Inflation (CPIAUCSL)
  - 10Y-2Y Treasury yield spread / inversion (T10Y2Y)
  - US Unemployment Rate (UNRATE)
  - VIX volatility index proxy via yfinance (^VIX)
  - DXY Dollar index proxy via yfinance (DX-Y.NYB)
  - 10Y Treasury yield (^TNX)

Produces:
  - MacroSnapshot dataclass with all indicators
  - macro_score() — a single -100 to +100 macro sentiment score
  - Regime classification: Expansion / Stagflation / Recession / Recovery

FRED data is free and requires no API key when accessed via pandas-datareader.
Falls back gracefully if internet access or data is unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Try pandas-datareader for FRED
try:
    import pandas_datareader.data as web
    FRED_AVAILABLE = True
except ImportError:
    FRED_AVAILABLE = False
    logger.warning("pandas-datareader not installed. FRED data unavailable. Run: pip install pandas-datareader")


@dataclass
class MacroSnapshot:
    """Current macro environment snapshot."""

    # Interest rates
    fed_funds_rate:     Optional[float] = None   # % e.g. 5.33
    treasury_10y:       Optional[float] = None   # % e.g. 4.45
    treasury_2y:        Optional[float] = None   # % e.g. 4.80
    yield_spread_10y2y: Optional[float] = None   # 10Y - 2Y (negative = inverted = recession risk)

    # Inflation
    cpi_yoy:            Optional[float] = None   # % year-over-year CPI change

    # Labour market
    unemployment_rate:  Optional[float] = None   # % e.g. 4.1

    # Market stress
    vix:                Optional[float] = None   # VIX level
    dxy:                Optional[float] = None   # Dollar index level
    dxy_trend:          Optional[float] = None   # 20d return of DXY

    # Gold trend (risk-off indicator)
    gold_trend_20d:     Optional[float] = None   # 20d return of GC=F

    # Derived macro score
    macro_score:        float = 0.0              # -100 to +100
    regime:             str   = "Unknown"        # Expansion / Stagflation / Recession / Recovery

    # Data freshness
    data_date:          str   = ""
    sources_available:  list  = field(default_factory=list)
    warnings:           list  = field(default_factory=list)


def _fetch_fred(series_id: str, periods: int = 3) -> Optional[float]:
    """
    Fetch the latest value of a FRED series.
    Returns the most recent non-NaN value, or None if unavailable.
    """
    if not FRED_AVAILABLE:
        return None
    try:
        end   = date.today()
        start = end - timedelta(days=365)
        data  = web.DataReader(series_id, "fred", start, end)
        clean = data.dropna()
        if clean.empty:
            return None
        return float(clean.iloc[-1, 0])
    except Exception as e:
        logger.debug(f"FRED fetch failed for {series_id}: {e}")
        return None


def _fetch_yf_price(ticker: str, period: str = "1mo") -> Optional[pd.Series]:
    """Fetch recent close prices for a yfinance ticker."""
    try:
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df.empty:
            return None
        close = df["Close"].squeeze()
        return close.dropna()
    except Exception as e:
        logger.debug(f"yfinance fetch failed for {ticker}: {e}")
        return None


def _pct_change_nd(series: pd.Series, n: int = 20) -> Optional[float]:
    """Return n-day percentage change of a price series."""
    if series is None or len(series) < n + 1:
        return None
    return float((series.iloc[-1] / series.iloc[-n - 1]) - 1)


def fetch_macro_snapshot() -> MacroSnapshot:
    """
    Fetch all macro indicators and return a MacroSnapshot.
    Fails gracefully — missing data returns None for that field.
    """
    snap = MacroSnapshot(data_date=str(date.today()))
    sources = []

    # --- FRED data ---
    if FRED_AVAILABLE:
        snap.fed_funds_rate    = _fetch_fred("FEDFUNDS")
        snap.cpi_yoy           = _fetch_fred("CPIAUCSL")   # Monthly level; we compute YoY below
        snap.unemployment_rate = _fetch_fred("UNRATE")
        snap.yield_spread_10y2y = _fetch_fred("T10Y2Y")
        sources.append("FRED")

        # CPI YoY: fetch 13 months to compute year-over-year manually
        if FRED_AVAILABLE:
            try:
                end   = date.today()
                start = end - timedelta(days=400)
                cpi_raw = web.DataReader("CPIAUCSL", "fred", start, end).dropna()
                if len(cpi_raw) >= 13:
                    latest   = float(cpi_raw.iloc[-1, 0])
                    year_ago = float(cpi_raw.iloc[-13, 0])
                    snap.cpi_yoy = round((latest / year_ago - 1) * 100, 2)
            except Exception:
                pass
    else:
        snap.warnings.append("FRED data unavailable — install pandas-datareader")

    # --- yfinance market data ---
    # VIX
    vix_series = _fetch_yf_price("^VIX", period="5d")
    if vix_series is not None and not vix_series.empty:
        snap.vix = round(float(vix_series.iloc[-1]), 2)
        sources.append("VIX")

    # 10Y Treasury yield
    tnx_series = _fetch_yf_price("^TNX", period="5d")
    if tnx_series is not None and not tnx_series.empty:
        snap.treasury_10y = round(float(tnx_series.iloc[-1]), 3)
        sources.append("10Y")

    # 2Y Treasury yield
    tyx_series = _fetch_yf_price("^IRX", period="5d")   # 13-week T-bill as proxy
    if tyx_series is not None and not tyx_series.empty:
        snap.treasury_2y = round(float(tyx_series.iloc[-1]) / 100, 3)

    # DXY
    dxy_series = _fetch_yf_price("DX-Y.NYB", period="3mo")
    if dxy_series is not None and not dxy_series.empty:
        snap.dxy       = round(float(dxy_series.iloc[-1]), 2)
        snap.dxy_trend = round(_pct_change_nd(dxy_series, 20) or 0.0, 4)
        sources.append("DXY")

    # Gold trend
    gold_series = _fetch_yf_price("GC=F", period="3mo")
    if gold_series is not None and not gold_series.empty:
        snap.gold_trend_20d = round(_pct_change_nd(gold_series, 20) or 0.0, 4)
        sources.append("Gold")

    snap.sources_available = sources

    # --- Compute macro score and regime ---
    snap.macro_score = _compute_macro_score(snap)
    snap.regime      = _classify_regime(snap)

    return snap


def _compute_macro_score(snap: MacroSnapshot) -> float:
    """
    Compute an overall macro score from -100 (very bearish) to +100 (very bullish).

    Factors:
    - VIX:          low = bullish, high = bearish
    - Yield spread: positive = bullish, inverted = bearish
    - CPI:          moderate = neutral, very high = bearish
    - DXY trend:    rising dollar = bearish for risk assets
    - Gold trend:   rising gold = risk-off = mild bearish for equities
    - Fed funds:    very high rates = slightly bearish (restrictive)
    """
    score = 0.0
    weight_used = 0.0

    # VIX (weight 25%)
    if snap.vix is not None:
        vix = snap.vix
        if vix < 15:
            vix_score = 40
        elif vix < 20:
            vix_score = 20
        elif vix < 25:
            vix_score = 0
        elif vix < 35:
            vix_score = -30
        else:
            vix_score = -60
        score += vix_score * 0.25
        weight_used += 0.25

    # Yield spread (weight 25%)
    if snap.yield_spread_10y2y is not None:
        spread = snap.yield_spread_10y2y
        if spread > 1.0:
            spread_score = 40    # Steep curve = growth expected
        elif spread > 0.0:
            spread_score = 15
        elif spread > -0.5:
            spread_score = -20   # Mild inversion
        else:
            spread_score = -50   # Deep inversion = recession risk
        score += spread_score * 0.25
        weight_used += 0.25

    # CPI inflation (weight 20%)
    if snap.cpi_yoy is not None:
        cpi = snap.cpi_yoy
        if cpi < 2.5:
            cpi_score = 20       # Near target = good
        elif cpi < 3.5:
            cpi_score = 5
        elif cpi < 5.0:
            cpi_score = -20      # Above target = restrictive policy
        else:
            cpi_score = -40      # High inflation = very bearish
        score += cpi_score * 0.20
        weight_used += 0.20

    # DXY trend (weight 15%) — rising dollar hurts risk assets, gold, EM
    if snap.dxy_trend is not None:
        dxy_score = float(np.clip(-snap.dxy_trend * 300, -30, 30))
        score += dxy_score * 0.15
        weight_used += 0.15

    # Gold trend (weight 15%) — rising gold = risk-off
    if snap.gold_trend_20d is not None:
        gold_score = float(np.clip(-snap.gold_trend_20d * 150, -20, 20))
        score += gold_score * 0.15
        weight_used += 0.15

    # Normalise for missing data
    if weight_used > 0:
        score = score / weight_used * 1.0  # Already weighted correctly

    return round(float(np.clip(score, -100, 100)), 2)


def _classify_regime(snap: MacroSnapshot) -> str:
    """
    Classify the macro regime based on growth + inflation indicators.

    Quadrant model:
    - High growth + Low inflation  → Expansion (Goldilocks)
    - High growth + High inflation → Stagflation
    - Low growth  + Low inflation  → Recovery / Deflation risk
    - Low growth  + High inflation → Recession
    """
    cpi        = snap.cpi_yoy or 3.0
    spread     = snap.yield_spread_10y2y or 0.0
    vix        = snap.vix or 20.0
    unemp      = snap.unemployment_rate or 4.5

    high_inflation = cpi > 3.5
    low_growth     = spread < 0 or vix > 25 or unemp > 5.5

    if not high_inflation and not low_growth:
        return "Expansion (Goldilocks)"
    elif high_inflation and not low_growth:
        return "Stagflation Risk"
    elif not high_inflation and low_growth:
        return "Slowdown / Recovery"
    else:
        return "Recession Risk"


def macro_to_json(snap: MacroSnapshot) -> dict:
    """Serialise MacroSnapshot to JSON-safe dict."""
    return {
        "fed_funds_rate":      snap.fed_funds_rate,
        "treasury_10y":        snap.treasury_10y,
        "treasury_2y":         snap.treasury_2y,
        "yield_spread_10y2y":  snap.yield_spread_10y2y,
        "cpi_yoy":             snap.cpi_yoy,
        "unemployment_rate":   snap.unemployment_rate,
        "vix":                 snap.vix,
        "dxy":                 snap.dxy,
        "dxy_trend":           snap.dxy_trend,
        "gold_trend_20d":      snap.gold_trend_20d,
        "macro_score":         snap.macro_score,
        "regime":              snap.regime,
        "data_date":           snap.data_date,
        "sources_available":   snap.sources_available,
        "warnings":            snap.warnings,
    }
