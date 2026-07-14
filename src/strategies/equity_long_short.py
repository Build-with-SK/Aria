# src/strategies/equity_long_short.py
"""
Equity Long/Short Strategy
Ranks equities by composite signal score, longs top N, shorts bottom N.
Dollar-neutral with optional sector neutrality.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class LongShortPosition:
    ticker: str
    name: str
    direction: str          # "long" or "short"
    signal_score: float
    sector: str
    momentum_score: float
    quality_score: float
    value_score: float
    weight: float           # portfolio weight (0-1)
    dollar_allocation: float


@dataclass
class LongShortPortfolio:
    longs: List[LongShortPosition]
    shorts: List[LongShortPosition]
    gross_exposure: float
    net_exposure: float
    long_count: int
    short_count: int
    factor_exposures: Dict[str, float]  # momentum, value, quality
    sector_breakdown: Dict[str, float]
    estimated_sharpe: float
    strategy_score: float               # [-100, +100] combined conviction
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "longs": [vars(p) for p in self.longs],
            "shorts": [vars(p) for p in self.shorts],
            "gross_exposure": round(self.gross_exposure, 4),
            "net_exposure": round(self.net_exposure, 4),
            "long_count": self.long_count,
            "short_count": self.short_count,
            "factor_exposures": {k: round(v, 4) for k, v in self.factor_exposures.items()},
            "sector_breakdown": self.sector_breakdown,
            "estimated_sharpe": round(self.estimated_sharpe, 3),
            "strategy_score": round(self.strategy_score, 2),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Sector map — static fallback (yfinance sector data not always available)
# ---------------------------------------------------------------------------
_SECTOR_MAP: Dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "NVDA": "Technology", "META": "Technology", "NFLX": "Communication",
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "JNJ": "Healthcare", "PFE": "Healthcare", "ABBV": "Healthcare",
    "UNH": "Healthcare", "MRK": "Healthcare",
    "PG": "Consumer Staples", "KO": "Consumer Staples", "WMT": "Consumer Staples",
    "HD": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "BA": "Industrials", "CAT": "Industrials", "GE": "Industrials",
    "BRK-B": "Financials", "V": "Financials", "MA": "Financials",
    "SPY": "ETF", "QQQ": "ETF", "IWM": "ETF", "DIA": "ETF",
    "GLD": "Commodities", "SLV": "Commodities", "USO": "Commodities",
    "TLT": "Fixed Income", "IEF": "Fixed Income", "AGG": "Fixed Income",
    "HYG": "Credit", "LQD": "Credit", "JNK": "Credit",
}


def _get_sector(ticker: str) -> str:
    return _SECTOR_MAP.get(ticker, "Unknown")


def _compute_momentum_score(df: pd.DataFrame) -> float:
    """12-1 month momentum (skip last month). Returns z-normalised score."""
    try:
        if len(df) < 252:
            return 0.0
        ret_12m = (df["Close"].iloc[-252] / df["Close"].iloc[-max(252, len(df))] - 1) if len(df) >= 252 else 0.0
        ret_1m = (df["Close"].iloc[-1] / df["Close"].iloc[-21] - 1) if len(df) >= 21 else 0.0
        return float(ret_12m - ret_1m)
    except Exception:
        return 0.0


def _compute_quality_score(info: dict) -> float:
    """ROE + profit margin proxy. Higher = better quality."""
    try:
        roe = info.get("returnOnEquity") or 0.0
        margin = info.get("profitMargins") or 0.0
        return float((roe + margin) / 2.0)
    except Exception:
        return 0.0


def _compute_value_score(info: dict) -> float:
    """Earnings yield + book yield. Higher = cheaper."""
    try:
        pe = info.get("trailingPE") or 0.0
        pb = info.get("priceToBook") or 0.0
        ey = (1.0 / pe) if pe and pe > 0 else 0.0
        by = (1.0 / pb) if pb and pb > 0 else 0.0
        return float((ey + by) / 2.0)
    except Exception:
        return 0.0


def _zscore(series: pd.Series) -> pd.Series:
    std = series.std()
    if std == 0:
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def run_equity_long_short(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    signal_scores: Optional[Dict[str, float]] = None,
    yf_info: Optional[Dict[str, dict]] = None,
) -> LongShortPortfolio:
    """
    Main entry point.
    featured_data: {ticker: DataFrame with OHLCV + indicators}
    signal_scores: {ticker: composite_score} from main model
    yf_info: {ticker: yfinance .info dict} for fundamentals
    """
    cfg = config.get("strategies", {}).get("equity_long_short", {})
    n_longs = int(cfg.get("n_longs", 5))
    n_shorts = int(cfg.get("n_shorts", 5))
    sector_neutral = bool(cfg.get("sector_neutral", False))
    capital = float(config.get("backtest", {}).get("initial_capital", 100_000))

    notes = []

    # --- Build candidate table ---
    rows = []
    for ticker, df in featured_data.items():
        if df is None or df.empty:
            continue
        # Only equities (skip ETFs, futures, crypto, FX)
        suffix_skip = ["=X", "-USD", "=F"]
        if any(ticker.endswith(s) for s in suffix_skip):
            continue
        if ticker in ("SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO",
                      "TLT", "IEF", "AGG", "HYG", "LQD", "JNK", "VCIT",
                      "BKLN", "SJNK", "SHYG", "CWB", "BTC-USD", "ETH-USD"):
            continue

        score = (signal_scores or {}).get(ticker, 0.0)
        info = (yf_info or {}).get(ticker, {})
        mom = _compute_momentum_score(df)
        qual = _compute_quality_score(info)
        val = _compute_value_score(info)
        sector = _get_sector(ticker)

        rows.append({
            "ticker": ticker,
            "score": score,
            "momentum": mom,
            "quality": qual,
            "value": val,
            "sector": sector,
        })

    if len(rows) < 2:
        notes.append("Insufficient equity universe for long/short ranking.")
        return LongShortPortfolio(
            longs=[], shorts=[], gross_exposure=0, net_exposure=0,
            long_count=0, short_count=0, factor_exposures={},
            sector_breakdown={}, estimated_sharpe=0.0,
            strategy_score=0.0, notes=notes,
        )

    df_rank = pd.DataFrame(rows)

    # Z-score each factor, combine
    for col in ["score", "momentum", "quality", "value"]:
        df_rank[f"{col}_z"] = _zscore(df_rank[col])

    df_rank["combined"] = (
        df_rank["score_z"] * 0.40
        + df_rank["momentum_z"] * 0.25
        + df_rank["quality_z"] * 0.20
        + df_rank["value_z"] * 0.15
    )
    df_rank = df_rank.sort_values("combined", ascending=False).reset_index(drop=True)

    # --- Sector neutrality ---
    if sector_neutral:
        longs_list, shorts_list = _sector_neutral_select(df_rank, n_longs, n_shorts)
        notes.append("Sector-neutral mode active.")
    else:
        longs_list = df_rank.head(n_longs).to_dict("records")
        shorts_list = df_rank.tail(n_shorts).to_dict("records")

    # --- Build positions ---
    long_capital = capital / 2.0
    short_capital = capital / 2.0
    n_l = max(len(longs_list), 1)
    n_s = max(len(shorts_list), 1)

    long_positions = [
        LongShortPosition(
            ticker=r["ticker"],
            name=r["ticker"],
            direction="long",
            signal_score=round(r["score"], 2),
            sector=r["sector"],
            momentum_score=round(r["momentum"], 4),
            quality_score=round(r["quality"], 4),
            value_score=round(r["value"], 4),
            weight=round(1.0 / n_l, 4),
            dollar_allocation=round(long_capital / n_l, 2),
        )
        for r in longs_list
    ]

    short_positions = [
        LongShortPosition(
            ticker=r["ticker"],
            name=r["ticker"],
            direction="short",
            signal_score=round(r["score"], 2),
            sector=r["sector"],
            momentum_score=round(r["momentum"], 4),
            quality_score=round(r["quality"], 4),
            value_score=round(r["value"], 4),
            weight=round(1.0 / n_s, 4),
            dollar_allocation=round(short_capital / n_s, 2),
        )
        for r in shorts_list
    ]

    gross_exp = long_capital + short_capital
    net_exp = long_capital - short_capital

    # --- Aggregate factor exposures ---
    all_pos = longs_list + [dict(**r, _dir=1) for r in longs_list]
    avg_mom = np.mean([r["momentum"] for r in longs_list]) - np.mean([r["momentum"] for r in shorts_list])
    avg_val = np.mean([r["value"] for r in longs_list]) - np.mean([r["value"] for r in shorts_list])
    avg_qual = np.mean([r["quality"] for r in longs_list]) - np.mean([r["quality"] for r in shorts_list])

    factor_exp = {
        "momentum": round(float(avg_mom), 4),
        "value": round(float(avg_val), 4),
        "quality": round(float(avg_qual), 4),
    }

    # Sector breakdown
    sector_bkdn: Dict[str, float] = {}
    for p in long_positions:
        sector_bkdn[p.sector] = sector_bkdn.get(p.sector, 0.0) + p.weight / n_l
    for p in short_positions:
        sector_bkdn[p.sector] = sector_bkdn.get(p.sector, 0.0) - p.weight / n_s

    # Estimated Sharpe (naive heuristic from signal dispersion)
    long_scores = [r["score"] for r in longs_list]
    short_scores = [r["score"] for r in shorts_list]
    score_spread = np.mean(long_scores) - np.mean(short_scores) if long_scores and short_scores else 0.0
    est_sharpe = float(np.clip(score_spread / 30.0, -3.0, 3.0))

    strategy_score = float(np.clip(score_spread, -100, 100))

    notes.append(f"Long top {len(long_positions)}, short bottom {len(short_positions)} equities.")
    notes.append(f"Score spread (long avg - short avg): {score_spread:.1f}")

    return LongShortPortfolio(
        longs=long_positions,
        shorts=short_positions,
        gross_exposure=round(gross_exp, 2),
        net_exposure=round(net_exp, 2),
        long_count=len(long_positions),
        short_count=len(short_positions),
        factor_exposures=factor_exp,
        sector_breakdown={k: round(v, 4) for k, v in sector_bkdn.items()},
        estimated_sharpe=round(est_sharpe, 3),
        strategy_score=round(strategy_score, 2),
        notes=notes,
    )


def _sector_neutral_select(df_rank: pd.DataFrame, n_longs: int, n_shorts: int):
    """Select longs and shorts from same sectors to maintain sector neutrality."""
    sectors = df_rank["sector"].unique()
    longs_list, shorts_list = [], []

    for sector in sectors:
        sub = df_rank[df_rank["sector"] == sector]
        if len(sub) < 2:
            continue
        top = sub.head(max(1, n_longs // max(len(sectors), 1)))
        bot = sub.tail(max(1, n_shorts // max(len(sectors), 1)))
        longs_list.extend(top.to_dict("records"))
        shorts_list.extend(bot.to_dict("records"))

    # Trim to n
    longs_list = sorted(longs_list, key=lambda r: r["combined"], reverse=True)[:n_longs]
    shorts_list = sorted(shorts_list, key=lambda r: r["combined"])[:n_shorts]
    return longs_list, shorts_list
