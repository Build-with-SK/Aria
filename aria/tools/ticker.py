"""
ARIA Tool: get_ticker_data
Fetches OHLCV, technical indicators, and a composite signal score via yfinance.
"""

import asyncio
from datetime import datetime
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np


def _compute_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2) if not rsi.empty else None


def _compute_macd(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return (
        round(float(macd_line.iloc[-1]), 4),
        round(float(signal_line.iloc[-1]), 4),
        round(float(histogram.iloc[-1]), 4),
    )


def _compute_bollinger(series: pd.Series, period: int = 20):
    ma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    price = series.iloc[-1]
    ma_val = float(ma.iloc[-1])
    upper_val = float(upper.iloc[-1])
    lower_val = float(lower.iloc[-1])
    # Percent B
    pct_b = (price - lower_val) / (upper_val - lower_val) if upper_val != lower_val else 0.5
    return round(ma_val, 4), round(upper_val, 4), round(lower_val, 4), round(pct_b, 4)


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    tr = pd.DataFrame({
        "hl": high - low,
        "hc": (high - close.shift()).abs(),
        "lc": (low - close.shift()).abs(),
    }).max(axis=1)
    atr = tr.rolling(period).mean()
    return round(float(atr.iloc[-1]), 4)


def _compute_vwap(df: pd.DataFrame) -> float:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    vwap = (typical * df["Volume"]).cumsum() / df["Volume"].cumsum()
    return round(float(vwap.iloc[-1]), 4)


def _compute_signal_score(indicators: dict) -> dict:
    """
    Composite signal score (0-100) from multiple technical factors.
    Returns score, direction, and factor breakdown.
    """
    scores = []
    factors = {}

    rsi = indicators.get("rsi")
    if rsi is not None:
        if rsi < 30:
            s = 80
            note = "oversold"
        elif rsi < 45:
            s = 60
            note = "mildly oversold"
        elif rsi < 55:
            s = 50
            note = "neutral"
        elif rsi < 70:
            s = 60
            note = "mildly overbought"
        else:
            s = 25
            note = "overbought"
        scores.append(s)
        factors["rsi"] = {"value": rsi, "score": s, "note": note}

    macd_hist = indicators.get("macd_histogram")
    if macd_hist is not None:
        s = 70 if macd_hist > 0 else 35
        note = "bullish momentum" if macd_hist > 0 else "bearish momentum"
        scores.append(s)
        factors["macd"] = {"value": macd_hist, "score": s, "note": note}

    pct_b = indicators.get("bb_pct_b")
    if pct_b is not None:
        if pct_b < 0.2:
            s = 75
            note = "near lower band (support)"
        elif pct_b > 0.8:
            s = 30
            note = "near upper band (resistance)"
        else:
            s = 55
            note = "mid-band"
        scores.append(s)
        factors["bollinger"] = {"value": pct_b, "score": s, "note": note}

    price = indicators.get("current_price")
    ma50  = indicators.get("ma50")
    ma200 = indicators.get("ma200")
    if price and ma50 and ma200:
        if price > ma50 > ma200:
            s = 75
            note = "price above both MAs (bullish alignment)"
        elif price > ma50 and price < ma200:
            s = 55
            note = "above 50MA, below 200MA"
        elif price < ma50 < ma200:
            s = 25
            note = "price below both MAs (bearish alignment)"
        else:
            s = 45
            note = "mixed MA alignment"
        scores.append(s)
        factors["moving_averages"] = {"score": s, "note": note}

    vol = indicators.get("volume_ratio")
    if vol is not None:
        if vol > 1.5:
            s = 65
            note = "high volume (conviction)"
        elif vol < 0.7:
            s = 40
            note = "low volume (caution)"
        else:
            s = 55
            note = "normal volume"
        scores.append(s)
        factors["volume"] = {"value": vol, "score": s, "note": note}

    if not scores:
        return {"score": None, "direction": "unknown", "factors": {}}

    composite = round(sum(scores) / len(scores), 1)
    direction = "bullish" if composite >= 57 else ("bearish" if composite <= 43 else "neutral")

    return {"score": composite, "direction": direction, "factors": factors}


async def get_ticker_data(ticker: str, period: str = "3mo") -> dict:
    """Main tool function — fetch and compute all signals for a ticker."""
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: yf.download(ticker, period=period, progress=False, auto_adjust=True))
        info = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).info)
    except Exception as e:
        return {"error": f"Data fetch failed for {ticker}: {e}", "ticker": ticker}

    if data.empty:
        return {"error": f"No price data returned for {ticker}", "ticker": ticker}

    close = data["Close"].squeeze()
    high  = data["High"].squeeze()
    low   = data["Low"].squeeze()
    vol   = data["Volume"].squeeze()

    current_price = round(float(close.iloc[-1]), 4)
    prev_close    = round(float(close.iloc[-2]), 4) if len(close) >= 2 else current_price
    day_change    = round((current_price - prev_close) / prev_close * 100, 2)

    # Moving averages
    ma20  = round(float(close.rolling(20).mean().iloc[-1]), 4)  if len(close) >= 20  else None
    ma50  = round(float(close.rolling(50).mean().iloc[-1]), 4)  if len(close) >= 50  else None
    ma200 = round(float(close.rolling(200).mean().iloc[-1]), 4) if len(close) >= 200 else None

    # Volume ratio (today vs 20-day avg)
    avg_vol = float(vol.rolling(20).mean().iloc[-1]) if len(vol) >= 20 else None
    vol_ratio = round(float(vol.iloc[-1]) / avg_vol, 2) if avg_vol else None

    # Indicators
    rsi = _compute_rsi(close)
    macd_line, macd_signal, macd_hist = _compute_macd(close)
    bb_ma, bb_upper, bb_lower, bb_pct_b = _compute_bollinger(close)
    atr = _compute_atr(high, low, close)
    vwap = _compute_vwap(data)

    # 52-week range
    try:
        high_52w = info.get("fiftyTwoWeekHigh")
        low_52w  = info.get("fiftyTwoWeekLow")
    except Exception:
        high_52w = low_52w = None

    # Key levels (support / resistance from recent pivots)
    recent_high = round(float(high.tail(20).max()), 4)
    recent_low  = round(float(low.tail(20).min()), 4)

    indicators = {
        "current_price": current_price,
        "rsi": rsi,
        "macd_histogram": macd_hist,
        "bb_pct_b": bb_pct_b,
        "ma50": ma50,
        "ma200": ma200,
        "volume_ratio": vol_ratio,
    }

    signal = _compute_signal_score(indicators)

    # Company info
    market_cap = info.get("marketCap")
    sector     = info.get("sector", "N/A")
    beta       = info.get("beta")
    pe_ratio   = info.get("trailingPE")
    short_float= info.get("shortPercentOfFloat")

    return {
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "price": {
            "current": current_price,
            "prev_close": prev_close,
            "day_change_pct": day_change,
            "52w_high": high_52w,
            "52w_low": low_52w,
            "vwap": vwap,
        },
        "key_levels": {
            "recent_20d_high": recent_high,
            "recent_20d_low": recent_low,
        },
        "moving_averages": {
            "ma20": ma20,
            "ma50": ma50,
            "ma200": ma200,
        },
        "indicators": {
            "rsi_14": rsi,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_histogram": macd_hist,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_pct_b": bb_pct_b,
            "atr_14": atr,
            "volume_ratio_20d": vol_ratio,
        },
        "signal": signal,
        "fundamentals": {
            "market_cap": market_cap,
            "sector": sector,
            "beta": beta,
            "pe_trailing": pe_ratio,
            "short_float": short_float,
        },
        "data_quality": {
            "rows_available": len(close),
            "ma200_available": ma200 is not None,
            "period": period,
        },
    }
