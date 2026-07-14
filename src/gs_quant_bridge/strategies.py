"""
strategies.py — GS-Quant Bridge: Multi-Leg Derivatives Strategies
================================================================
Analytics for common option strategies (spreads, straddles, condors) built
on the standalone Black-Scholes engine. No GS API, no external data — pure
math. Each strategy returns net Greeks, max profit/loss, breakevens, and a
payoff curve ARIA can reason over or the dashboard can plot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .options_analytics import BlackScholes


@dataclass
class Leg:
    option_type: str      # "call" | "put"
    strike: float
    qty: int              # +1 long, -1 short (contracts)
    entry_price: float = 0.0   # theoretical BS price at build time


@dataclass
class StrategyResult:
    name: str
    legs: List[dict]
    net_debit_credit: float          # negative = net credit received
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float
    max_profit: Optional[float]
    max_loss: Optional[float]
    breakevens: List[float]
    payoff_curve: List[tuple] = field(default_factory=list)  # [(spot, pnl)]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        f = lambda x: None if x is None else float(round(float(x), 4))
        return {
            "name": self.name,
            "legs": [{k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                      for k, v in leg.items()} for leg in self.legs],
            "net_debit_credit": f(self.net_debit_credit),
            "net_greeks": {
                "delta": f(self.net_delta), "gamma": f(self.net_gamma),
                "theta": f(self.net_theta), "vega": f(self.net_vega),
            },
            "max_profit": f(self.max_profit),
            "max_loss": f(self.max_loss),
            "breakevens": [f(b) for b in self.breakevens],
            "payoff_curve": [[f(s), f(p)] for s, p in self.payoff_curve],
            "notes": self.notes,
        }


class StrategyEngine:
    """Prices multi-leg option strategies and computes their risk profile."""

    def __init__(self, spot: float, time_to_expiry: float,
                 risk_free_rate: float = 0.05, iv: float = 0.25,
                 contract_multiplier: int = 100):
        self.S = spot
        self.T = max(time_to_expiry, 1e-6)
        self.r = risk_free_rate
        self.iv = iv
        self.mult = contract_multiplier

    # ── leg pricing ──────────────────────────────────────────────────────

    def _price_leg(self, leg: Leg) -> Leg:
        g = BlackScholes(self.S, leg.strike, self.T, self.r, self.iv).price_and_greeks(leg.option_type)
        leg.entry_price = g.price
        return leg

    def _net_greeks(self, legs: List[Leg]) -> dict:
        d = g = t = v = 0.0
        for leg in legs:
            gr = BlackScholes(self.S, leg.strike, self.T, self.r, self.iv).price_and_greeks(leg.option_type)
            d += gr.delta * leg.qty * self.mult
            g += gr.gamma * leg.qty * self.mult
            t += gr.theta * leg.qty * self.mult
            v += gr.vega * leg.qty * self.mult
        return {"delta": d, "gamma": g, "theta": t, "vega": v}

    @staticmethod
    def _leg_payoff(leg: Leg, spot_at_expiry: float) -> float:
        if leg.option_type == "call":
            intrinsic = max(0.0, spot_at_expiry - leg.strike)
        else:
            intrinsic = max(0.0, leg.strike - spot_at_expiry)
        return (intrinsic - leg.entry_price) * leg.qty

    def _build(self, name: str, legs: List[Leg], notes: List[str]) -> StrategyResult:
        legs = [self._price_leg(l) for l in legs]
        net_cost = sum(l.entry_price * l.qty for l in legs)   # +debit / -credit, per share
        greeks = self._net_greeks(legs)

        # payoff curve ±40% around spot
        lo, hi = self.S * 0.6, self.S * 1.4
        steps = 81
        curve = []
        for i in range(steps):
            s = lo + (hi - lo) * i / (steps - 1)
            pnl = sum(self._leg_payoff(l, s) for l in legs) * self.mult
            curve.append((s, pnl))

        pnls = [p for _, p in curve]
        max_profit = max(pnls)
        max_loss = min(pnls)
        # breakevens: sign changes along the curve
        breakevens = []
        for i in range(1, len(curve)):
            p0, p1 = curve[i - 1][1], curve[i][1]
            if (p0 <= 0 <= p1) or (p0 >= 0 >= p1):
                s0, s1 = curve[i - 1][0], curve[i][0]
                if p1 != p0:
                    breakevens.append(s0 + (s1 - s0) * (-p0) / (p1 - p0))

        return StrategyResult(
            name=name,
            legs=[{"type": l.option_type, "strike": round(l.strike, 2),
                   "qty": l.qty, "price": round(l.entry_price, 2)} for l in legs],
            net_debit_credit=net_cost * self.mult,
            net_delta=greeks["delta"], net_gamma=greeks["gamma"],
            net_theta=greeks["theta"], net_vega=greeks["vega"],
            max_profit=max_profit, max_loss=max_loss,
            breakevens=sorted(set(round(b, 2) for b in breakevens)),
            payoff_curve=curve, notes=notes,
        )

    # ── strategy definitions ─────────────────────────────────────────────

    def bull_call_spread(self, lower_strike=None, upper_strike=None) -> StrategyResult:
        lo = lower_strike or round(self.S)
        hi = upper_strike or round(self.S * 1.05)
        return self._build("Bull Call Spread",
            [Leg("call", lo, +1), Leg("call", hi, -1)],
            ["Bullish, defined risk. Max gain if spot ≥ upper strike at expiry.",
             "Net debit strategy — you pay to enter.",
             "Lower cost than a naked long call; caps upside in exchange."])

    def bear_put_spread(self, upper_strike=None, lower_strike=None) -> StrategyResult:
        hi = upper_strike or round(self.S)
        lo = lower_strike or round(self.S * 0.95)
        return self._build("Bear Put Spread",
            [Leg("put", hi, +1), Leg("put", lo, -1)],
            ["Bearish, defined risk. Max gain if spot ≤ lower strike at expiry.",
             "Net debit — profits as the underlying falls."])

    def long_straddle(self, strike=None) -> StrategyResult:
        k = strike or round(self.S)
        return self._build("Long Straddle",
            [Leg("call", k, +1), Leg("put", k, +1)],
            ["Long volatility — profits from a big move in EITHER direction.",
             "Loses if the underlying stays near the strike (theta bleed).",
             "Best before an expected catalyst (earnings, event)."])

    def iron_condor(self, wing=None) -> StrategyResult:
        w = wing or self.S * 0.05
        put_short = round(self.S - w)
        put_long = round(self.S - 2 * w)
        call_short = round(self.S + w)
        call_long = round(self.S + 2 * w)
        return self._build("Iron Condor",
            [Leg("put", put_long, +1), Leg("put", put_short, -1),
             Leg("call", call_short, -1), Leg("call", call_long, +1)],
            ["Neutral, range-bound. Net CREDIT — you collect premium.",
             "Max profit if the underlying stays between the short strikes.",
             "Defined risk on both wings. Short volatility / theta positive."])

    def run(self, strategy: str, **params) -> Optional[StrategyResult]:
        s = strategy.lower().replace(" ", "_").replace("-", "_")
        dispatch = {
            "bull_call_spread": lambda: self.bull_call_spread(**params),
            "bull_call": lambda: self.bull_call_spread(**params),
            "bear_put_spread": lambda: self.bear_put_spread(**params),
            "bear_put": lambda: self.bear_put_spread(**params),
            "long_straddle": lambda: self.long_straddle(**params),
            "straddle": lambda: self.long_straddle(**params),
            "iron_condor": lambda: self.iron_condor(**params),
            "condor": lambda: self.iron_condor(**params),
        }
        fn = dispatch.get(s)
        return fn() if fn else None


AVAILABLE_STRATEGIES = ["bull_call_spread", "bear_put_spread", "long_straddle", "iron_condor"]
