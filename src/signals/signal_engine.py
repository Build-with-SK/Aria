"""
signal_engine.py
================
Situation-aware signal engine. Thinks like a human trader, not a formula.

The core insight: a 20-year trader doesn't apply the same weight to momentum
signals in a bear collapse as they do in an early bull market. Context changes
everything. This engine classifies the current market situation first, selects
the right analytical playbook, detects internal contradictions, and only then
produces a signal — with a full trade thesis explaining the WHY.

Score bands:
  +75 to +100  = Strong Buy
  +40 to  +74  = Buy
  +10 to  +39  = Mild Bullish
   -9 to   +9  = Neutral
  -10 to  -39  = Mild Bearish
  -40 to  -74  = Sell
  -75 to -100  = Strong Sell

IMPORTANT: Research tool only. Signals are probabilistic.
No model guarantees profitability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ===========================================================================
# Situation Archetypes
# ===========================================================================

ARCHETYPES = {
    # Name              : description
    "EARLY_BULL"        : "Early bull market — trend just turned, vol contracting, risk-on",
    "LATE_BULL"         : "Late-stage bull — extended above MAs, overbought, cracks forming",
    "BEAR_COLLAPSE"     : "Active bear / sell-off — macro and regime dominate, technicals noisy",
    "BEAR_RELIEF_RALLY" : "Bear market rally — momentum spike inside a bear trend, trap risk",
    "RECOVERY"          : "Post-bear recovery — stabilizing after collapse, fragile bull",
    "SIDEWAYS_GRIND"    : "Range-bound / sideways — no clear trend, mean reversion rules",
    "VOLATILITY_SPIKE"  : "Volatility event — signals unreliable, sizing must be minimal",
    "REGIME_FLIP"       : "Regime transition — mixed signals, wait for confirmation",
    "RISK_OFF_FLIGHT"   : "Global risk-off — quality flight, macro and sentiment dominate",
    "TRENDING_MOMENTUM" : "Strong trend with momentum confirmation — trend + momentum dominant",
}

# Dynamic weights per archetype. Must each sum to 1.0.
# Human insight: in a bear collapse, technicals lie. In a momentum trend, ride it.
ARCHETYPE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "EARLY_BULL": {
        "trend": 0.25, "momentum": 0.35, "volatility": 0.10,
        "regime": 0.20, "macro": 0.07, "sentiment": 0.03,
    },
    "LATE_BULL": {
        "trend": 0.15, "momentum": 0.20, "volatility": 0.20,
        "regime": 0.15, "macro": 0.20, "sentiment": 0.10,
    },
    "BEAR_COLLAPSE": {
        "trend": 0.05, "momentum": 0.05, "volatility": 0.10,
        "regime": 0.35, "macro": 0.35, "sentiment": 0.10,
    },
    "BEAR_RELIEF_RALLY": {
        "trend": 0.10, "momentum": 0.25, "volatility": 0.20,
        "regime": 0.30, "macro": 0.10, "sentiment": 0.05,
    },
    "RECOVERY": {
        "trend": 0.20, "momentum": 0.25, "volatility": 0.15,
        "regime": 0.25, "macro": 0.10, "sentiment": 0.05,
    },
    "SIDEWAYS_GRIND": {
        "trend": 0.15, "momentum": 0.25, "volatility": 0.30,
        "regime": 0.10, "macro": 0.10, "sentiment": 0.10,
    },
    "VOLATILITY_SPIKE": {
        "trend": 0.10, "momentum": 0.10, "volatility": 0.40,
        "regime": 0.20, "macro": 0.15, "sentiment": 0.05,
    },
    "REGIME_FLIP": {
        "trend": 0.15, "momentum": 0.15, "volatility": 0.20,
        "regime": 0.30, "macro": 0.15, "sentiment": 0.05,
    },
    "RISK_OFF_FLIGHT": {
        "trend": 0.10, "momentum": 0.10, "volatility": 0.15,
        "regime": 0.30, "macro": 0.25, "sentiment": 0.10,
    },
    "TRENDING_MOMENTUM": {
        "trend": 0.35, "momentum": 0.35, "volatility": 0.05,
        "regime": 0.15, "macro": 0.07, "sentiment": 0.03,
    },
}

# Verify all weights sum to 1.0
for _arch, _w in ARCHETYPE_WEIGHTS.items():
    assert abs(sum(_w.values()) - 1.0) < 1e-9, f"Weights for {_arch} must sum to 1.0"


# ===========================================================================
# Output dataclasses
# ===========================================================================

@dataclass
class ConflictFlag:
    """A detected contradiction between two sub-signals."""
    severity: str          # "HIGH" | "MEDIUM" | "LOW"
    bull_side: str
    bear_side: str
    description: str
    impact: str            # How this affects the final signal


@dataclass
class TradeThesis:
    """
    Structured reasoning for a trade — the WHY, not just the score.
    A human trader builds this before sizing a position.
    """
    situation: str             # Current archetype + what it means
    bull_case: List[str]       # Top reasons to be long
    bear_case: List[str]       # Top reasons to be short / cautious
    invalidation: List[str]    # What would prove the thesis wrong
    catalyst_risk: str         # Upcoming events that could override the signal
    time_horizon: str
    sizing_rationale: str      # Why this position size


@dataclass
class AssetSignal:
    """Full structured signal for one asset."""
    ticker:            str
    name:              str
    asset_class:       str

    # Price info
    current_price:     float
    price_52w_high:    float
    price_52w_low:     float

    # Signal
    composite_score:   float
    action:            str
    confidence:        str
    bullish_prob:      float
    bearish_prob:      float
    time_horizon:      str

    # Sub-scores (each -100 to +100)
    trend_score:       float
    momentum_score:    float
    volatility_score:  float
    regime_score:      float
    macro_score:       float
    sentiment_score:   float

    # Risk
    risk_level:        str
    atr_pct:           float
    realised_vol:      float
    stop_loss:         Optional[float]
    take_profit:       Optional[float]
    invalidation:      Optional[float]
    position_size_pct: float

    # Regime
    regime:            str

    # Situation intelligence (new)
    situation_archetype:   str = "UNKNOWN"
    archetype_description: str = ""
    active_weights:        Dict[str, float] = field(default_factory=dict)
    conflict_flags:        List[ConflictFlag] = field(default_factory=list)
    conflict_penalty:      float = 0.0
    trade_thesis:          Optional[TradeThesis] = None

    # Legacy fields
    drivers:           List[str] = field(default_factory=list)
    risks:             List[str] = field(default_factory=list)
    explanation:       str = ""


# ===========================================================================
# Helper
# ===========================================================================

def _clamp(value: float, low: float = -100, high: float = 100) -> float:
    return float(np.clip(value, low, high))


# ===========================================================================
# Sub-score calculators (unchanged logic, same quality)
# ===========================================================================

def compute_trend_score(row: pd.Series) -> float:
    score = 0.0
    if "dist_sma_20" in row and pd.notna(row["dist_sma_20"]):
        score += _clamp(row["dist_sma_20"] * 300, -30, 30)
    if "dist_sma_50" in row and pd.notna(row["dist_sma_50"]):
        score += _clamp(row["dist_sma_50"] * 200, -25, 25)
    if "dist_sma_200" in row and pd.notna(row["dist_sma_200"]):
        score += _clamp(row["dist_sma_200"] * 150, -20, 20)
    if "ema_12" in row and "ema_26" in row:
        if pd.notna(row["ema_12"]) and pd.notna(row["ema_26"]) and row["ema_26"] != 0:
            ema_signal = (row["ema_12"] - row["ema_26"]) / row["ema_26"] * 500
            score += _clamp(ema_signal, -25, 25)
    return _clamp(score)


def compute_momentum_score(row: pd.Series) -> float:
    score = 0.0
    if "rsi" in row and pd.notna(row["rsi"]):
        rsi = row["rsi"]
        if rsi > 70:
            rsi_score = 30 - (rsi - 70) * 3
        elif rsi < 30:
            rsi_score = -30 + (30 - rsi) * 3
        else:
            rsi_score = (rsi - 50) * 1.2
        score += _clamp(rsi_score, -35, 35)
    if "macd_histogram" in row and pd.notna(row["macd_histogram"]):
        score += 20 if row["macd_histogram"] > 0 else -20
    if "roc" in row and pd.notna(row["roc"]):
        score += _clamp(row["roc"] * 2, -25, 25)
    if "volume_trend" in row and pd.notna(row["volume_trend"]):
        vol_bonus = (row["volume_trend"] - 1) * 10
        roc_sign = np.sign(row.get("roc", 0))
        score += _clamp(vol_bonus * roc_sign, -20, 20)
    return _clamp(score)


def compute_volatility_score(row: pd.Series) -> float:
    score = 0.0
    if "realised_vol" in row and pd.notna(row["realised_vol"]):
        vol = row["realised_vol"]
        if vol < 0.10:    score = 20
        elif vol < 0.20:  score = 5
        elif vol < 0.35:  score = -15
        elif vol < 0.60:  score = -35
        else:             score = -60
    if "bb_width" in row and pd.notna(row["bb_width"]):
        bw = row["bb_width"]
        if bw > 0.15:   score -= 10
        elif bw < 0.05: score += 5
    return _clamp(score)


def compute_regime_score(row: pd.Series) -> float:
    score = 0.0
    if row.get("bull_regime", 0) == 1:   score += 40
    elif row.get("bear_regime", 0) == 1: score -= 40
    if row.get("risk_on", 0) == 1:   score += 20
    elif row.get("risk_off", 0) == 1: score -= 20
    if row.get("high_vol_regime", 0) == 1: score -= 10
    elif row.get("low_vol_regime", 0) == 1: score += 10
    return _clamp(score)


def compute_macro_score(row: pd.Series, asset_class: str) -> float:
    """
    Macro score — uses yield curve, VIX, and DXY signals if present.
    Falls back to 0 if macro data not yet populated in features.
    """
    score = 0.0
    # Yield curve (inverted = bearish)
    if "yield_curve" in row and pd.notna(row["yield_curve"]):
        yc = row["yield_curve"]
        if yc < -0.5:    score -= 30
        elif yc < 0:     score -= 10
        elif yc > 1.0:   score += 15
    # VIX proxy
    if "vix_level" in row and pd.notna(row["vix_level"]):
        vix = row["vix_level"]
        if vix > 35:     score -= 40
        elif vix > 25:   score -= 20
        elif vix < 15:   score += 15
    # DXY — strong dollar hurts EM and commodities
    if "dxy_trend" in row and pd.notna(row["dxy_trend"]):
        if asset_class in ("commodities", "em_equities", "crypto"):
            score -= row["dxy_trend"] * 20
    return _clamp(score)


def compute_sentiment_score(row: pd.Series) -> float:
    """Uses news_sentiment field if present from sentiment pipeline."""
    if "news_sentiment" in row and pd.notna(row["news_sentiment"]):
        return _clamp(row["news_sentiment"] * 100, -50, 50)
    return 0.0


# ===========================================================================
# SITUATION CLASSIFIER
# The most important new component. Context before calculation.
# ===========================================================================

def classify_situation(row: pd.Series, ticker_vol: float) -> str:
    """
    Classify the current market situation into one of 10 archetypes.
    This drives which weights are used and shapes the whole analysis.

    Decision logic mirrors how an experienced trader reads the tape:
    first ask 'what kind of market is this?' before looking at any signal.
    """
    bull_regime  = row.get("bull_regime", 0) == 1
    bear_regime  = row.get("bear_regime", 0) == 1
    risk_on      = row.get("risk_on", 0) == 1
    risk_off     = row.get("risk_off", 0) == 1
    high_vol     = row.get("high_vol_regime", 0) == 1
    low_vol      = row.get("low_vol_regime", 0) == 1

    vol = ticker_vol
    rsi = row.get("rsi", 50)
    dist_200 = row.get("dist_sma_200", 0) or 0
    dist_50  = row.get("dist_sma_50", 0) or 0
    macd_hist = row.get("macd_histogram", 0) or 0

    # 1. Volatility spike — everything else is secondary
    if vol > 0.55 or high_vol:
        if bear_regime or risk_off:
            return "BEAR_COLLAPSE"
        return "VOLATILITY_SPIKE"

    # 2. Clear bear regime
    if bear_regime or risk_off:
        # Momentum spiking inside a bear? Relief rally trap.
        if rsi > 60 and macd_hist > 0:
            return "BEAR_RELIEF_RALLY"
        return "BEAR_COLLAPSE"

    # 3. Regime flip — bull and bear signals both present (transition)
    trend_bull = dist_200 > 0 and dist_50 > 0
    trend_bear = dist_200 < 0 and dist_50 < 0
    if not trend_bull and not trend_bear:
        return "REGIME_FLIP"

    # 4. Bull market variants
    if bull_regime or (risk_on and trend_bull):
        # Extended / late-stage: price way above 200MA + RSI elevated
        if dist_200 > 0.15 and rsi > 65:
            return "LATE_BULL"
        # Low vol, fresh breakout → early bull
        if low_vol and vol < 0.20:
            return "EARLY_BULL"
        # Strong trending momentum — both trend and momentum aligned
        if macd_hist > 0 and rsi > 55 and dist_50 > 0:
            return "TRENDING_MOMENTUM"
        return "EARLY_BULL"

    # 5. Recovery — price below 200MA but stabilizing (was in bear, recovering)
    if dist_200 < 0 and dist_50 > 0 and not bear_regime:
        return "RECOVERY"

    # 6. Risk-off without a clear regime
    if risk_off:
        return "RISK_OFF_FLIGHT"

    # 7. Sideways — no dominant trend, vol normal
    if abs(dist_200) < 0.05 and abs(dist_50) < 0.03:
        return "SIDEWAYS_GRIND"

    # Default: treat as trending
    return "TRENDING_MOMENTUM"


# ===========================================================================
# CONFLICT DETECTOR
# Before issuing a signal, check if sub-scores are fighting each other.
# A human trader pauses when their indicators disagree. So does ARIA.
# ===========================================================================

def detect_conflicts(
    trend_s: float,
    momentum_s: float,
    vol_s: float,
    regime_s: float,
    macro_s: float,
    archetype: str,
) -> Tuple[List[ConflictFlag], float]:
    """
    Detect contradictions between sub-scores.
    Returns (list of conflicts, confidence penalty 0-30).
    """
    conflicts = []
    penalty = 0.0

    # Trend vs Momentum disagreement (common in exhausted moves)
    if trend_s > 40 and momentum_s < -20:
        conflicts.append(ConflictFlag(
            severity="HIGH",
            bull_side=f"Trend score: +{trend_s:.0f} (price above MAs, uptrend)",
            bear_side=f"Momentum score: {momentum_s:.0f} (RSI/MACD weakening)",
            description="Strong trend but deteriorating momentum — classic trend exhaustion signal.",
            impact="Reduce conviction. Trend may continue but momentum divergence warns of pullback.",
        ))
        penalty += 15
    elif trend_s < -40 and momentum_s > 20:
        conflicts.append(ConflictFlag(
            severity="MEDIUM",
            bull_side=f"Momentum score: +{momentum_s:.0f} (RSI/MACD recovering)",
            bear_side=f"Trend score: {trend_s:.0f} (price below MAs, downtrend)",
            description="Momentum bouncing inside a downtrend — potential bear market rally.",
            impact="Be cautious with any long signal here. Could be a relief rally trap.",
        ))
        penalty += 10

    # Regime vs Technicals disagreement
    if regime_s < -30 and trend_s > 30:
        conflicts.append(ConflictFlag(
            severity="HIGH",
            bull_side=f"Trend score: +{trend_s:.0f} (technicals bullish)",
            bear_side=f"Regime score: {regime_s:.0f} (regime is bearish/risk-off)",
            description=(
                "Technicals look bullish but the macro regime is bearish. "
                "This is the most dangerous type of false positive — "
                "regime shifts tend to eventually overwhelm technical signals."
            ),
            impact="Macro regime overrides technicals in bear markets. Do not go full conviction long.",
        ))
        penalty += 20
    elif regime_s > 30 and trend_s < -30:
        conflicts.append(ConflictFlag(
            severity="MEDIUM",
            bull_side=f"Regime score: +{regime_s:.0f} (macro/regime bullish)",
            bear_side=f"Trend score: {trend_s:.0f} (technicals bearish)",
            description="Regime is supportive but technicals are negative. Could be early-stage setup.",
            impact="Wait for technical confirmation before acting on the macro tailwind.",
        ))
        penalty += 8

    # Macro vs everything else
    if macro_s < -40 and (trend_s + momentum_s) / 2 > 30:
        conflicts.append(ConflictFlag(
            severity="HIGH",
            bull_side=f"Technical average: +{(trend_s + momentum_s)/2:.0f}",
            bear_side=f"Macro score: {macro_s:.0f} (yield curve / VIX / macro headwinds)",
            description=(
                "Strong macro headwinds (inverted yield curve, elevated VIX, or DXY strength) "
                "while technicals look bullish. Macro tends to win over 1-3 month horizons."
            ),
            impact="Shorten time horizon or reduce position. Macro overrides on a 1+ month basis.",
        ))
        penalty += 15

    # Within a BEAR_COLLAPSE archetype, any bullish signal should be flagged
    if archetype == "BEAR_COLLAPSE" and (trend_s + momentum_s) > 20:
        conflicts.append(ConflictFlag(
            severity="MEDIUM",
            bull_side=f"Technical scores showing +{(trend_s + momentum_s)/2:.0f} average",
            bear_side="Current archetype: BEAR_COLLAPSE — regime is actively bearish",
            description=(
                "Technical signals appear bullish during an active bear collapse. "
                "In sell-offs, technicals lag the actual price damage. "
                "The regime read is more reliable here."
            ),
            impact="Downweight technical signals by 50% during bear collapses.",
        ))
        penalty += 8

    # Late bull with high macro headwinds = peak warning
    if archetype == "LATE_BULL" and macro_s < -20:
        conflicts.append(ConflictFlag(
            severity="MEDIUM",
            bull_side="Price action still bullish (late bull archetype)",
            bear_side=f"Macro score: {macro_s:.0f} — macro headwinds building",
            description=(
                "Late-stage bull market with macro headwinds building. "
                "This combination preceded every major market peak in history."
            ),
            impact="This is a peak-risk signal. Tighten stops, reduce position size.",
        ))
        penalty += 10

    penalty = min(penalty, 30.0)  # Cap penalty at 30 points
    return conflicts, penalty


# ===========================================================================
# TRADE THESIS BUILDER
# Every signal gets a structured argument, not just a score.
# A number without reasoning is not a decision — it's a guess.
# ===========================================================================

def build_trade_thesis(
    ticker: str,
    row: pd.Series,
    composite: float,
    archetype: str,
    conflicts: List[ConflictFlag],
    price: float,
    stop_loss: float,
    take_profit: float,
) -> TradeThesis:
    """
    Build a structured trade thesis — the reasoning behind the signal.
    This is what separates a decision from a data point.
    """
    bull_case = []
    bear_case = []
    invalidation = []

    # ── Bull case ─────────────────────────────────────────────────────────

    if row.get("dist_sma_200", 0) > 0:
        pct = abs(row["dist_sma_200"]) * 100
        bull_case.append(f"Price is {pct:.1f}% above 200-day MA — long-term trend is intact")

    if row.get("bull_regime", 0) == 1:
        bull_case.append("Confirmed bull regime — SMA50 above SMA200 on long-term basis")

    if row.get("rsi", 50) < 65 and row.get("rsi", 50) > 40:
        rsi_val = row.get("rsi", 50)
        bull_case.append(f"RSI at {rsi_val:.0f} — not overbought, room to run")

    if row.get("macd_histogram", 0) > 0:
        bull_case.append("MACD histogram positive — upward momentum building")

    if row.get("risk_on", 0) == 1:
        bull_case.append("Risk-on regime — institutional money is flowing into risk assets")

    if row.get("volume_trend", 1) > 1.3:
        bull_case.append(f"Volume {row['volume_trend']:.1f}× above average on up moves — institutional buying")

    # ── Bear case ─────────────────────────────────────────────────────────

    if row.get("dist_sma_200", 0) < 0:
        pct = abs(row["dist_sma_200"]) * 100
        bear_case.append(f"Price is {pct:.1f}% BELOW 200-day MA — long-term trend is bearish")

    if row.get("bear_regime", 0) == 1:
        bear_case.append("Confirmed bear regime — death cross in effect, institutional sellers in control")

    if row.get("rsi", 50) > 72:
        bear_case.append(f"RSI at {row['rsi']:.0f} — significantly overbought, pullback risk is real")

    if row.get("risk_off", 0) == 1:
        bear_case.append("Risk-off regime — money is flowing out of risk assets into safe havens")

    vol = row.get("realised_vol", 0.20)
    if vol > 0.35:
        bear_case.append(f"Elevated volatility ({vol:.0%} annualised) — wide price swings, difficult to hold")

    if row.get("bb_pct_b", 0.5) > 0.90:
        bear_case.append("Price at upper Bollinger Band — statistically stretched, reversion risk")

    # Add conflict-sourced bear/bull cases
    for cf in conflicts:
        if cf.severity == "HIGH":
            bear_case.append(f"⚠ Signal conflict: {cf.description}")

    # ── Invalidation triggers ─────────────────────────────────────────────

    sma_50 = row.get("sma_50", price * 0.95)
    sma_200 = row.get("sma_200", price * 0.90)

    if composite > 0:
        invalidation.append(f"Close below SMA50 (${sma_50:.2f}) on heavy volume invalidates the bull thesis")
        invalidation.append(f"RSI dropping below 40 would signal momentum failure")
        if row.get("macd_histogram", 0) > 0:
            invalidation.append("MACD histogram turning negative would reduce conviction materially")
    else:
        invalidation.append(f"Rally back above SMA50 (${sma_50:.2f}) would require re-assessment")
        invalidation.append(f"RSI recovering above 55 would signal the downtrend is losing steam")

    invalidation.append(f"Position stop at ${stop_loss:.2f} — breach = thesis is wrong, exit immediately")

    # ── Archetype-specific situation context ──────────────────────────────

    archetype_context = {
        "EARLY_BULL":         "Early bull conditions: ride momentum, don't overthink pullbacks.",
        "LATE_BULL":          "Late bull: risk/reward is deteriorating. Tighten stops, take partial profits.",
        "BEAR_COLLAPSE":      "Bear collapse: do NOT fight the tape. Cash is a position. Shorting only for experienced.",
        "BEAR_RELIEF_RALLY":  "Relief rally inside a bear: short-term trade only. Exit quickly. Do not hold overnight.",
        "RECOVERY":           "Recovery phase: asymmetric upside but fragile. Size conservatively, add on confirmation.",
        "SIDEWAYS_GRIND":     "Sideways market: fade extremes, buy support, sell resistance. Trend-following will lose.",
        "VOLATILITY_SPIKE":   "Volatility event: signals are unreliable. Wait for stabilization before acting.",
        "REGIME_FLIP":        "Regime transition: wait for confirmation. False breakouts are common here.",
        "RISK_OFF_FLIGHT":    "Risk-off: quality names only. Defensives, utilities, gold outperform.",
        "TRENDING_MOMENTUM":  "Momentum trend: stay with it until it breaks. Don't anticipate the top.",
    }

    situation_desc = archetype_context.get(archetype, "Standard market conditions.")

    # ── Catalyst risk ─────────────────────────────────────────────────────

    catalyst_risk = (
        "Always check: earnings dates, Fed meeting, CPI/jobs report, or geopolitical events "
        "within the signal horizon that could override technical signals regardless of setup quality."
    )

    # ── Sizing rationale ─────────────────────────────────────────────────

    n_conflicts = len(conflicts)
    high_conflicts = sum(1 for c in conflicts if c.severity == "HIGH")

    if high_conflicts >= 2:
        sizing_rationale = (
            f"REDUCED SIZE: {high_conflicts} HIGH severity signal conflicts detected. "
            f"Start at 25-33% of intended size. Add only if conflicts resolve."
        )
    elif n_conflicts >= 2:
        sizing_rationale = (
            f"MODERATE SIZE: {n_conflicts} signal conflicts detected. "
            f"Start at 50% of intended size. Full position after confirmation."
        )
    elif archetype in ("VOLATILITY_SPIKE", "BEAR_COLLAPSE", "REGIME_FLIP"):
        sizing_rationale = (
            f"SMALL SIZE: {archetype} archetype warrants caution. "
            f"25-50% of normal position. Volatility makes normal sizing dangerous."
        )
    elif abs(composite) > 65 and n_conflicts == 0:
        sizing_rationale = (
            f"FULL SIZE ELIGIBLE: Strong signal ({composite:.0f}) with no conflicts. "
            f"Size per normal risk rules (1.5× ATR stop, 2.5× ATR target)."
        )
    else:
        sizing_rationale = (
            f"NORMAL SIZE: Signal strength {composite:.0f}, {n_conflicts} minor conflicts. "
            f"Standard position size per risk framework."
        )

    # Ensure at least one item in each list
    if not bull_case:
        bull_case.append("No strong bullish signals detected at this time")
    if not bear_case:
        bear_case.append("No significant bearish concerns detected at this time")
    if not invalidation:
        invalidation.append(f"Stop at ${stop_loss:.2f}")

    return TradeThesis(
        situation=situation_desc,
        bull_case=bull_case[:5],
        bear_case=bear_case[:5],
        invalidation=invalidation[:4],
        catalyst_risk=catalyst_risk,
        time_horizon="3 days to 4 weeks depending on archetype",
        sizing_rationale=sizing_rationale,
    )


# ===========================================================================
# Composite score — now archetype-weighted
# ===========================================================================

def compute_composite_score(
    trend: float,
    momentum: float,
    volatility: float,
    regime: float,
    macro: float,
    sentiment: float,
    weights: Dict[str, float],
) -> float:
    return (
        trend      * weights["trend"]
        + momentum * weights["momentum"]
        + volatility * weights["volatility"]
        + regime   * weights["regime"]
        + macro    * weights["macro"]
        + sentiment * weights["sentiment"]
    )


def score_to_action(score: float) -> str:
    if score >= 75:   return "Strong Buy"
    if score >= 40:   return "Buy"
    if score >= 10:   return "Mild Bullish"
    if score >= -9:   return "Neutral"
    if score >= -40:  return "Mild Bearish"
    if score >= -75:  return "Sell"
    return "Strong Sell"


def score_to_confidence(
    score: float,
    vol: float,
    n_conflicts: int,
    high_conflicts: int,
) -> str:
    """
    Confidence now accounts for signal conflicts, not just volatility.
    A high score with multiple conflicts is not HIGH confidence.
    """
    abs_score = abs(score)

    # Hard caps from conflicts
    if high_conflicts >= 2:
        return "Low"
    if high_conflicts >= 1 or n_conflicts >= 3:
        return "Low" if abs_score < 40 else "Medium"

    # Volatility cap
    if vol > 0.50 or abs_score < 15:
        return "Low"
    if vol > 0.30 or abs_score < 40:
        return "Medium"

    return "High"


def score_to_risk_level(atr_pct: float, vol: float, beta: float) -> str:
    if atr_pct > 0.04 or vol > 0.50 or beta > 1.8: return "Very High"
    if atr_pct > 0.02 or vol > 0.30 or beta > 1.3: return "High"
    if atr_pct > 0.01 or vol > 0.15:               return "Medium"
    return "Low"


def score_to_probabilities(score: float):
    bull_prob = float(np.clip(0.5 + (score / 100) * 0.45, 0.05, 0.95))
    return bull_prob, 1.0 - bull_prob


def identify_regime(row: pd.Series) -> str:
    if row.get("risk_on", 0) == 1:     return "Risk-On Bull"
    if row.get("risk_off", 0) == 1:    return "Risk-Off Bear"
    if row.get("bull_regime", 0) == 1: return "Bull"
    if row.get("bear_regime", 0) == 1: return "Bear"
    return "Sideways"


# ===========================================================================
# Risk parameters
# ===========================================================================

def compute_risk_params(row: pd.Series, price: float, score: float, archetype: str):
    atr = row.get("atr", price * 0.02)
    atr_pct = row.get("atr_pct", 0.02)
    direction = 1 if score > 0 else -1

    # In high-volatility archetypes, widen stops to avoid whipsaws
    if archetype in ("VOLATILITY_SPIKE", "BEAR_COLLAPSE"):
        stop_mult, tp_mult = 2.0, 3.0
    elif archetype in ("LATE_BULL", "REGIME_FLIP"):
        stop_mult, tp_mult = 1.2, 2.0
    else:
        stop_mult, tp_mult = 1.5, 2.5

    stop_loss   = price - direction * stop_mult * atr
    take_profit = price + direction * tp_mult * atr
    invalidation = row.get("sma_50", price * 0.95)

    vol = row.get("realised_vol", 0.20)

    # Archetype also affects position sizing
    archetype_size_scalar = {
        "EARLY_BULL": 1.0, "TRENDING_MOMENTUM": 1.0,
        "LATE_BULL": 0.7, "RECOVERY": 0.8,
        "SIDEWAYS_GRIND": 0.9, "REGIME_FLIP": 0.6,
        "RISK_OFF_FLIGHT": 0.5, "BEAR_RELIEF_RALLY": 0.4,
        "BEAR_COLLAPSE": 0.3, "VOLATILITY_SPIKE": 0.25,
    }.get(archetype, 0.8)

    base_risk = 0.02
    vol_scalar = min(0.20 / max(vol, 0.05), 1.0)
    position_size_pct = base_risk * vol_scalar * archetype_size_scalar * 100

    return stop_loss, take_profit, invalidation, atr_pct, position_size_pct


# ===========================================================================
# Explainability (drivers/risks — retained for API compatibility)
# ===========================================================================

def build_drivers_and_risks(row: pd.Series, score: float) -> tuple:
    drivers, risks = [], []

    if "dist_sma_20" in row and pd.notna(row["dist_sma_20"]):
        if row["dist_sma_20"] > 0.02:
            drivers.append("Price above 20-day SMA — short-term trend positive")
        elif row["dist_sma_20"] < -0.02:
            risks.append("Price below 20-day SMA — short-term trend negative")

    if "dist_sma_200" in row and pd.notna(row["dist_sma_200"]):
        if row["dist_sma_200"] > 0:
            drivers.append("Price above 200-day SMA — long-term bull trend intact")
        else:
            risks.append("Price below 200-day SMA — long-term trend bearish")

    if "rsi" in row and pd.notna(row["rsi"]):
        rsi = row["rsi"]
        if rsi > 70:
            risks.append(f"RSI {rsi:.0f} — overbought, watch for pullback")
        elif rsi < 30:
            drivers.append(f"RSI {rsi:.0f} — oversold, mean-reversion opportunity")
        elif rsi > 55:
            drivers.append(f"RSI {rsi:.0f} — momentum positive")
        elif rsi < 45:
            risks.append(f"RSI {rsi:.0f} — momentum weakening")

    if "macd_histogram" in row and pd.notna(row["macd_histogram"]):
        if row["macd_histogram"] > 0:
            drivers.append("MACD histogram positive — upward momentum building")
        else:
            risks.append("MACD histogram negative — downward momentum present")

    if row.get("bull_regime", 0) == 1:
        drivers.append("Confirmed bull regime")
    elif row.get("bear_regime", 0) == 1:
        risks.append("Confirmed bear regime — caution advised")

    vol = row.get("realised_vol", np.nan)
    if pd.notna(vol):
        if vol > 0.50:
            risks.append(f"Very high volatility ({vol:.0%}) — reduce size significantly")
        elif vol > 0.30:
            risks.append(f"Elevated volatility ({vol:.0%}) — size conservatively")
        elif vol < 0.10:
            drivers.append(f"Low volatility ({vol:.0%}) — favourable for trend-following")

    if "bb_pct_b" in row and pd.notna(row["bb_pct_b"]):
        pct_b = row["bb_pct_b"]
        if pct_b > 0.9:
            risks.append("Price near upper Bollinger Band — stretched upside")
        elif pct_b < 0.1:
            drivers.append("Price near lower Bollinger Band — mean reversion zone")

    return drivers, risks


# ===========================================================================
# MAIN SIGNAL BUILDER
# ===========================================================================

def build_signal(
    ticker:      str,
    df:          pd.DataFrame,
    meta:        Dict,
    asset_class: str,
) -> Optional[AssetSignal]:
    """
    Build a situation-aware AssetSignal from feature data.

    Process (mirrors how a human trader thinks):
      1. Classify the situation — what kind of market is this?
      2. Select weights appropriate for this situation
      3. Compute all sub-scores
      4. Detect contradictions before committing to a view
      5. Apply conflict penalty to composite score
      6. Build a trade thesis — the WHY, not just the WHAT
      7. Return full signal with all context
    """
    if df is None or df.empty or len(df) < 60:
        logger.warning(f"Insufficient data for {ticker}")
        return None

    row = df.iloc[-1]
    price = float(row.get("Close", np.nan))
    if np.isnan(price):
        return None

    vol = float(row.get("realised_vol", 0.20))
    beta = float(row.get("rolling_beta", 1.0)) if pd.notna(row.get("rolling_beta")) else 1.0

    # ── Step 1: Classify situation ────────────────────────────────────────
    archetype = classify_situation(row, vol)
    weights = ARCHETYPE_WEIGHTS[archetype]

    # ── Step 2: Compute sub-scores ────────────────────────────────────────
    trend_s     = compute_trend_score(row)
    momentum_s  = compute_momentum_score(row)
    vol_s       = compute_volatility_score(row)
    regime_s    = compute_regime_score(row)
    macro_s     = compute_macro_score(row, asset_class)
    sentiment_s = compute_sentiment_score(row)

    # ── Step 3: Detect conflicts ──────────────────────────────────────────
    conflicts, conflict_penalty = detect_conflicts(
        trend_s, momentum_s, vol_s, regime_s, macro_s, archetype
    )

    # ── Step 4: Compute composite with situation weights ──────────────────
    raw_composite = compute_composite_score(
        trend_s, momentum_s, vol_s, regime_s, macro_s, sentiment_s, weights
    )

    # Apply conflict penalty — contradictions reduce the signal strength
    direction_sign = np.sign(raw_composite) if raw_composite != 0 else 1
    composite = _clamp(raw_composite - direction_sign * conflict_penalty)

    # ── Step 5: Risk params ───────────────────────────────────────────────
    stop_loss, take_profit, invalidation_level, atr_pct, pos_size = compute_risk_params(
        row, price, composite, archetype
    )
    risk_level = score_to_risk_level(atr_pct, vol, beta)

    # ── Step 6: Signal labels ─────────────────────────────────────────────
    action = score_to_action(composite)
    n_conflicts = len(conflicts)
    high_conflicts = sum(1 for c in conflicts if c.severity == "HIGH")
    confidence = score_to_confidence(composite, vol, n_conflicts, high_conflicts)
    bull_prob, bear_prob = score_to_probabilities(composite)
    regime = identify_regime(row)

    # ── Step 7: Trade thesis ──────────────────────────────────────────────
    thesis = build_trade_thesis(
        ticker, row, composite, archetype, conflicts, price, stop_loss, take_profit
    )

    # ── Step 8: Legacy drivers/risks ──────────────────────────────────────
    drivers, risks = build_drivers_and_risks(row, composite)

    # 52-week range
    recent_252 = df["Close"].iloc[-252:] if len(df) >= 252 else df["Close"]
    high_52w = float(recent_252.max())
    low_52w  = float(recent_252.min())

    # Explanation — now includes situation context
    weight_summary = " | ".join(
        f"{k}: {v*100:.0f}%" for k, v in weights.items() if v > 0.08
    )
    explanation = (
        f"[{archetype}] {ticker} scores {composite:.1f} ({action}) — "
        f"confidence: {confidence}. "
        f"Active weights: {weight_summary}. "
        f"{len(conflicts)} signal conflict(s) detected"
        + (f" ({high_conflicts} HIGH)" if high_conflicts else "") + ". "
        f"Archetype: {ARCHETYPES[archetype]}"
    )

    return AssetSignal(
        ticker=ticker,
        name=meta.get("name", ticker),
        asset_class=asset_class,
        current_price=price,
        price_52w_high=high_52w,
        price_52w_low=low_52w,
        composite_score=round(composite, 2),
        action=action,
        confidence=confidence,
        bullish_prob=round(bull_prob, 3),
        bearish_prob=round(bear_prob, 3),
        time_horizon="1 week to 1 month",
        trend_score=round(trend_s, 2),
        momentum_score=round(momentum_s, 2),
        volatility_score=round(vol_s, 2),
        regime_score=round(regime_s, 2),
        macro_score=round(macro_s, 2),
        sentiment_score=round(sentiment_s, 2),
        risk_level=risk_level,
        atr_pct=round(atr_pct, 4),
        realised_vol=round(vol, 4),
        stop_loss=round(stop_loss, 4),
        take_profit=round(take_profit, 4),
        invalidation=round(invalidation_level, 4),
        position_size_pct=round(pos_size, 2),
        regime=regime,
        situation_archetype=archetype,
        archetype_description=ARCHETYPES[archetype],
        active_weights=weights,
        conflict_flags=conflicts,
        conflict_penalty=round(conflict_penalty, 2),
        trade_thesis=thesis,
        drivers=drivers,
        risks=risks,
        explanation=explanation,
    )


def build_all_signals(
    featured_data: Dict[str, pd.DataFrame],
    metadata:      Dict[str, Dict],
) -> Dict[str, AssetSignal]:
    """Generate signals for all assets in the universe."""
    signals: Dict[str, AssetSignal] = {}
    for ticker, df in featured_data.items():
        meta = metadata.get(ticker, {"name": ticker, "asset_class": "unknown"})
        sig = build_signal(ticker, df, meta, meta.get("asset_class", "unknown"))
        if sig is not None:
            signals[ticker] = sig
        else:
            logger.warning(f"No signal generated for {ticker}")
    logger.info(f"Generated signals for {len(signals)} / {len(featured_data)} assets")
    return signals


def get_top_signals(
    signals: Dict[str, AssetSignal],
    n: int = 5,
    direction: str = "bullish",
) -> List[AssetSignal]:
    sorted_signals = sorted(
        signals.values(),
        key=lambda s: s.composite_score,
        reverse=(direction == "bullish"),
    )
    return sorted_signals[:n]
