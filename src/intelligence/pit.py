"""
src/intelligence/pit.py
=======================
A prediction at instant T may depend only on what was knowable at T.

`PointInTimeData` holds raw inputs — daily bars (price and volume), FX
fixings, fundamentals with the time each value became public, news with its
publication time, factor returns, macro-regime readings with their release
time, and graded model records — and hands each one out only as known at T:

    bars          a daily bar dated d is known once its session has closed
                  (src/markets/sessions.bar_known_by); a bar from today's
                  still-open session is NOT known
    fx, factors   a daily observation dated d is known from 00:00 UTC on d+1
    fundamentals  a value is known from its `available_at` (the filing or
                  release time, never the period it describes)
    news          an item is known from its `published_at`
    macro regime  a reading is known from its `available_at`
    model records a call is visible if made by T; its OUTCOME only if it was
                  graded by T (resolved_at <= T). Otherwise it is pending.

`predict_at(symbol, T, data)` is the reference point-in-time prediction: the
technical composite (the `technical` model in the ledger) on the bars known at
T, plus every input of the decision context — market regime, FX, fundamentals,
news, factor exposure, the macro reading, the evidence and routing as of T —
and a hash of all of it. tests/test_pit_adversarial.py mutates every input
AFTER T and requires the result to be identical, then mutates each input
BEFORE T and requires it to change, so the test is shown to have power.

Production predictions are made live and recorded at creation, which is what
makes them point in time; this module is how that property is TESTED for the
code that builds a prediction's context, and how a historical replay must
read its inputs.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from src.intelligence import regime as _regime

TECH_DIRECTION = {"Strong Buy": "bull", "Buy": "bull", "Sell": "bear",
                  "Strong Sell": "bear", "Neutral": "neutral"}
NEWS_LOOKBACK_DAYS = 7
BETA_WINDOW = 60


def _aware(at) -> datetime:
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    if at.tzinfo is None:
        raise ValueError("a point-in-time read needs an aware instant")
    return at


def _end_of_day_known(d, at: datetime) -> bool:
    """A daily observation dated d is known from 00:00 UTC on d+1."""
    return datetime.combine(d + timedelta(days=1), time(0), timezone.utc) <= at


@dataclass
class PointInTimeData:
    bars: dict = field(default_factory=dict)            # symbol -> OHLCV DataFrame
    venues: dict = field(default_factory=dict)          # symbol -> venue code
    fx: dict = field(default_factory=dict)              # "EURUSD" -> Series (daily)
    fundamentals: list = field(default_factory=list)    # {symbol, field, value, available_at}
    news: list = field(default_factory=list)            # {symbol, headline, sentiment, published_at}
    factor_returns: Optional[pd.DataFrame] = None       # daily, columns = factors
    macro_regimes: list = field(default_factory=list)   # {label, available_at}
    model_records: list = field(default_factory=list)   # ledger-shaped rows
    benchmark: str = "SPY"

    # ── readers, each cut at `at` ───────────────────────────────────────────
    def bars_at(self, symbol: str, at) -> Optional[pd.DataFrame]:
        from src.markets import sessions
        at = _aware(at)
        f = self.bars.get(symbol)
        if f is None or f.empty:
            return None
        venue = self.venues.get(symbol) or sessions.resolve(symbol).venue
        f = f.sort_index()
        keep = [sessions.bar_known_by(venue, ts.date(), at) for ts in f.index]
        out = f[np.array(keep, dtype=bool)]
        return out.copy() if len(out) else None

    def fx_at(self, pair: str, at) -> Optional[float]:
        at = _aware(at)
        s = self.fx.get(pair)
        if s is None:
            return None
        known = [v for ts, v in s.sort_index().items() if _end_of_day_known(ts.date(), at)]
        return float(known[-1]) if known else None

    def fundamentals_at(self, symbol: str, at) -> dict:
        at = _aware(at)
        out: dict = {}
        for f in sorted(self.fundamentals, key=lambda x: _aware(x["available_at"])):
            if f["symbol"] == symbol and _aware(f["available_at"]) <= at:
                out[f["field"]] = f["value"]
        return out

    def news_at(self, symbol: str, at) -> list:
        at = _aware(at)
        lo = at - timedelta(days=NEWS_LOOKBACK_DAYS)
        return [n for n in self.news if n["symbol"] == symbol
                and lo <= _aware(n["published_at"]) <= at]

    def factors_at(self, at) -> Optional[pd.DataFrame]:
        at = _aware(at)
        f = self.factor_returns
        if f is None or f.empty:
            return None
        mask = np.array([_end_of_day_known(ts.date(), at) for ts in f.index], dtype=bool)
        out = f.sort_index()[mask]
        return out if len(out) else None

    def macro_regime_at(self, at) -> Optional[str]:
        at = _aware(at)
        known = [m for m in self.macro_regimes if _aware(m["available_at"]) <= at]
        return max(known, key=lambda m: _aware(m["available_at"]))["label"] if known else None

    def model_records_at(self, at) -> list:
        """Calls made by `at`; an outcome only if it was graded by `at`."""
        at = _aware(at)
        cut = at.astimezone().replace(tzinfo=None)
        out = []
        for r in self.model_records:
            created = datetime.fromisoformat(str(r["created_at"]))
            created = created.astimezone().replace(tzinfo=None) if created.tzinfo else created
            if created > cut:
                continue
            row = dict(r)
            ra = row.get("resolved_at")
            if ra:
                rdt = datetime.fromisoformat(str(ra))
                rdt = rdt.astimezone().replace(tzinfo=None) if rdt.tzinfo else rdt
                if rdt > cut:
                    row.update(resolved=0, resolved_at=None, correct=None,
                               actual_return=None, price_end=None, outcome_note=None)
            out.append(row)
        return out


# ── the snapshot and the prediction ─────────────────────────────────────────

def _r(x, nd: int = 8):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return None if not math.isfinite(f) else round(f, nd)


def _beta(asset: pd.Series, factors: pd.DataFrame) -> dict:
    r = np.log(asset).diff().dropna()
    r.index = [ts.date() for ts in r.index]
    fac = factors.copy()
    fac.index = [ts.date() for ts in fac.index]
    joined = pd.concat([r.rename("asset"), fac], axis=1, join="inner").dropna().tail(BETA_WINDOW)
    out = {}
    for col in factors.columns:
        x = joined[col].to_numpy(float)
        y = joined["asset"].to_numpy(float)
        if len(x) >= 20 and x.var() > 0:
            out[col] = _r(np.cov(x, y, ddof=1)[0, 1] / x.var(ddof=1), 6)
        else:
            out[col] = None
    return out


def snapshot(symbol: str, at, data: PointInTimeData, *, fx_pair: Optional[str] = None) -> dict:
    """Every input a prediction about `symbol` at `at` may use."""
    at = _aware(at)
    snap: dict = {"symbol": symbol, "as_of": at.isoformat()}
    bars = data.bars_at(symbol, at)
    if bars is not None and "Close" in bars:
        c = bars["Close"].astype(float)
        snap["last_bar"] = str(bars.index[-1].date())
        snap["last_close"] = _r(c.iloc[-1])
        snap["ret_20d"] = _r(c.iloc[-1] / c.iloc[-21] - 1) if len(c) > 21 else None
        lr = np.log(c).diff().dropna()
        snap["vol_20d"] = _r(lr.tail(20).std(ddof=1) * math.sqrt(252)) if len(lr) >= 20 else None
        if "Volume" in bars and len(bars) >= 20:
            v = bars["Volume"].astype(float)
            snap["volume_ratio"] = _r(v.iloc[-1] / v.tail(20).mean()) if v.tail(20).mean() else None
        from src.data.technical_summary import score_frame
        tech = score_frame(bars)
        snap["technical"] = ({"summary": tech["summary"], "counts": tech["counts"]}
                             if tech else None)
    else:
        snap["last_bar"] = None
        snap["technical"] = None
    bench = data.bars_at(data.benchmark, at)
    snap["market_regime"] = (
        {k: v for k, v in _regime.classify_market(bench["Close"]).items()
         if k in ("trend", "vol", "risk", "label", "last_bar")}
        if bench is not None else None)
    snap["fx"] = {fx_pair: _r(data.fx_at(fx_pair, at))} if fx_pair else {}
    snap["fundamentals"] = {k: _r(v) for k, v in sorted(data.fundamentals_at(symbol, at).items())}
    news = data.news_at(symbol, at)
    snap["news"] = {"count": len(news),
                    "mean_sentiment": _r(np.mean([n.get("sentiment", 0.0) for n in news]))
                    if news else None}
    fac = data.factors_at(at)
    snap["factor_beta"] = (_beta(bars["Close"].astype(float), fac)
                           if fac is not None and bars is not None else {})
    snap["macro_regime"] = data.macro_regime_at(at)
    return snap


def evidence_at(at, data: PointInTimeData) -> dict:
    """The graded record, the registry status and the routing for the
    technical model, exactly as they could have been computed at `at`."""
    from src.intelligence import evaluation, registry, routing
    from src.intelligence import dataset as _ds
    from src.intelligence import calibration
    at = _aware(at)
    rows = data.model_records_at(at)
    ds = _ds.build(rows, as_of=at, regrades={}, v5_bases={})
    ev = evaluation.evaluate_all(ds)
    cal = {m: calibration.status(ds.for_model(m), model_id=m) for m in ds.models()}
    cards = registry.cards(ds, ev, cal, ledger_rows=rows, as_of=at)
    tech = ev.get("technical", {}).get("directional_accuracy", {})
    return {"dataset_hash": ds.dataset_hash,
            "technical_record": {k: tech.get(k) for k in ("status", "n", "rate", "verdict")},
            "technical_status": cards.get("technical", {}).get("status"),
            "routing": routing.lookup(routing.table(cards, ds, as_of=at), "equity", 10)
            .get("reason_code")}


def predict_at(symbol: str, at, data: PointInTimeData, *, fx_pair: Optional[str] = None,
               with_evidence: bool = True) -> dict:
    """The technical model's call at `at`, with its full point-in-time context."""
    snap = snapshot(symbol, at, data, fx_pair=fx_pair)
    tech = snap.get("technical") or {}
    direction = TECH_DIRECTION.get(tech.get("summary"), "neutral") if tech else None
    out = {"symbol": symbol, "as_of": snap["as_of"], "model": "technical",
           "direction": direction, "snapshot": snap}
    if with_evidence:
        out["evidence"] = evidence_at(at, data)
    blob = json.dumps(out, sort_keys=True, default=str)
    out["snapshot_hash"] = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return out
