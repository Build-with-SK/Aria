# src/strategies/quant_systematic.py
"""
Quantitative / Systematic Strategy
Multi-factor model: momentum, value, quality, low-vol, size.
Cross-sectional z-scoring + TSMOM (time-series momentum).
Weekly rebalancing signal.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AssetFactorScore:
    ticker: str
    momentum_score: float       # 12-1 month momentum
    value_score: float          # earnings yield + book yield
    quality_score: float        # ROE + margin
    low_vol_score: float        # lower vol = higher score
    size_score: float           # log market cap
    combined_score: float       # weighted combination
    tsmom_signal: int           # +1 long, -1 short, 0 flat
    recommended_action: str
    conviction: float


@dataclass
class FactorPortfolio:
    asset_scores: List[AssetFactorScore]
    factor_weights: Dict[str, float]
    long_names: List[str]
    short_names: List[str]
    tsmom_longs: List[str]
    tsmom_shorts: List[str]
    factor_returns: Dict[str, float]    # estimated factor premium
    strategy_score: float
    rebalance_due: bool
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "asset_scores": [vars(a) for a in self.asset_scores],
            "factor_weights": self.factor_weights,
            "long_names": self.long_names,
            "short_names": self.short_names,
            "tsmom_longs": self.tsmom_longs,
            "tsmom_shorts": self.tsmom_shorts,
            "factor_returns": {k: round(v, 4) for k, v in self.factor_returns.items()},
            "strategy_score": round(self.strategy_score, 2),
            "rebalance_due": self.rebalance_due,
            "notes": self.notes,
        }


def _momentum_factor(df: pd.DataFrame) -> float:
    """12-1 month cross-sectional momentum. Skip last month."""
    try:
        if len(df) < 252:
            return 0.0
        ret_12m = df["Close"].iloc[-252:].iloc[0]
        ret_1m_ago = df["Close"].iloc[-21]
        ret_now = df["Close"].iloc[-1]
        if ret_12m == 0:
            return 0.0
        mom_12_1 = (ret_1m_ago / ret_12m) - 1.0   # 12-1 (exclude last month)
        return float(mom_12_1)
    except Exception:
        return 0.0


def _value_factor(info: dict) -> float:
    """Earnings yield + book yield."""
    try:
        pe = info.get("trailingPE") or 0
        pb = info.get("priceToBook") or 0
        ey = (1.0 / pe) if pe and pe > 0 else 0.0
        by = (1.0 / pb) if pb and pb > 0 else 0.0
        return float((ey + by) / 2.0)
    except Exception:
        return 0.0


def _quality_factor(info: dict) -> float:
    """ROE + profit margin. Penalise high accruals (proxy: low FCF/earnings ratio)."""
    try:
        roe = float(info.get("returnOnEquity") or 0)
        margin = float(info.get("profitMargins") or 0)
        # Accruals penalty (low FCF/NI = more accruals)
        ni = float(info.get("netIncomeToCommon") or 1)
        fcf = float(info.get("freeCashflow") or 0)
        accrual_ratio = (fcf / abs(ni)) if ni != 0 else 0.5
        accrual_bonus = float(np.clip(accrual_ratio, -1, 1)) * 0.5
        return float(roe + margin + accrual_bonus)
    except Exception:
        return 0.0


def _low_vol_factor(df: pd.DataFrame, window: int = 252) -> float:
    """Realised vol rank score. Lower vol = higher factor score."""
    try:
        ret = df["Close"].pct_change().dropna()
        vol = float(ret.tail(window).std() * np.sqrt(252))
        return float(-vol)  # negate so lower vol = higher score
    except Exception:
        return 0.0


def _size_factor(info: dict, df: pd.DataFrame) -> float:
    """Log market cap. Larger cap = higher size score for size-neutral exposure."""
    try:
        mc = info.get("marketCap") or 0
        if mc <= 0:
            price = float(df["Close"].iloc[-1])
            shares = float(info.get("sharesOutstanding") or 1e9)
            mc = price * shares
        return float(np.log1p(mc))
    except Exception:
        return 18.0  # ~$60M default


def _zscore_series(values: List[float]) -> List[float]:
    """Cross-sectional z-score."""
    arr = np.array(values, dtype=float)
    std = arr.std()
    if std == 0:
        return [0.0] * len(arr)
    return list((arr - arr.mean()) / std)


def _tsmom_signal(df: pd.DataFrame) -> int:
    """Time-series momentum: long if 12-month return > 0, else short."""
    try:
        if len(df) < 252:
            return 0
        ret_12m = float(df["Close"].iloc[-1] / df["Close"].iloc[-252] - 1)
        return 1 if ret_12m > 0 else -1
    except Exception:
        return 0


def run_quant_systematic(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    yf_info: Optional[Dict[str, dict]] = None,
) -> FactorPortfolio:
    """
    Run multi-factor model across universe.
    Cross-sectional z-score each factor, combine, rank, produce signals.
    """
    cfg = config.get("strategies", {}).get("quant_systematic", {})
    factor_weights = {
        "momentum": float(cfg.get("w_momentum", 0.30)),
        "value": float(cfg.get("w_value", 0.20)),
        "quality": float(cfg.get("w_quality", 0.20)),
        "low_vol": float(cfg.get("w_low_vol", 0.15)),
        "size": float(cfg.get("w_size", 0.15)),
    }
    n_longs = int(cfg.get("n_longs", 10))
    n_shorts = int(cfg.get("n_shorts", 10))
    notes: List[str] = []

    # Build factor table
    tickers_raw = []
    mom_raw, val_raw, qual_raw, lvol_raw, size_raw, tsmom_raw = [], [], [], [], [], []

    skip_suffixes = ["=X", "-USD", "=F"]
    skip_etfs = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO",
                 "TLT", "IEF", "AGG", "HYG", "LQD", "JNK", "VCIT",
                 "BKLN", "SJNK", "SHYG", "CWB"}

    # For TSMOM include ALL asset classes
    all_tickers = []

    for ticker, df in featured_data.items():
        if df is None or df.empty or len(df) < 60:
            continue
        info = (yf_info or {}).get(ticker, {})

        # Factor model: equities only
        if not any(ticker.endswith(s) for s in skip_suffixes) and ticker not in skip_etfs:
            tickers_raw.append(ticker)
            mom_raw.append(_momentum_factor(df))
            val_raw.append(_value_factor(info))
            qual_raw.append(_quality_factor(info))
            lvol_raw.append(_low_vol_factor(df))
            size_raw.append(_size_factor(info, df))

        # TSMOM: all asset classes
        all_tickers.append((ticker, df))

    # Cross-sectional z-scores
    if len(tickers_raw) < 2:
        notes.append("Insufficient equities for factor model.")
        return FactorPortfolio(
            asset_scores=[], factor_weights=factor_weights, long_names=[], short_names=[],
            tsmom_longs=[], tsmom_shorts=[], factor_returns={},
            strategy_score=0.0, rebalance_due=False, notes=notes,
        )

    mom_z = _zscore_series(mom_raw)
    val_z = _zscore_series(val_raw)
    qual_z = _zscore_series(qual_raw)
    lvol_z = _zscore_series(lvol_raw)
    size_z = _zscore_series(size_raw)

    asset_scores: List[AssetFactorScore] = []
    for i, ticker in enumerate(tickers_raw):
        df = featured_data[ticker]
        combined = (
            factor_weights["momentum"] * mom_z[i]
            + factor_weights["value"] * val_z[i]
            + factor_weights["quality"] * qual_z[i]
            + factor_weights["low_vol"] * lvol_z[i]
            + factor_weights["size"] * size_z[i]
        )
        ts = _tsmom_signal(df)
        if combined > 0.5:
            action = "long"
            conv = min(80, abs(combined) * 25)
        elif combined < -0.5:
            action = "short"
            conv = min(80, abs(combined) * 25)
        else:
            action = "neutral"
            conv = 20

        asset_scores.append(AssetFactorScore(
            ticker=ticker,
            momentum_score=round(mom_z[i], 4),
            value_score=round(val_z[i], 4),
            quality_score=round(qual_z[i], 4),
            low_vol_score=round(lvol_z[i], 4),
            size_score=round(size_z[i], 4),
            combined_score=round(float(combined), 4),
            tsmom_signal=ts,
            recommended_action=action,
            conviction=round(float(conv), 1),
        ))

    asset_scores.sort(key=lambda a: a.combined_score, reverse=True)
    long_names = [a.ticker for a in asset_scores[:n_longs]]
    short_names = [a.ticker for a in asset_scores[-n_shorts:]]

    # TSMOM across all asset classes
    tsmom_longs = [t for t, df in all_tickers if _tsmom_signal(df) == 1]
    tsmom_shorts = [t for t, df in all_tickers if _tsmom_signal(df) == -1]

    # Factor premium estimates (long-short spread of each factor)
    factor_returns = {
        "momentum_premium": round(float(np.mean([mom_raw[i] for i in range(len(tickers_raw))[:n_longs]])) -
                                   float(np.mean([mom_raw[i] for i in range(len(tickers_raw))[-n_shorts:]])), 4),
        "value_premium": round(float(np.mean([val_raw[i] for i in range(len(tickers_raw))[:n_longs]])) -
                                float(np.mean([val_raw[i] for i in range(len(tickers_raw))[-n_shorts:]])), 4),
        "quality_premium": round(float(np.mean([qual_raw[i] for i in range(len(tickers_raw))[:n_longs]])) -
                                  float(np.mean([qual_raw[i] for i in range(len(tickers_raw))[-n_shorts:]])), 4),
    }

    top_combined = asset_scores[0].combined_score if asset_scores else 0
    bot_combined = asset_scores[-1].combined_score if asset_scores else 0
    strategy_score = float(np.clip((top_combined - bot_combined) * 20, -100, 100))

    # Weekly rebalance check (simplified: always due if run on Monday)
    import datetime
    rebalance_due = datetime.datetime.now().weekday() == 0  # Monday

    notes.append(f"Factor model: {len(tickers_raw)} equities screened.")
    notes.append(f"Longs: {long_names[:5]} | Shorts: {short_names[:5]}")
    notes.append(f"TSMOM: {len(tsmom_longs)} long, {len(tsmom_shorts)} short across all assets.")
    notes.append(f"Factor weights: {factor_weights}")

    return FactorPortfolio(
        asset_scores=asset_scores,
        factor_weights=factor_weights,
        long_names=long_names,
        short_names=short_names,
        tsmom_longs=tsmom_longs,
        tsmom_shorts=tsmom_shorts,
        factor_returns=factor_returns,
        strategy_score=round(strategy_score, 2),
        rebalance_due=rebalance_due,
        notes=notes,
    )
