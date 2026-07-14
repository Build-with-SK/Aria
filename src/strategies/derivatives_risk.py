# src/strategies/derivatives_risk.py
"""
Derivatives Risk Engine
Portfolio-level Greeks aggregation, scenario P&L matrix,
VaR / CVaR (historical simulation), correlation stress.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class NetGreeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    def _to_json(self) -> dict:
        return {k: round(v, 6) for k, v in vars(self).items()}


@dataclass
class ScenarioPnL:
    scenario: str
    shock_description: str
    estimated_pnl: float
    pnl_pct: float          # % of portfolio NAV

    def _to_json(self) -> dict:
        return {
            "scenario": self.scenario,
            "shock_description": self.shock_description,
            "estimated_pnl": round(self.estimated_pnl, 2),
            "pnl_pct": round(self.pnl_pct, 4),
        }


@dataclass
class DerivativesRiskReport:
    net_greeks: NetGreeks
    scenario_pnl: List[ScenarioPnL]
    var_1d_99: float            # 1-day 99% VaR ($)
    var_10d_99: float           # 10-day 99% VaR ($)
    cvar_1d_99: float           # Expected Shortfall
    var_pct_nav: float          # VaR as % of portfolio
    correlation_stress_loss: float
    portfolio_nav: float
    positions_analysed: int
    notes: List[str]

    def _to_json(self) -> dict:
        return {
            "net_greeks": self.net_greeks._to_json(),
            "scenario_pnl": [s._to_json() for s in self.scenario_pnl],
            "var_1d_99": round(self.var_1d_99, 2),
            "var_10d_99": round(self.var_10d_99, 2),
            "cvar_1d_99": round(self.cvar_1d_99, 2),
            "var_pct_nav": round(self.var_pct_nav, 4),
            "correlation_stress_loss": round(self.correlation_stress_loss, 2),
            "portfolio_nav": round(self.portfolio_nav, 2),
            "positions_analysed": self.positions_analysed,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Greeks aggregation from options signals
# ---------------------------------------------------------------------------

def _aggregate_greeks(options_signals: List) -> NetGreeks:
    """Sum Greeks across all options positions."""
    net_delta = net_gamma = net_theta = net_vega = net_rho = 0.0
    for sig in options_signals:
        try:
            g = sig.net_greeks
            # Sign: long position = positive, short = negative (strategies vary)
            sign = -1 if sig.strategy in ("covered_call", "csp", "iron_condor") else 1
            net_delta += g.delta * sign
            net_gamma += g.gamma * sign
            net_theta += g.theta * sign
            net_vega  += g.vega  * sign
            net_rho   += g.rho   * sign
        except Exception:
            continue
    return NetGreeks(
        delta=round(net_delta, 4),
        gamma=round(net_gamma, 6),
        theta=round(net_theta, 4),
        vega=round(net_vega, 4),
        rho=round(net_rho, 4),
    )


# ---------------------------------------------------------------------------
# Scenario analysis
# ---------------------------------------------------------------------------

def _scenario_pnl(
    portfolio_nav: float,
    net_greeks: NetGreeks,
    featured_data: Dict[str, pd.DataFrame],
) -> List[ScenarioPnL]:
    """
    Estimate P&L under standard macro shocks.
    Uses delta/vega/rho to price shocks linearly (first-order approximation).
    Also applies direct return shock to underlying equity book.
    """
    scenarios = []

    # Equity position notional (long - short, crude estimate)
    equity_nav = portfolio_nav * 0.50  # assume 50% equity allocation

    def pnl(equity_shock_pct: float, vix_shock_pct: float, rate_shock_bps: float,
            usd_shock_pct: float, description: str, label: str) -> ScenarioPnL:

        # Equity P&L: direct position + delta of options
        eq_pnl = equity_nav * equity_shock_pct
        delta_pnl = net_greeks.delta * equity_nav * equity_shock_pct

        # Vega P&L: vix shock → vol shock (assume 1% VIX = 0.5% IV change)
        vega_pnl = net_greeks.vega * vix_shock_pct * 0.5

        # Rho P&L: rate shock
        rho_pnl = net_greeks.rho * (rate_shock_bps / 100.0)

        # USD shock: affects FX pairs ~25% allocation
        fx_nav = portfolio_nav * 0.10
        fx_pnl = fx_nav * usd_shock_pct

        total = eq_pnl + delta_pnl + vega_pnl + rho_pnl + fx_pnl
        pct = total / portfolio_nav if portfolio_nav > 0 else 0.0

        return ScenarioPnL(
            scenario=label,
            shock_description=description,
            estimated_pnl=round(float(total), 2),
            pnl_pct=round(float(pct), 4),
        )

    # Define scenarios
    scenarios_def = [
        (-0.10, 0.50, 0,    0.0,  "Equity -10%, VIX +50%",          "equity_down_10"),
        (-0.20, 1.00, 0,    0.0,  "Equity -20%, VIX +100%",         "equity_down_20"),
        (-0.30, 1.50, 0,    0.0,  "Equity -30%, VIX +150% (crash)", "equity_down_30"),
        (0.0,   0.50, 0,    0.0,  "VIX +50% (vol spike)",           "vix_spike_50"),
        (0.0,   1.00, 0,    0.0,  "VIX +100% (vol shock)",          "vix_spike_100"),
        (-0.05, 0.20, 100,  0.0,  "Rates +100bps",                  "rates_up_100bps"),
        (-0.08, 0.30, 200,  0.0,  "Rates +200bps",                  "rates_up_200bps"),
        (0.0,   0.0,  0,    0.05, "USD +5% (dollar surge)",         "usd_up_5"),
        (0.0,   0.0,  0,   -0.05, "USD -5% (dollar weakness)",      "usd_down_5"),
        (0.10,  -0.20, -50, 0.0,  "Risk-on: Equity +10%, VIX -20%", "risk_on"),
    ]

    for args in scenarios_def:
        scenarios.append(pnl(*args))

    return scenarios


# ---------------------------------------------------------------------------
# Historical VaR
# ---------------------------------------------------------------------------

def _compute_var(
    featured_data: Dict[str, pd.DataFrame],
    portfolio_nav: float,
    confidence: float = 0.99,
) -> tuple:
    """
    Historical simulation VaR.
    Builds equal-weight portfolio return series from all assets.
    Returns (var_1d, var_10d, cvar_1d).
    """
    try:
        returns_list = []
        for ticker, df in featured_data.items():
            if df is None or df.empty or len(df) < 252:
                continue
            ret = df["Close"].pct_change().dropna()
            returns_list.append(ret.tail(504))   # ~2 years

        if not returns_list:
            return 0.0, 0.0, 0.0

        # Align and equal-weight
        combined = pd.concat(returns_list, axis=1).dropna()
        portfolio_ret = combined.mean(axis=1)   # equal-weight

        # 1-day VaR
        var_1d_pct = float(np.percentile(portfolio_ret, (1 - confidence) * 100))
        var_1d = abs(var_1d_pct) * portfolio_nav

        # 10-day VaR (square root of time scaling)
        var_10d = var_1d * np.sqrt(10)

        # CVaR (Expected Shortfall): mean of losses beyond VaR threshold
        tail = portfolio_ret[portfolio_ret <= var_1d_pct]
        cvar_1d_pct = float(tail.mean()) if len(tail) > 0 else var_1d_pct * 1.3
        cvar_1d = abs(cvar_1d_pct) * portfolio_nav

        return round(float(var_1d), 2), round(float(var_10d), 2), round(float(cvar_1d), 2)

    except Exception as e:
        logger.debug(f"VaR computation failed: {e}")
        return 0.0, 0.0, 0.0


# ---------------------------------------------------------------------------
# Correlation stress
# ---------------------------------------------------------------------------

def _correlation_stress_loss(
    featured_data: Dict[str, pd.DataFrame],
    portfolio_nav: float,
    position_weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    Estimate loss when all pairwise correlations → 1.
    Under perfect correlation, portfolio vol = weighted sum of individual vols
    (worst case — diversification benefit disappears).
    """
    try:
        vols = []
        weights = []
        for ticker, df in featured_data.items():
            if df is None or df.empty or len(df) < 60:
                continue
            vol = float(df["Close"].pct_change().dropna().tail(60).std())
            w = float((position_weights or {}).get(ticker, 1.0 / max(len(featured_data), 1)))
            vols.append(vol)
            weights.append(w)

        if not vols:
            return 0.0

        # Normal portfolio vol (with diversification)
        weights_arr = np.array(weights)
        weights_arr = weights_arr / weights_arr.sum()
        vols_arr = np.array(vols)

        normal_vol = float(np.sqrt(np.sum((weights_arr * vols_arr) ** 2)))

        # Stress vol (all correlations = 1)
        stress_vol = float(np.dot(weights_arr, vols_arr))

        diversification_benefit = stress_vol - normal_vol
        # 1-day 99% loss under stressed correlation (2.33 sigma)
        stress_loss = diversification_benefit * 2.33 * portfolio_nav

        return round(float(stress_loss), 2)

    except Exception as e:
        logger.debug(f"Correlation stress failed: {e}")
        return 0.0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_derivatives_risk(
    featured_data: Dict[str, pd.DataFrame],
    config: dict,
    options_signals: Optional[List] = None,
    position_weights: Optional[Dict[str, float]] = None,
) -> DerivativesRiskReport:
    """
    Build full derivatives risk report:
    - Net Greeks from options positions
    - Scenario P&L matrix
    - Historical VaR / CVaR
    - Correlation stress loss
    """
    notes: List[str] = []
    portfolio_nav = float(config.get("backtest", {}).get("initial_capital", 100_000))

    # Greeks
    if options_signals:
        net_greeks = _aggregate_greeks(options_signals)
        notes.append(f"Aggregated Greeks from {len(options_signals)} options positions.")
    else:
        net_greeks = NetGreeks(0, 0, 0, 0, 0)
        notes.append("No options signals provided — Greeks are zero.")

    # Scenario P&L
    scenarios = _scenario_pnl(portfolio_nav, net_greeks, featured_data)
    worst = min(scenarios, key=lambda s: s.estimated_pnl)
    notes.append(f"Worst scenario: {worst.scenario} → ${worst.estimated_pnl:,.0f} ({worst.pnl_pct*100:.1f}%)")

    # VaR
    var_1d, var_10d, cvar_1d = _compute_var(featured_data, portfolio_nav)
    var_pct = var_1d / portfolio_nav if portfolio_nav > 0 else 0.0
    notes.append(f"1d 99% VaR: ${var_1d:,.0f} ({var_pct*100:.2f}% NAV) | 10d: ${var_10d:,.0f} | CVaR: ${cvar_1d:,.0f}")

    # Correlation stress
    corr_stress = _correlation_stress_loss(featured_data, portfolio_nav, position_weights)
    notes.append(f"Correlation stress loss (all ρ→1): ${corr_stress:,.0f}")

    n_pos = len([t for t, df in featured_data.items() if df is not None and not df.empty])

    return DerivativesRiskReport(
        net_greeks=net_greeks,
        scenario_pnl=scenarios,
        var_1d_99=var_1d,
        var_10d_99=var_10d,
        cvar_1d_99=cvar_1d,
        var_pct_nav=round(var_pct, 6),
        correlation_stress_loss=corr_stress,
        portfolio_nav=portfolio_nav,
        positions_analysed=n_pos,
        notes=notes,
    )
