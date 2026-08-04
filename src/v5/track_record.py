"""
src/v5/track_record.py
======================
The flywheel, in one place.

ARIA produces labelled outcomes from four independent loops that until now had
no common surface:

    1. V5 predictions      every analysis, its stated confidence, its outcome
    2. The desk            closed paper trades, realised P&L, per-analyst hit rates
    3. Technical tracker   daily recommendation snapshots, forward hit rate
    4. Brain training data fine-tuning rows generated from signals

This module merges them and adds the measurement none of them had: CALIBRATION.

A hit rate answers "was it right?". Calibration answers the harder question —
"when it said 62%, did it happen 62% of the time?" A system that claims
calibrated confidence and never checks that claim is asserting something it has
not measured, which is precisely what Law 2 forbids. So the numbers here are:

    Brier score     mean squared error of the probability itself. Lower is
                    better; 0.25 is what you score by always saying 50%.
    Skill           1 - brier/0.25. Positive means the confidence numbers carry
                    information. Negative means they are worse than useless and
                    should be ignored until fixed.
    ECE             expected calibration error — the average gap between stated
                    confidence and realised frequency, weighted by bucket size.

Every statistic here refuses to report below its minimum sample. An unmeasurable
quantity is reported as unmeasurable, never as a number with a wide error bar
that readers will treat as fact.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"

MIN_FOR_CALIBRATION = 20        # resolved predictions before calibration is reported
MIN_PER_BUCKET = 5              # resolved predictions before a bucket is reported
BUCKETS = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65),
           (0.65, 0.70), (0.70, 0.80), (0.80, 1.01)]


def _pct_or_none(x: Optional[float], digits: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), digits)


# ── calibration ─────────────────────────────────────────────────────────────

def calibration() -> dict:
    """Stated confidence versus realised frequency, bucketed.

    Only V5 predictions carry a stated probability, so only they can be
    calibrated. The desk and technical loops report hit rates, which is a
    weaker but still useful claim.
    """
    from src.v5.learning import load_predictions

    resolved = [e for e in load_predictions()
                if e.get("resolved") and e.get("correct") is not None
                and e.get("direction") in ("bull", "bear")]

    if len(resolved) < MIN_FOR_CALIBRATION:
        return {
            "measurable": False,
            "n_resolved": len(resolved),
            "n_required": MIN_FOR_CALIBRATION,
            "note": (f"{len(resolved)} directional calls have resolved. Calibration is not "
                     f"reported below {MIN_FOR_CALIBRATION} — a curve drawn through three "
                     f"points would be decoration, not evidence."),
            "buckets": [],
        }

    rows = []
    for e in resolved:
        p = float(e.get("confidence") or 0.5)
        rows.append((max(0.5, min(1.0, p)), 1.0 if e.get("correct") else 0.0))

    brier = sum((p - o) ** 2 for p, o in rows) / len(rows)
    baseline = sum((0.5 - o) ** 2 for _, o in rows) / len(rows)
    skill = 1 - (brier / baseline) if baseline > 0 else None

    buckets, ece_num, ece_den = [], 0.0, 0
    for lo, hi in BUCKETS:
        sel = [(p, o) for p, o in rows if lo <= p < hi]
        if not sel:
            continue
        n = len(sel)
        stated = sum(p for p, _ in sel) / n
        realised = sum(o for _, o in sel) / n
        entry = {
            "range": f"{lo:.0%}-{hi if hi <= 1 else 1:.0%}",
            "n": n,
            "stated_confidence": round(stated, 4),
            "realised_frequency": round(realised, 4) if n >= MIN_PER_BUCKET else None,
            "gap": round(stated - realised, 4) if n >= MIN_PER_BUCKET else None,
            "reportable": n >= MIN_PER_BUCKET,
        }
        if n >= MIN_PER_BUCKET:
            ece_num += n * abs(stated - realised)
            ece_den += n
        buckets.append(entry)

    ece = (ece_num / ece_den) if ece_den else None
    verdict = _calibration_verdict(skill, ece, ece_den)

    return {
        "measurable": True,
        "n_resolved": len(rows),
        "brier": round(brier, 4),
        "brier_baseline": round(baseline, 4),
        "skill": _pct_or_none(skill),
        "ece": _pct_or_none(ece),
        "ece_sample": ece_den,
        "buckets": buckets,
        "verdict": verdict,
    }


def _calibration_verdict(skill: Optional[float], ece: Optional[float], n: int) -> str:
    if skill is None:
        return "Not enough resolved calls to judge the confidence numbers."
    if skill < 0:
        return ("The stated confidences are worse than a flat 50% guess (negative skill). "
                "Until this turns positive, treat every confidence number on this system as "
                "uninformative and size on the risk gate alone.")
    if ece is None or n < MIN_PER_BUCKET:
        return (f"Positive skill ({skill:.0%}), but no confidence bucket yet has "
                f"{MIN_PER_BUCKET} resolved calls, so the curve itself is not yet readable.")
    if ece <= 0.05:
        return (f"Well calibrated: stated confidence tracks realised frequency within "
                f"{ece:.1%} on average, with {skill:.0%} skill over a coin flip.")
    if ece <= 0.12:
        return (f"Roughly calibrated: an average gap of {ece:.1%} between what was claimed "
                f"and what happened. Usable, but the confidence numbers should be read as "
                f"ordinal rather than literal.")
    return (f"Poorly calibrated: stated confidence misses realised frequency by {ece:.1%} on "
            f"average. The direction may still have skill ({skill:.0%}), but the probability "
            f"attached to it is not yet trustworthy.")


# ── the four source loops ───────────────────────────────────────────────────

def _source_v5() -> dict:
    from src.v5.learning import load_predictions, weights_version
    entries = load_predictions()
    resolved = [e for e in entries if e.get("resolved")]
    pending = [e for e in entries if not e.get("resolved")]
    next_due = None
    if pending:
        dates = sorted(str(e.get("resolve_after")) for e in pending if e.get("resolve_after"))
        next_due = dates[0] if dates else None
    return {
        "id": "v5",
        "name": "V5 predictions",
        "what_it_labels": "Every analysis: direction, stated confidence, and what the price "
                          "actually did over the module-weighted horizon.",
        "produces": "calibration + per-module skill",
        "total": len(entries),
        "resolved": len(resolved),
        "pending": len(pending),
        "next_resolution": next_due,
        "weights_version": weights_version(),
        "endpoint": "/api/v5/learning",
        "live": len(entries) > 0,
    }


def _source_desk() -> dict:
    out = {"id": "desk", "name": "The desk", "endpoint": "/api/desk/performance",
           "what_it_labels": "Closed paper trades: realised P&L and R-multiple per position, "
                             "and which analyst was right about it.",
           "produces": "per-analyst weights + Fable's written lessons",
           "total": 0, "resolved": 0, "live": False}
    try:
        from src.desk.position_manager import read_closed_trades
        from src.desk.scorecard import agent_hit_rates, current_weights
        trades = read_closed_trades(2000) or []
        out.update({
            "total": len(trades),
            "resolved": len(trades),
            "agent_hit_rates": agent_hit_rates(),
            "analyst_weights": current_weights(),
            "live": len(trades) > 0,
        })
    except Exception as e:
        out["error"] = f"desk loop unavailable: {type(e).__name__}"
        logger.debug(f"track_record: desk source failed: {e}")
    return out


def _source_technical() -> dict:
    out = {"id": "technical", "name": "Technical tracker", "endpoint": "/api/technical/performance",
           "what_it_labels": "Daily recommendation snapshots across the universe, scored "
                             "forward once they are old enough to judge.",
           "produces": "hit rate by signal label",
           "total": 0, "resolved": 0, "live": False}
    log = DATA / "technical" / "recommendations_log.jsonl"
    try:
        if log.exists():
            n = sum(1 for line in log.read_text(encoding="utf-8").splitlines() if line.strip())
            out["total"] = n
            out["live"] = n > 0
        from src.data.technical_tracker import evaluate
        ev = evaluate() or {}
        if "note" not in ev:
            out["by_label"] = ev
            overall = ev.get("ALL") or {}
            out["resolved"] = overall.get("n", 0)
            out["hit_rate"] = overall.get("hit_rate")
        else:
            out["note"] = ev["note"]
    except Exception as e:
        out["error"] = f"technical loop unavailable: {type(e).__name__}"
        logger.debug(f"track_record: technical source failed: {e}")
    return out


def _source_brain() -> dict:
    out = {"id": "brain", "name": "Brain training data",
           "endpoint": "/api/brain/generate-training-data",
           "what_it_labels": "Fine-tuning rows generated from signals and their outcomes.",
           "produces": "supervised rows for the local model",
           "total": 0, "resolved": 0, "live": False}
    f = DATA / "brain_training_data.jsonl"
    try:
        if f.exists():
            n = sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
            out.update({"total": n, "resolved": n, "live": n > 0,
                        "updated": datetime.fromtimestamp(f.stat().st_mtime)
                        .isoformat(timespec="seconds")})
    except Exception as e:
        out["error"] = f"brain loop unavailable: {type(e).__name__}"
    return out


def sources() -> list[dict]:
    return [_source_v5(), _source_desk(), _source_technical(), _source_brain()]


# ── lessons ─────────────────────────────────────────────────────────────────

def lessons(n: int = 12) -> list[dict]:
    """What the system has written down about its own mistakes."""
    try:
        from src.desk.teacher import read_lessons
        return list(reversed(read_lessons(n)))[:n]
    except Exception as e:
        logger.debug(f"track_record: lessons unavailable: {e}")
        return []


# ── the flywheel state ──────────────────────────────────────────────────────

def flywheel() -> list[dict]:
    """The loop as four stages, each showing whether it is actually turning.

    A stage that is stalled says so. The most common failure of a 'self-improving'
    system is that stage 2 never happens and nobody notices.
    """
    from src.v5.learning import MIN_RESOLVED, load_predictions, load_weights, module_scorecard

    entries = load_predictions()
    resolved = [e for e in entries if e.get("resolved")]
    attributed = [e for e in resolved if (e.get("attribution") or {}).get("causes")]
    weights = load_weights()
    card = module_scorecard()
    eligible = [m for m, r in card.items() if (r.get("n") or 0) >= MIN_RESOLVED]

    return [
        {"stage": "PREDICT", "count": len(entries),
         "turning": len(entries) > 0,
         "detail": f"{len(entries)} analyses logged with a stated direction and confidence.",
         "blocked": None if entries else "Run an analysis on the V5 page to start the loop."},
        {"stage": "RESOLVE", "count": len(resolved),
         "turning": len(resolved) > 0,
         "detail": f"{len(resolved)} have reached their horizon and been scored against price.",
         "blocked": (None if resolved else
                     "No prediction has reached its horizon yet. This stage is time-gated, "
                     "not broken.")},
        {"stage": "ATTRIBUTE", "count": len(attributed),
         "turning": len(attributed) > 0,
         "detail": f"{len(attributed)} outcomes carry a specific cause, not just a win/loss flag.",
         "blocked": None if attributed else "Waiting on resolved outcomes."},
        {"stage": "REWEIGHT", "count": int(weights.get("version", 1)) - 1,
         "turning": int(weights.get("version", 1)) > 1,
         "detail": (f"{len(eligible)} module(s) have the {MIN_RESOLVED}+ resolved calls needed to "
                    f"qualify; weights are at v{weights.get('version', 1)} after "
                    f"{max(0, int(weights.get('version', 1)) - 1)} change(s)."),
         # Two very different reasons a weight has not moved, and the difference
         # is the whole point of the guard: not enough data, versus enough data
         # that simply does not show skill. Saying "0" without which one is
         # meaningless.
         "blocked": (
             f"No module has {MIN_RESOLVED} resolved calls yet. Weights deliberately cannot move "
             f"before then — that is the guard against learning from noise."
             if not eligible else
             (f"{len(eligible)} module(s) have the sample size, but none has passed the p<0.05 "
              f"binomial test, so no weight has moved. Sample size alone never earns a change — "
              f"a module must beat a coin flip by more than luck explains."
              if int(weights.get("version", 1)) == 1 else None)),
         "eligible_modules": eligible},
    ]


# ── the page payload ────────────────────────────────────────────────────────

def build() -> dict:
    from src.v5.learning import performance_summary

    perf = performance_summary()
    cal = calibration()
    srcs = sources()

    # Calibration answers "are the confidence numbers honest". It does NOT
    # answer "is any of this better than buying and holding", and a page that
    # reports only the first invites the reader to assume the second. Both are
    # carried here so they are read together; see src/v5/baselines.py.
    #
    # Failure-tolerant: the baseline comparison re-fetches price history, and a
    # dead vendor must not blank the calibration numbers, which need no network.
    try:
        from src.v5 import baselines as baselines_mod
        vs_baseline = baselines_mod.report()
    except Exception as e:                          # pragma: no cover - defensive
        logger.warning(f"baseline comparison unavailable: {e}")
        vs_baseline = {"measurable": False, "note": f"baseline comparison unavailable: {e}"}

    try:
        from src.v5 import tiers as tiers_mod
        module_tiers = tiers_mod.summary()
    except Exception as e:                          # pragma: no cover - defensive
        logger.warning(f"module tiers unavailable: {e}")
        module_tiers = {"headline": f"module tiers unavailable: {e}"}

    total_labels = sum(s.get("resolved", 0) or 0 for s in srcs)
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "headline": _headline(perf, cal, total_labels),
        "flywheel": flywheel(),
        "calibration": cal,
        "vs_baseline": vs_baseline,
        "module_tiers": module_tiers,
        "performance": perf,
        "sources": srcs,
        "lessons": lessons(),
        "total_labelled_outcomes": total_labels,
    }


def _headline(perf: dict, cal: dict, total_labels: int) -> dict:
    """One paragraph a stranger can read to know whether to trust this system."""
    resolved = perf.get("resolved", 0)
    if resolved == 0:
        text = (f"Nothing has resolved yet. {perf.get('total_predictions', 0)} prediction(s) are "
                f"logged and waiting on their horizon, and {total_labels} labelled outcomes exist "
                f"across the other loops. Until predictions resolve, this system has no track "
                f"record — and it will say so rather than show you a number.")
    elif not cal.get("measurable"):
        text = (f"{resolved} call(s) resolved at a "
                f"{(perf.get('hit_rate') or 0):.0%} hit rate. That is too small a sample to mean "
                f"anything: {cal.get('n_required')} resolved calls are required before the "
                f"confidence numbers are graded.")
    else:
        text = (f"{resolved} resolved calls, {(perf.get('hit_rate') or 0):.0%} correct against "
                f"{(perf.get('mean_confidence') or 0):.0%} average stated confidence. "
                f"{cal.get('verdict')}")
    return {
        "text": text,
        "resolved": resolved,
        "hit_rate": perf.get("hit_rate"),
        "stated_confidence": perf.get("mean_confidence"),
        "calibration_gap": perf.get("calibration_gap"),
        "total_labelled_outcomes": total_labels,
    }


# ── contributor export ──────────────────────────────────────────────────────

def training_export() -> list[dict]:
    """Resolved predictions as supervised rows: the module vector in, the
    realised outcome out.

    This is the artefact a contributor's usage actually produces. Only resolved
    predictions are exported — an unresolved prediction has no label, and
    exporting it would invite training on the system's own opinion.
    """
    from src.v5.learning import load_predictions

    rows = []
    for e in load_predictions():
        if not e.get("resolved") or e.get("realised_return") is None:
            continue
        features = {m["module"]: m.get("net")
                    for m in (e.get("modules") or []) if not m.get("abstained")}
        rows.append({
            "id": e.get("id"),
            "at": e.get("at"),
            "ticker": e.get("ticker"),
            "horizon_days": e.get("horizon_days"),
            "features": features,
            "abstentions": [m["module"] for m in (e.get("modules") or []) if m.get("abstained")],
            "predicted_direction": e.get("direction"),
            "stated_confidence": e.get("confidence"),
            "realised_return": e.get("realised_return"),
            "label_correct": bool(e.get("correct")),
            "attribution": (e.get("attribution") or {}).get("causes", []),
            "weights_version": e.get("weights_version"),
        })
    return rows


def training_export_jsonl() -> str:
    return "\n".join(json.dumps(r, default=str) for r in training_export())
