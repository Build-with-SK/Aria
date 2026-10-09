"""
src/intelligence/research/evidence_graph.py
===========================================
Evidence items, what each is derived from, and how many are really independent.

THE DEPENDENCY GRAPH
--------------------
Every item names its ROOT — the raw input it is computed from — and its
GROUP (src/intelligence/research/sources.GROUPS):

    PRICE:<symbol>          trend, momentum, RSI, MACD, composite, volume,
                            residual return              -> PRICE_DERIVED
    PRICE:<proxy>           market regime, market / sector contribution
                                                         -> MARKET
    FUNDAMENTALS:<symbol>   growth, forward-vs-trailing earnings
                                                         -> FUNDAMENTAL
    MACRO:<series>          the macro regime and its inputs -> MACRO
    NEWS:<cluster>          one item per STORY           -> NEWS
    PRICE:<pair>            cross-asset implications     -> CROSS_ASSET
    EVENTS:<symbol>         earnings surprise, event risk -> EVENT
    MODEL:<source>          a recorded prediction; technical and ML models
                            are PRICE_DERIVED by their inputs, V5 and the desk
                            span groups                  -> MODEL

Agreement is reported twice: over raw items, and over independent GROUPS.
Five price indicators pointing up are one group pointing up, and the report
says "5 items, 1 independent group". Inside PRICE_DERIVED the dependence is
MEASURED rather than asserted: the daily direction of each indicator over the
past year, their correlation matrix, and its participation ratio
(sum(lambda))^2 / sum(lambda^2) — the effective number of independent signals.

Direction rules are written next to each item and are descriptive heuristics,
NOT validated predictors; every item's reliability says so unless a measured
record exists (model predictions carry their evaluated record).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from src.intelligence.research.sources import GROUPS, MODEL_GROUP

UNVALIDATED = {"state": "UNVALIDATED",
               "basis": "a descriptive rule; its predictive value has not been measured"}


@dataclass
class EvidenceItem:
    item_id: str
    label: str
    group: str
    root: str
    direction: int                  # +1 bullish, -1 bearish, 0 non-directional
    magnitude: Optional[float]
    unit: str
    as_of: Optional[str]
    freshness: str
    reliability: dict
    source: str
    rule: str
    explanation: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction_label"] = {1: "BULLISH", -1: "BEARISH", 0: "NON_DIRECTIONAL"}[self.direction]
        return d


# ── price features (causal: every value at a bar uses bars up to it) ────────

def price_features(close: pd.Series) -> pd.DataFrame:
    c = pd.Series(close, dtype=float)
    f = pd.DataFrame(index=c.index)
    f["close"] = c
    f["sma20"] = c.rolling(20).mean()
    f["sma50"] = c.rolling(50).mean()
    f["sma200"] = c.rolling(200).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    f["rsi14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    f["ret21"] = c / c.shift(21) - 1
    f["ret63"] = c / c.shift(63) - 1
    return f


def _sign(x, lo=0.0) -> int:
    if x is None or not np.isfinite(x):
        return 0
    return 1 if x > lo else -1 if x < -lo else 0


def indicator_directions(f: pd.DataFrame) -> pd.DataFrame:
    """Daily +1/-1/0 of each price indicator — the series whose correlation
    measures how dependent they are."""
    out = pd.DataFrame(index=f.index)
    out["trend_sma"] = np.where((f.close > f.sma200) & (f.sma50 > f.sma200), 1,
                                np.where((f.close < f.sma200) & (f.sma50 < f.sma200), -1, 0))
    out["momentum_21d"] = np.sign(f.ret21)
    out["momentum_63d"] = np.sign(f.ret63)
    out["rsi_14"] = np.where(f.rsi14 > 55, 1, np.where(f.rsi14 < 45, -1, 0))
    out["macd_hist"] = np.sign(f.macd_hist)
    out["price_vs_sma20"] = np.sign(f.close - f.sma20)
    return out.where(f[["sma200", "ret63", "rsi14"]].notna().all(axis=1)).dropna()


def price_dependence(close: pd.Series, window: int = 252) -> dict:
    """Measured dependence of the price indicators over the last `window` bars."""
    dirs = indicator_directions(price_features(close)).iloc[-window:]
    if len(dirs) < 120:
        return {"state": "INSUFFICIENT_DATA", "n_days": len(dirs),
                "why": "fewer than 120 days with every indicator defined"}
    keep = [c for c in dirs.columns if dirs[c].std() > 0]
    if len(keep) < 2:
        return {"state": "INSUFFICIENT_DATA", "n_days": len(dirs),
                "why": "indicators did not vary"}
    corr = np.corrcoef(dirs[keep].to_numpy(dtype=float).T)
    lam = np.clip(np.linalg.eigvalsh(corr), 0, None)
    eff = float(lam.sum() ** 2 / (lam ** 2).sum()) if (lam ** 2).sum() > 0 else float(len(keep))
    off = corr[~np.eye(len(keep), dtype=bool)]
    return {"state": "MEASURED", "indicators": keep, "n_days": len(dirs),
            "mean_pairwise_corr": round(float(off.mean()), 3),
            "effective_independent_signals": round(eff, 2),
            "correlation": {a: {b: round(float(corr[i, j]), 3) for j, b in enumerate(keep)}
                            for i, a in enumerate(keep)},
            "method": "participation ratio of the eigenvalues of the correlation "
                      "matrix of daily indicator directions"}


# ── items from each part of the snapshot ────────────────────────────────────

def _item(i, label, group, root, direction, magnitude, unit, as_of, freshness, source,
          rule, explanation, reliability=None, **detail) -> EvidenceItem:
    return EvidenceItem(i, label, group, root, int(direction), magnitude, unit, as_of,
                        freshness, reliability or UNVALIDATED, source, rule, explanation,
                        detail)


def price_items(symbol: str, close: pd.Series, volume: Optional[pd.Series], *, as_of,
                freshness, technical: Optional[dict]) -> list[EvidenceItem]:
    f = price_features(close)
    last = f.iloc[-1]
    root, src = f"PRICE:{symbol}", "bars known at T (session-cut)"
    out = []
    if np.isfinite(last.sma200):
        d = 1 if (last.close > last.sma200 and last.sma50 > last.sma200) else \
            -1 if (last.close < last.sma200 and last.sma50 < last.sma200) else 0
        out.append(_item("price.trend_sma", "Trend (close / SMA50 vs SMA200)", "PRICE_DERIVED",
                         root, d, round(float(last.close / last.sma200 - 1), 4), "vs SMA200",
                         as_of, freshness, src, "bull if close>SMA200 and SMA50>SMA200; bear "
                         "if both below; else neutral",
                         f"close is {float(last.close / last.sma200 - 1):+.1%} from its 200-day average"))
    for k, lab in (("ret21", "21-day momentum"), ("ret63", "63-day momentum")):
        if np.isfinite(last[k]):
            out.append(_item(f"price.momentum_{k[3:]}d", lab, "PRICE_DERIVED", root,
                             _sign(last[k]), round(float(last[k]), 4), "return", as_of,
                             freshness, src, "sign of the return",
                             f"{lab} {float(last[k]):+.1%}"))
    if np.isfinite(last.rsi14):
        r = float(last.rsi14)
        note = (" — overbought by convention; momentum and mean-reversion readings disagree "
                "at extremes" if r > 70 else
                " — oversold by convention; momentum and mean-reversion readings disagree "
                "at extremes" if r < 30 else "")
        out.append(_item("price.rsi_14", "RSI(14)", "PRICE_DERIVED", root,
                         1 if r > 55 else -1 if r < 45 else 0, round(r, 1), "index", as_of,
                         freshness, src, "momentum reading: >55 bull, <45 bear",
                         f"RSI {r:.0f}{note}"))
    if np.isfinite(last.macd_hist):
        out.append(_item("price.macd_hist", "MACD histogram", "PRICE_DERIVED", root,
                         _sign(last.macd_hist), round(float(last.macd_hist), 4), "price units",
                         as_of, freshness, src, "sign of MACD minus its signal line",
                         f"MACD histogram {'positive' if last.macd_hist > 0 else 'negative'}"))
    if technical and technical.get("summary"):
        lab = technical["summary"]
        d = 1 if lab in ("Buy", "Strong Buy") else -1 if lab in ("Sell", "Strong Sell") else 0
        out.append(_item("price.technical_composite", "Technical composite (vote count)",
                         "PRICE_DERIVED", root, d, None, "label", as_of, freshness,
                         "src/data/technical_summary.score_frame", "Buy/Strong Buy bull, "
                         "Sell/Strong Sell bear", f"composite reads {lab} "
                         f"({technical.get('counts')})"))
    if volume is not None and len(volume) >= 21:
        v = pd.Series(volume, dtype=float)
        avg = float(v.iloc[-21:-1].mean())
        if avg > 0:
            ratio = float(v.iloc[-1] / avg)
            day = float(close.iloc[-1] / close.iloc[-2] - 1) if len(close) > 1 else 0.0
            d = _sign(day) if ratio >= 2 else 0
            out.append(_item("price.volume_anomaly", "Volume vs 20-day average",
                             "PRICE_DERIVED", root, d, round(ratio, 2), "x average", as_of,
                             freshness, src, "directional only when volume >= 2x average: "
                             "the direction of that day's move",
                             f"volume {ratio:.1f}x its 20-day average on a {day:+.1%} day"))
    return out


def attribution_items(symbol: str, attr: dict, *, as_of, freshness) -> list[EvidenceItem]:
    out = []
    w = (attr or {}).get("windows", {}).get("21d") or {}
    if w.get("status") != "MEASURED":
        return out
    mk = (w.get("contributions") or {}).get("market")
    if mk:
        out.append(_item("market.contribution_21d", "Market contribution (21d)", "MARKET",
                         f"PRICE:{attr.get('market_proxy')}", _sign(mk["contribution"], 0.002),
                         mk["contribution"], "log return", as_of, freshness,
                         "attribution (betas estimated before the window)",
                         "sign of beta x market move, if beyond 0.2%",
                         f"the market accounts for {mk['contribution']:+.2%} of the move "
                         f"(beta {mk['beta']})"))
    sc = (w.get("contributions") or {}).get("sector")
    if sc:
        out.append(_item("market.sector_contribution_21d", "Sector contribution (21d)", "MARKET",
                         f"PRICE:{attr.get('sector')}", _sign(sc["contribution"], 0.002),
                         sc["contribution"], "log return", as_of, freshness,
                         "attribution", "sign of beta x sector-excess move, if beyond 0.2%",
                         f"the sector (vs market) accounts for {sc['contribution']:+.2%}"))
    res = w.get("residual") or {}
    if res:
        out.append(_item("price.unexplained_residual_21d", "UNEXPLAINED_RESIDUAL (21d)",
                         "PRICE_DERIVED", f"PRICE:{symbol}",
                         _sign(res["log"]) if res.get("unusual") else 0, res["log"],
                         "log return", as_of, freshness, "attribution",
                         "directional only when unusual (|z| >= 2); never an alpha claim",
                         w.get("sentence", ""), z=res.get("z")))
    return out


def regime_items(market_regime: Optional[dict], macro: Optional[dict], *, as_of,
                 freshness, macro_freshness) -> list[EvidenceItem]:
    out = []
    if market_regime and market_regime.get("trend") not in (None, "UNKNOWN"):
        t = market_regime["trend"]
        out.append(_item("market.regime_trend", "Market regime (benchmark trend)", "MARKET",
                         f"PRICE:{market_regime.get('benchmark', 'SPY')}",
                         1 if t == "BULL" else -1 if t == "BEAR" else 0, None, "label",
                         as_of, freshness, "src/intelligence/regime.classify_market",
                         "BULL bull, BEAR bear, SIDEWAYS neutral",
                         f"benchmark {t}, volatility {market_regime.get('vol')}"))
    if macro and macro.get("regime"):
        out.append(_item("macro.regime", "Macro regime (quadrant)", "MACRO", "MACRO:quadrant",
                         0, macro.get("coverage"), "input coverage", as_of, macro_freshness,
                         "src/macro/regime.classify on macro releases known at T",
                         "context only: a monthly quadrant is not a direction for one "
                         "instrument", f"{macro['regime']} (coverage {macro.get('coverage')})"))
    return out


def fundamental_items(symbol: str, values: Optional[dict], *, as_of,
                      freshness) -> list[EvidenceItem]:
    if not values:
        return []
    root, src = f"FUNDAMENTALS:{symbol}", "vendor snapshot known at T"
    out = []
    for key, lab in (("revenueGrowth", "Revenue growth (YoY)"),
                     ("earningsGrowth", "Earnings growth (YoY)")):
        v = values.get(key)
        if isinstance(v, (int, float)) and np.isfinite(v):
            out.append(_item(f"fundamental.{key}", lab, "FUNDAMENTAL", root, _sign(v),
                             round(float(v), 4), "fraction", as_of, freshness, src,
                             "sign of growth", f"{lab} {float(v):+.1%}"))
    tpe, fpe = values.get("trailingPE"), values.get("forwardPE")
    if all(isinstance(x, (int, float)) and x and x > 0 for x in (tpe, fpe)):
        out.append(_item("fundamental.forward_vs_trailing_pe", "Forward vs trailing P/E",
                         "FUNDAMENTAL", root, 1 if fpe < tpe else -1, round(fpe / tpe, 3),
                         "ratio", as_of, freshness, src,
                         "forward < trailing (earnings expected to rise) bull, else bear",
                         f"forward P/E {fpe:.1f} vs trailing {tpe:.1f}"))
    pm = values.get("profitMargins")
    if isinstance(pm, (int, float)) and np.isfinite(pm):
        out.append(_item("fundamental.profit_margin", "Profit margin", "FUNDAMENTAL", root,
                         -1 if pm < 0 else 0, round(float(pm), 4), "fraction", as_of,
                         freshness, src, "loss-making bear; a positive margin is context, "
                         "not a direction", f"profit margin {float(pm):.1%}"))
    return out


def news_items(news: Optional[dict], *, as_of) -> list[EvidenceItem]:
    out = []
    for c in (news or {}).get("story_clusters", []):
        if c["relevance"] not in ("HIGH", "MEDIUM"):
            continue
        d = {"POSITIVE": 1, "NEGATIVE": -1}.get(c["direction"], 0)
        out.append(_item(f"news.cluster_{c['cluster_id']}", f"News: {c['event_type']}",
                         "NEWS", f"NEWS:{c['cluster_id']}", d, c["items"], "items in story",
                         c["first_known_at"], c["freshness"],
                         f"{c['publishers']} publisher(s), best source {c['best_source']['tier']}",
                         "LEXICON on the original headline; one item per story",
                         c["headline"], story_novelty=c.get("story_novelty"),
                         syndicated_copies=c["items"] - 1))
    return out


def cross_asset_items(cross: Optional[dict], *, as_of, freshness) -> list[EvidenceItem]:
    out = []
    for pair, p in ((cross or {}).get("pairs") or {}).items():
        if pair in ("SPY", "ACWI"):
            continue                              # that is MARKET, already counted
        st = p.get("state")
        d = 0
        if st == "CORRELATED" and p.get("pair_move") is not None and p.get("beta_long"):
            d = _sign(p["beta_long"] * p["pair_move"], 0.002)
        out.append(_item(f"cross.{pair}", f"{p.get('role', pair)} ({pair})", "CROSS_ASSET",
                         f"PRICE:{pair}", d, p.get("corr_recent"), "correlation", as_of,
                         freshness, "rolling correlation / beta",
                         "only CORRELATED pairs carry a direction: sign(beta x the pair's "
                         "recent move); association, not cause",
                         f"{st}: corr {p.get('corr_recent')} recent vs {p.get('corr_long')} "
                         f"long-run", state=st))
    return out


def event_items(symbol: str, ev: Optional[dict], *, as_of) -> list[EvidenceItem]:
    out = []
    s = (ev or {}).get("last_earnings_surprise")
    if s and s.get("outcome", {}).get("surprise_pct") is not None:
        sp = s["outcome"]["surprise_pct"]
        out.append(_item("event.earnings_surprise", "Last earnings surprise", "EVENT",
                         f"EVENTS:{symbol}", _sign(sp), sp, "% vs estimate",
                         s.get("event_time"), "FRESH", s.get("source", ""),
                         "sign of the reported surprise",
                         f"reported EPS {s['outcome'].get('reported_eps')} vs estimate "
                         f"{s.get('schedule', {}).get('eps_estimate')} ({sp:+.1f}%)"))
    risk = (ev or {}).get("event_risk")
    if risk:
        out.append(_item("event.risk", "Event risk in horizon", "EVENT", f"EVENTS:{symbol}",
                         0, None, "state", as_of, "FRESH" if risk["state"] != "UNKNOWN"
                         else "MISSING", "events known at T",
                         "never directional: an event is a source of variance",
                         f"{risk['state']}: {risk['basis']}"))
    return out


def model_items(predictions: list[dict], *, as_of) -> list[EvidenceItem]:
    out = []
    for p in predictions:
        src = p.get("model_id") or "unknown"
        group = MODEL_GROUP.get(src, "MODEL")
        rel = p.get("model_reliability") or {}
        d = {"LONG": 1, "SHORT": -1}.get(p.get("direction"), 0)
        root = (f"PRICE:{p.get('symbol')}" if group == "PRICE_DERIVED" else f"MODEL:{src}")
        out.append(_item(f"model.{src}", f"Model prediction: {src}", group, root, d,
                         (p.get("probability") or {}).get("value"),
                         p.get("probability_type") or "", p.get("as_of"),
                         (p.get("data_quality") or {}).get("value") or "UNKNOWN",
                         f"ledger {p.get('prediction_ref')}",
                         "the recorded direction; its number is shown with its type",
                         "; ".join(p.get("labels") or []),
                         reliability={"state": rel.get("status") or "UNKNOWN",
                                      "basis": rel.get("basis") or ""},
                         horizon_days=p.get("horizon_days")))
    return out


# ── agreement ───────────────────────────────────────────────────────────────

def group_direction(items: list[EvidenceItem]) -> dict:
    b = sum(1 for i in items if i.direction > 0)
    s = sum(1 for i in items if i.direction < 0)
    n = b + s
    if n == 0:
        state, d = "NONE", 0
    elif b >= 2 / 3 * n and b > s:
        state, d = "BULLISH", 1
    elif s >= 2 / 3 * n and s > b:
        state, d = "BEARISH", -1
    else:
        state, d = "MIXED", 0
    return {"state": state, "direction": d, "bullish_items": b, "bearish_items": s,
            "non_directional_items": len(items) - n, "items": len(items),
            "roots": sorted({i.root for i in items})}


def agreement(items: list[EvidenceItem], dependence: Optional[dict] = None) -> dict:
    by = {g: [i for i in items if i.group == g] for g in GROUPS}
    groups = {g: group_direction(v) for g, v in by.items() if v}
    bull_g = [g for g, v in groups.items() if v["direction"] > 0]
    bear_g = [g for g, v in groups.items() if v["direction"] < 0]
    mixed_g = [g for g, v in groups.items() if v["state"] == "MIXED"]
    raw_b = sum(1 for i in items if i.direction > 0)
    raw_s = sum(1 for i in items if i.direction < 0)
    largest = max(groups.items(), key=lambda kv: kv[1]["items"], default=(None, {"items": 0}))
    share = largest[1]["items"] / len(items) if items else 0.0
    return {
        "raw": {"bullish": raw_b, "bearish": raw_s,
                "non_directional": len(items) - raw_b - raw_s, "items": len(items),
                "distinct_roots": len({i.root for i in items})},
        "groups": groups,
        "independent": {"bullish_groups": bull_g, "bearish_groups": bear_g,
                        "mixed_groups": mixed_g,
                        "groups_with_evidence": len(groups)},
        "concentration": {"largest_group": largest[0], "share_of_items": round(share, 3)},
        "price_dependence": dependence or {"state": "NOT_MEASURED"},
        "statement": _statement(raw_b, raw_s, bull_g, bear_g, groups, dependence),
    }


def _statement(raw_b, raw_s, bull_g, bear_g, groups, dependence) -> str:
    parts = [f"{raw_b} bullish and {raw_s} bearish raw items come from "
             f"{len(bull_g)} bullish and {len(bear_g)} bearish independent evidence group(s)"]
    pd_ = groups.get("PRICE_DERIVED")
    if pd_ and dependence and dependence.get("state") == "MEASURED":
        parts.append(f"the {pd_['items']} price-derived items are one group; their daily "
                     f"directions behave like about "
                     f"{dependence['effective_independent_signals']} independent signals "
                     f"(mean pairwise correlation {dependence['mean_pairwise_corr']})")
    return "; ".join(parts) + "."
