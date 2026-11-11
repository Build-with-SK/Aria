"""
src/intelligence/research/engine.py
===================================
One research request, end to end, and where it sits in the pipeline:

    MARKET DATA -> PIT RESEARCH SNAPSHOT -> FEATURES / FACTORS / EVENTS
      -> INDEPENDENT EVIDENCE -> RESEARCH THESIS -> MODEL PREDICTIONS
      -> MODEL ROUTING -> CONVICTION -> PORTFOLIO RISK -> SESSION -> EXECUTION

This module runs the first five stages and READS the next two (recorded
predictions and their routing). It never calls the last four: research can
explain a view, it cannot authorise, size or send anything. Its only path
toward a trade is a compact thesis summary (`save_latest`) that the
conviction for a desk trade may cite as context (src/intelligence/
conviction.for_queued_trade) — and a conviction can never enlarge an order.

POINT IN TIME, INCLUDING THE MODELS' OWN RECORD
-----------------------------------------------
Model predictions are the calls made by T (outcomes only if graded by T), and
their reliability comes from an evidence snapshot built AS OF T: the saved
snapshot is used only if it was built at or before T; otherwise one is built
for T from the ledger. A historical query therefore cannot see a model's
later performance.

CACHING
-------
Only live requests are cached (10 minutes for "now", a day for a past
instant, keyed on symbol, instant, horizon and options). Frame-backed
requests — tests and replays — are always recomputed, so an adversarial test
that rewrites the data is answered from the rewritten data.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.intelligence.research import pit_data as P

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent
LATEST_DIR = ROOT / "data" / "intelligence" / "research" / "latest"
LIVE_TTL_S = 600
HISTORICAL_TTL_S = 86400
CALENDAR_PER_TRADING_DAY = 7 / 5
PIPELINE = ("MARKET_DATA", "PIT_RESEARCH_SNAPSHOT", "FEATURES_FACTORS_EVENTS",
            "INDEPENDENT_EVIDENCE", "RESEARCH_THESIS", "MODEL_PREDICTIONS",
            "MODEL_ROUTING", "CONVICTION", "PORTFOLIO_RISK", "SESSION", "EXECUTION")

_cache: dict = {}
_cache_lock = threading.Lock()


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


def _all_records(data) -> list[dict]:
    if isinstance(data, P.FrameResearchData):
        return list(data._model_records)
    from src.intelligence.dataset import ledger_rows
    return ledger_rows()


def evidence_as_of(at: datetime, data, *, live: bool,
                   evidence_snapshot: Optional[dict] = None) -> tuple[Optional[dict], str]:
    """(evidence snapshot valid at `at`, how it was obtained)."""
    if evidence_snapshot is not None:
        return evidence_snapshot, "supplied"
    from src.intelligence import evidence
    if live:
        saved = evidence.load()
        built = P.aware((saved or {}).get("built_at"))
        if saved and built and built <= at:
            return saved, f"saved snapshot built {built.isoformat()} (before T)"
    try:
        from src.intelligence import dataset, regrade
        snap = evidence.build(as_of=at, rows=_all_records(data),
                              regrades=regrade.load() if live else {},
                              v5_bases=dataset.v5_return_bases() if live else {})
        snap.pop("_dataset", None)
        return snap, "built as of T from the ledger"
    except Exception as e:
        logger.warning("evidence as of %s unavailable: %s", at, e)
        return None, f"unavailable: {e}"


def model_predictions(symbol: str, at: datetime, data, ev_snap: Optional[dict]) -> list[dict]:
    """The latest unexpired recorded call from each model, as of `at`."""
    from src.intelligence import conviction
    rows = P.visible_model_records(data.model_records(symbol), at)
    latest: dict = {}
    for r in sorted(rows, key=lambda r: r.get("created_at") or ""):
        created = P.aware(r.get("created_at"))
        h = int(r.get("horizon_days") or 0)
        if created is None or created + timedelta(days=h * CALENDAR_PER_TRADING_DAY) < at:
            continue                               # its horizon has elapsed
        latest[r.get("source")] = r
    out = []
    for src, r in sorted(latest.items(), key=lambda kv: str(kv[0])):
        c = conviction.from_ledger_row(r, as_of=at, snapshot=ev_snap).to_dict()
        c["symbol"] = symbol
        out.append(c)
    return out


def research(symbol: str, *, at: Optional[datetime] = None, data=None,
             horizon_days: int = 21, analogues: bool = True, cross_asset: bool = True,
             dispersion: bool = False, evidence_snapshot: Optional[dict] = None,
             use_cache: bool = True) -> dict:
    """The full research report for `symbol` at `at` (default: now)."""
    from src.intelligence.research import analogues as A
    from src.intelligence.research import evidence_graph as G
    from src.intelligence.research import snapshot as S
    from src.intelligence.research import thesis as T
    live = data is None or isinstance(data, P.LiveResearchData)
    now = datetime.now(timezone.utc)
    requested_now = at is None
    at = P.require_aware(at) if at is not None else now
    key = (symbol.upper(), at.replace(second=0, microsecond=0).isoformat(), horizon_days,
           analogues, cross_asset, dispersion)
    if live and use_cache:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and time.time() - hit[0] < (LIVE_TTL_S if (now - at) < timedelta(hours=1)
                                           else HISTORICAL_TTL_S):
            return {**hit[1], "cache": "hit"}
    t_all = time.perf_counter()
    timings = {}
    data = data or P.LiveResearchData()
    if live and requested_now:
        # A live query's instant is when its inputs were assembled: fetch,
        # THEN fix T, so a schedule fetched for this query is known at T.
        t0 = time.perf_counter()
        data.prefetch(symbol)
        timings["prefetch"] = _ms(t0)
        at = datetime.now(timezone.utc)

    t0 = time.perf_counter()
    snap = S.build(symbol, at, data, horizon_days=horizon_days, cross_asset=cross_asset,
                   dispersion=dispersion)
    timings["snapshot"] = _ms(t0)

    t0 = time.perf_counter()
    close, volume = snap.frames.get("close"), snap.frames.get("volume")
    pas = snap.get("price").as_of
    pfresh = snap.get("price").freshness
    items = []
    # A series evidently unadjusted across a known split cannot feed anything
    # computed from returns across it (src/intelligence/research/snapshot).
    split_suspect = bool(snap.get("price").provenance.get("unadjusted_split_suspected"))
    if close is not None and len(close) >= 30 and not split_suspect:
        items += G.price_items(symbol, close, volume, as_of=pas, freshness=pfresh,
                               technical=snap.get("technical").value)
    fac = snap.get("factors")
    if fac.value and fac.freshness != "CONFLICTED":
        items += G.attribution_items(symbol, fac.value, as_of=fac.as_of,
                                     freshness=fac.freshness)
    reg = snap.get("regime").value or {}
    mac = snap.get("macro")
    items += G.regime_items(reg.get("market"), (mac.value or {}).get("regime"),
                            as_of=snap.get("regime").as_of, freshness=snap.get("regime").freshness,
                            macro_freshness=mac.freshness)
    fun = snap.get("fundamentals")
    if fun.value and fun.freshness != "CONFLICTED":
        items += G.fundamental_items(symbol, fun.value, as_of=fun.as_of,
                                     freshness=fun.freshness)
    items += G.news_items(snap.get("news").value, as_of=snap.get("news").as_of)
    ca = snap.get("cross_asset")
    items += G.cross_asset_items(ca.value, as_of=ca.as_of, freshness=ca.freshness)
    ev = snap.get("event_risk")
    items += G.event_items(symbol, ev.value, as_of=ev.as_of)
    dependence = (G.price_dependence(close) if close is not None
                  else {"state": "INSUFFICIENT_DATA", "why": "no bars"})
    timings["evidence"] = _ms(t0)

    t0 = time.perf_counter()
    an = (A.find(close, snap.frames.get("benchmark"), volume, horizon=horizon_days)
          if analogues and close is not None and not split_suspect else
          {"status": A.INSUFFICIENT,
           "why": ("not requested" if not analogues else "no bars" if close is None else
                   "the price series is not adjusted across a known split")})
    timings["analogues"] = _ms(t0)

    t0 = time.perf_counter()
    if (evidence_snapshot is None and not live
            and not P.visible_model_records(data.model_records(symbol), at)):
        # Nothing recorded for this instrument by T: no reliability to evaluate,
        # and the routing default needs no evidence to be the default.
        ev_snap, ev_how = None, "not built: no prediction for this instrument was recorded by T"
    else:
        ev_snap, ev_how = evidence_as_of(at, data, live=live,
                                         evidence_snapshot=evidence_snapshot)
    models = model_predictions(symbol, at, data, ev_snap)
    items += G.model_items(models, as_of=at.isoformat())
    from src.intelligence import routing as R
    route = R.lookup((ev_snap or {}).get("routing_table"),
                     snap.instrument.get("asset_class"), horizon_days)
    timings["models_and_routing"] = _ms(t0)

    t0 = time.perf_counter()
    agree = G.agreement(items, dependence)
    thesis = T.build(snap, agree, items, analogues=an, models=models, routing=route,
                     live=live)
    timings["thesis"] = _ms(t0)
    timings["total"] = _ms(t_all)

    report = {
        "symbol": symbol.upper(), "at": at.isoformat(), "live": live,
        "horizon_days": horizon_days,
        "snapshot": snap.to_dict(),
        "evidence": {"items": [i.to_dict() for i in items], "agreement": agree},
        "analogues": an,
        "model_predictions": models,
        "routing": route,
        "evidence_snapshot": {"how": ev_how, "built_at": (ev_snap or {}).get("built_at")},
        "thesis": thesis,
        "pipeline": {"stages": list(PIPELINE),
                     "run_here": list(PIPELINE[:5]), "read_here": list(PIPELINE[5:7]),
                     "never_called": list(PIPELINE[7:]),
                     "note": "research explains; conviction, portfolio risk, the session "
                             "engine and execution stay authoritative"},
        "timings_ms": timings,
    }
    if live:
        with _cache_lock:
            _cache[key] = (time.time(), report)
            if len(_cache) > 256:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
                    _cache.pop(k, None)
    return report


def save_latest(report: dict) -> Optional[Path]:
    """A compact thesis summary the order path may READ (no network there)."""
    if not report.get("live"):
        return None
    th = report["thesis"]
    blob = {"symbol": report["symbol"], "built_at": datetime.now(timezone.utc).isoformat(),
            "information_time": report["at"], "direction": th["direction"],
            "basis": th["basis"], "summary": th["summary"],
            "scorecard": {k: v["state"] for k, v in th["quality_scorecard"].items()}}
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    path = LATEST_DIR / f"{P._safe(report['symbol'])}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_latest(symbol: str) -> Optional[dict]:
    path = LATEST_DIR / f"{P._safe(symbol.upper())}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
