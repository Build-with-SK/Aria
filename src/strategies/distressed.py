# src/strategies/distressed.py
"""
Distressed / Opportunistic Strategy
Altman Z-score, debt screening, FCF analysis.
Short distressed, opportunistically long oversold with improving fundamentals.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DistressedName:
    ticker: str
    altman_z_score: float
    debt_equity: float
    fcf_positive: bool          # is FCF currently positive?
    fcf_improving: bool         # turning positive from negative?
    price_vs_52w_high_pct: float  # e.g. -45 means 45% below 52w high
    sentiment_score: float
    distress_flags: List[str]
    recommended_direction: str  # "short" | "long" | "watch" | "skip"
    conviction: float
    rationale: str


@dataclass
class DistressedScreen:
    distressed_names: List[DistressedName]
    short_candidates: List[str]
    long_candidates: List[str]     # opportunistic oversold
    total_screened: int
    strategy_score: float
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "distressed_names": [vars(d) for d in self.distressed_names],
            "short_candidates": self.short_candidates,
            "long_candidates": self.long_candidates,
            "total_screened": self.total_screened,
            "strategy_score": round(self.strategy_score, 2),
            "notes": self.notes,
        }


def _compute_altman_z(info: dict) -> float:
    """
    Altman Z-Score (public company version):
    Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
    X1 = Working capital / Total assets
    X2 = Retained earnings / Total assets
    X3 = EBIT / Total assets
    X4 = Market cap / Total liabilities
    X5 = Revenue / Total assets

    Falls back gracefully to neutral (1.81) if data incomplete.
    """
    try:
        total_assets = float(info.get("totalAssets") or 0)
        if total_assets <= 0:
            return 1.81  # neutral (boundary)

        current_assets = float(info.get("totalCurrentAssets") or 0)
        current_liabilities = float(info.get("totalCurrentLiabilities") or 0)
        working_capital = current_assets - current_liabilities

        retained_earnings = float(info.get("retainedEarnings") or 0)
        ebit = float(info.get("ebit") or info.get("operatingIncome") or 0)
        mkt_cap = float(info.get("marketCap") or 0)
        total_liabilities = float(info.get("totalDebt") or 0) + current_liabilities
        revenue = float(info.get("totalRevenue") or 0)

        if total_liabilities <= 0:
            total_liabilities = total_assets * 0.4  # conservative default

        x1 = working_capital / total_assets
        x2 = retained_earnings / total_assets
        x3 = ebit / total_assets
        x4 = mkt_cap / total_liabilities if total_liabilities > 0 else 1.0
        x5 = revenue / total_assets

        z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
        return float(np.clip(z, -5, 15))
    except Exception:
        return 1.81


def _compute_debt_equity(info: dict) -> float:
    try:
        de = info.get("debtToEquity")
        if de is not None:
            return float(de) / 100.0   # yfinance returns as %, normalise
        # Fallback: compute from balance sheet
        debt = float(info.get("totalDebt") or 0)
        equity = float(info.get("stockholdersEquity") or info.get("totalStockholderEquity") or 1)
        if equity <= 0:
            return 99.0  # extremely leveraged
        return float(debt / equity)
    except Exception:
        return 0.0


def _is_fcf_positive(info: dict) -> bool:
    try:
        fcf = info.get("freeCashflow")
        if fcf is None:
            # OCF - capex proxy
            ocf = float(info.get("operatingCashflow") or 0)
            capex = float(info.get("capitalExpenditures") or 0)
            return (ocf + capex) > 0   # capex often negative in yf
        return float(fcf) > 0
    except Exception:
        return True


def _price_vs_52w_high(df: pd.DataFrame) -> float:
    """Returns % drawdown from 52w high. Negative = below high."""
    try:
        lookback = min(252, len(df))
        high_52w = df["High"].tail(lookback).max()
        current = float(df["Close"].iloc[-1])
        if high_52w == 0:
            return 0.0
        return float((current / high_52w - 1) * 100)
    except Exception:
        return 0.0


def _screen_ticker(
    ticker: str,
    df: pd.DataFrame,
    info: dict,
    sentiment_scores: Optional[dict],
    cfg: dict,
) -> Optional[DistressedName]:
    """Screen a single ticker for distress signals."""
    z_score = _compute_altman_z(info)
    de_ratio = _compute_debt_equity(info)
    fcf_pos = _is_fcf_positive(info)
    price_vs_52w = _price_vs_52w_high(df)

    sent_val = (sentiment_scores or {}).get(ticker, {})
    if isinstance(sent_val, dict):
        sent_val = sent_val.get("score", 0)
    sent_score = float(sent_val or 0)

    flags: List[str] = []
    distress_thresh_z = float(cfg.get("altman_z_distress", 1.81))
    distress_thresh_de = float(cfg.get("debt_equity_max", 3.0))
    distress_price_pct = float(cfg.get("price_vs_52w_pct", -40.0))

    if z_score < distress_thresh_z:
        flags.append(f"Altman Z={z_score:.2f} < {distress_thresh_z} (distress zone)")
    if de_ratio > distress_thresh_de:
        flags.append(f"D/E={de_ratio:.1f} > {distress_thresh_de}")
    if not fcf_pos:
        flags.append("Negative FCF")
    if price_vs_52w < distress_price_pct:
        flags.append(f"Price {price_vs_52w:.1f}% vs 52w high")

    if not flags:
        return None     # not distressed, skip

    # Determine direction
    is_severely_distressed = z_score < 1.0 and de_ratio > 4.0 and not fcf_pos
    is_oversold_opportunity = (
        price_vs_52w < -40
        and (z_score > 1.0 or fcf_pos)
        and sent_score > 0
    )

    if is_severely_distressed and sent_score < 0:
        direction = "short"
        conviction = min(80, len(flags) * 20)
        rationale = f"Severely distressed: Z={z_score:.1f}, negative sentiment"
    elif is_oversold_opportunity:
        direction = "long"
        conviction = 45
        rationale = "Oversold with stabilising fundamentals — contrarian opportunity"
    elif len(flags) >= 2:
        direction = "watch"
        conviction = 30
        rationale = f"{len(flags)} distress flags — monitor for deterioration"
    else:
        direction = "skip"
        conviction = 10
        rationale = "Single distress flag, insufficient conviction"

    return DistressedName(
        ticker=ticker,
        altman_z_score=round(z_score, 3),
        debt_equity=round(de_ratio, 3),
        fcf_positive=fcf_pos,
        fcf_improving=False,   # would need multi-period FCF to assess
        price_vs_52w_high_pct=round(price_vs_52w, 2),
        sentiment_score=round(sent_score, 2),
        distress_flags=flags,
        recommended_direction=direction,
        conviction=float(conviction),
        rationale=rationale,
    )


def run_distressed(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    yf_info: Optional[Dict[str, dict]] = None,
    sentiment_results: Optional[dict] = None,
) -> DistressedScreen:
    """
    Screen equity universe for distressed names.
    Returns DistressedScreen with recommended short/long candidates.
    """
    cfg = config.get("strategies", {}).get("distressed", {})
    notes = []

    skip_suffixes = ["=X", "-USD", "=F"]
    skip_tickers = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO",
                    "TLT", "IEF", "AGG", "HYG", "LQD", "JNK", "VCIT",
                    "BKLN", "SJNK", "SHYG", "CWB"}

    equity_tickers = [
        t for t in featured_data
        if not any(t.endswith(s) for s in skip_suffixes)
        and t not in skip_tickers
        and featured_data[t] is not None
        and not featured_data[t].empty
    ]

    distressed_names: List[DistressedName] = []
    total_screened = len(equity_tickers)

    if not yf_info:
        notes.append("No yf_info provided — Altman Z-score calculation will use fallback values.")

    for ticker in equity_tickers:
        df = featured_data[ticker]
        info = (yf_info or {}).get(ticker, {})
        try:
            result = _screen_ticker(ticker, df, info, sentiment_results, cfg)
            if result:
                distressed_names.append(result)
        except Exception as e:
            logger.debug(f"Distressed screen failed for {ticker}: {e}")

    short_candidates = [d.ticker for d in distressed_names if d.recommended_direction == "short"]
    long_candidates = [d.ticker for d in distressed_names if d.recommended_direction == "long"]

    notes.append(f"Screened {total_screened} equities.")
    notes.append(f"Distress signals found: {len(distressed_names)} tickers.")
    notes.append(f"Short candidates: {short_candidates}")
    notes.append(f"Long (opportunistic) candidates: {long_candidates}")

    # Strategy score: negative = bearish (distress = short bias)
    strategy_score = float(np.clip(
        (len(long_candidates) - len(short_candidates)) * 10, -100, 100
    ))

    return DistressedScreen(
        distressed_names=sorted(distressed_names, key=lambda d: d.altman_z_score),
        short_candidates=short_candidates,
        long_candidates=long_candidates,
        total_screened=total_screened,
        strategy_score=round(strategy_score, 2),
        notes=notes,
    )
