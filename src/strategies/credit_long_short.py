# src/strategies/credit_long_short.py
"""
Credit Long/Short Strategy
Proxy credit exposure via HY/IG ETFs.
Credit spread signal + macro overlay + fundamental overlay.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ETF universe for credit
_CREDIT_ETFS = {
    "HY": ["HYG", "JNK"],
    "IG": ["LQD", "VCIT"],
    "SHORT_DURATION": ["SJNK", "SHYG"],
    "LEVERAGED_LOANS": ["BKLN"],
}

_ETF_DESCRIPTIONS = {
    "HYG": "iShares iBoxx $ HY Corp Bond ETF",
    "JNK": "SPDR Bloomberg HY Bond ETF",
    "LQD": "iShares iBoxx $ IG Corp Bond ETF",
    "VCIT": "Vanguard Intermediate-Term Corp Bond ETF",
    "SJNK": "SPDR Bloomberg ST HY Bond ETF",
    "SHYG": "iShares 0-5 Year HY Corp Bond ETF",
    "BKLN": "Invesco Senior Loan ETF",
}


@dataclass
class CreditETFSignal:
    ticker: str
    description: str
    credit_class: str       # HY | IG | SHORT_DURATION | LEVERAGED_LOANS
    current_price: float
    price_vs_sma20: float   # % above/below 20d SMA
    price_vs_sma50: float
    momentum_3m: float      # 3-month return
    signal: str             # "long" | "short" | "neutral"
    conviction: float


@dataclass
class CreditPositioning:
    etf_signals: List[CreditETFSignal]
    spread_regime: str          # "risk_on" | "risk_off" | "neutral"
    hyg_lqd_ratio_signal: str   # "falling" → risk-off | "rising" → risk-on
    macro_adjusted_signal: str  # after VIX/unemployment overlay
    hy_position: str            # "long" | "short" | "neutral"
    ig_position: str
    overall_credit_score: float     # -100 to +100
    strategy_score: float
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "etf_signals": [vars(s) for s in self.etf_signals],
            "spread_regime": self.spread_regime,
            "hyg_lqd_ratio_signal": self.hyg_lqd_ratio_signal,
            "macro_adjusted_signal": self.macro_adjusted_signal,
            "hy_position": self.hy_position,
            "ig_position": self.ig_position,
            "overall_credit_score": round(self.overall_credit_score, 2),
            "strategy_score": round(self.strategy_score, 2),
            "notes": self.notes,
        }


def _etf_signal(ticker: str, df: pd.DataFrame) -> CreditETFSignal:
    """Compute technical signal for a single credit ETF."""
    if df is None or df.empty:
        return CreditETFSignal(
            ticker=ticker,
            description=_ETF_DESCRIPTIONS.get(ticker, ticker),
            credit_class=_etf_class(ticker),
            current_price=0, price_vs_sma20=0, price_vs_sma50=0,
            momentum_3m=0, signal="neutral", conviction=0,
        )

    price = float(df["Close"].iloc[-1])
    sma20 = float(df["Close"].tail(20).mean())
    sma50 = float(df["Close"].tail(50).mean()) if len(df) >= 50 else sma20
    ret_3m = float(df["Close"].pct_change(63).iloc[-1]) if len(df) >= 63 else 0.0
    vs_20 = (price / sma20 - 1) * 100 if sma20 > 0 else 0
    vs_50 = (price / sma50 - 1) * 100 if sma50 > 0 else 0

    if vs_50 > 1 and ret_3m > 0:
        signal = "long"
        conviction = min(80, abs(vs_50) * 5 + abs(ret_3m) * 100)
    elif vs_50 < -1 and ret_3m < 0:
        signal = "short"
        conviction = min(80, abs(vs_50) * 5 + abs(ret_3m) * 100)
    else:
        signal = "neutral"
        conviction = 20

    return CreditETFSignal(
        ticker=ticker,
        description=_ETF_DESCRIPTIONS.get(ticker, ticker),
        credit_class=_etf_class(ticker),
        current_price=round(price, 4),
        price_vs_sma20=round(vs_20, 3),
        price_vs_sma50=round(vs_50, 3),
        momentum_3m=round(ret_3m * 100, 3),
        signal=signal,
        conviction=round(float(conviction), 1),
    )


def _etf_class(ticker: str) -> str:
    for cls, etfs in _CREDIT_ETFS.items():
        if ticker in etfs:
            return cls
    return "UNKNOWN"


def _hyg_lqd_spread_signal(
    df_hyg: Optional[pd.DataFrame],
    df_lqd: Optional[pd.DataFrame],
    window: int = 20,
) -> str:
    """
    HYG/LQD ratio falling → risk-off (short HY, long IG)
    HYG/LQD ratio rising → risk-on (long HY)
    """
    if df_hyg is None or df_lqd is None or df_hyg.empty or df_lqd.empty:
        return "unknown"
    try:
        combined = pd.concat([df_hyg["Close"], df_lqd["Close"]], axis=1).dropna()
        combined.columns = ["hyg", "lqd"]
        ratio = combined["hyg"] / combined["lqd"]
        recent_slope = float(ratio.tail(window).diff().tail(5).mean())
        if recent_slope > 0.001:
            return "rising"    # risk-on
        elif recent_slope < -0.001:
            return "falling"   # risk-off
        else:
            return "flat"
    except Exception:
        return "unknown"


def _macro_overlay(
    base_signal: str,
    vix: float,
    macro_score: float,
) -> str:
    """Adjust credit signal based on macro environment."""
    if vix > 25 or macro_score < -30:
        if base_signal == "long":
            return "neutral"    # reduce risk-on in volatile macro
        elif base_signal == "neutral":
            return "short"
    elif vix < 15 and macro_score > 20:
        if base_signal == "neutral":
            return "long"
    return base_signal


def run_credit_long_short(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    macro_snapshot: Optional[object] = None,
) -> CreditPositioning:
    """
    Build credit long/short positioning from ETF signals + macro overlay.
    """
    notes = []
    cfg = config.get("strategies", {}).get("credit_long_short", {})

    # Get macro inputs
    vix = 18.0
    macro_score = 0.0
    if macro_snapshot is not None:
        try:
            vix = float(getattr(macro_snapshot, "vix", 18.0))
            macro_score = float(getattr(macro_snapshot, "macro_score", 0.0))
        except Exception:
            pass

    # Compute individual ETF signals
    all_etf_tickers = [t for tickers in _CREDIT_ETFS.values() for t in tickers]
    etf_signals: List[CreditETFSignal] = []

    for ticker in all_etf_tickers:
        df = featured_data.get(ticker)
        if df is not None and not df.empty:
            sig = _etf_signal(ticker, df)
            etf_signals.append(sig)
        else:
            notes.append(f"{ticker} not in universe — skipped.")

    # HYG/LQD spread signal
    df_hyg = featured_data.get("HYG")
    df_lqd = featured_data.get("LQD")
    ratio_signal = _hyg_lqd_spread_signal(df_hyg, df_lqd)
    notes.append(f"HYG/LQD ratio: {ratio_signal}")

    # Spread regime
    if ratio_signal == "rising":
        spread_regime = "risk_on"
        base_hy = "long"
        base_ig = "neutral"
    elif ratio_signal == "falling":
        spread_regime = "risk_off"
        base_hy = "short"
        base_ig = "long"
    else:
        spread_regime = "neutral"
        base_hy = "neutral"
        base_ig = "neutral"

    # Macro overlay
    macro_hy = _macro_overlay(base_hy, vix, macro_score)
    macro_ig = _macro_overlay(base_ig, vix, macro_score)
    notes.append(f"VIX={vix:.1f}, macro_score={macro_score:.1f} — HY: {base_hy}→{macro_hy}, IG: {base_ig}→{macro_ig}")

    # Overall credit score
    signal_map = {"long": 1, "neutral": 0, "short": -1}
    hy_val = signal_map.get(macro_hy, 0) * 60
    ig_val = signal_map.get(macro_ig, 0) * 40
    credit_score = float(hy_val + ig_val)
    strategy_score = float(np.clip(credit_score, -100, 100))

    if not etf_signals:
        notes.append("No credit ETFs found in universe. Add HYG, LQD, JNK, VCIT, BKLN, SJNK, SHYG to configs/universe.yaml.")

    return CreditPositioning(
        etf_signals=etf_signals,
        spread_regime=spread_regime,
        hyg_lqd_ratio_signal=ratio_signal,
        macro_adjusted_signal=f"HY={macro_hy}, IG={macro_ig}",
        hy_position=macro_hy,
        ig_position=macro_ig,
        overall_credit_score=round(credit_score, 2),
        strategy_score=round(strategy_score, 2),
        notes=notes,
    )
