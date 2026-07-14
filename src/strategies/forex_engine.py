# src/strategies/forex_engine.py
"""
Forex Strategy Engine
Major, cross, and EM pairs via yfinance.
Strategies: carry trade, momentum, PPP mean reversion, CB divergence, risk overlay.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static reference tables
# ---------------------------------------------------------------------------

# Central bank rates (kept in sync with global_macro.py)
_CB_RATES: Dict[str, float] = {
    "USD": 5.25, "EUR": 4.00, "GBP": 5.25, "JPY": 0.10,
    "AUD": 4.35, "NZD": 5.50, "CAD": 5.00, "CHF": 1.75,
    "TRY": 50.00, "BRL": 10.75, "INR": 6.50, "MXN": 11.00,
}

# CB trajectory (same source as global_macro.py)
_CB_TRAJECTORY: Dict[str, str] = {
    "USD": "cutting", "EUR": "cutting", "GBP": "cutting", "JPY": "hiking",
    "AUD": "cutting", "NZD": "cutting", "CAD": "cutting", "CHF": "cutting",
    "TRY": "cutting", "BRL": "cutting", "INR": "neutral", "MXN": "cutting",
}

# Pair → (base currency, quote currency)
_FX_PAIRS: Dict[str, tuple] = {
    # Majors
    "EURUSD=X": ("EUR", "USD"), "GBPUSD=X": ("GBP", "USD"),
    "USDJPY=X": ("USD", "JPY"), "USDCHF=X": ("USD", "CHF"),
    "AUDUSD=X": ("AUD", "USD"), "NZDUSD=X": ("NZD", "USD"),
    "USDCAD=X": ("USD", "CAD"),
    # Crosses
    "EURGBP=X": ("EUR", "GBP"), "EURJPY=X": ("EUR", "JPY"),
    "GBPJPY=X": ("GBP", "JPY"),
    # EM
    "USDTRY=X": ("USD", "TRY"), "USDBRL=X": ("USD", "BRL"),
    "USDINR=X": ("USD", "INR"), "USDMXN=X": ("USD", "MXN"),
}

# PPP fair value (Big Mac index proxy — static, updated periodically)
# Source: Economist Big Mac Index approximate values vs USD
# Format: ticker → (fair_value, current_approx) — using spot as current
_PPP_FAIR_VALUE: Dict[str, float] = {
    "EURUSD=X": 1.12,  "GBPUSD=X": 1.27,  "USDJPY=X": 108.0,
    "USDCHF=X": 0.90,  "AUDUSD=X": 0.74,  "NZDUSD=X": 0.68,
    "USDCAD=X": 1.20,  "EURGBP=X": 0.88,  "EURJPY=X": 121.0,
    "GBPJPY=X": 136.0, "USDTRY=X": 12.0,  "USDBRL=X": 5.00,
    "USDINR=X": 75.0,  "USDMXN=X": 18.0,
}


@dataclass
class ForexSignal:
    pair: str
    base_ccy: str
    quote_ccy: str
    spot_price: float
    carry_score: float          # positive = long base bullish
    momentum_score: float       # 1/3/6m momentum composite
    ppp_deviation_pct: float    # (spot - fair_value) / fair_value * 100
    ppp_score: float            # signal toward fair value
    cb_divergence_score: float
    vix_overlay: str            # "normal" | "risk_off"
    combined_score: float       # -100 to +100
    signal: str                 # "strong_long" | "long" | "neutral" | "short" | "strong_short"
    conviction: float
    rationale: str

    def _to_json(self) -> dict:
        return {
            "pair": self.pair,
            "base_ccy": self.base_ccy,
            "quote_ccy": self.quote_ccy,
            "spot_price": round(self.spot_price, 6),
            "carry_score": round(self.carry_score, 2),
            "momentum_score": round(self.momentum_score, 2),
            "ppp_deviation_pct": round(self.ppp_deviation_pct, 2),
            "ppp_score": round(self.ppp_score, 2),
            "cb_divergence_score": round(self.cb_divergence_score, 2),
            "vix_overlay": self.vix_overlay,
            "combined_score": round(self.combined_score, 2),
            "signal": self.signal,
            "conviction": round(self.conviction, 1),
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# Individual signal components
# ---------------------------------------------------------------------------

def _carry_score(base: str, quote: str) -> float:
    """
    Carry score: interest rate differential.
    Positive = long base is carry-positive (base rate > quote rate).
    """
    rate_base = _CB_RATES.get(base, 2.0)
    rate_quote = _CB_RATES.get(quote, 2.0)
    diff = rate_base - rate_quote
    # Normalise: 10% diff = 100 score
    return float(np.clip(diff * 10, -100, 100))


def _momentum_score(df: pd.DataFrame) -> float:
    """Composite momentum: 1m, 3m, 6m returns, equal-weight."""
    try:
        if df is None or df.empty:
            return 0.0
        price = df["Close"].dropna()
        ret_1m  = float(price.pct_change(21).iloc[-1])  if len(price) >= 21  else 0.0
        ret_3m  = float(price.pct_change(63).iloc[-1])  if len(price) >= 63  else 0.0
        ret_6m  = float(price.pct_change(126).iloc[-1]) if len(price) >= 126 else 0.0
        composite = (ret_1m + ret_3m + ret_6m) / 3.0
        # Scale: 10% avg return = 100 score
        return float(np.clip(composite * 1000, -100, 100))
    except Exception:
        return 0.0


def _ppp_score(pair: str, spot: float) -> tuple:
    """
    PPP mean-reversion score.
    Returns (ppp_deviation_pct, ppp_score).
    Positive ppp_score = base undervalued → long base signal.
    """
    fair = _PPP_FAIR_VALUE.get(pair)
    if fair is None or fair == 0:
        return 0.0, 0.0
    deviation_pct = (spot - fair) / fair * 100  # positive = base overvalued vs PPP
    # Reversion signal: if spot > fair, base is overvalued → short base → negative score
    score = float(np.clip(-deviation_pct * 5, -100, 100))
    return round(float(deviation_pct), 2), round(score, 2)


def _cb_divergence_score(base: str, quote: str) -> float:
    """
    CB divergence: rate level + trajectory.
    Positive = base CB more hawkish → long base.
    """
    traj_val = {"hiking": 1.0, "neutral": 0.0, "cutting": -1.0}
    rate_diff = (_CB_RATES.get(base, 2.0) - _CB_RATES.get(quote, 2.0)) / 10.0
    traj_diff = (traj_val.get(_CB_TRAJECTORY.get(base, "neutral"), 0.0)
                 - traj_val.get(_CB_TRAJECTORY.get(quote, "neutral"), 0.0))
    raw = (rate_diff + traj_diff * 0.5) * 50
    return float(np.clip(raw, -100, 100))


def _vix_overlay(vix: float, is_carry_trade: bool) -> str:
    """Unwind carry trades when VIX > 25."""
    if vix > 25 and is_carry_trade:
        return "risk_off"
    return "normal"


def _signal_from_score(score: float) -> str:
    if score >= 60:
        return "strong_long"
    elif score >= 25:
        return "long"
    elif score <= -60:
        return "strong_short"
    elif score <= -25:
        return "short"
    else:
        return "neutral"


def _analyse_pair(
    pair: str,
    df: Optional[pd.DataFrame],
    vix: float,
    cfg: dict,
) -> Optional[ForexSignal]:
    """Build ForexSignal for a single FX pair."""
    try:
        base, quote = _FX_PAIRS.get(pair, ("USD", "USD"))
        if base == quote:
            return None

        spot = float(df["Close"].iloc[-1]) if (df is not None and not df.empty) else (
            _PPP_FAIR_VALUE.get(pair, 1.0)
        )

        carry = _carry_score(base, quote)
        carry_enabled = bool(cfg.get("carry_enabled", True))
        ppp_enabled = bool(cfg.get("ppp_enabled", True))

        mom = _momentum_score(df)
        ppp_dev, ppp_sig = _ppp_score(pair, spot) if ppp_enabled else (0.0, 0.0)
        cb_div = _cb_divergence_score(base, quote)

        # Carry trade flag: large rate differential
        is_carry = abs(carry) > 30
        vix_flag = _vix_overlay(vix, is_carry)

        # Weights
        w_carry   = 0.30 if carry_enabled else 0.0
        w_mom     = 0.35
        w_ppp     = 0.20 if ppp_enabled else 0.0
        w_cb      = 0.15
        total_w   = w_carry + w_mom + w_ppp + w_cb
        if total_w == 0:
            total_w = 1.0

        combined = (w_carry * carry + w_mom * mom + w_ppp * ppp_sig + w_cb * cb_div) / total_w

        # Risk-off: flatten carry component
        if vix_flag == "risk_off":
            combined = combined - (carry * w_carry / total_w) * 0.8
            combined = float(np.clip(combined, -100, 100))

        signal = _signal_from_score(combined)
        conviction = float(np.clip(abs(combined), 10, 85))

        rationale_parts = [
            f"Carry({base}-{quote})={carry:.0f}",
            f"Mom={mom:.0f}",
            f"PPP_dev={ppp_dev:.1f}%",
            f"CB_div={cb_div:.0f}",
            f"VIX={vix:.1f}({vix_flag})",
        ]

        return ForexSignal(
            pair=pair,
            base_ccy=base,
            quote_ccy=quote,
            spot_price=round(spot, 6),
            carry_score=round(carry, 2),
            momentum_score=round(mom, 2),
            ppp_deviation_pct=round(ppp_dev, 2),
            ppp_score=round(ppp_sig, 2),
            cb_divergence_score=round(cb_div, 2),
            vix_overlay=vix_flag,
            combined_score=round(float(combined), 2),
            signal=signal,
            conviction=round(conviction, 1),
            rationale=" | ".join(rationale_parts),
        )

    except Exception as e:
        logger.debug(f"Forex signal failed for {pair}: {e}")
        return None


def run_forex_engine(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    macro_snapshot: Optional[object] = None,
) -> List[ForexSignal]:
    """
    Build ForexSignal for all FX pairs in universe.
    Returns list sorted by |combined_score|.
    """
    cfg = config.get("strategies", {}).get("forex", {})

    vix = 18.0
    if macro_snapshot is not None:
        try:
            vix = float(getattr(macro_snapshot, "vix", 18.0))
        except Exception:
            pass

    fx_tickers = [t for t in featured_data if t.endswith("=X")]
    signals: List[ForexSignal] = []

    for pair in fx_tickers:
        if pair not in _FX_PAIRS:
            continue
        df = featured_data.get(pair)
        sig = _analyse_pair(pair, df, vix, cfg)
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: abs(s.combined_score), reverse=True)

    if not signals:
        logger.info("No FX signals generated. Add pairs (e.g. EURUSD=X) to universe.yaml.")

    return signals
