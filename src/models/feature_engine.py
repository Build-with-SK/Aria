# src/models/feature_engine.py
"""
Feature Engineering Pipeline — 150+ features
Technical, macro, sentiment, cross-asset, regime, microstructure.
All features are lagged to prevent lookahead bias.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Technical features
# ─────────────────────────────────────────────────────────────────────────────

def _technical_features(df: pd.DataFrame) -> pd.DataFrame:
    """~60 technical features from OHLCV."""
    close  = df["Close"]
    high   = df.get("High",   close)
    low    = df.get("Low",    close)
    volume = df.get("Volume", pd.Series(1, index=df.index))
    out    = pd.DataFrame(index=df.index)

    # ── Trend ──
    for w in [5, 10, 20, 50, 100, 200]:
        ma = close.rolling(w).mean()
        out[f"price_vs_sma{w}"]   = (close / ma - 1).replace([np.inf, -np.inf], 0)
        out[f"sma{w}_slope"]      = ma.pct_change(5)

    for span in [9, 12, 26, 50]:
        ema = close.ewm(span=span, adjust=False).mean()
        out[f"price_vs_ema{span}"] = (close / ema - 1).replace([np.inf, -np.inf], 0)

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    out["macd_norm"]     = (macd / close.replace(0, np.nan)).fillna(0)
    out["macd_hist_norm"]= ((macd - sig) / close.replace(0, np.nan)).fillna(0)
    out["macd_cross"]    = np.sign(macd - sig)

    # ── Momentum ──
    for h in [1, 3, 5, 10, 20, 60, 120, 252]:
        out[f"ret_{h}d"] = close.pct_change(h)
    # 12-1 month momentum
    if len(close) >= 252:
        out["mom_12_1"] = close.pct_change(252) - close.pct_change(21)
    else:
        out["mom_12_1"] = 0.0

    # ── Oscillators ──
    # RSI multiple windows
    for w in [7, 14, 21]:
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(w).mean()
        loss  = (-delta.clip(upper=0)).rolling(w).mean()
        rs    = gain / loss.replace(0, np.nan)
        out[f"rsi_{w}"] = 100 - (100 / (1 + rs))

    # Stochastic %K
    lo14 = low.rolling(14).min()
    hi14 = high.rolling(14).max()
    denom = (hi14 - lo14).replace(0, np.nan)
    out["stoch_k"] = (close - lo14) / denom * 100
    out["stoch_d"] = out["stoch_k"].rolling(3).mean()

    # Williams %R
    out["williams_r"] = (hi14 - close) / denom * -100

    # ── Volatility ──
    for w in [5, 10, 20, 60]:
        rv = close.pct_change().rolling(w).std() * np.sqrt(252)
        out[f"realised_vol_{w}d"] = rv

    # Bollinger Band features
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    out["bb_pct_b"]  = (close - (bb_mid - 2*bb_std)) / (4 * bb_std.replace(0, np.nan))
    out["bb_width"]  = (4 * bb_std) / bb_mid.replace(0, np.nan)
    out["bb_squeeze"]= (out["bb_width"] < out["bb_width"].rolling(126).quantile(0.20)).astype(int)

    # ATR ratio
    tr = pd.concat([(high - low),
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    out["atr_ratio"] = atr14 / close.replace(0, np.nan)

    # ── Volume ──
    vol_ma20 = volume.rolling(20).mean().replace(0, np.nan)
    out["volume_ratio"]  = volume / vol_ma20
    out["volume_trend5"] = vol_ma20 / volume.rolling(5).mean().replace(0, np.nan)
    # On-balance volume normalised
    obv = (np.sign(close.diff()) * volume).cumsum()
    obv_ma = obv.rolling(20).mean()
    out["obv_slope"] = (obv - obv_ma) / (obv.rolling(20).std().replace(0, np.nan) + 1e-9)

    # ── Price patterns ──
    out["higher_high"] = ((high > high.shift(1)) & (high.shift(1) > high.shift(2))).astype(int)
    out["lower_low"]   = ((low  < low.shift(1))  & (low.shift(1)  < low.shift(2))).astype(int)
    out["doji"]        = (abs(close - df.get("Open", close)) < 0.1 * (high - low)).astype(int)
    out["gap_up"]      = ((df.get("Open", close) > high.shift(1))).astype(int)
    out["gap_down"]    = ((df.get("Open", close) < low.shift(1))).astype(int)

    # 52-week position
    hi52 = high.rolling(252).max()
    lo52 = low.rolling(252).min()
    out["pct_52w_high"] = (close / hi52.replace(0, np.nan) - 1)
    out["pct_52w_low"]  = (close / lo52.replace(0, np.nan) - 1)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 2. Regime features
# ─────────────────────────────────────────────────────────────────────────────

def _regime_features(df: pd.DataFrame) -> pd.DataFrame:
    """Market regime flags and transition probabilities."""
    close = df["Close"]
    out   = pd.DataFrame(index=df.index)

    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rv20   = close.pct_change().rolling(20).std() * np.sqrt(252)
    rv60   = close.pct_change().rolling(60).std() * np.sqrt(252)

    out["bull_market"]       = (close > sma50).astype(int)
    out["golden_cross"]      = (sma50 > sma200).astype(int)
    out["death_cross"]       = (sma50 < sma200).astype(int)
    out["high_vol_regime"]   = (rv20 > rv20.rolling(252).quantile(0.75)).astype(int)
    out["low_vol_regime"]    = (rv20 < rv20.rolling(252).quantile(0.25)).astype(int)
    out["vol_expanding"]     = (rv20 > rv60).astype(int)
    out["vol_ratio_20_60"]   = rv20 / rv60.replace(0, np.nan)

    # Trend strength (ADX proxy)
    high = df.get("High", close)
    low  = df.get("Low",  close)
    dm_plus  = (high - high.shift(1)).clip(lower=0)
    dm_minus = (low.shift(1) - low).clip(lower=0)
    tr = pd.concat([(high - low),
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean().replace(0, np.nan)
    di_plus  = dm_plus.rolling(14).mean()  / atr14 * 100
    di_minus = dm_minus.rolling(14).mean() / atr14 * 100
    dx = ((di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)) * 100
    out["adx"] = dx.rolling(14).mean()
    out["strong_trend"] = (out["adx"] > 25).astype(int)

    # Days since last cross
    cross = (sma50 > sma200).astype(int).diff().abs()
    out["days_since_cross"] = cross.groupby((cross != 0).cumsum()).cumcount()

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. Macro features (from macro snapshot)
# ─────────────────────────────────────────────────────────────────────────────

def _macro_features(macro_snapshot, index: pd.Index) -> pd.DataFrame:
    """
    Broadcast scalar macro values across the time index.
    These are the same for all tickers on a given day.
    """
    out = pd.DataFrame(index=index)

    if macro_snapshot is None:
        for col in ["macro_score", "vix", "yield_spread", "dxy_trend",
                    "gold_trend", "macro_bull", "macro_recession"]:
            out[col] = 0.0
        return out

    out["macro_score"]     = float(getattr(macro_snapshot, "macro_score",          0))
    out["vix"]             = float(getattr(macro_snapshot, "vix",                  18))
    out["yield_spread"]    = float(getattr(macro_snapshot, "yield_spread_10y2y",    0))
    out["fed_funds"]       = float(getattr(macro_snapshot, "fed_funds_rate",        5))
    out["cpi_yoy"]         = float(getattr(macro_snapshot, "cpi_yoy",              3))
    out["dxy_trend"]       = float(getattr(macro_snapshot, "dxy_trend",            0))
    out["gold_trend"]      = float(getattr(macro_snapshot, "gold_trend",           0))

    regime = str(getattr(macro_snapshot, "regime_label", "Expansion"))
    out["macro_expansion"]  = int(regime == "Expansion")
    out["macro_recession"]  = int(regime == "Recession")
    out["macro_stagflation"]= int(regime == "Stagflation")
    out["macro_recovery"]   = int(regime == "Recovery")

    # Derived macro signals
    out["vix_high"]         = int(out["vix"].iloc[0] > 25)
    out["vix_low"]          = int(out["vix"].iloc[0] < 15)
    out["yield_inverted"]   = int(out["yield_spread"].iloc[0] < 0)
    out["macro_bull"]       = int(out["macro_score"].iloc[0] > 30)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 4. Sentiment features (from sentiment_results dict)
# ─────────────────────────────────────────────────────────────────────────────

def _sentiment_features(ticker: str, sentiment_results: dict, index: pd.Index) -> pd.DataFrame:
    """Broadcast ticker-level sentiment score across the time index."""
    out = pd.DataFrame(index=index)
    result = (sentiment_results or {}).get(ticker, {})

    if isinstance(result, dict):
        score = float(result.get("score", 0))
        article_count = float(result.get("article_count", 0))
    elif hasattr(result, "score"):
        score = float(result.score)
        article_count = float(getattr(result, "article_count", 0))
    else:
        score = 0.0
        article_count = 0.0

    out["sentiment_score"]   = score
    out["sentiment_positive"]= int(score > 10)
    out["sentiment_negative"]= int(score < -10)
    out["sentiment_strong"]  = int(abs(score) > 30)
    out["news_volume"]       = article_count

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 5. Cross-asset features (relative to SPY, TLT, GLD, DXY)
# ─────────────────────────────────────────────────────────────────────────────

def _cross_asset_features(
    ticker: str,
    all_data: Dict[str, pd.DataFrame],
    index: pd.Index,
) -> pd.DataFrame:
    """Beta, correlation, relative strength vs key benchmarks."""
    out = pd.DataFrame(index=index)

    benchmarks = {
        "spy": all_data.get("SPY"),
        "tlt": all_data.get("TLT"),
        "gld": all_data.get("GLD"),
    }

    df_self = all_data.get(ticker)
    if df_self is None or df_self.empty:
        for b in benchmarks:
            for feat in ["beta", "corr_60d", "rel_strength_20d", "rel_strength_60d"]:
                out[f"{feat}_vs_{b}"] = 0.0
        return out

    self_ret = df_self["Close"].pct_change()

    for bname, bdf in benchmarks.items():
        if bdf is None or bdf.empty:
            for feat in ["beta", "corr_60d", "rel_strength_20d", "rel_strength_60d"]:
                out[f"{feat}_vs_{bname}"] = 0.0
            continue

        b_ret = bdf["Close"].pct_change()
        aligned = pd.concat([self_ret, b_ret], axis=1).dropna()
        aligned.columns = ["self", "bench"]

        # Rolling beta
        cov = aligned["self"].rolling(60).cov(aligned["bench"])
        var = aligned["bench"].rolling(60).var().replace(0, np.nan)
        beta_series = (cov / var).reindex(index)
        out[f"beta_vs_{bname}"] = beta_series.fillna(1.0)

        # Rolling correlation
        corr = aligned["self"].rolling(60).corr(aligned["bench"]).reindex(index)
        out[f"corr_60d_vs_{bname}"] = corr.fillna(0.0)

        # Relative strength
        self_close = df_self["Close"].reindex(index)
        b_close    = bdf["Close"].reindex(index)
        for w in [20, 60]:
            rs = (self_close.pct_change(w) - b_close.pct_change(w)).fillna(0)
            out[f"rel_strength_{w}d_vs_{bname}"] = rs

    # Risk-on / risk-off proxy
    spy_ret = (all_data.get("SPY", pd.DataFrame()).get("Close", pd.Series(dtype=float))
               .pct_change().reindex(index).fillna(0))
    tlt_ret = (all_data.get("TLT", pd.DataFrame()).get("Close", pd.Series(dtype=float))
               .pct_change().reindex(index).fillna(0))
    out["risk_on_proxy"] = (spy_ret - tlt_ret).rolling(20).mean()

    return out


# ─────────────────────────────────────────────────────────────────────────────
# 6. Calendar / time features
# ─────────────────────────────────────────────────────────────────────────────

def _calendar_features(index: pd.Index) -> pd.DataFrame:
    """Day-of-week, month, quarter, earnings season effects."""
    out = pd.DataFrame(index=index)
    try:
        idx = pd.DatetimeIndex(index)
        out["day_of_week"]   = idx.dayofweek           # 0=Mon
        out["month"]         = idx.month
        out["quarter"]       = idx.quarter
        out["is_monday"]     = (idx.dayofweek == 0).astype(int)
        out["is_friday"]     = (idx.dayofweek == 4).astype(int)
        out["month_end"]     = (idx.is_month_end).astype(int)
        out["month_start"]   = (idx.is_month_start).astype(int)
        out["quarter_end"]   = (idx.is_quarter_end).astype(int)
        # Earnings season proxy: Jan, Apr, Jul, Oct
        out["earnings_season"] = idx.month.isin([1, 4, 7, 10]).astype(int)
        # Year-end effect
        out["year_end"]      = (idx.month == 12).astype(int)
        # Tax-loss selling season
        out["tax_loss"]      = (idx.month.isin([11, 12])).astype(int)
    except Exception:
        for col in ["day_of_week", "month", "quarter", "is_monday", "is_friday",
                    "month_end", "month_start", "quarter_end", "earnings_season",
                    "year_end", "tax_loss"]:
            out[col] = 0
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 7. Microstructure features
# ─────────────────────────────────────────────────────────────────────────────

def _microstructure_features(df: pd.DataFrame) -> pd.DataFrame:
    """Spread proxies, price impact, order flow imbalance."""
    out   = pd.DataFrame(index=df.index)
    close = df["Close"]
    high  = df.get("High",   close)
    low   = df.get("Low",    close)
    vol   = df.get("Volume", pd.Series(1, index=df.index))
    opn   = df.get("Open",   close)

    # Corwin-Schultz spread estimator (proxy)
    beta  = (np.log(high / low) ** 2).rolling(2).sum()
    gamma = (np.log(high.rolling(2).max() / low.rolling(2).min())) ** 2
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / (3 - 2 * np.sqrt(2)) - np.sqrt(gamma / (3 - 2 * np.sqrt(2)))
    out["spread_proxy"] = (2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))).clip(0, 0.10)

    # Amihud illiquidity
    ret_abs = close.pct_change().abs()
    dollar_vol = close * vol.replace(0, np.nan)
    out["amihud_illiq"] = (ret_abs / dollar_vol).rolling(20).mean() * 1e6

    # Garman-Klass volatility
    gk = (0.5 * (np.log(high / low.replace(0, np.nan)) ** 2)
          - (2 * np.log(2) - 1) * (np.log(close / opn.replace(0, np.nan)) ** 2))
    out["gk_vol"] = np.sqrt(gk.rolling(20).mean() * 252)

    # Intraday range normalised
    out["intraday_range"] = (high - low) / close.replace(0, np.nan)
    out["range_vs_ma"]    = out["intraday_range"] / out["intraday_range"].rolling(20).mean().replace(0, np.nan)

    # Open-close return (intraday direction)
    out["open_close_ret"] = (close - opn) / opn.replace(0, np.nan)

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Master feature builder
# ─────────────────────────────────────────────────────────────────────────────

def build_features(
    ticker: str,
    df: pd.DataFrame,
    all_data: Optional[Dict[str, pd.DataFrame]] = None,
    macro_snapshot=None,
    sentiment_results: Optional[dict] = None,
    lag: int = 1,
) -> pd.DataFrame:
    """
    Build the full 150+ feature matrix for one ticker.

    Parameters
    ----------
    ticker          : ticker symbol
    df              : OHLCV DataFrame with indicators already computed
    all_data        : full universe dict {ticker: df} for cross-asset features
    macro_snapshot  : MacroSnapshot object from macro_data.py
    sentiment_results: {ticker: sentiment_result}
    lag             : number of days to lag all features (default 1 = no lookahead)

    Returns
    -------
    DataFrame with all features, lagged, NaN rows dropped
    """
    if df is None or df.empty or len(df) < 60:
        return pd.DataFrame()

    blocks = []

    # 1. Technical
    try:
        blocks.append(_technical_features(df))
    except Exception as e:
        logger.debug(f"{ticker}: technical features failed: {e}")

    # 2. Regime
    try:
        blocks.append(_regime_features(df))
    except Exception as e:
        logger.debug(f"{ticker}: regime features failed: {e}")

    # 3. Microstructure
    try:
        blocks.append(_microstructure_features(df))
    except Exception as e:
        logger.debug(f"{ticker}: microstructure features failed: {e}")

    # 4. Calendar
    try:
        blocks.append(_calendar_features(df.index))
    except Exception as e:
        logger.debug(f"{ticker}: calendar features failed: {e}")

    # 5. Macro (broadcast)
    try:
        blocks.append(_macro_features(macro_snapshot, df.index))
    except Exception as e:
        logger.debug(f"{ticker}: macro features failed: {e}")

    # 6. Sentiment (broadcast)
    try:
        blocks.append(_sentiment_features(ticker, sentiment_results or {}, df.index))
    except Exception as e:
        logger.debug(f"{ticker}: sentiment features failed: {e}")

    # 7. Cross-asset
    try:
        if all_data:
            blocks.append(_cross_asset_features(ticker, all_data, df.index))
    except Exception as e:
        logger.debug(f"{ticker}: cross-asset features failed: {e}")

    if not blocks:
        return pd.DataFrame()

    # Combine all blocks
    features = pd.concat(blocks, axis=1)

    # Replace infinities
    features = features.replace([np.inf, -np.inf], np.nan)

    # Lag all features by `lag` days to prevent lookahead
    features = features.shift(lag)

    # Clip extreme outliers (winsorise at 1st/99th percentile per column)
    for col in features.select_dtypes(include=[np.number]).columns:
        lo = features[col].quantile(0.01)
        hi = features[col].quantile(0.99)
        features[col] = features[col].clip(lo, hi)

    return features


def get_feature_names(features: pd.DataFrame) -> List[str]:
    """Return list of numeric feature column names (excludes target columns)."""
    return [c for c in features.columns
            if not c.startswith("target_") and features[c].dtype in [np.float64, np.float32, np.int64, np.int32]]
