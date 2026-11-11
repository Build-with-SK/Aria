"""
src/intelligence/registry.py
============================
One card per production prediction source, and the only way a model's
standing can rise.

Two kinds of status, kept apart:

    evidence status   computed, never stored: INSUFFICIENT_DATA until the
                      record holds enough graded calls on enough dates, then
                      EXPERIMENTAL. Evidence alone never says more than that.
    owner status      VALIDATED, PRODUCTION or RETIRED, set by the owner
                      through `set_status()` and appended to
                      data/intelligence/model_status.jsonl. VALIDATED is
                      refused unless `promotion_check()` passes at that
                      moment; PRODUCTION additionally requires VALIDATED
                      first. Going DOWN (RETIRED, EXPERIMENTAL) never needs
                      evidence.

A model becomes VALIDATED because the gate passed AND the owner said so —
never because a hit rate rose, a backtest Sharpe rose, or a language model
said it was better (the gate takes no text input). If the evidence later
fails, the card says `owner_status_lapsed` and the router stops treating the
model as validated; the owner's record is not rewritten.

THE PROMOTION GATE (Phase 17)
-----------------------------
    sufficient_sample          >= PROMOTION_MIN_EVENTS directional graded calls
                               on >= PROMOTION_MIN_CLUSTERS information dates
    temporal_holdout           on the most recent 30% of calls (held out, label-
                               purged), better than chance on the cluster-robust
                               interval AND better than always-long (McNemar),
                               Holm-adjusted across every model examined
    no_pit_violation           no row of this model excluded for a point-in-
                               time reason, no regime label from after a call
    stable_performance         three chronological blocks, none worse than
                               chance, first and last not significantly apart
    known_probability_semantics no UNKNOWN probability type in the record
    reproducible_model_version every evaluated call carries a code hash, and
                               they are all the same version
"""
from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from src.intelligence import evaluation, semantics, walkforward
from src.intelligence.multiple_testing import holm

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
STATUS_LOG = DATA / "intelligence" / "model_status.jsonl"
MODELS_DIR = DATA / "models"
SIGNALS_FILE = DATA / "signals.json"
ML_PREDICTIONS_FILE = DATA / "ml_predictions.json"
_lock = threading.Lock()

EXPERIMENTAL = "EXPERIMENTAL"
VALIDATED = "VALIDATED"
PRODUCTION = "PRODUCTION"
RETIRED = "RETIRED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NOT_RECORDED = "NOT_RECORDED"
OWNER_STATUSES = (EXPERIMENTAL, VALIDATED, PRODUCTION, RETIRED)

PROMOTION_MIN_EVENTS = 200
PROMOTION_MIN_CLUSTERS = 20
HOLDOUT_FRACTION = 0.3
STABILITY_BLOCKS = 3


@dataclass(frozen=True)
class ModelSpec:
    """What a source IS — declared, from the code, never from its results."""
    model_id: str
    name: str
    package: str                     # code fingerprinted for the model hash
    ledger_source: Optional[str]     # None: the source writes no ledger rows
    kind: str
    probability_type: str
    declared_probability: bool       # does it claim P(direction right)?
    horizons_days: tuple
    asset_classes: tuple
    feature_set: tuple
    training: str
    notes: tuple = field(default_factory=tuple)


CATALOGUE: dict[str, ModelSpec] = {
    "desk": ModelSpec(
        model_id="desk", name="desk-debate", package="src/desk", ledger_source="desk",
        kind=("deterministic weighted vote of the technical / fundamental / sentiment "
              "analysts, conditioned by macro; a language model writes the debate "
              "prose and the judge's explanation and decides NOTHING"),
        probability_type=semantics.ProbabilityType.CONFIDENCE.value,
        declared_probability=False, horizons_days=(1,),
        asset_classes=("crypto", "equity"),
        feature_set=("technical agent: data/signals.json composite, trend, momentum, "
                     "realised vol, heuristic bullish_prob; data/ml_predictions.json "
                     "horizons (only while unexpired)",
                     "fundamental agent", "sentiment agent (news)",
                     "macro conditioner: regime, VIX, conviction bar"),
        training=("none — judge weights are src/desk/scorecard defaults; they tilt "
                  "after 20 closed trades on raw hit rates WITHOUT a significance "
                  "test (flagged, unchanged)"),
        notes=("ledger holds 61 graded events 2026-07-18..2026-08-01 (imported from "
               "data/desk/backfilled_outcomes.jsonl); debates since then were never "
               "graded into the ledger — see the debate evaluation",)),
    "technical": ModelSpec(
        model_id="technical", name="technical-composite",
        package="src/data/technical_summary.py", ledger_source="technical",
        kind="indicator vote count (moving averages + oscillators) on daily bars",
        probability_type=semantics.ProbabilityType.NONE.value,
        declared_probability=False, horizons_days=(10,),
        asset_classes=("equity", "crypto", "forex", "index"),
        feature_set=("SMA/EMA 10-200 crosses", "RSI", "stochastic", "CCI", "ADX",
                     "MACD", "momentum", "Williams %R", "ultimate oscillator"),
        training="none — fixed rules"),
    "v5": ModelSpec(
        model_id="v5", name="v5-ensemble", package="src/v5", ledger_source="v5",
        kind=("41 research modules -> family-weighted ensemble -> meta-review; "
              "confidence is the stated P(direction right) on bull/bear calls"),
        probability_type=semantics.ProbabilityType.STATED_PROBABILITY.value,
        declared_probability=True, horizons_days=(22, 48),
        asset_classes=("equity",),
        feature_set=("price, fundamental, quant, macro, volatility, machine and "
                     "behavioural module families (src/v5/modules)",),
        training=("not fitted — family weights hand-set (src/v5/ensemble.FAMILY_WEIGHTS); "
                  "module multipliers versioned by src/v5/learning behind a binomial test"),
        notes=("not point in time for fundamentals/news modules: historical replay "
               "is not possible, only the live record counts",)),
    "ml-ensemble": ModelSpec(
        model_id="ml-ensemble", name="ml-stacked-ensemble", package="src/models",
        ledger_source="ml-ensemble",
        kind=("per-ticker stacked classifiers (logistic, random forest, gradient "
              "boosting, xgboost) + meta-learner, purged walk-forward CV"),
        probability_type=semantics.ProbabilityType.SCORE.value,
        declared_probability=False, horizons_days=(1, 5, 20),
        asset_classes=("equity",),
        feature_set=("src/models/feature_engine (104 features: returns, trend, "
                     "momentum, volatility, regime flags, volume)",),
        training="per ticker and horizon; metadata in data/models/*.json",
        notes=("its predict_proba is shifted +-0.03 by a hard-coded regime rule "
               "(ensemble_model._regime_adjust): a SCORE, not a probability",
               "had never been written to the ledger, so it has no graded record; "
               "recorded at creation from 2026-10-08 (src/intelligence/recorders), "
               "forward only",
               "training labels: the last `horizon` rows had an unknown future but "
               "were labelled 'not up' (fixed 2026-10-08 in src/models/targets.py)")),
    "signal-engine": ModelSpec(
        model_id="signal-engine", name="signal-engine-composite",
        package="src/signals", ledger_source=None,
        kind="composite technical/momentum/regime score with a heuristic bullish_prob",
        probability_type=semantics.ProbabilityType.SCORE.value,
        declared_probability=False, horizons_days=(),
        asset_classes=("equity", "crypto", "forex", "futures"),
        feature_set=("data/signals.json composite_score, trend, momentum, "
                     "realised vol, regime",),
        training="none — fixed rules",
        notes=("feeds the desk's technical analyst and the reflex lane; never "
               "written to the ledger as a falsifiable claim",)),
}


# ── facts gathered from disk ────────────────────────────────────────────────

def code_hash(package: str) -> Optional[str]:
    try:
        from src.core.versioning import code_fingerprint
        h = code_fingerprint(package)
        return None if h == "unknown" else h
    except Exception:                            # pragma: no cover - defensive
        return None


@lru_cache(maxsize=4)
def _ml_metadata(_mtime: float) -> dict:
    files = sorted(MODELS_DIR.glob("*.json")) if MODELS_DIR.exists() else []
    trained, rows, versions, horizons, cv = [], [], set(), set(), []
    for f in files:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if m.get("trained_on"):
            trained.append(str(m["trained_on"]))
        if m.get("n_rows"):
            rows.append(int(m["n_rows"]))
        if m.get("model_version"):
            versions.add(str(m["model_version"]))
        if m.get("horizon") is not None:
            horizons.add(int(m["horizon"]))
        if m.get("cv_score") is not None:
            cv.append(float(m["cv_score"]))
    trained.sort()
    return {"checkpoints": len(files),
            "trained_from": trained[0] if trained else None,
            "trained_to": trained[-1] if trained else None,
            "rows_per_model": [min(rows), max(rows)] if rows else None,
            "versions": sorted(versions), "horizons": sorted(horizons),
            "in_sample_cv_accuracy": ([round(min(cv), 4), round(max(cv), 4)] if cv else None),
            "cv_note": "purged walk-forward CV during training: in-sample selection, "
                       "not a ledger holdout"}


def ml_metadata() -> dict:
    mtime = MODELS_DIR.stat().st_mtime if MODELS_DIR.exists() else 0.0
    return _ml_metadata(mtime)


def _file_age(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    ts = datetime.fromtimestamp(path.stat().st_mtime)
    return {"exists": True, "modified": ts.isoformat(timespec="seconds"),
            "age_days": round((datetime.now() - ts).total_seconds() / 86400, 1)}


# ── the owner's record ──────────────────────────────────────────────────────

def status_log() -> list[dict]:
    if not STATUS_LOG.exists():
        return []
    out = []
    for line in STATUS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def owner_status(model_id: str, as_of: Optional[datetime] = None) -> Optional[dict]:
    """The latest owner decision for `model_id` (made by `as_of`), or None."""
    cut = as_of.isoformat() if as_of else None
    latest = None
    for e in status_log():
        if e.get("model_id") != model_id:
            continue
        if cut and str(e.get("at", "")) > cut[:19]:
            continue
        latest = e
    return latest


class PromotionRefused(ValueError):
    pass


def set_status(model_id: str, status: str, *, by: str, reason: str,
               check: Optional[dict] = None) -> dict:
    """Record an owner decision. VALIDATED / PRODUCTION only through the gate.

    `check` is the promotion_check() result the decision rests on; for an
    upgrade it must be passing, and it is stored with the decision.
    """
    if model_id not in CATALOGUE:
        raise PromotionRefused(f"unknown model {model_id!r}")
    status = (status or "").upper()
    if status not in OWNER_STATUSES:
        raise PromotionRefused(f"{status!r} is not an owner status {OWNER_STATUSES}; "
                               f"INSUFFICIENT_DATA is evidence-derived and cannot be set")
    if status in (VALIDATED, PRODUCTION):
        if not check or not check.get("eligible"):
            failed = [k for k, v in (check or {}).get("criteria", {}).items()
                      if not v.get("passed")]
            raise PromotionRefused(
                f"{model_id} cannot become {status}: the promotion gate does not pass "
                f"({', '.join(failed) or 'no check supplied'})")
        if status == PRODUCTION:
            cur = owner_status(model_id)
            if not cur or cur.get("status") not in (VALIDATED, PRODUCTION):
                raise PromotionRefused(f"{model_id} must be VALIDATED before PRODUCTION")
    entry = {"at": datetime.now().isoformat(timespec="seconds"), "model_id": model_id,
             "status": status, "by": by, "reason": reason,
             "evidence": {k: (check or {}).get(k) for k in
                          ("eligible", "criteria", "dataset_hash")}}
    with _lock:
        STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with STATUS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    return entry


# ── the gate ────────────────────────────────────────────────────────────────

def _criterion(passed: bool, detail: str, **extra) -> dict:
    return {"passed": bool(passed), "detail": detail, **extra}


def _holdout_pvalue(later: list) -> Optional[float]:
    """One-sided evidence the holdout beats always-long (exact McNemar)."""
    v = evaluation.vs_always_long(later)
    if v.get("status") != "MEASURED":
        return None
    b, c = v["model_right_long_wrong"], v["model_wrong_long_right"]
    return v["p_value"] if b > c else 1.0


def promotion_check(model_id: str, dataset, *, ledger_rows: Optional[list] = None,
                    family_pvalues: Optional[dict] = None) -> dict:
    """Every criterion, pass or fail, with the numbers behind it."""
    rows = [r for r in dataset.for_model(CATALOGUE[model_id].ledger_source or model_id)]
    d = [r for r in rows if r["direction"] in ("bull", "bear")]
    crit = {}

    clusters = len({r.get("information_date") for r in d})
    crit["sufficient_sample"] = _criterion(
        len(d) >= PROMOTION_MIN_EVENTS and clusters >= PROMOTION_MIN_CLUSTERS,
        f"{len(d)} directional graded calls on {clusters} dates "
        f"(need {PROMOTION_MIN_EVENTS} on {PROMOTION_MIN_CLUSTERS})")

    earlier, later = walkforward.holdout_split(d, holdout_fraction=HOLDOUT_FRACTION)
    hold = evaluation.rate_block(later)
    pv = _holdout_pvalue(later) if later else None
    family = dict(family_pvalues or {})
    if pv is not None:
        family.setdefault(model_id, pv)
    adj = holm(family).get(model_id) if pv is not None else None
    crit["temporal_holdout"] = _criterion(
        hold.get("status") == "MEASURED" and hold.get("verdict") == "better than chance"
        and adj is not None and adj < 0.05,
        (f"holdout {hold.get('rate')} over {hold.get('n')} calls ({hold.get('verdict')}); "
         f"vs always-long Holm-adjusted p={adj}" if hold.get("status") == "MEASURED"
         else f"holdout: {hold.get('why') or 'insufficient data'}"),
        holdout_window=[later[0]["created_at"], later[-1]["created_at"]] if later else None,
        n_holdout=len(later), family_size=len(family))

    excluded = [e for e in dataset.excluded if e["model"] == (CATALOGUE[model_id].ledger_source
                                                              or model_id)]
    from src.intelligence.dataset import PIT_REASONS
    pit = [e for e in excluded if PIT_REASONS & set(e["reasons"])]
    lookahead = sum(1 for r in rows if r.get("regime_macro_status") == "LOOKAHEAD")
    crit["no_pit_violation"] = _criterion(
        not pit and not lookahead and bool(rows),
        (f"{len(pit)} rows excluded for a point-in-time reason, {lookahead} regime "
         f"labels from after the call" if rows else "no evaluated rows"))

    blocks = []
    if d:
        ordered = sorted(d, key=lambda r: r.get("created_at") or "")
        size = max(1, len(ordered) // STABILITY_BLOCKS)
        blocks = [ordered[i * size:(i + 1) * size if i < STABILITY_BLOCKS - 1 else None]
                  for i in range(STABILITY_BLOCKS)]
    rates = [evaluation.rate_block(b) for b in blocks]
    measured = len(rates) == STABILITY_BLOCKS and all(r["status"] == "MEASURED" for r in rates)
    stable = measured and not any(r["verdict"] == "worse than chance" for r in rates)
    if measured:
        a, b = rates[0], rates[-1]
        p = (a["hits"] + b["hits"]) / (a["n"] + b["n"])
        se = math.sqrt(p * (1 - p) * (1 / a["n"] + 1 / b["n"])) if 0 < p < 1 else 0
        z = (b["rate"] - a["rate"]) / se if se else 0.0
        stable = stable and abs(z) < 1.96
    crit["stable_performance"] = _criterion(
        stable, ("block rates " + ", ".join(str(r.get("rate")) for r in rates)
                 if measured else "fewer than three measurable chronological blocks"))

    # The ledger's own rows, never "none supplied": an empty list would make
    # this criterion pass vacuously.
    if ledger_rows is None:
        from src.intelligence.dataset import ledger_rows as _read
        ledger_rows = _read()
    lr = ledger_rows
    src = CATALOGUE[model_id].ledger_source
    unknown = sum(1 for r in lr if r.get("source") == src
                  and semantics.type_of(r) is semantics.ProbabilityType.UNKNOWN)
    crit["known_probability_semantics"] = _criterion(
        unknown == 0 and bool(rows),
        f"{unknown} ledger rows with an UNRECORDED probability kind")

    hashes = {r.get("model_hash") for r in rows}
    crit["reproducible_model_version"] = _criterion(
        bool(rows) and None not in hashes and len(hashes) == 1,
        (f"{sum(1 for r in rows if not r.get('model_hash'))} evaluated calls carry no "
         f"code hash; {len(hashes - {None})} distinct versions"))

    return {"model_id": model_id, "eligible": all(c["passed"] for c in crit.values()),
            "criteria": crit, "dataset_hash": dataset.dataset_hash,
            "note": "the gate takes no text input: no argument, from a person or a "
                    "language model, can satisfy a criterion"}


# ── the cards ───────────────────────────────────────────────────────────────

def evidence_status(spec: ModelSpec, ev: Optional[dict]) -> str:
    if spec.ledger_source is None:
        return NOT_RECORDED
    if not ev or ev["directional_accuracy"]["status"] != "MEASURED":
        return INSUFFICIENT_DATA
    return EXPERIMENTAL


def cards(dataset, evaluations: dict, calibrations: dict, *,
          ledger_rows: Optional[list] = None, as_of: Optional[datetime] = None) -> dict:
    """{model_id: card} for every catalogued model."""
    lr = ledger_rows or []
    # One family for the holdout tests: every model the gate looks at today.
    family = {}
    for mid, spec in CATALOGUE.items():
        if spec.ledger_source:
            d = [r for r in dataset.for_model(spec.ledger_source)
                 if r["direction"] in ("bull", "bear")]
            _, later = walkforward.holdout_split(d, holdout_fraction=HOLDOUT_FRACTION)
            pv = _holdout_pvalue(later) if later else None
            if pv is not None:
                family[mid] = pv
    out = {}
    for mid, spec in CATALOGUE.items():
        ev = evaluations.get(spec.ledger_source) if spec.ledger_source else None
        rows = [r for r in lr if r.get("source") == spec.ledger_source] if spec.ledger_source else []
        versions = {}
        for r in rows:
            versions[r.get("model_version") or "UNRECORDED"] = \
                versions.get(r.get("model_version") or "UNRECORDED", 0) + 1
        latest = max((r for r in rows if r.get("model_version")),
                     key=lambda r: r.get("created_at") or "", default=None)
        ev_status = evidence_status(spec, ev)
        check = (promotion_check(mid, dataset, ledger_rows=lr, family_pvalues=family)
                 if spec.ledger_source else None)
        owner = owner_status(mid, as_of)
        lapsed = bool(owner and owner["status"] in (VALIDATED, PRODUCTION)
                      and not (check or {}).get("eligible"))
        if owner and owner["status"] == RETIRED:
            effective = RETIRED
        elif owner and owner["status"] in (VALIDATED, PRODUCTION) and not lapsed:
            effective = owner["status"]
        else:
            effective = ev_status
        sample = (ev or {}).get("sample", {})
        card = {
            "model_id": mid, "name": spec.name, "kind": spec.kind,
            "ledger_source": spec.ledger_source,
            "status": effective, "evidence_status": ev_status,
            "owner_status": owner, "owner_status_lapsed": lapsed,
            "model_version": (latest or {}).get("model_version"),
            "versions_in_record": versions,
            "code_hash_now": code_hash(spec.package), "package": spec.package,
            "probability_type": spec.probability_type,
            "declared_probability": spec.declared_probability,
            "calibration": ((calibrations.get(spec.ledger_source) if spec.ledger_source else None)
                            or {"status": "CALIBRATION_NOT_APPLICABLE" if not spec.declared_probability
                                else "CALIBRATION_INSUFFICIENT_DATA",
                                "calibration_version": "none",
                                "why": "no graded calls in the record"}),
            "supported": {"asset_classes_declared": list(spec.asset_classes),
                          "asset_classes_observed": sorted({r["asset_class"] for r in
                                                            dataset.for_model(spec.ledger_source)})
                          if spec.ledger_source else [],
                          "horizons_days": list(spec.horizons_days),
                          "regimes_observed": sorted({r["regime_macro"] for r in
                                                      dataset.for_model(spec.ledger_source)
                                                      if r.get("regime_macro")})
                          if spec.ledger_source else []},
            "feature_set": list(spec.feature_set),
            "training": spec.training,
            "performance_sample": {"graded_events": sample.get("graded_events", 0),
                                   "directional": sample.get("directional", 0),
                                   "information_dates": sample.get("information_dates", 0),
                                   "window": [sample.get("first_call"), sample.get("last_call")]},
            "validation_window": ((check or {}).get("criteria", {})
                                  .get("temporal_holdout", {}).get("holdout_window")),
            "promotion_check": check,
            "notes": list(spec.notes),
        }
        if mid == "ml-ensemble":
            card["training_data"] = ml_metadata()
            card["predictions_file"] = _file_age(ML_PREDICTIONS_FILE)
        if mid == "signal-engine":
            card["signals_file"] = _file_age(SIGNALS_FILE)
        out[mid] = card
    return out
