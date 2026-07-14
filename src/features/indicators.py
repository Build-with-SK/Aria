"""
indicators.py
=============
Feature engineering layer.

Computes technical indicators, return features, volatility metrics,
regime flags, and cross-asset correlation features for each asset.

All functions are pure: they take a DataFrame and return a DataFrame.
No side effects, no global state. Easy to test and extend.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ===========================================================================
# Price and Return Features
# ===========================================================================

def add_return_features(df: pd.DataFrame, windows: Dict) -> pd.DataFrame:
    """
    Compute return-based features.

    - Daily return (simple and log)
    - Rolling returns over short / medium / long windows
    - Rolling realised volatility
    - Maximum drawdown from rolling high
    """
    df = df.copy()
    close = df["Close"]

    # Simple and log daily returns
    df["return_1d"]    = close.pct_change()
    df["log_return_1d"] = np.log(close / close.shift(1))

    # Rolling returns
    for label, w in windows.items():
        df[f"return_{w}d"] = close.pct_change(periods=w)

    # Realised volatility (annualised)
    vol_window = windows.get("vol", 20)
    df["realised_vol"] = (
        df["log_return_1d"]
        .rolling(vol_window)
        .std() * np.sqrt(252)
    )

    # Drawdown: distance from rolling 252-day high
    rolling_high = close.rolling(252, min_periods=1).max()
    df["drawdown"] = (close - rolling_high) / rolling_high

    return df


def add_moving_averages(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    """
    Simple and exponential moving averages, and price distance from each.
    """
    df = df.copy()
    close = df["Close"]

    sma_short  = cfg.get("sma_short",  20)
    sma_medium = cfg.get("sma_medium", 50)
    sma_long   = cfg.get("sma_long",  200)
    ema_short  = cfg.get("ema_short",  12)
    ema_long   = cfg.get("ema_long",   26)

    df[f"sma_{sma_short}"]  = close.rolling(sma_short).mean()
    df[f"sma_{sma_medium}"] = close.rolling(sma_medium).mean()
    df[f"sma_{sma_long}"]   = close.rolling(sma_long).mean()

    df[f"ema_{ema_short}"] = close.ewm(span=ema_short, adjust=False).mean()
    df[f"ema_{ema_long}"]  = close.ewm(span=ema_long,  adjust=False).mean()

    # Price relative to each MA (positive = price above MA)
    df[f"dist_sma_{sma_short}"]  = (close - df[f"sma_{sma_short}"])  / df[f"sma_{sma_short}"]
    df[f"dist_sma_{sma_medium}"] = (close - df[f"sma_{sma_medium}"]) / df[f"sma_{sma_medium}"]
    df[f"dist_sma_{sma_long}"]   = (close - df[f"sma_{sma_long}"])   / df[f"sma_{sma_long}"]

    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Relative Strength Index.
    RSI > 70 → potentially overbought
    RSI < 30 → potentially oversold
    """
    df = df.copy()
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    MACD line, signal line, and histogram.
    MACD cross above signal → bullish momentum
    MACD cross below signal → bearish momentum
    """
    df = df.copy()
    close = df["Close"]

    ema_fast   = close.ewm(span=fast,   adjust=False).mean()
    ema_slow   = close.ewm(span=slow,   adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"]    = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_histogram"] = df["macd"] - df["macd_signal"]
    return df


def add_bollinger_bands(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """
    Bollinger Bands.
    Price touching upper band → potentially overbought
    Price touching lower band → potentially oversold
    %B shows position within the bands.
    """
    df = df.copy()
    close = df["Close"]

    mid   = close.rolling(period).mean()
    std   = close.rolling(period).std()

    df["bb_upper"]  = mid + (num_std * std)
    df["bb_middle"] = mid
    df["bb_lower"]  = mid - (num_std * std)
    df["bb_width"]  = (df["bb_upper"] - df["bb_lower"]) / mid     # Band width
    df["bb_pct_b"]  = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])  # %B
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Average True Range — measures market volatility.
    Higher ATR → bigger price swings → reduce position size.
    """
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]

    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    df["atr"] = true_range.ewm(com=period - 1, min_periods=period).mean()
    df["atr_pct"] = df["atr"] / close   # ATR as % of price — normalises across assets
    return df


def add_momentum(df: pd.DataFrame, period: int = 10) -> pd.DataFrame:
    """
    Price momentum and Rate of Change.
    Positive momentum with rising volume is a strong bullish signal.
    """
    df = df.copy()
    close = df["Close"]

    df["momentum"]  = close - close.shift(period)
    df["roc"]       = close.pct_change(periods=period) * 100   # Rate of change in %

    # Volume trend (if volume data is present and non-zero)
    if "Volume" in df.columns and df["Volume"].sum() > 0:
        df["volume_sma_20"] = df["Volume"].rolling(20).mean()
        df["volume_trend"]  = df["Volume"] / df["volume_sma_20"]  # > 1 = above-average volume
    else:
        df["volume_trend"] = np.nan

    return df


def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Volume Weighted Average Price — cumulative within each trading year.
    Useful as a fair-value anchor. Price above VWAP = bullish bias.
    Note: VWAP is most meaningful on intraday data. Here we use it
    as a rolling 20-day proxy on daily data.
    """
    df = df.copy()

    if "Volume" in df.columns and df["Volume"].sum() > 0:
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
        window = 20
        df["vwap_20d"] = (
            (typical_price * df["Volume"]).rolling(window).sum()
            / df["Volume"].rolling(window).sum()
        )
        df["dist_vwap"] = (df["Close"] - df["vwap_20d"]) / df["vwap_20d"]
    else:
        df["vwap_20d"]  = np.nan
        df["dist_vwap"] = np.nan

    return df


# ===========================================================================
# Rolling Beta vs Benchmark
# ===========================================================================

def add_rolling_beta(
    df: pd.DataFrame,
    benchmark_returns: pd.Series,
    window: int = 60,
) -> pd.DataFrame:
    """
    Rolling beta measures how sensitive the asset is to the benchmark (S&P 500).
    Beta > 1 → asset amplifies market moves (higher risk/reward)
    Beta < 1 → asset is less sensitive (more defensive)
    Beta < 0 → asset tends to move opposite to market (hedge)
    """
    df = df.copy()

    if "return_1d" not in df.columns:
        df["return_1d"] = df["Close"].pct_change()

    # Align on same dates
    aligned = df["return_1d"].align(benchmark_returns, join="inner")
    asset_r, bench_r = aligned

    rolling_cov  = asset_r.rolling(window).cov(bench_r)
    rolling_var  = bench_r.rolling(window).var()
    rolling_beta = rolling_cov / rolling_var.replace(0, np.nan)

    df["rolling_beta"] = rolling_beta.reindex(df.index)
    return df


# ===========================================================================
# Regime Detection
# ===========================================================================

def add_regime_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Label the market regime based on trend and volatility.

    Regimes:
    - bull_regime: price above SMA200, SMA50 above SMA200
    - bear_regime: price below SMA200, SMA50 below SMA200
    - sideways: neither clearly bullish nor bearish
    - high_vol_regime: realised vol above 80th percentile
    - low_vol_regime: realised vol below 20th percentile
    - risk_on: bull regime + low vol
    - risk_off: bear regime + high vol
    """
    df = df.copy()
    close = df["Close"]

    # Require the moving average columns to exist
    sma50  = df.get("sma_50",  close.rolling(50).mean())
    sma200 = df.get("sma_200", close.rolling(200).mean())
    vol    = df.get("realised_vol", df["Close"].pct_change().rolling(20).std() * np.sqrt(252))

    df["bull_regime"]    = ((close > sma200) & (sma50 > sma200)).astype(int)
    df["bear_regime"]    = ((close < sma200) & (sma50 < sma200)).astype(int)
    df["sideways"]       = (1 - df["bull_regime"] - df["bear_regime"]).clip(0, 1)

    vol_high = vol.rolling(252, min_periods=60).quantile(0.80)
    vol_low  = vol.rolling(252, min_periods=60).quantile(0.20)

    df["high_vol_regime"] = (vol > vol_high).astype(int)
    df["low_vol_regime"]  = (vol < vol_low).astype(int)

    df["risk_on"]  = (df["bull_regime"] & df["low_vol_regime"]).astype(int)
    df["risk_off"] = (df["bear_regime"] & df["high_vol_regime"]).astype(int)

    return df


# ===========================================================================
# Cross-Asset Correlation Features
# ===========================================================================

def add_cross_asset_features(
    df: pd.DataFrame,
    reference_returns: Dict[str, pd.Series],
    window: int = 60,
) -> pd.DataFrame:
    """
    Add rolling correlations with key reference assets.

    reference_returns: dict like {
        "sp500":  pd.Series of S&P 500 daily returns,
        "gold":   pd.Series of GC=F daily returns,
        "oil":    pd.Series of CL=F daily returns,
        "dxy":    pd.Series of DXY proxy returns,
    }
    """
    df = df.copy()

    if "return_1d" not in df.columns:
        df["return_1d"] = df["Close"].pct_change()

    for label, ref_series in reference_returns.items():
        try:
            aligned_asset, aligned_ref = df["return_1d"].align(ref_series, join="inner")
            rolling_corr = aligned_asset.rolling(window).corr(aligned_ref)
            df[f"corr_{label}_{window}d"] = rolling_corr.reindex(df.index)
        except Exception as e:
            logger.warning(f"Cross-asset correlation failed for {label}: {e}")
            df[f"corr_{label}_{window}d"] = np.nan

    return df


# ===========================================================================
# Master Feature Builder
# ===========================================================================

def build_features(
    df: pd.DataFrame,
    cfg: Dict,
    benchmark_returns: Optional[pd.Series] = None,
    reference_returns: Optional[Dict[str, pd.Series]] = None,
) -> pd.DataFrame:
    """
    Apply all feature engineering steps to a single asset DataFrame.

    Parameters
    ----------
    df                : Raw OHLCV DataFrame for one asset
    cfg               : indicators section of universe.yaml
    benchmark_returns : S&P 500 daily returns (for beta calculation)
    reference_returns : Dict of cross-asset return series

    Returns
    -------
    DataFrame with all features appended.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # Ensure consistent column names
    df = df.copy()
    df.columns = [c.capitalize() if isinstance(c, str) else c for c in df.columns]

    required = {"Open", "High", "Low", "Close"}
    missing = required - set(df.columns)
    if missing:
        logger.error(f"Missing required columns: {missing}")
        return df

    windows = {
        "short":  cfg.get("windows", {}).get("short",  5),
        "medium": cfg.get("windows", {}).get("medium", 20),
        "long":   cfg.get("windows", {}).get("long",   60),
        "vol":    cfg.get("windows", {}).get("vol",    20),
    }

    try:
        df = add_return_features(df, windows)
        df = add_moving_averages(df, cfg.get("indicators", cfg))
        df = add_rsi(df, period=cfg.get("indicators", cfg).get("rsi_period", 14))
        df = add_macd(
            df,
            fast=cfg.get("indicators", cfg).get("macd_fast", 12),
            slow=cfg.get("indicators", cfg).get("macd_slow", 26),
            signal=cfg.get("indicators", cfg).get("macd_signal", 9),
        )
        df = add_bollinger_bands(
            df,
            period=cfg.get("indicators", cfg).get("bb_period", 20),
            num_std=cfg.get("indicators", cfg).get("bb_std", 2),
        )
        df = add_atr(df, period=cfg.get("indicators", cfg).get("atr_period", 14))
        df = add_momentum(
            df,
            period=cfg.get("indicators", cfg).get("momentum_period", 10),
        )
        df = add_vwap(df)
        df = add_regime_flags(df)

        if benchmark_returns is not None:
            df = add_rolling_beta(
                df,
                benchmark_returns,
                window=cfg.get("windows", {}).get("beta", 60),
            )

        if reference_returns is not None:
            df = add_cross_asset_features(df, reference_returns, window=60)

    except Exception as e:
        logger.error(f"Feature engineering failed: {e}", exc_info=True)

    return df


def build_all_features(
    all_data: Dict[str, pd.DataFrame],
    cfg: Dict,
    benchmark_ticker: str = "^GSPC",
) -> Dict[str, pd.DataFrame]:
    """
    Apply feature engineering to every asset in the universe.

    Parameters
    ----------
    all_data         : Dict of raw OHLCV DataFrames keyed by ticker
    cfg              : Full config dict from universe.yaml
    benchmark_ticker : Ticker used as market benchmark

    Returns
    -------
    Dict of feature-enriched DataFrames keyed by ticker
    """
    # Build benchmark return series first
    benchmark_returns = None
    reference_returns = {}

    if benchmark_ticker in all_data:
        bm_close = all_data[benchmark_ticker]["Close"]
        benchmark_returns = bm_close.pct_change().rename("benchmark")

    # Build reference return series for cross-asset correlations
    ref_map = {
        "sp500": "^GSPC",
        "gold":  "GC=F",
        "oil":   "CL=F",
    }
    for label, ticker in ref_map.items():
        if ticker in all_data:
            reference_returns[label] = all_data[ticker]["Close"].pct_change()

    featured_data: Dict[str, pd.DataFrame] = {}
    for ticker, df in all_data.items():
        logger.info(f"Building features for {ticker}")
        enriched = build_features(df, cfg, benchmark_returns, reference_returns)
        featured_data[ticker] = enriched

    return featured_data
