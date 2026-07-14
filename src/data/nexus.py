"""
nexus.py
========
NEXUS deep-research engine — the "thought process" behind the research page.

Design contract (borrowed from the reference terminal):
  * Every verdict is DETERMINISTIC. Red flags and the /10 score come from
    numeric rules whose thresholds live in configs/nexus_rules.yaml.
    No LLM touches a PASS/FAIL or a score point.
  * Every statement is EVIDENCE-LINKED. Each SWOT bullet, flag, and score
    component carries the value it was computed from and its source label.
  * Missing data is reported as N/A — never guessed.

Data arrives through the tiered universe layer (src/data/universe.py),
so a research call streams in only the one dossier it needs and quietly
warms sector peers in the background.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
RULES_PATH = ROOT / "configs" / "nexus_rules.yaml"
DOSSIER_DIR = ROOT / "data" / "cache" / "dossiers"

SRC_YF = "Yahoo Finance"
SRC_RULES = "Rules engine"
SRC_LOCAL = "Computed locally"


def _load_rules() -> Dict[str, Any]:
    import yaml
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "N/A"
    if pct:
        return f"{v * 100:.1f}%" if abs(v) < 1.5 else f"{v:.1f}%"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return f"{v:,}"


# ---------------------------------------------------------------------------
# Red-flag rules — deterministic, threshold-driven
# ---------------------------------------------------------------------------
def _run_red_flags(f: Dict[str, Any], d: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    t = cfg["red_flags"]
    price = d.get("price")
    hi52 = f.get("fiftyTwoWeekHigh")

    def rule(rid, label, value, ok: Optional[bool], evidence):
        status = "NA" if ok is None else ("PASS" if ok else "FAIL")
        return {"id": rid, "label": label, "status": status,
                "evidence": evidence, "source": SRC_RULES}

    pe = f.get("trailingPE")
    de = f.get("debtToEquity")
    pm = f.get("profitMargins")
    rg = f.get("revenueGrowth")
    roe = f.get("returnOnEquity")
    ins = f.get("heldPercentInsiders")
    dd = (1 - price / hi52) * 100 if price and hi52 else None

    return [
        rule("pe_extreme", f"P/E above {t['pe_extreme_max']} or negative", pe,
             None if pe is None else (0 < pe <= t["pe_extreme_max"]),
             f"trailing P/E = {_fmt(pe)}"),
        rule("leverage", f"Debt/Equity above {t['debt_to_equity_max']}%", de,
             None if de is None else de <= t["debt_to_equity_max"],
             f"D/E = {_fmt(de)}%"),
        rule("margins", "Negative net profit margin", pm,
             None if pm is None else pm >= t["profit_margin_min"],
             f"net margin = {_fmt(pm, pct=True)}"),
        rule("revenue", "Revenue shrinking year-on-year", rg,
             None if rg is None else rg >= t["revenue_growth_min"],
             f"revenue growth = {_fmt(rg, pct=True)}"),
        rule("roe", f"Return on equity below {t['roe_min']*100:.0f}%", roe,
             None if roe is None else roe >= t["roe_min"],
             f"ROE = {_fmt(roe, pct=True)}"),
        rule("drawdown", f"Trading more than {t['drawdown_from_52w_max']}% below 52-week high", dd,
             None if dd is None else dd <= t["drawdown_from_52w_max"],
             f"{_fmt(dd)}% below 52w high"),
        rule("insiders", f"Insider/promoter holding below {t['insider_holding_min']*100:.0f}%", ins,
             None if ins is None else ins >= t["insider_holding_min"],
             f"insider holding = {_fmt(ins, pct=True)}"),
    ]


# ---------------------------------------------------------------------------
# Score /10 — five components, 0-2 points each, evidence attached
# ---------------------------------------------------------------------------
def _run_score(f: Dict[str, Any], d: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    s = cfg["score"]
    returns = d.get("returns", {})
    price = d.get("price")
    hi52 = f.get("fiftyTwoWeekHigh")

    components: List[Dict[str, Any]] = []

    def component(name, checks):
        pts, evid, avail = 0, [], 0
        for label, value, passed in checks:
            if passed is None:
                evid.append(f"{label}: N/A")
                continue
            avail += 1
            pts += 1 if passed else 0
            evid.append(f"{label} {'✓' if passed else '✗'}")
        components.append({
            "name": name, "points": pts, "max": 2,
            "available_checks": avail, "evidence": " · ".join(evid),
            "source": SRC_RULES,
        })

    roe, pm = f.get("returnOnEquity"), f.get("profitMargins")
    component("Profitability", [
        (f"ROE {_fmt(roe, pct=True)} ≥ {s['profitability']['roe_good']*100:.0f}%",
         roe, None if roe is None else roe >= s["profitability"]["roe_good"]),
        (f"Net margin {_fmt(pm, pct=True)} ≥ {s['profitability']['margin_good']*100:.0f}%",
         pm, None if pm is None else pm >= s["profitability"]["margin_good"]),
    ])

    rg, eg = f.get("revenueGrowth"), f.get("earningsGrowth")
    component("Growth", [
        (f"Revenue growth {_fmt(rg, pct=True)} ≥ {s['growth']['revenue_growth_good']*100:.0f}%",
         rg, None if rg is None else rg >= s["growth"]["revenue_growth_good"]),
        (f"Earnings growth {_fmt(eg, pct=True)} ≥ {s['growth']['earnings_growth_good']*100:.0f}%",
         eg, None if eg is None else eg >= s["growth"]["earnings_growth_good"]),
    ])

    pe, pb = f.get("trailingPE"), f.get("priceToBook")
    component("Valuation", [
        (f"P/E {_fmt(pe)} ≤ {s['valuation']['pe_fair_max']}",
         pe, None if pe is None else 0 < pe <= s["valuation"]["pe_fair_max"]),
        (f"P/B {_fmt(pb)} ≤ {s['valuation']['pb_fair_max']}",
         pb, None if pb is None else 0 < pb <= s["valuation"]["pb_fair_max"]),
    ])

    de = f.get("debtToEquity")
    cash, debt = f.get("totalCash"), f.get("totalDebt")
    cash_ok = None if (cash is None or debt is None) else cash >= debt
    component("Balance sheet", [
        (f"D/E {_fmt(de)}% ≤ {s['balance_sheet']['debt_to_equity_ok']}%",
         de, None if de is None else de <= s["balance_sheet"]["debt_to_equity_ok"]),
        (f"Cash {_fmt(cash)} ≥ debt {_fmt(debt)}", cash, cash_ok),
    ])

    r1y = returns.get("1Y")
    near = (1 - price / hi52) * 100 if price and hi52 else None
    component("Momentum", [
        (f"1Y return {_fmt(r1y)}% ≥ {s['momentum']['return_1y_good']}%",
         r1y, None if r1y is None else r1y >= s["momentum"]["return_1y_good"]),
        (f"Within {s['momentum']['near_high_within']}% of 52w high ({_fmt(near)}% below)",
         near, None if near is None else near <= s["momentum"]["near_high_within"]),
    ])

    total = sum(c["points"] for c in components)
    return {
        "total": total, "out_of": 10, "components": components,
        "method": "Deterministic checklist — 5 components × 2 points. "
                  "Thresholds in configs/nexus_rules.yaml. No LLM.",
    }


# ---------------------------------------------------------------------------
# SWOT — every bullet built from a number, every bullet cites its source
# ---------------------------------------------------------------------------
def _run_swot(f: Dict[str, Any], d: Dict[str, Any],
              peers: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    returns = d.get("returns", {})
    price = d.get("price")
    hi52, lo52 = f.get("fiftyTwoWeekHigh"), f.get("fiftyTwoWeekLow")
    cur = f.get("currency") or ""

    S, W, O, T = [], [], [], []

    def add(bucket, text, src=SRC_YF):
        bucket.append({"text": text, "source": src})

    roe = f.get("returnOnEquity")
    if roe is not None:
        add(S if roe >= 0.15 else W,
            f"Return on equity of {roe*100:.1f}% ({'strong' if roe >= 0.15 else 'weak'} capital efficiency)")
    pm = f.get("profitMargins")
    if pm is not None:
        add(S if pm >= 0.10 else W, f"Net profit margin of {pm*100:.1f}%")
    om = f.get("operatingMargins")
    if om is not None and om >= 0.20:
        add(S, f"Operating margin of {om*100:.1f}% indicates pricing power")
    rg = f.get("revenueGrowth")
    if rg is not None:
        add(S if rg >= 0.10 else W, f"Revenue growth of {rg*100:.1f}% year-on-year")
    cash, debt = f.get("totalCash"), f.get("totalDebt")
    if cash is not None and debt is not None:
        if cash >= debt:
            add(S, f"Cash ({cash:,.0f} {cur}) exceeds total debt ({debt:,.0f} {cur})")
        else:
            add(W, f"Total debt ({debt:,.0f} {cur}) exceeds cash ({cash:,.0f} {cur})")
    de = f.get("debtToEquity")
    if de is not None and de > 150:
        add(W, f"Elevated leverage: debt/equity at {de:.0f}%")
    inst = f.get("heldPercentInstitutions")
    if inst is not None and inst >= 0.30:
        add(S, f"Institutional holding at {inst*100:.1f}%")

    fpe, tpe = f.get("forwardPE"), f.get("trailingPE")
    if fpe is not None and tpe is not None and 0 < fpe < tpe:
        add(O, f"Forward P/E ({fpe:.1f}) below trailing P/E ({tpe:.1f}) — market expects earnings improvement")
    if price and hi52:
        below = (1 - price / hi52) * 100
        if below >= 20:
            add(O, f"Trading {below:.0f}% below its 52-week high of {hi52:,.2f} — re-rating headroom if fundamentals hold", SRC_LOCAL)
        elif below <= 5:
            add(T, f"Trading within {below:.0f}% of its 52-week high — limited margin of safety", SRC_LOCAL)
    if peers:
        top = peers[0]
        add(O, f"Sector peer set led by {top['symbol']} (mcap {top['mcap']:,.0f}) — relative-value comparisons available", SRC_LOCAL)

    beta = f.get("beta")
    if beta is not None and beta >= 1.3:
        add(T, f"High beta of {beta:.2f} — amplifies market drawdowns")
    r1y = returns.get("1Y")
    if r1y is not None and r1y <= -20:
        add(T, f"Down {abs(r1y):.1f}% over the past year — persistent negative momentum", SRC_LOCAL)
    eg = f.get("earningsGrowth")
    if eg is not None and eg < 0:
        add(T, f"Earnings declining at {eg*100:.1f}% year-on-year")
    if price and lo52 and hi52:
        pos = (price - lo52) / (hi52 - lo52) * 100 if hi52 != lo52 else None
        if pos is not None:
            add(O if pos < 30 else T if pos > 90 else S,
                f"Positioned at {pos:.0f}% of its 52-week range ({lo52:,.2f} – {hi52:,.2f})", SRC_LOCAL)

    return {"strengths": S, "weaknesses": W, "opportunities": O, "threats": T}


# ---------------------------------------------------------------------------
# Peers — table always; chart series only from already-cached dossiers
# ---------------------------------------------------------------------------
def _peer_panel(symbol: str, universe) -> List[Dict[str, Any]]:
    peers = universe.peers(symbol, n=4)
    out = []
    for p in peers:
        entry = {**p, "series": None, "returns": None}
        path = DOSSIER_DIR / f"{p['symbol']}.json"
        if path.exists():
            try:
                dd = json.loads(path.read_text(encoding="utf-8"))
                entry["series"] = dd.get("weekly_closes_5y")
                entry["returns"] = dd.get("returns")
                pf = dd.get("fundamentals", {})
                entry["trailingPE"] = pf.get("trailingPE")
                entry["returnOnEquity"] = pf.get("returnOnEquity")
            except Exception:
                pass
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def research(symbol: str) -> Dict[str, Any]:
    from src.data.universe import get_universe  # lazy

    universe = get_universe()
    d = universe.dossier(symbol)  # streams in tier-2 + prefetches peers
    if "error" in d:
        return {"error": d["error"]}

    cfg = _load_rules()
    f = d.get("fundamentals", {})
    peers = _peer_panel(d["symbol"], universe)

    flags = _run_red_flags(f, d, cfg)
    score = _run_score(f, d, cfg)
    swot = _run_swot(f, d, peers)

    return {
        "symbol": d["symbol"],
        "name": d.get("name"),
        "exchange": d.get("exchange"),
        "asset_class": d.get("asset_class"),
        "price": d.get("price"),
        "currency": f.get("currency"),
        "returns": d.get("returns"),
        "fundamentals": f,
        "weekly_closes_5y": d.get("weekly_closes_5y"),
        "red_flags": {
            "items": flags,
            "failed": sum(1 for x in flags if x["status"] == "FAIL"),
            "source_note": "Deterministic rules — no LLM. Thresholds in configs/nexus_rules.yaml",
        },
        "swot": swot,
        "score": score,
        "peers": peers,
        "sources": {
            "prices": f"{SRC_YF} (5y weekly, returns computed locally)",
            "fundamentals": SRC_YF,
            "rules": "configs/nexus_rules.yaml",
        },
        "generated_at": datetime.now().isoformat(),
        "disclaimer": "Research and education only — not investment advice.",
    }
