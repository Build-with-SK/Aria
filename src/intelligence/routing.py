"""
src/intelligence/routing.py
===========================
Which model to consult for this asset, horizon, regime, market and data
quality — decided by evidence, or not at all.

There are no hand-set weights in this file. A model is routable for a request
only when ALL of these hold:

  * the owner has made it VALIDATED or PRODUCTION, and its promotion gate
    still passes (src/intelligence/registry);
  * it supports the request's asset class and horizon bucket;
  * in exactly that slice, its graded record is measurable (enough calls on
    enough dates) and better than chance and than always-long;
  * that slice result survives a Holm correction across every (model, slice)
    tested for this request.

If several qualify, the one with the highest lower confidence bound wins; they
are not averaged. If none qualifies the decision is the DEFAULT route, with
reason ROUTING_INSUFFICIENT_EVIDENCE: the source that produced the idea keeps
its existing role, no model is up-weighted, reliability is UNKNOWN, and nothing
about the route may increase a position.

POOR data quality short-circuits to ROUTING_DATA_QUALITY_ABSTAIN: no model's
output should influence a decision built on data that is known to be bad.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from src.intelligence import evaluation
from src.intelligence.dataset import horizon_bucket, instrument_of
from src.intelligence.multiple_testing import holm

ROUTED = "ROUTED_ON_EVIDENCE"
INSUFFICIENT = "ROUTING_INSUFFICIENT_EVIDENCE"
ABSTAIN = "ROUTING_DATA_QUALITY_ABSTAIN"
DEFAULT = "DEFAULT"

DEFAULT_ROUTE = ("the source that produced the idea keeps its existing role; no model is "
                 "up-weighted; model reliability is UNKNOWN; nothing on this route may "
                 "increase a position")
ROUTABLE_STATUSES = ("VALIDATED", "PRODUCTION")


@dataclass(frozen=True)
class RouteRequest:
    symbol: str
    horizon_days: int
    asset_class: Optional[str] = None
    venue: Optional[str] = None
    regime: Optional[str] = None             # market regime label, e.g. "BULL/LOW_VOL"
    data_quality: Optional[str] = None       # GOOD | DEGRADED | POOR | UNKNOWN


@dataclass
class RoutingDecision:
    reason_code: str
    route: str                               # a model_id, or DEFAULT
    request: dict
    slice: dict
    candidates: list = field(default_factory=list)
    family_size: int = 0
    as_of: Optional[str] = None
    default_route: str = DEFAULT_ROUTE

    @property
    def routed(self) -> bool:
        return self.reason_code == ROUTED

    def summary(self) -> str:
        if self.routed:
            return f"{ROUTED}: {self.route}"
        return f"{self.reason_code}: {DEFAULT}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["routed"] = self.routed
        d["summary"] = self.summary()
        return d


def _slice_rows(dataset, source: str, asset_class: str, bucket: str,
                regime: Optional[str]) -> tuple[list, bool]:
    rows = [r for r in dataset.for_model(source)
            if r["direction"] in ("bull", "bear") and r["asset_class"] == asset_class
            and r["horizon_bucket"] == bucket]
    if regime:
        conditioned = [r for r in rows if r.get("regime_market") == regime]
        if evaluation.rate_block(conditioned)["status"] == "MEASURED":
            return conditioned, True
    return rows, False


def route(req: RouteRequest, cards: dict, dataset, *,
          as_of: Optional[datetime] = None) -> RoutingDecision:
    asset_class, venue = req.asset_class, req.venue
    if asset_class is None or venue is None:
        ac, vn = instrument_of(req.symbol)
        asset_class, venue = asset_class or ac or "unknown", venue or vn or "unknown"
    bucket = horizon_bucket(req.horizon_days)
    request = {**asdict(req), "asset_class": asset_class, "venue": venue}
    sl = {"asset_class": asset_class, "horizon_bucket": bucket, "regime": req.regime}
    stamp = as_of.isoformat() if as_of else datetime.now().isoformat(timespec="seconds")

    if (req.data_quality or "").upper() == "POOR":
        return RoutingDecision(ABSTAIN, DEFAULT, request, sl, as_of=stamp)

    candidates, pvals, blocks = [], {}, {}
    for mid, card in cards.items():
        src = card.get("ledger_source")
        why = []
        status = card.get("status")
        if status not in ROUTABLE_STATUSES:
            why.append(f"status {status}: not VALIDATED or PRODUCTION")
        declared = card.get("supported", {}).get("asset_classes_declared") or []
        if declared and asset_class not in declared:
            why.append(f"does not cover {asset_class}")
        if src is None:
            why.append("writes no graded record")
            candidates.append({"model_id": mid, "eligible": False, "why": why})
            continue
        rows, conditioned = _slice_rows(dataset, src, asset_class, bucket, req.regime)
        rb = evaluation.rate_block(rows)
        blocks[mid] = (rb, conditioned)
        if rb["status"] != "MEASURED":
            why.append(f"slice {asset_class}/{bucket}: {rb.get('why') or 'insufficient data'}")
        else:
            v = evaluation.vs_always_long(rows)
            if v.get("status") == "MEASURED":
                pvals[mid] = (v["p_value"] if v["model_right_long_wrong"] >
                              v["model_wrong_long_right"] else 1.0)
            else:
                why.append("too few disagreements with always-long to test")
        candidates.append({"model_id": mid, "eligible": not why, "why": why,
                           "slice_rate": rb.get("rate"), "slice_n": rb.get("n"),
                           "regime_conditioned": conditioned})

    adjusted = holm(pvals)
    best, best_lo = None, -1.0
    for c in candidates:
        mid = c["model_id"]
        if mid in adjusted:
            c["holm_p"] = adjusted[mid]
        rb = blocks.get(mid, (None, False))[0]
        if not c["eligible"]:
            continue
        if adjusted.get(mid, 1.0) >= 0.05 or (rb or {}).get("verdict") != "better than chance":
            c["eligible"] = False
            c["why"].append("not significant after the Holm correction")
            continue
        lo = min(rb["ci95_wilson"][0], rb["ci95_cluster"][0])
        if lo > best_lo:
            best, best_lo = mid, lo
    if best is None:
        return RoutingDecision(INSUFFICIENT, DEFAULT, request, sl, candidates,
                               family_size=len(pvals), as_of=stamp)
    return RoutingDecision(ROUTED, best, request, sl, candidates,
                           family_size=len(pvals), as_of=stamp)


def table(cards: dict, dataset, *, as_of: Optional[datetime] = None) -> dict:
    """A decision for every (asset class, horizon bucket) any model declares,
    precomputed so the execution path reads a dict instead of re-evaluating."""
    keys = set()
    for card in cards.values():
        for ac in card.get("supported", {}).get("asset_classes_declared") or []:
            for h in card.get("supported", {}).get("horizons_days") or [1]:
                keys.add((ac, horizon_bucket(h)))
    for r in dataset.rows:
        keys.add((r["asset_class"], r["horizon_bucket"]))
    sample_h = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, ">3M": 126, "UNKNOWN": 1}
    out = {}
    for ac, b in sorted(keys):
        d = route(RouteRequest(symbol="", horizon_days=sample_h.get(b, 1),
                               asset_class=ac, venue="any"), cards, dataset, as_of=as_of)
        out[f"{ac}|{b}"] = d.to_dict()
    return out


def lookup(routing_table: dict, asset_class: Optional[str], horizon_days) -> dict:
    """The precomputed decision for a slice, or the DEFAULT route if absent."""
    key = f"{asset_class or 'unknown'}|{horizon_bucket(horizon_days)}"
    d = (routing_table or {}).get(key)
    if d:
        return d
    return {"reason_code": INSUFFICIENT, "route": DEFAULT, "routed": False,
            "summary": f"{INSUFFICIENT}: {DEFAULT}", "slice": {"key": key},
            "default_route": DEFAULT_ROUTE,
            "why": "no evidence has been computed for this slice"}
