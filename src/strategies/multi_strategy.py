# src/strategies/multi_strategy.py
"""
Multi-Strategy Allocator
Combines signals from all strategy modules.
Risk-parity weighting (default), equal-weight, max-Sharpe modes.
Correlation penalty for highly correlated strategies.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StrategyAllocation:
    strategy_name: str
    raw_score: float            # strategy's own score [-100, +100]
    weight: float               # portfolio weight (0-1)
    weighted_score: float       # weight * raw_score
    realised_vol: float         # estimated strategy vol
    sharpe_estimate: float
    correlation_penalty: float  # reduction due to high cross-correlation
    final_weight: float         # after correlation adjustment


@dataclass
class MultiStrategyAllocation:
    allocations: List[StrategyAllocation]
    weighting_mode: str         # "risk_parity" | "equal_weight" | "max_sharpe"
    combined_score: float       # weighted aggregate of all strategy scores
    strategy_weights: Dict[str, float]
    correlation_matrix: Dict[str, Dict[str, float]]
    estimated_portfolio_sharpe: float
    estimated_portfolio_vol: float
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "allocations": [vars(a) for a in self.allocations],
            "weighting_mode": self.weighting_mode,
            "combined_score": round(self.combined_score, 2),
            "strategy_weights": {k: round(v, 4) for k, v in self.strategy_weights.items()},
            "correlation_matrix": {
                k: {kk: round(vv, 3) for kk, vv in v.items()}
                for k, v in self.correlation_matrix.items()
            },
            "estimated_portfolio_sharpe": round(self.estimated_portfolio_sharpe, 3),
            "estimated_portfolio_vol": round(self.estimated_portfolio_vol, 4),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Strategy metadata: expected vol and Sharpe for risk-parity seeding
# ---------------------------------------------------------------------------
_STRATEGY_METADATA: Dict[str, dict] = {
    "equity_long_short": {"base_vol": 0.12, "base_sharpe": 0.8,  "asset_class": "equity"},
    "relative_value":    {"base_vol": 0.06, "base_sharpe": 1.2,  "asset_class": "arb"},
    "market_neutral":    {"base_vol": 0.08, "base_sharpe": 1.0,  "asset_class": "equity"},
    "global_macro":      {"base_vol": 0.10, "base_sharpe": 0.7,  "asset_class": "macro"},
    "event_driven":      {"base_vol": 0.09, "base_sharpe": 0.9,  "asset_class": "equity"},
    "distressed":        {"base_vol": 0.14, "base_sharpe": 0.6,  "asset_class": "credit"},
    "convertible_arb":   {"base_vol": 0.05, "base_sharpe": 1.1,  "asset_class": "arb"},
    "quant_systematic":  {"base_vol": 0.10, "base_sharpe": 0.9,  "asset_class": "equity"},
    "credit_long_short": {"base_vol": 0.07, "base_sharpe": 0.8,  "asset_class": "credit"},
    "options":           {"base_vol": 0.08, "base_sharpe": 1.0,  "asset_class": "derivatives"},
    "futures":           {"base_vol": 0.12, "base_sharpe": 0.7,  "asset_class": "futures"},
    "crypto":            {"base_vol": 0.45, "base_sharpe": 0.5,  "asset_class": "crypto"},
    "forex":             {"base_vol": 0.06, "base_sharpe": 0.6,  "asset_class": "forex"},
    "derivatives_risk":  {"base_vol": 0.08, "base_sharpe": 0.5,  "asset_class": "derivatives"},
}

# Cross-strategy correlation estimates (symmetric, used for penalty)
_STRATEGY_CORRELATIONS: Dict[str, Dict[str, float]] = {
    "equity_long_short": {"market_neutral": 0.3,  "quant_systematic": 0.5, "event_driven": 0.4, "distressed": 0.35},
    "relative_value":    {"convertible_arb": 0.4,  "credit_long_short": 0.3},
    "global_macro":      {"forex": 0.5,             "futures": 0.4,          "credit_long_short": 0.3},
    "futures":           {"global_macro": 0.4,      "crypto": 0.2},
    "crypto":            {"futures": 0.2},
    "forex":             {"global_macro": 0.5,      "futures": 0.3},
    "credit_long_short": {"relative_value": 0.3,   "distressed": 0.4},
}


def _get_correlation(s1: str, s2: str) -> float:
    """Get estimated pairwise strategy correlation."""
    c = _STRATEGY_CORRELATIONS.get(s1, {}).get(s2)
    if c is not None:
        return c
    c = _STRATEGY_CORRELATIONS.get(s2, {}).get(s1)
    if c is not None:
        return c
    return 0.05  # default low correlation


def _risk_parity_weights(strategy_vols: Dict[str, float]) -> Dict[str, float]:
    """Inverse volatility weighting (risk parity)."""
    inv_vols = {k: 1.0 / max(v, 0.01) for k, v in strategy_vols.items()}
    total = sum(inv_vols.values())
    return {k: v / total for k, v in inv_vols.items()}


def _equal_weights(strategies: List[str]) -> Dict[str, float]:
    n = max(len(strategies), 1)
    return {s: 1.0 / n for s in strategies}


def _max_sharpe_weights(
    strategy_scores: Dict[str, float],
    strategy_vols: Dict[str, float],
) -> Dict[str, float]:
    """Simplified max-Sharpe: weight by Sharpe estimate (score / vol)."""
    sharpes = {}
    for s, score in strategy_scores.items():
        vol = strategy_vols.get(s, 0.10)
        sharpes[s] = max(score / (vol * 100 + 1e-6), 0.0)
    total = sum(sharpes.values())
    if total == 0:
        return _equal_weights(list(strategy_scores.keys()))
    return {k: v / total for k, v in sharpes.items()}


def _correlation_penalty(strategy: str, all_strategies: List[str], weights: Dict[str, float]) -> float:
    """
    Penalty factor for a strategy that is highly correlated with others.
    Returns a multiplier (1.0 = no penalty, 0.5 = 50% reduction).
    """
    total_corr = 0.0
    for other in all_strategies:
        if other == strategy:
            continue
        corr = _get_correlation(strategy, other)
        w_other = weights.get(other, 0.0)
        total_corr += corr * w_other
    # Convert to penalty: 100% corr with equal weight → 0.5 multiplier
    penalty = max(0.5, 1.0 - total_corr * 0.5)
    return round(float(penalty), 4)


def _build_correlation_matrix(strategies: List[str]) -> Dict[str, Dict[str, float]]:
    matrix = {}
    for s1 in strategies:
        matrix[s1] = {}
        for s2 in strategies:
            matrix[s1][s2] = 1.0 if s1 == s2 else _get_correlation(s1, s2)
    return matrix


def run_multi_strategy(
    strategy_scores: Dict[str, float],
    config: dict,
    strategy_vols: Optional[Dict[str, float]] = None,
) -> MultiStrategyAllocation:
    """
    Aggregate all strategy scores into a weighted multi-strategy allocation.

    strategy_scores: {strategy_name: score [-100, +100]}
    strategy_vols: {strategy_name: realised_vol} — if None, uses metadata defaults
    """
    cfg = config.get("strategies", {}).get("multi_strategy", {})
    mode = str(cfg.get("weighting_mode", "risk_parity"))
    notes: List[str] = []

    strategies = list(strategy_scores.keys())
    if not strategies:
        notes.append("No strategies provided to allocator.")
        return MultiStrategyAllocation(
            allocations=[], weighting_mode=mode,
            combined_score=0.0, strategy_weights={},
            correlation_matrix={}, estimated_portfolio_sharpe=0.0,
            estimated_portfolio_vol=0.0, notes=notes,
        )

    # Strategy vols: use provided or fall back to metadata defaults
    s_vols: Dict[str, float] = {}
    for s in strategies:
        if strategy_vols and s in strategy_vols:
            s_vols[s] = strategy_vols[s]
        else:
            s_vols[s] = _STRATEGY_METADATA.get(s, {}).get("base_vol", 0.10)

    # Raw weights by mode
    if mode == "risk_parity":
        raw_weights = _risk_parity_weights(s_vols)
    elif mode == "equal_weight":
        raw_weights = _equal_weights(strategies)
    elif mode == "max_sharpe":
        raw_weights = _max_sharpe_weights(strategy_scores, s_vols)
    else:
        raw_weights = _risk_parity_weights(s_vols)

    notes.append(f"Weighting mode: {mode}")

    # Correlation adjustment
    final_weights: Dict[str, float] = {}
    corr_penalties: Dict[str, float] = {}
    for s in strategies:
        penalty = _correlation_penalty(s, strategies, raw_weights)
        corr_penalties[s] = penalty
        final_weights[s] = raw_weights[s] * penalty

    # Re-normalise after penalty
    total_w = sum(final_weights.values())
    if total_w > 0:
        final_weights = {k: v / total_w for k, v in final_weights.items()}

    # Sharpe estimates
    strategy_sharpes: Dict[str, float] = {}
    for s in strategies:
        score = strategy_scores.get(s, 0.0)
        vol = s_vols.get(s, 0.10)
        meta_sharpe = _STRATEGY_METADATA.get(s, {}).get("base_sharpe", 0.7)
        # Blend: if score positive, use meta Sharpe scaled by score conviction
        conviction_scale = abs(score) / 100.0
        strategy_sharpes[s] = round(float(meta_sharpe * conviction_scale * np.sign(score + 1e-9)), 3)

    # Build allocations
    allocations: List[StrategyAllocation] = []
    for s in strategies:
        raw_w = raw_weights.get(s, 0)
        final_w = final_weights.get(s, 0)
        score = strategy_scores.get(s, 0)
        alloc = StrategyAllocation(
            strategy_name=s,
            raw_score=round(score, 2),
            weight=round(raw_w, 4),
            weighted_score=round(score * raw_w, 4),
            realised_vol=round(s_vols.get(s, 0.10), 4),
            sharpe_estimate=strategy_sharpes.get(s, 0.0),
            correlation_penalty=round(corr_penalties.get(s, 1.0), 4),
            final_weight=round(final_w, 4),
        )
        allocations.append(alloc)

    allocations.sort(key=lambda a: abs(a.final_weight), reverse=True)

    # Combined score: weighted sum
    combined = float(np.clip(
        sum(a.raw_score * a.final_weight for a in allocations),
        -100, 100
    ))

    # Portfolio-level vol estimate (simplified: sum of weighted vols, partial correlation)
    corr_matrix_full = _build_correlation_matrix(strategies)
    w_arr = np.array([final_weights.get(s, 0) for s in strategies])
    vol_arr = np.array([s_vols.get(s, 0.10) for s in strategies])

    # Build corr matrix array
    corr_arr = np.array([
        [_get_correlation(s1, s2) if s1 != s2 else 1.0 for s2 in strategies]
        for s1 in strategies
    ])
    cov_arr = np.outer(vol_arr, vol_arr) * corr_arr
    port_var = float(w_arr @ cov_arr @ w_arr)
    port_vol = float(np.sqrt(max(port_var, 0)))

    # Portfolio Sharpe estimate
    weighted_sharpe = float(sum(a.sharpe_estimate * a.final_weight for a in allocations))

    high_corr_pairs = []
    for i, s1 in enumerate(strategies):
        for j, s2 in enumerate(strategies):
            if j <= i:
                continue
            c = _get_correlation(s1, s2)
            if c > 0.40:
                high_corr_pairs.append(f"{s1}/{s2}={c:.2f}")

    if high_corr_pairs:
        notes.append(f"High cross-strategy correlations: {', '.join(high_corr_pairs)}")
    notes.append(f"Combined score: {combined:.1f} | Portfolio vol: {port_vol*100:.1f}% | Est. Sharpe: {weighted_sharpe:.2f}")

    return MultiStrategyAllocation(
        allocations=allocations,
        weighting_mode=mode,
        combined_score=round(combined, 2),
        strategy_weights={s: round(final_weights.get(s, 0), 4) for s in strategies},
        correlation_matrix=corr_matrix_full,
        estimated_portfolio_sharpe=round(weighted_sharpe, 3),
        estimated_portfolio_vol=round(port_vol, 4),
        notes=notes,
    )
