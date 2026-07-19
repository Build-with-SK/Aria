"""
src/desk/analysts/macro_agent.py
================================
Macro strategist — market-wide conditioner, not per-ticker. Grounds in
macro_data.json (regime, VIX, DXY, yields). Its output scales every
debate: risk multiplier for sizing and the conviction bar to clear.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.desk.opinion import Opinion, ev, load_data_json

SRC = "data/macro_data.json"

RISK_OFF_REGIMES = ("Contraction", "Crisis", "Recession", "Stagflation", "Risk-Off")


@dataclass
class MacroConditioner:
    regime: str
    risk_multiplier: float      # scales position size (0.25 crisis .. 1.0 goldilocks)
    conviction_bar: int         # minimum conviction the judge must clear
    opinion: Opinion = None     # the market-wide Opinion, evidence-cited


def condition() -> MacroConditioner:
    macro = load_data_json("macro_data.json")
    regime = macro.get("regime", "Unknown")
    vix = macro.get("vix")
    dxy = macro.get("dxy")
    y10 = macro.get("treasury_10y")
    spread = macro.get("yield_spread_10y2y")
    mscore = macro.get("macro_score") or 0.0

    evidence = [
        ev(f"Macro regime: {regime} (macro score {mscore:+.1f})", mscore, SRC,
           "bear" if any(r in regime for r in RISK_OFF_REGIMES) else "bull" if mscore > 5 else "neutral"),
    ]
    if vix is not None:
        evidence.append(ev(f"VIX at {vix}", vix, SRC,
                           "bear" if vix > 25 else "bull" if vix < 15 else "neutral"))
    if dxy is not None:
        evidence.append(ev(f"DXY at {dxy} (trend {macro.get('dxy_trend', 0):+.2%})", dxy, SRC, "neutral"))
    if y10 is not None:
        evidence.append(ev(f"10Y treasury {y10}%", y10, SRC,
                           "bear" if y10 > 5 else "neutral"))
    if spread is not None:
        evidence.append(ev(f"10Y-2Y spread {spread:+.2f}", spread, SRC,
                           "bear" if spread < 0 else "neutral"))

    risk_off = any(r in regime for r in RISK_OFF_REGIMES)
    vix_high = isinstance(vix, (int, float)) and vix > 25
    vix_extreme = isinstance(vix, (int, float)) and vix > 35

    if vix_extreme or "Crisis" in regime:
        mult, bar, view = 0.25, 85, "bear"
    elif risk_off or vix_high:
        mult, bar, view = 0.5, 80, "bear"
    elif mscore > 5 and (vix is None or vix < 20):
        mult, bar, view = 1.0, 65, "bull"
    else:
        mult, bar, view = 0.75, 70, "neutral"

    conviction = int(min(100, abs(mscore) * 3 + 30))
    thesis = (
        f"Regime {regime}, VIX {vix}, macro score {mscore:+.1f}. Desk conditioning: "
        f"risk multiplier {mult:.2f}x, conviction bar {bar}. "
        + ("Risk-off — smaller size, higher bar for new longs." if view == "bear"
           else "Constructive backdrop — normal sizing permitted." if view == "bull"
           else "Mixed backdrop — three-quarter sizing.")
    )
    op = Opinion(agent="macro", ticker="MARKET", view=view,
                 conviction=conviction, thesis=thesis, evidence=evidence)
    return MacroConditioner(regime=regime, risk_multiplier=mult,
                            conviction_bar=bar, opinion=op)
