"""
src/intelligence/baseline.py
============================
The data-integrity baseline of the prediction ledger — counts, never claims.

`audit(rows)` answers, per model and overall: how many predictions there are,
how many carry a valid probability and of which type, how they split by
horizon, asset class, venue and regime, how many are graded, pending,
undecidable or duplicated, and — field by field — how many carry each piece of
provenance a reproducible evaluation needs. Nothing is inferred to fill a gap:
a field that was not recorded is counted as missing.

It is a pure function of ledger rows, so it is tested on synthetic rows with
known answers, and its output holds AGGREGATES only (no tickers), so it can be
committed (docs/baselines/model_intelligence_baseline.json).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from src.intelligence import semantics
from src.intelligence.dataset import (horizon_bucket, instrument_of, macro_regime,
                                      model_hash)

#: Phase 1's checklist: field -> how presence is judged on a ledger row.
FIELDS = {
    "prediction_id": lambda r: bool(r.get("id")),
    "symbol": lambda r: bool(r.get("subject")),
    "provider_symbol": lambda r: bool(r.get("provider_symbol")),
    "timestamp": lambda r: bool(r.get("created_at")),
    "horizon": lambda r: r.get("horizon_days") is not None,
    "direction": lambda r: (r.get("direction") or "") in ("bull", "bear", "neutral"),
    "probability": lambda r: semantics.scorable_probability(r)[0] is not None,
    "probability_kind": lambda r: semantics.type_of(r) is not semantics.ProbabilityType.UNKNOWN,
    "model": lambda r: bool(r.get("source")),
    "model_version": lambda r: bool(r.get("model_version")),
    "model_hash": lambda r: bool(r.get("model_hash") or model_hash(r.get("model_version"))),
    "data_timestamp": lambda r: bool(r.get("data_as_of")),
    "market_session": lambda r: bool(r.get("market_session") or r.get("information_session")),
    "venue": lambda r: bool(r.get("venue")),
    "regime": lambda r: bool(r.get("regime_at")),
    "regime_basis": lambda r: bool(r.get("regime_basis")),
    "feature_version": lambda r: bool(r.get("feature_version")),
    "routing_decision": lambda r: bool(r.get("routing_decision")),
    "calibration_version": lambda r: bool(r.get("calibration_version")),
    "grading_timestamp": lambda r: (not r.get("resolved")) or bool(r.get("resolved_at")),
    "grading_status": lambda r: r.get("resolved") is not None,
}


def audit(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    from src.intelligence import provenance
    by_model: dict = defaultdict(list)
    for r in rows:
        by_model[r.get("source") or "unknown"].append(r)

    def block(rs: list) -> dict:
        types = Counter(semantics.type_of(r).value for r in rs)
        not_scored = Counter(semantics.scorable_probability(r)[1] for r in rs
                             if semantics.scorable_probability(r)[1])
        ac, venue = Counter(), Counter()
        for r in rs:
            a, v = instrument_of(r.get("provider_symbol") or r.get("subject") or "")
            ac[a or "unknown"] += 1
            venue[(r.get("venue") or v or "unknown")] += 1
        regimes, regime_status = Counter(), Counter()
        for r in rs:
            label, status = macro_regime(r)
            regimes[label or "none"] += 1
            regime_status[status] += 1
        graded = [r for r in rs if r.get("resolved")]
        refs = Counter(r.get("external_ref") for r in rs if r.get("external_ref"))
        prov = [provenance.completeness(r) for r in rs]
        missing = Counter(f for p in prov for f in p["missing"])
        return {
            "total": len(rs),
            "probability_types": dict(types),
            "valid_probabilities": sum(1 for r in rs
                                       if semantics.scorable_probability(r)[0] is not None),
            "without_valid_probability": dict(not_scored),
            "by_horizon": dict(Counter(horizon_bucket(r.get("horizon_days")) for r in rs)),
            "by_horizon_days": dict(sorted(Counter(r.get("horizon_days") for r in rs).items(),
                                           key=lambda kv: (kv[0] is None, kv[0] or 0))),
            "by_asset_class": dict(ac),
            "by_venue": dict(venue),
            "by_direction": dict(Counter(r.get("direction") for r in rs)),
            "by_macro_regime": dict(regimes),
            "macro_regime_validity": dict(regime_status),
            "graded": len(graded),
            "ungraded": len(rs) - len(graded),
            "graded_undecidable": sum(1 for r in graded if r.get("correct") is None),
            "duplicate_external_refs": sum(n - 1 for n in refs.values() if n > 1),
            "model_versions": dict(Counter(r.get("model_version") or "UNRECORDED" for r in rs)),
            "created": [min((r.get("created_at") or "") for r in rs) if rs else None,
                        max((r.get("created_at") or "") for r in rs) if rs else None],
            "field_presence": {f: sum(1 for r in rs if fn(r)) for f, fn in FIELDS.items()},
            "provenance_complete": sum(1 for p in prov if p["complete"]),
            "provenance_missing": dict(missing.most_common()),
        }

    return {"overall": block(rows),
            "by_model": {m: block(rs) for m, rs in sorted(by_model.items())},
            "fields_checked": list(FIELDS)}
