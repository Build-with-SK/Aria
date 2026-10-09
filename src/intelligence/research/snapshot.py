"""
src/intelligence/research/snapshot.py
=====================================
The canonical point-in-time research object for one instrument at instant T.

    ResearchSnapshot
        instrument, asset_class, venue, information_time, market_session
        price, returns, volume, volatility, liquidity, technical
        fundamentals, macro, factors, news, sentiment, cross_asset, regime,
        event_risk
        provenance, freshness, missingness, contradictions

Every component is a src/intelligence/research/components.Component with its
own as_of, source, version, freshness and PIT status. A component that cannot
be built at T is MISSING with the reason — it is never filled from today's
value, a neighbour, or a default. A provider that raises is recorded as a
provider failure, and everything that depends on it says so.

Freshness:
    price         FRESH if the last bar is the venue's latest completed
                  session at T (crypto: the last full UTC day), else STALE
    fundamentals  FRESH up to 7 days after the snapshot was taken, STALE after
    macro         by series frequency: daily 5 days, monthly 45 days after the
                  observation period
    news          FRESH if the newest story became known within 48 h
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from src.intelligence.research import components as C
from src.intelligence.research import pit_data as P

BENCHMARK = "SPY"
FUNDAMENTALS_FRESH_DAYS = 7
MACRO_FRESH_DAYS = {"daily": 5, "monthly": 45}
CONFLICT_TOLERANCE = 0.05
FUND_FIELDS = ("trailingPE", "forwardPE", "priceToBook", "revenueGrowth", "earningsGrowth",
               "profitMargins", "operatingMargins", "returnOnEquity", "debtToEquity",
               "marketCap", "dividendYield", "beta", "sector", "industry")


@dataclass
class ResearchSnapshot:
    symbol: str
    information_time: str
    instrument: dict
    components: dict = field(default_factory=dict)        # name -> Component
    provenance: dict = field(default_factory=dict)
    frames: dict = field(default_factory=dict)            # in-process only

    def get(self, name: str) -> C.Component:
        return self.components.get(name) or C.missing(name, "not built")

    @property
    def freshness(self) -> dict:
        return {k: v.freshness for k, v in self.components.items()}

    @property
    def missingness(self) -> list[dict]:
        return [{"component": k, "why": v.missing} for k, v in self.components.items()
                if v.freshness == C.MISSING]

    def digest(self) -> str:
        blob = json.dumps({k: v.to_dict() for k, v in sorted(self.components.items())},
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "information_time": self.information_time,
                "instrument": self.instrument,
                "components": {k: v.to_dict() for k, v in self.components.items()},
                "freshness": self.freshness, "missingness": self.missingness,
                "provenance": self.provenance, "digest": self.digest()}


def _expected_last_bar(venue: Optional[str], asset_class: str, at: datetime):
    if asset_class in ("crypto", "forex") or venue in ("CRYPTO", "FX"):
        return (at.astimezone(timezone.utc) - timedelta(days=1)).date()
    if not venue:
        return None
    from src.markets import sessions
    try:
        return sessions.latest_completed_session(venue, at)
    except Exception:
        return None


def _price_freshness(last_bar, expected) -> str:
    if last_bar is None:
        return C.MISSING
    if expected is None:
        return C.UNKNOWN
    return C.FRESH if last_bar >= expected else C.STALE


def build(symbol: str, at: datetime, data: P.ResearchData, *, horizon_days: int = 21,
          cross_asset: bool = True, dispersion: bool = False) -> ResearchSnapshot:
    """Every component for `symbol` as knowable at `at`."""
    at = P.require_aware(at)
    from src.risk.portfolio_risk import describe
    d = describe(symbol)
    ac = d["asset_class"]
    snap = ResearchSnapshot(symbol, at.isoformat(), {"symbol": symbol, "asset_class": ac,
                                                     "venue": d["venue"],
                                                     "country": d["country"]})
    ver = getattr(data, "version", "")
    snap.provenance = {"data_source": getattr(data, "name", "?"), "data_version": ver,
                       "provider_failures": {}}
    comp = snap.components

    # ── market session ────────────────────────────────────────────────────
    try:
        from src.markets import sessions
        s = sessions.session(symbol, at, asset_class=ac)
        comp["market_session"] = C.Component("market_session", s.state.value, at.isoformat(),
                                              "src/markets/sessions", "", C.FRESH,
                                              C.PIT_BY_CONSTRUCTION,
                                              {"venue": s.venue, "next_open": s.next_open})
    except Exception as e:
        comp["market_session"] = C.missing("market_session", f"session engine: {e}")

    # ── the tape ──────────────────────────────────────────────────────────
    try:
        bars = data.history(symbol, at)
    except Exception as e:
        bars = None
        snap.provenance["provider_failures"]["history"] = str(e)[:200]
    close = bars["Close"].astype(float).dropna() if bars is not None and "Close" in bars else None
    if close is not None and close.empty:
        close = None
    volume = (bars["Volume"].astype(float) if bars is not None and "Volume" in bars
              and float(bars["Volume"].fillna(0).sum()) > 0 else None)
    snap.frames.update(close=close, volume=volume, bars=bars)
    last_bar = close.index[-1].date() if close is not None else None
    expected = _expected_last_bar(d["venue"], ac, at)
    pfresh = _price_freshness(last_bar, expected)
    pas = (str(last_bar) if last_bar else None)
    if close is None:
        why = snap.provenance["provider_failures"].get("history") or "no bars known at T"
        for name in ("price", "returns", "volume", "volatility", "liquidity", "technical"):
            comp[name] = C.missing(name, why, source="bars")
    else:
        prov = {"bars_known": len(close), "last_bar": pas,
                "expected_last_bar": str(expected) if expected else None,
                "adjustment": "split+dividend (vendor)"}
        comp["price"] = C.Component("price", round(float(close.iloc[-1]), 6), pas, "bars",
                                    ver, pfresh, C.PIT_BY_CONSTRUCTION, prov)
        rets = {f"{n}d": (round(float(close.iloc[-1] / close.iloc[-1 - n] - 1), 6)
                          if len(close) > n else None) for n in (1, 5, 21, 63, 252)}
        comp["returns"] = C.Component("returns", rets, pas, "bars", ver, pfresh,
                                      C.PIT_BY_CONSTRUCTION, {})
        lr = np.log(close).diff().dropna()
        if len(lr) >= 20:
            v20 = float(lr.iloc[-20:].std(ddof=1) * math.sqrt(252))
            hist = (lr.rolling(20).std() * math.sqrt(252)).dropna().iloc[-252:]
            pct = float((hist <= v20).mean()) if len(hist) >= 60 else None
            comp["volatility"] = C.Component(
                "volatility", {"realised_20d": round(v20, 5),
                               "percentile_1y": None if pct is None else round(pct, 3),
                               "regime": (None if pct is None else "HIGH_VOL" if pct > 0.8
                                          else "LOW_VOL" if pct < 0.2 else "NORMAL_VOL")},
                pas, "bars", ver, pfresh, C.PIT_BY_CONSTRUCTION, {"n": len(hist)})
        else:
            comp["volatility"] = C.missing("volatility", "fewer than 20 returns")
        if volume is not None and len(volume) >= 21:
            avg20 = float(volume.iloc[-21:-1].mean())
            comp["volume"] = C.Component(
                "volume", {"last": float(volume.iloc[-1]), "avg_20d": round(avg20, 1),
                           "ratio": round(float(volume.iloc[-1]) / avg20, 3) if avg20 else None},
                pas, "bars", ver, pfresh, C.PIT_BY_CONSTRUCTION, {})
            comp["liquidity"] = C.Component(
                "liquidity", {"adv_shares_20d": round(avg20, 1),
                              "adv_value_native_20d": round(avg20 * float(close.iloc[-1]), 1),
                              "spread": None},
                pas, "bars", ver, pfresh, C.PIT_BY_CONSTRUCTION,
                {"spread_missing": "no quote history is kept, so the spread at T is "
                                   "unknown"})
        else:
            comp["volume"] = C.missing("volume", "no volume reported (FX and some indices "
                                                 "report none)")
            comp["liquidity"] = C.missing("liquidity", "no volume history known at T")
        try:
            from src.data.technical_summary import score_frame
            t = score_frame(bars.loc[close.index])
            comp["technical"] = (C.Component("technical", {"summary": t["summary"],
                                                           "counts": t["counts"]},
                                             pas, "src/data/technical_summary.score_frame",
                                             ver, pfresh, C.PIT_BY_CONSTRUCTION, {})
                                 if t else C.missing("technical", "fewer than 30 bars"))
        except Exception as e:
            comp["technical"] = C.missing("technical", f"technical summary failed: {e}")

    # ── fundamentals ──────────────────────────────────────────────────────
    comp["fundamentals"] = _fundamentals(symbol, at, data, ac, snap)

    # ── macro ─────────────────────────────────────────────────────────────
    comp["macro"] = _macro(at, data, snap)

    # ── regime (market) ───────────────────────────────────────────────────
    try:
        bench = data.history(BENCHMARK, at)
        bclose = bench["Close"].astype(float).dropna() if bench is not None else None
    except Exception as e:
        bclose = None
        snap.provenance["provider_failures"]["benchmark"] = str(e)[:200]
    snap.frames["benchmark"] = bclose
    from src.intelligence.regime import classify_market
    if bclose is not None and len(bclose):
        mr = classify_market(bclose)
        mr["benchmark"] = BENCHMARK
        comp["regime"] = C.Component(
            "regime", {"market": mr, "macro": (comp["macro"].value or {}).get("regime")},
            mr.get("last_bar"), "src/intelligence/regime.classify_market + macro quadrant",
            ver, _price_freshness(bclose.index[-1].date(), _expected_last_bar("XNYS", "equity", at)),
            C.PIT_BY_CONSTRUCTION, {})
    else:
        comp["regime"] = C.missing("regime", "no benchmark bars known at T")

    # ── factors / relative return ─────────────────────────────────────────
    from src.intelligence.research import attribution
    try:
        attr = attribution.attribute(symbol, at, _MarketDataView(data))
        comp["factors"] = C.Component(
            "factors", attr, pas, "src/intelligence/research/attribution", ver,
            pfresh if attr.get("status") == "MEASURED" else C.MISSING,
            C.PIT_BY_CONSTRUCTION, {"missing_factors": attr.get("missing_factors")},
            None if attr.get("status") == "MEASURED" else attr.get("why", "insufficient data"))
    except Exception as e:
        comp["factors"] = C.missing("factors", f"attribution failed: {e}")

    # ── cross-asset ───────────────────────────────────────────────────────
    if cross_asset:
        from src.intelligence.research import cross_asset as X
        try:
            ca = X.analyse(symbol, at, _MarketDataView(data), asset_class=ac,
                           sector=(comp["fundamentals"].value or {}).get("sector")
                           or (comp["factors"].value or {}).get("sector"))
            if dispersion:
                ca["dispersion"] = X.dispersion(at, _MarketDataView(data))
            st = ca.get("status")
            comp["cross_asset"] = C.Component(
                "cross_asset", ca, pas, "src/intelligence/research/cross_asset", ver,
                pfresh if st == "MEASURED" else C.NO_DATA if st == "NOT_APPLICABLE" else C.MISSING,
                C.PIT_BY_CONSTRUCTION, {},
                None if st == "MEASURED" else ca.get("why", st))
        except Exception as e:
            comp["cross_asset"] = C.missing("cross_asset", f"cross-asset failed: {e}")

    # ── events ────────────────────────────────────────────────────────────
    comp["event_risk"] = _events(symbol, at, data, snap, horizon_days, close)
    _split_boundary_check(comp, close, at)

    # ── news and the sentiment derived from it ───────────────────────────
    comp["news"], comp["sentiment"] = _news(symbol, at, data, snap)
    snap.provenance["provider_status"] = data.provider_status()
    return snap


class _MarketDataView:
    """ResearchData seen as src/risk MarketData (history only)."""

    def __init__(self, data):
        self.data = data

    def history(self, symbol, at):
        return self.data.history(symbol, at)


def _fundamentals(symbol, at, data, ac, snap) -> C.Component:
    if ac in ("crypto", "forex", "futures", "index"):
        return C.Component("fundamentals", None, None, "", "", C.NO_DATA, C.UNAVAILABLE, {},
                           f"{ac} has no company accounts")
    try:
        raw = data.fundamentals(symbol)
    except Exception as e:
        snap.provenance["provider_failures"]["fundamentals"] = str(e)[:200]
        return C.missing("fundamentals", f"provider failed: {e}")
    vis = P.visible_fundamentals(raw, symbol, at)
    if not vis:
        first = min((s.available_at for s in raw if s.symbol == symbol), default=None)
        return C.missing("fundamentals",
                         ("no fundamentals snapshot had been taken by T"
                          + (f" (the first is {first.isoformat()}); today's numbers are "
                             f"not used for an earlier instant" if first else "")),
                         pit=C.LIVE_ONLY)
    latest_by_source: dict = {}
    for s in vis:
        latest_by_source[s.source] = s
    latest = max(latest_by_source.values(), key=lambda s: s.available_at)
    conflicts = {}
    if len(latest_by_source) > 1:
        for f in FUND_FIELDS:
            vals = {src: s.values.get(f) for src, s in latest_by_source.items()
                    if isinstance(s.values.get(f), (int, float))}
            if len(vals) > 1:
                lo, hi = min(vals.values()), max(vals.values())
                if abs(hi - lo) > CONFLICT_TOLERANCE * max(abs(hi), abs(lo), 1e-9):
                    conflicts[f] = vals
    age_days = (at - latest.available_at).total_seconds() / 86400
    fresh = (C.CONFLICTED if conflicts else
             C.FRESH if age_days <= FUNDAMENTALS_FRESH_DAYS else C.STALE)
    values = {f: latest.values.get(f) for f in FUND_FIELDS if f in latest.values}
    if conflicts:
        for f in conflicts:
            values[f] = None                        # not chosen silently
    return C.Component("fundamentals", values, latest.available_at.isoformat(), latest.source,
                       "", fresh, C.PIT_VERIFIED,
                       {"snapshots_known": len(vis), "age_days": round(age_days, 1),
                        "sources": sorted(latest_by_source),
                        "conflicts": conflicts,
                        "caveat": "the vendor does not say which filing a number reflects; "
                                  "the snapshot is knowable from when it was taken"})


def _macro(at, data, snap) -> C.Component:
    try:
        raw = data.macro()
    except Exception as e:
        snap.provenance["provider_failures"]["macro"] = str(e)[:200]
        return C.missing("macro", f"provider failed: {e}")
    vis = P.visible_macro(raw, at)
    if not vis:
        return C.missing("macro", "no macro release known at T", pit=C.PIT_VERIFIED)
    latest: dict = {}
    for o in vis:
        cur = latest.get(o.series)
        if cur is None or (o.observation_date, o.known_at()) > (cur.observation_date, cur.known_at()):
            latest[o.series] = o
    fresh = []
    series = {}
    for k, o in latest.items():
        lim = MACRO_FRESH_DAYS.get(o.frequency, 45)
        age = (at.date() - o.observation_date).days
        f = C.FRESH if age <= lim else C.STALE
        fresh.append(f)
        series[k] = {"value": o.value, "unit": o.unit,
                     "observation_date": o.observation_date.isoformat(),
                     "known_at": o.known_at().isoformat(), "source": o.source,
                     "known_at_basis": "release time" if o.release_time else
                                       "retrieval time (no release time published)",
                     "freshness": f}
    from src.macro.regime import classify
    regime_inputs = {"cpi_yoy": series.get("cpi_yoy", {}).get("value"),
                     "yield_spread_10y2y": series.get("yield_spread_10y2y", {}).get("value"),
                     "vix": series.get("vix", {}).get("value"),
                     "unemployment_rate": series.get("unemployment_rate", {}).get("value")}
    reg = classify(regime_inputs)
    return C.Component("macro", {"series": series,
                                 "regime": {"regime": reg["regime"], "coverage": reg["coverage"],
                                            "assumed_inputs": reg["assumed_inputs"]}},
                       max(o.known_at() for o in latest.values()).isoformat(),
                       "src/macro/feeds observations known at T", "", C.worst(fresh),
                       C.PIT_VERIFIED, {"series_known": len(latest)})


def _events(symbol, at, data, snap, horizon_days, close) -> C.Component:
    from src.intelligence.research import events as E
    ok = True
    raw = []
    try:
        raw = list(data.events(symbol))
    except Exception as e:
        ok = False
        snap.provenance["provider_failures"]["events"] = str(e)[:200]
    status = data.provider_status().get("events", "OK")
    if str(status).startswith("FAILED"):
        ok = False
    try:
        raw += E.macro_release_events(data.macro())
    except Exception:
        pass
    vis = P.visible_events(raw, at)
    wstart = None
    if close is not None and len(close) > 21:
        wstart = P.aware(close.index[-22])
    ctx = E.context(vis, at, horizon_days=horizon_days, provider_ok=ok, window_start=wstart)
    ctx["_splits_known"] = [{"effective_time": (e.effective_time or e.event_time).isoformat(),
                             "ratio": e.outcome.get("ratio")}
                            for e in vis if e.kind == "SPLIT" and e.event_time <= at]
    return C.Component("event_risk", ctx, at.isoformat(), "events known at T", "",
                       C.FRESH if ok else C.MISSING, C.PIT_VERIFIED,
                       {"events_known": len(vis), "provider_status": status},
                       None if ok else "event provider failed: event risk is UNKNOWN")


SPLIT_MATCH_TOLERANCE = 0.15


def _split_boundary_check(comp, close, at) -> None:
    """A known split inside the price history, and a jump across it matching
    its ratio, means the series is NOT adjusted for it: returns across that
    date are not comparable, and saying so beats attributing a phantom move."""
    ev = (comp.get("event_risk") or C.missing("event_risk", "")).value or {}
    splits = [e for e in ev.get("_splits_known", [])]
    if close is None or not splits:
        return
    suspects = []
    for e in splits:
        eff = P.aware(e["effective_time"])
        before = close[close.index < pd.Timestamp(eff.date())]
        after = close[close.index >= pd.Timestamp(eff.date())]
        ratio = float(e.get("ratio") or 0)
        if before.empty or after.empty or ratio <= 0:
            continue
        jump = float(before.iloc[-1] / after.iloc[0])
        if abs(jump / ratio - 1) <= SPLIT_MATCH_TOLERANCE:
            suspects.append({"split_date": str(eff.date()), "ratio": ratio,
                             "price_jump": round(jump, 3)})
    comp["price"].provenance["unadjusted_split_suspected"] = suspects
    if suspects:
        why = ("the price series jumps by the split ratio across "
               + ", ".join(x["split_date"] for x in suspects)
               + ": it is not adjusted for the split, so returns across it are not "
                 "comparable")
        for name in ("returns", "factors"):
            c = comp.get(name)
            if c is not None and c.value is not None:
                c.freshness = C.CONFLICTED
                c.missing = why


def _news(symbol, at, data, snap):
    from src.intelligence.research import news as N
    try:
        raw = data.news(symbol)
    except Exception as e:
        snap.provenance["provider_failures"]["news"] = str(e)[:200]
        miss = C.missing("news", f"provider failed: {e}")
        return miss, C.missing("sentiment", "derived from news, which failed")
    status = {k: v for k, v in data.provider_status().items() if k.startswith("news")}
    vis = P.visible_news(raw, at)
    out = N.analyse(vis, at, provider_status=status)
    newest = out.get("newest_known_at")
    if out["status"] == "MISSING":
        news = C.missing("news", f"news provider unavailable: {status}")
    else:
        fresh = (C.NO_DATA if out["status"] == "NO_DATA" else
                 C.FRESH if out["fresh_stories"] else C.STALE)
        news = C.Component("news", out, newest, "research eye + NewsAPI", "", fresh,
                           C.PIT_VERIFIED, {"items": out["items"], "clusters": out["clusters"]})
    stories = [c for c in out.get("story_clusters", []) if c["relevance"] != "LOW"]
    pos = sum(1 for c in stories if c["direction"] == "POSITIVE")
    neg = sum(1 for c in stories if c["direction"] == "NEGATIVE")
    sent = C.Component("sentiment", {"positive_stories": pos, "negative_stories": neg,
                                     "stories": len(stories)},
                       newest, "derived from NEWS clusters (LEXICON)", "", news.freshness,
                       news.pit, {"independent_of_news": False,
                                  "note": "not separate evidence: the same headlines"},
                       news.missing)
    return news, sent
