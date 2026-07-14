# src/strategies/global_macro.py
"""
Global Macro / Discretionary Strategy
Maps macro regime → asset class positioning.
Yield curve trades, CB divergence FX signals.
Extends the existing macro_data.py snapshot.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# Central bank rate table (static fallback — updated periodically)
_CB_RATES: Dict[str, float] = {
    "Fed": 5.25,    # US Fed Funds
    "ECB": 4.00,    # ECB deposit rate
    "BOJ": 0.10,    # Bank of Japan
    "BOE": 5.25,    # Bank of England
    "RBA": 4.35,    # Reserve Bank of Australia
    "RBNZ": 5.50,   # Reserve Bank of New Zealand
    "BOC": 5.00,    # Bank of Canada
    "SNB": 1.75,    # Swiss National Bank
}

# CB trajectory (heuristic from macro environment)
_CB_TRAJECTORY: Dict[str, str] = {
    "Fed": "cutting",
    "ECB": "cutting",
    "BOJ": "hiking",   # unusual — positive for JPY
    "BOE": "cutting",
    "RBA": "cutting",
    "RBNZ": "cutting",
    "BOC": "cutting",
    "SNB": "cutting",
}

# FX pair → (base CB, quote CB)
_FX_CB_MAP: Dict[str, tuple] = {
    "EURUSD=X": ("ECB", "Fed"),
    "GBPUSD=X": ("BOE", "Fed"),
    "USDJPY=X": ("Fed", "BOJ"),
    "AUDUSD=X": ("RBA", "Fed"),
    "NZDUSD=X": ("RBNZ", "Fed"),
    "USDCAD=X": ("Fed", "BOC"),
    "USDCHF=X": ("Fed", "SNB"),
    "EURGBP=X": ("ECB", "BOE"),
    "EURJPY=X": ("ECB", "BOJ"),
    "GBPJPY=X": ("BOE", "BOJ"),
}


@dataclass
class AssetTilt:
    asset_class: str
    direction: str      # "long" | "short" | "neutral"
    conviction: float   # 0-100
    ticker_proxies: List[str]
    rationale: str


@dataclass
class FXSignalMacro:
    pair: str
    base_cb: str
    quote_cb: str
    rate_differential: float
    trajectory_score: float  # +ve = base currency bullish
    signal: str
    conviction: float


@dataclass
class MacroPositioning:
    regime: str                         # Expansion | Stagflation | Recession | Recovery
    asset_tilts: List[AssetTilt]
    yield_curve_signal: str             # steepener | flattener | neutral
    cb_divergence_scores: Dict[str, float]
    fx_signals: List[FXSignalMacro]
    macro_score_input: float            # from existing macro_data.py
    strategy_score: float
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "regime": self.regime,
            "asset_tilts": [vars(t) for t in self.asset_tilts],
            "yield_curve_signal": self.yield_curve_signal,
            "cb_divergence_scores": {k: round(v, 2) for k, v in self.cb_divergence_scores.items()},
            "fx_signals": [vars(s) for s in self.fx_signals],
            "macro_score_input": round(self.macro_score_input, 2),
            "strategy_score": round(self.strategy_score, 2),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Regime → positioning map
# ---------------------------------------------------------------------------
_REGIME_TILTS: Dict[str, List[dict]] = {
    "Expansion": [
        {"asset_class": "Equities", "direction": "long", "conviction": 80,
         "ticker_proxies": ["SPY", "QQQ", "IWM"], "rationale": "Growth positive for risk assets"},
        {"asset_class": "Bonds", "direction": "short", "conviction": 60,
         "ticker_proxies": ["TLT", "IEF"], "rationale": "Rising rates in expansion"},
        {"asset_class": "Gold", "direction": "short", "conviction": 40,
         "ticker_proxies": ["GLD"], "rationale": "Lower safe-haven demand"},
        {"asset_class": "Credit", "direction": "long", "conviction": 70,
         "ticker_proxies": ["HYG", "JNK"], "rationale": "Spread compression in expansion"},
    ],
    "Stagflation": [
        {"asset_class": "Commodities", "direction": "long", "conviction": 85,
         "ticker_proxies": ["GLD", "SLV", "USO"], "rationale": "Inflation hedge"},
        {"asset_class": "Equities", "direction": "short", "conviction": 60,
         "ticker_proxies": ["SPY", "QQQ"], "rationale": "Margin compression from costs"},
        {"asset_class": "Bonds", "direction": "short", "conviction": 75,
         "ticker_proxies": ["TLT"], "rationale": "High inflation erodes fixed income"},
        {"asset_class": "TIPS_Proxy", "direction": "long", "conviction": 70,
         "ticker_proxies": ["TIP"], "rationale": "Inflation-protected bonds"},
    ],
    "Recession": [
        {"asset_class": "Bonds", "direction": "long", "conviction": 90,
         "ticker_proxies": ["TLT", "IEF", "AGG"], "rationale": "Flight to safety + rate cuts"},
        {"asset_class": "Gold", "direction": "long", "conviction": 80,
         "ticker_proxies": ["GLD"], "rationale": "Safe haven demand"},
        {"asset_class": "Equities", "direction": "short", "conviction": 75,
         "ticker_proxies": ["SPY", "QQQ", "IWM"], "rationale": "Earnings contraction"},
        {"asset_class": "USD", "direction": "long", "conviction": 65,
         "ticker_proxies": ["UUP"], "rationale": "Risk-off USD strength"},
        {"asset_class": "Credit", "direction": "short", "conviction": 70,
         "ticker_proxies": ["HYG", "JNK"], "rationale": "Default risk rising"},
    ],
    "Recovery": [
        {"asset_class": "Equities", "direction": "long", "conviction": 85,
         "ticker_proxies": ["SPY", "IWM", "QQQ"], "rationale": "Early cycle recovery"},
        {"asset_class": "HY_Credit", "direction": "long", "conviction": 75,
         "ticker_proxies": ["HYG", "JNK"], "rationale": "Spread normalization"},
        {"asset_class": "USD", "direction": "short", "conviction": 55,
         "ticker_proxies": ["UUP"], "rationale": "Risk-on weakens USD"},
        {"asset_class": "Bonds", "direction": "neutral", "conviction": 30,
         "ticker_proxies": ["IEF"], "rationale": "Uncertain rate path early cycle"},
    ],
}


def _classify_regime(macro_score: float, yield_spread: float, vix: float) -> str:
    """Map macro inputs → regime label."""
    if macro_score > 30 and yield_spread > 0 and vix < 20:
        return "Expansion"
    elif macro_score < -30 and vix > 25:
        return "Recession"
    elif macro_score < 0 and yield_spread < 0:
        return "Stagflation"
    elif macro_score > 0 and vix < 25:
        return "Recovery"
    else:
        return "Expansion" if macro_score >= 0 else "Recession"


def _cb_divergence_score(cb_a: str, cb_b: str) -> float:
    """
    Score divergence between two CBs.
    Positive = CB_A more hawkish than CB_B → bullish base currency.
    """
    traj_score = {"hiking": 1.0, "neutral": 0.0, "cutting": -1.0}
    rate_a = _CB_RATES.get(cb_a, 2.0)
    rate_b = _CB_RATES.get(cb_b, 2.0)
    traj_a = traj_score.get(_CB_TRAJECTORY.get(cb_a, "neutral"), 0.0)
    traj_b = traj_score.get(_CB_TRAJECTORY.get(cb_b, "neutral"), 0.0)
    rate_diff = (rate_a - rate_b) / 10.0   # normalise
    traj_diff = (traj_a - traj_b) * 0.5
    return float(np.clip((rate_diff + traj_diff) * 50, -100, 100))


def _yield_curve_signal(yield_spread: float) -> str:
    """2s10s yield spread → steepener/flattener."""
    if yield_spread > 0.5:
        return "steepener"
    elif yield_spread < -0.5:
        return "flattener"
    else:
        return "neutral"


def run_global_macro(
    config: dict,
    macro_snapshot: Optional[object] = None,  # MacroSnapshot from macro_data.py
) -> MacroPositioning:
    """
    Build macro positioning from existing macro_data snapshot.
    macro_snapshot: the MacroSnapshot dataclass from macro_data.py
    """
    notes = []

    # Extract from existing MacroSnapshot if available
    macro_score = 0.0
    yield_spread = 0.0
    vix = 18.0

    if macro_snapshot is not None:
        try:
            macro_score = float(getattr(macro_snapshot, "macro_score", 0.0))
            yield_spread = float(getattr(macro_snapshot, "yield_spread_10y2y", 0.0))
            vix = float(getattr(macro_snapshot, "vix", 18.0))
            notes.append(f"Macro snapshot loaded: score={macro_score:.1f}, yield_spread={yield_spread:.2f}, VIX={vix:.1f}")
        except Exception as e:
            notes.append(f"Could not parse macro snapshot: {e}")
    else:
        notes.append("No macro snapshot provided — using defaults.")

    regime = _classify_regime(macro_score, yield_spread, vix)
    notes.append(f"Classified regime: {regime}")

    # Build asset tilts from regime map
    tilt_dicts = _REGIME_TILTS.get(regime, _REGIME_TILTS["Expansion"])
    asset_tilts = [
        AssetTilt(
            asset_class=t["asset_class"],
            direction=t["direction"],
            conviction=float(t["conviction"]),
            ticker_proxies=t["ticker_proxies"],
            rationale=t["rationale"],
        )
        for t in tilt_dicts
    ]

    # Yield curve signal
    yc_signal = _yield_curve_signal(yield_spread)
    notes.append(f"Yield curve: {yc_signal} (spread={yield_spread:.2f}%)")

    # CB divergence scores
    cb_scores: Dict[str, float] = {}
    for cb_name in _CB_RATES:
        for cb_other in _CB_RATES:
            if cb_name != cb_other:
                key = f"{cb_name}_vs_{cb_other}"
                cb_scores[key] = _cb_divergence_score(cb_name, cb_other)

    # FX signals based on CB divergence
    fx_signals: List[FXSignalMacro] = []
    for pair, (base_cb, quote_cb) in _FX_CB_MAP.items():
        div_score = _cb_divergence_score(base_cb, quote_cb)
        rate_diff = _CB_RATES.get(base_cb, 0) - _CB_RATES.get(quote_cb, 0)
        traj_map = {"hiking": 1, "neutral": 0, "cutting": -1}
        traj_score_val = (
            traj_map.get(_CB_TRAJECTORY.get(base_cb, "neutral"), 0)
            - traj_map.get(_CB_TRAJECTORY.get(quote_cb, "neutral"), 0)
        )

        if div_score > 20:
            signal = "long_base"
        elif div_score < -20:
            signal = "short_base"
        else:
            signal = "neutral"

        # VIX overlay — unwind carry in risk-off
        if vix > 25 and "USD" in pair:
            signal = "neutral"
            notes.append(f"VIX > 25: neutralised {pair} signal")

        fx_signals.append(FXSignalMacro(
            pair=pair,
            base_cb=base_cb,
            quote_cb=quote_cb,
            rate_differential=round(rate_diff, 2),
            trajectory_score=round(float(traj_score_val), 2),
            signal=signal,
            conviction=round(abs(div_score), 1),
        ))

    # Overall strategy score
    strategy_score = float(np.clip(macro_score, -100, 100))

    return MacroPositioning(
        regime=regime,
        asset_tilts=asset_tilts,
        yield_curve_signal=yc_signal,
        cb_divergence_scores={k: round(v, 2) for k, v in list(cb_scores.items())[:20]},
        fx_signals=fx_signals,
        macro_score_input=round(macro_score, 2),
        strategy_score=round(strategy_score, 2),
        notes=notes,
    )
