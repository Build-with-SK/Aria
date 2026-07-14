"""
scenario_engine.py — GS-Quant Bridge: Scenario Engine
Standalone risk scenario analysis — no GS API required.
Inspired by GS-Quant's risk scenario framework, implemented in pure Python.
Uses signal scores as position weight proxies.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ScenarioResult:
    name: str
    description: str
    params: Dict
    pnl_estimate: float          # Estimated P&L impact in $ (proxy)
    pnl_pct: float               # As % of portfolio
    top_losers: List[tuple] = field(default_factory=list)
    top_winners: List[tuple] = field(default_factory=list)
    key_risks: List[str] = field(default_factory=list)
    confidence: str = "MEDIUM"   # HIGH / MEDIUM / LOW

    def summary_line(self) -> str:
        direction = "gain" if self.pnl_estimate >= 0 else "loss"
        return (
            f"{self.name}: estimated {direction} of ${abs(self.pnl_estimate):,.0f} "
            f"({self.pnl_pct:+.1f}%) | Confidence: {self.confidence}"
        )


class ScenarioEngine:
    """
    GS-Quant inspired standalone risk scenario engine.
    Scenarios represent standardised macro shock events.
    """

    # Sector sensitivity mappings to shock types
    # Positive = benefits from shock, Negative = hurt by shock
    RATE_SENSITIVITY = {
        # Sectors that benefit from rising rates
        "GS": 0.8, "JPM": 0.8, "BAC": 0.9, "WFC": 0.7, "MS": 0.6,
        "BRK-B": 0.3, "V": -0.1, "MA": -0.1,
        # Sectors hurt by rising rates
        "TSLA": -1.2, "NVDA": -1.0, "AMZN": -0.8, "META": -0.7,
        "GOOGL": -0.6, "MSFT": -0.5, "AAPL": -0.4,
        "TLT": -2.5, "HYG": -1.0, "LQD": -1.5,
        "XOM": 0.2, "CVX": 0.2, "GLD": -0.3,
        "SPY": -0.5, "QQQ": -0.8,
    }

    EQUITY_SENSITIVITY = {
        # Beta proxies — high beta gets hit hardest
        "TSLA": 1.8, "NVDA": 1.6, "AMZN": 1.3, "META": 1.3,
        "GOOGL": 1.1, "MSFT": 1.0, "AAPL": 1.0, "JPM": 1.1,
        "GS": 1.2, "BAC": 1.3, "XOM": 0.8, "CVX": 0.7,
        "GLD": -0.2, "TLT": 0.4, "SPY": 1.0, "QQQ": 1.2,
    }

    VOL_SENSITIVITY = {
        # High vol hurts long equity, helps optionality
        "TSLA": -1.5, "NVDA": -1.2, "AMZN": -0.9, "META": -0.8,
        "GLD": 0.5, "TLT": 0.2, "XOM": -0.3, "JPM": -0.6,
        "GS": -0.7, "BAC": -0.8, "SPY": -0.8,
    }

    CREDIT_SENSITIVITY = {
        "HYG": -2.0, "JNK": -2.0, "LQD": -1.0, "BAC": -0.8,
        "JPM": -0.6, "GS": -0.7, "XOM": -0.2, "AMZN": -0.3,
        "TSLA": -1.0, "SPY": -0.4,
    }

    def __init__(self, base_path: str = None):
        if base_path is None:
            base_path = Path(__file__).parent.parent.parent
        self.base_path = Path(base_path)
        self._signals: Dict = {}
        self._meta: Dict = {}
        self._load_signals()

    def _load_signals(self):
        path = self.base_path / "data" / "signals.json"
        if path.exists():
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                self._meta = data.pop("__meta__", {})
                self._signals = data
            except Exception:
                pass

    def _position_weight(self, ticker: str) -> float:
        """Convert signal score to a position weight proxy (-1 to +1).

        signals.json uses `composite_score` (roughly -100..+100) and `action`
        ("Buy"/"Sell"/"Hold"). Older code looked for `score`/`direction` which
        do not exist — every weight came out 0. This reads the real fields.
        """
        sig = self._signals.get(ticker, {})
        if not isinstance(sig, dict):
            return 0.0
        # prefer composite_score; fall back to legacy `score` if present
        score = sig.get("composite_score", sig.get("score"))
        if not isinstance(score, (int, float)):
            return 0.0
        normalised = max(-1.0, min(1.0, score / 100.0))  # -1 to +1
        action = str(sig.get("action", sig.get("direction", ""))).lower()
        if action in ("sell", "short", "strong sell"):
            normalised = -abs(normalised)
        elif action in ("buy", "long", "strong buy"):
            normalised = abs(normalised)
        return normalised

    def _estimate_var(self) -> float:
        """Use stored VaR or estimate from meta."""
        var = self._meta.get("var_1d")
        if isinstance(var, (int, float)):
            return var
        return 5000.0  # Default $5k if not available

    def _portfolio_pnl(self, sensitivity_map: Dict, shock_magnitude: float, default_sensitivity: float = -0.8) -> tuple:
        """
        Estimate portfolio P&L impact given a shock and sensitivity map.
        Returns (total_pnl, pnl_pct, [(ticker, pnl)...])
        """
        var = self._estimate_var()
        portfolio_size = var * 20  # Proxy: assume VaR is ~5% of portfolio
        impacts = []

        tickers = list(self._signals.keys())[:50]  # Process top 50 tickers

        for ticker in tickers:
            weight = self._position_weight(ticker)
            if abs(weight) < 0.01:
                continue
            sensitivity = sensitivity_map.get(ticker, default_sensitivity)
            # P&L = weight × sensitivity × shock × notional_per_position
            notional = portfolio_size / max(len(tickers), 1) * 2  # rough notional
            pnl = weight * sensitivity * shock_magnitude * notional
            impacts.append((ticker, pnl))

        total_pnl = sum(p for _, p in impacts)
        pnl_pct = (total_pnl / portfolio_size * 100) if portfolio_size > 0 else 0.0

        impacts.sort(key=lambda x: x[1])
        return total_pnl, pnl_pct, impacts

    # ── Scenario definitions ─────────────────────────────────────────────────

    def rate_shock(self, bps: float = 100) -> ScenarioResult:
        """Parallel shift in the yield curve (bps = basis points, positive = higher rates)."""
        magnitude = bps / 100  # Convert to percentage
        total_pnl, pnl_pct, impacts = self._portfolio_pnl(
            self.RATE_SENSITIVITY, magnitude, default_sensitivity=-0.5
        )
        return ScenarioResult(
            name=f"Rate Shock ({bps:+.0f}bps)",
            description=f"Parallel yield curve shift of {bps:+.0f} basis points.",
            params={"type": "rate_shock", "bps": bps},
            pnl_estimate=total_pnl,
            pnl_pct=pnl_pct,
            top_losers=impacts[:5],
            top_winners=list(reversed(impacts))[:5],
            key_risks=self._rate_risks(bps),
            confidence="MEDIUM",
        )

    def equity_crash(self, pct: float = -20.0) -> ScenarioResult:
        """Broad equity market selloff."""
        magnitude = abs(pct) / 100
        if pct > 0:
            magnitude = -magnitude  # positive pct = rally
        total_pnl, pnl_pct, impacts = self._portfolio_pnl(
            self.EQUITY_SENSITIVITY, magnitude, default_sensitivity=1.0
        )
        label = "Crash" if pct < -10 else "Correction" if pct < 0 else "Rally"
        return ScenarioResult(
            name=f"Equity {label} ({pct:+.0f}%)",
            description=f"Broad equity market moves {pct:+.0f}% over a short period.",
            params={"type": "equity_crash", "pct": pct},
            pnl_estimate=total_pnl,
            pnl_pct=pnl_pct,
            top_losers=impacts[:5],
            top_winners=list(reversed(impacts))[:5],
            key_risks=self._equity_risks(pct),
            confidence="HIGH" if abs(pct) > 15 else "MEDIUM",
        )

    def vol_spike(self, vix_move: float = 20.0) -> ScenarioResult:
        """VIX spike — volatility expansion."""
        magnitude = vix_move / 20  # Normalise to standard VIX spike
        total_pnl, pnl_pct, impacts = self._portfolio_pnl(
            self.VOL_SENSITIVITY, magnitude, default_sensitivity=-0.7
        )
        return ScenarioResult(
            name=f"Vol Spike (+{vix_move:.0f} VIX pts)",
            description=f"VIX surges by {vix_move:.0f} points (e.g. from 15 to {15 + vix_move:.0f}).",
            params={"type": "vol_spike", "vix_move": vix_move},
            pnl_estimate=total_pnl,
            pnl_pct=pnl_pct,
            top_losers=impacts[:5],
            top_winners=list(reversed(impacts))[:5],
            key_risks=[
                "Short volatility positions suffer severe losses",
                "Liquidity dries up, bid-ask spreads widen",
                "Forced de-risking creates cascading sells",
                "Correlation to 1 in severe vol events",
            ],
            confidence="MEDIUM",
        )

    def credit_widening(self, bps: float = 200) -> ScenarioResult:
        """Credit spread widening — stress in fixed income markets."""
        magnitude = bps / 100
        total_pnl, pnl_pct, impacts = self._portfolio_pnl(
            self.CREDIT_SENSITIVITY, magnitude, default_sensitivity=-0.4
        )
        return ScenarioResult(
            name=f"Credit Widening (+{bps:.0f}bps spreads)",
            description=f"HY/IG credit spreads widen by {bps:.0f} basis points.",
            params={"type": "credit_widening", "bps": bps},
            pnl_estimate=total_pnl,
            pnl_pct=pnl_pct,
            top_losers=impacts[:5],
            top_winners=list(reversed(impacts))[:5],
            key_risks=[
                f"High-yield bonds lose {bps * 0.06:.1f}% on duration",
                "Financials face NIM compression and loan-loss provisions",
                "Leveraged companies see refinancing risk spike",
            ],
            confidence="MEDIUM",
        )

    def soft_landing(self) -> ScenarioResult:
        """Benign disinflation — Fed cuts rates, growth holds up."""
        total_pnl, pnl_pct, impacts = self._portfolio_pnl(
            {t: -s * 0.6 for t, s in self.RATE_SENSITIVITY.items()},  # Reverse of rate shock (cuts)
            -0.75,
            default_sensitivity=0.4,
        )
        return ScenarioResult(
            name="Soft Landing",
            description="Fed achieves disinflation without recession. Rates fall 75bps, growth stays positive.",
            params={"type": "soft_landing"},
            pnl_estimate=total_pnl,
            pnl_pct=pnl_pct,
            top_losers=impacts[:5],
            top_winners=list(reversed(impacts))[:5],
            key_risks=[
                "Risk: market already pricing this — limited upside surprise",
                "Duration names (TLT) rally hard, rates-sensitive shorts get squeezed",
                "Growth tech reprices higher on lower discount rate",
            ],
            confidence="LOW",  # Soft landings are rare — don't overweight
        )

    def stagflation(self) -> ScenarioResult:
        """Stagflation — high inflation + slow growth. Worst of both worlds."""
        combined = {t: self.RATE_SENSITIVITY.get(t, -0.5) * 0.5 + self.EQUITY_SENSITIVITY.get(t, 1.0) * -0.5
                    for t in set(list(self.RATE_SENSITIVITY) + list(self.EQUITY_SENSITIVITY))}
        total_pnl, pnl_pct, impacts = self._portfolio_pnl(combined, 1.0, default_sensitivity=-0.8)
        return ScenarioResult(
            name="Stagflation",
            description="High sticky inflation + growth recession. Rates stay high, equities re-rate lower.",
            params={"type": "stagflation"},
            pnl_estimate=total_pnl,
            pnl_pct=pnl_pct,
            top_losers=impacts[:5],
            top_winners=list(reversed(impacts))[:5],
            key_risks=[
                "Equity + rate risk compounds — no diversification benefit",
                "Commodities (XOM, CVX) outperform; tech crushes",
                "Fed trapped: can't cut without re-igniting inflation",
                "Historical parallel: 1973-74 (Nifty Fifty collapse)",
            ],
            confidence="LOW",
        )

    # ── Run all / custom ─────────────────────────────────────────────────────

    def run_all_scenarios(self) -> List[ScenarioResult]:
        self._load_signals()  # Refresh
        return [
            self.rate_shock(+100),
            self.rate_shock(-75),
            self.equity_crash(-20),
            self.equity_crash(+15),
            self.vol_spike(20),
            self.credit_widening(200),
            self.soft_landing(),
            self.stagflation(),
        ]

    def run_custom_scenario(self, scenario_type: str, **params) -> Optional[ScenarioResult]:
        self._load_signals()
        scenario_type = scenario_type.lower().replace(" ", "_").replace("-", "_")
        dispatch = {
            "rate_shock": lambda: self.rate_shock(params.get("bps", 100)),
            "rate": lambda: self.rate_shock(params.get("bps", 100)),
            "equity_crash": lambda: self.equity_crash(params.get("pct", -20)),
            "equity": lambda: self.equity_crash(params.get("pct", -20)),
            "vol_spike": lambda: self.vol_spike(params.get("vix_move", 20)),
            "vol": lambda: self.vol_spike(params.get("vix_move", 20)),
            "credit_widening": lambda: self.credit_widening(params.get("bps", 200)),
            "credit": lambda: self.credit_widening(params.get("bps", 200)),
            "soft_landing": lambda: self.soft_landing(),
            "stagflation": lambda: self.stagflation(),
        }
        fn = dispatch.get(scenario_type)
        return fn() if fn else None

    def format_report(self, result: ScenarioResult) -> str:
        lines = [
            f"\n{'='*60}",
            f"SCENARIO: {result.name}",
            f"{'='*60}",
            f"Description : {result.description}",
            f"Est. P&L    : ${result.pnl_estimate:+,.0f} ({result.pnl_pct:+.1f}%)",
            f"Confidence  : {result.confidence}",
            "",
        ]
        if result.top_losers:
            lines.append("Top Losers:")
            for ticker, pnl in result.top_losers[:5]:
                lines.append(f"  {ticker:<8} ${pnl:+,.0f}")
        if result.top_winners:
            lines.append("Top Winners:")
            for ticker, pnl in result.top_winners[:5]:
                lines.append(f"  {ticker:<8} ${pnl:+,.0f}")
        if result.key_risks:
            lines.append("\nKey Risks:")
            for r in result.key_risks:
                lines.append(f"  • {r}")
        lines.append(f"{'='*60}\n")
        return "\n".join(lines)

    def format_all_scenarios(self) -> str:
        results = self.run_all_scenarios()
        lines = ["\n=== ARIA SCENARIO ANALYSIS — ALL SHOCKS ===\n"]
        for r in results:
            lines.append(r.summary_line())
        lines.append("\nDetailed breakdown:\n")
        for r in results:
            lines.append(self.format_report(r))
        return "\n".join(lines)

    # ── Helper risk narratives ────────────────────────────────────────────────

    def _rate_risks(self, bps: float) -> List[str]:
        if bps > 0:
            return [
                f"Duration pain: +{bps}bps costs ~{bps * 0.08:.1f}% on 10yr bonds",
                "Growth equities (high P/E) reprice on higher discount rate",
                "Financials benefit via NIM expansion — long banks is a natural hedge",
                "Mortgage rates rise, housing sector under pressure",
            ]
        else:
            return [
                "Yield curve flattening may signal growth slowdown",
                "Banks face NIM compression if cuts are aggressive",
                "Duration assets (TLT) rally — short bonds gets squeezed",
            ]

    def _equity_risks(self, pct: float) -> List[str]:
        if pct < -10:
            return [
                f"Leverage unwind: forced selling amplifies the {pct:.0f}% move",
                "Correlation to 1 — diversification fails in crashes",
                "VaR breaches trigger systematic de-risking",
                "Options market: short gamma desks accelerate decline",
            ]
        else:
            return [
                "Crowded longs get released — watch for momentum reversal",
                "Short squeeze in heavily shorted names",
            ]
