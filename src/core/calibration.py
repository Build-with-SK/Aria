"""
src/core/calibration.py
=======================
What ARIA's confidence numbers actually mean — Phase 18.

WHY THIS MODULE EXISTS, AND WHAT IT CORRECTS
--------------------------------------------
The first calibration pass over the desk's outcome file reported a Brier score
of 0.3055 and a skill of −0.222, and concluded that stated conviction was
"inverted" — that higher confidence went with lower accuracy. That conclusion
was wrong, for two reasons this module exists to prevent recurring:

  1. NON-INDEPENDENCE. The 306 rows in data/desk/backfilled_outcomes.jsonl are
     61 distinct events. The desk re-debates the same ticker and each debate
     writes a row with the same entry, exit and outcome; BONK-USD appears
     eighteen times with identical prices. Pooling them inflated n fivefold, so
     every interval was roughly √5 too narrow.

  2. POPULATION MIXING. Mean conviction is 26 among NO_TRADE verdicts and 79
     among traded ones. Mapping conviction onto a probability and bucketing
     across both therefore put NO_TRADE rows in the low buckets and traded rows
     in the high ones. The resulting "curve" mostly compared two different
     populations to each other, which is a selection effect, not calibration.

Deduplicated and separated, the record is 61 events at 45.9%. At that size the
honest finding is that there is NO MEASURABLE EDGE YET — a coin flip is well
inside the interval. That is a much weaker claim than the first one, and it is
the one the data supports.

WHAT THIS MODULE ENFORCES
-------------------------
  * events are deduplicated before anything is counted;
  * populations are never pooled unless the caller asks for it explicitly;
  * every rate is reported with a Wilson interval and the n behind it;
  * a result whose interval spans 0.5 is reported as UNDECIDED, not as a
    direction;
  * confidence-as-probability and confidence-as-signal-strength are measured
    separately, because they are different claims (§18) and only the first can
    be calibrated at all.

Nothing here changes a confidence formula. Phase 18 says to investigate before
adjusting, and at n=61 there is nothing to adjust toward.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: Below this many INDEPENDENT resolved events, no rate is reported as a
#: finding. Chosen so the Wilson interval on a 50% rate is narrower than
#: ±0.14 — wide, but narrow enough to exclude an obviously broken signal.
MIN_EVENTS = 50

#: Below this, a calibration bucket is reported as present but unmeasurable.
MIN_BUCKET = 12


def wilson(successes: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    """Wilson score interval for a binomial proportion.

    Wilson rather than the normal approximation because the normal interval is
    badly wrong at the sample sizes this system actually has, and can produce
    bounds outside [0, 1] — which would then be rendered as a confidence range
    that cannot exist.
    """
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def rate(successes: int, n: int) -> dict:
    """A proportion, its interval, and an explicit verdict on whether it means
    anything at this sample size."""
    if n <= 0:
        return {"n": 0, "rate": None, "ci95": None, "verdict": "no data"}
    p = successes / n
    lo, hi = wilson(successes, n)
    if n < MIN_EVENTS:
        verdict = f"undecided — {n} events is below the {MIN_EVENTS} needed to call it"
    elif lo > 0.5:
        verdict = "better than chance"
    elif hi < 0.5:
        verdict = "worse than chance"
    else:
        verdict = "indistinguishable from chance"
    return {"n": n, "successes": successes, "rate": round(p, 4),
            "ci95": [round(lo, 4), round(hi, 4)], "verdict": verdict}


def _dedupe(preds: Iterable[dict]) -> list[dict]:
    """Collapse repeated assertions of the same event to one observation.

    Identity is (subject, direction, price_at, price_end) — the same claim
    about the same move. A ledger counts facts, and the same fact asserted
    eighteen times is one fact.
    """
    seen, out = set(), []
    for p in preds:
        key = (p.get("subject"), p.get("direction"),
               p.get("price_at"), p.get("price_end"))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _brier(rows: list[dict]) -> Optional[float]:
    usable = [r for r in rows
              if r.get("probability") is not None and r.get("correct") is not None]
    if not usable:
        return None
    return sum((r["probability"] - (1 if r["correct"] else 0)) ** 2
               for r in usable) / len(usable)


def investigate(source: Optional[str] = None, *, pool_populations: bool = False) -> dict:
    """The full diagnostic, per source, with its own limitations stated.

    `pool_populations=False` (the default) keeps traded and non-traded calls
    apart. Pooling them is available but has to be asked for, because doing it
    by accident is what produced the first, wrong conclusion.
    """
    from src.core import ledger

    raw = ledger.predictions(limit=2000, source=source, resolved=True)
    raw = [r for r in raw if r.get("correct") is not None]
    deduped = _dedupe(raw)

    out: dict = {
        "source": source or "all",
        "rows_in_ledger": len(raw),
        "distinct_events": len(deduped),
        "duplication": {
            "repeats_removed": len(raw) - len(deduped),
            "note": (None if len(raw) == len(deduped) else
                     f"{len(raw) - len(deduped)} of {len(raw)} resolved rows were "
                     f"repeat assertions of an event already counted. Treating "
                     f"them as independent would narrow every interval by about "
                     f"a factor of {math.sqrt(len(raw) / max(1, len(deduped))):.1f}."),
        },
    }

    # ── overall, on independent events only ─────────────────────────────────
    hits = sum(1 for r in deduped if r["correct"])
    out["overall"] = rate(hits, len(deduped))

    # ── by population ───────────────────────────────────────────────────────
    # The desk records its verdict in the rationale ("Desk verdict NO_TRADE at
    # conviction 21"), which is the only place the traded/not distinction
    # survives ingestion.
    def population(r: dict) -> str:
        rat = (r.get("rationale") or "")
        if "verdict NO_TRADE" in rat:
            return "not traded"
        if "verdict BUY" in rat or "verdict SELL" in rat:
            return "traded"
        return "unclassified"

    pops = defaultdict(list)
    for r in deduped:
        pops[population(r)].append(r)

    out["by_population"] = {
        name: {**rate(sum(1 for r in rs if r["correct"]), len(rs)),
               "mean_confidence": (round(sum(r["probability"] for r in rs
                                             if r.get("probability") is not None)
                                         / max(1, sum(1 for r in rs
                                                      if r.get("probability") is not None)), 4)
                                   if any(r.get("probability") is not None for r in rs) else None)}
        for name, rs in sorted(pops.items(), key=lambda kv: -len(kv[1]))
    }
    if len(pops) > 1 and not pool_populations:
        means = {k: v.get("mean_confidence") for k, v in out["by_population"].items()
                 if v.get("mean_confidence") is not None}
        if len(means) > 1:
            spread = max(means.values()) - min(means.values())
            if spread > 0.1:
                out["duplication"]["population_warning"] = (
                    f"Mean stated confidence differs by {spread:.2f} between "
                    f"populations ({means}). A calibration curve drawn across all "
                    f"of them measures the difference between populations more "
                    f"than it measures calibration.")

    # ── calibration, within the largest population only ─────────────────────
    target = max(pops.values(), key=len) if pops else deduped
    if pool_populations:
        target = deduped
    out["calibration"] = _calibration_curve(target)
    out["calibration"]["population"] = (
        "all (pooled on request)" if pool_populations
        else max(pops, key=lambda k: len(pops[k])) if pops else "all")

    # ── confidence as probability vs as ranking ─────────────────────────────
    out["confidence_semantics"] = _semantics(target)
    return out


def _calibration_curve(rows: list[dict], min_bucket: int = MIN_BUCKET) -> dict:
    usable = [r for r in rows
              if r.get("probability") is not None and r.get("correct") is not None]
    if len(usable) < MIN_EVENTS:
        return {"measurable": False, "n": len(usable), "n_required": MIN_EVENTS,
                "note": (f"{len(usable)} independent events carry a stated "
                         f"probability. Calibration is not reported below "
                         f"{MIN_EVENTS}: at this size every bucket interval "
                         f"spans chance, so the curve would show a shape that "
                         f"the data does not contain."),
                "buckets": []}

    edges = [(0.0, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 1.01)]
    buckets, ece, brier = [], 0.0, _brier(usable)
    for lo, hi in edges:
        b = [r for r in usable if lo <= r["probability"] < hi]
        if len(b) < min_bucket:
            buckets.append({"range": f"{lo:.2f}–{hi:.2f}", "n": len(b),
                            "measurable": False,
                            "why": f"below {min_bucket} events"})
            continue
        stated = sum(r["probability"] for r in b) / len(b)
        hits = sum(1 for r in b if r["correct"])
        r_ = rate(hits, len(b))
        ece += (len(b) / len(usable)) * abs(stated - hits / len(b))
        buckets.append({"range": f"{lo:.2f}–{hi:.2f}", "n": len(b), "measurable": True,
                        "stated": round(stated, 4), "realised": r_["rate"],
                        "ci95": r_["ci95"],
                        # A bucket is only "miscalibrated" if the stated value
                        # sits OUTSIDE the realised interval. Anything else is
                        # noise being read as a finding.
                        "miscalibrated": not (r_["ci95"][0] <= stated <= r_["ci95"][1])})
    measurable = [b for b in buckets if b.get("measurable")]
    return {"measurable": True, "n": len(usable),
            "brier": round(brier, 4) if brier is not None else None,
            "skill": round(1 - brier / 0.25, 4) if brier is not None else None,
            "ece": round(ece, 4),
            "buckets": buckets,
            "buckets_miscalibrated": sum(1 for b in measurable if b["miscalibrated"]),
            "verdict": (
                "no bucket is miscalibrated beyond its own error bar"
                if measurable and not any(b["miscalibrated"] for b in measurable)
                else "at least one bucket sits outside its interval"
                if measurable else "no bucket had enough events to measure")}


def _semantics(rows: list[dict]) -> dict:
    """Is confidence a probability, or only a ranking? (§18)

    These are different claims. A number can order outcomes correctly while
    being badly scaled — useful for sizing, useless as a probability. The
    rank test asks only whether higher-confidence calls are right more often
    than lower-confidence ones, which survives at smaller samples than a full
    calibration curve.
    """
    usable = [r for r in rows
              if r.get("probability") is not None and r.get("correct") is not None]
    if len(usable) < 20:
        return {"measurable": False, "n": len(usable),
                "note": "fewer than 20 events — neither reading is testable yet"}

    usable.sort(key=lambda r: r["probability"])
    half = len(usable) // 2
    low, high = usable[:half], usable[len(usable) - half:]
    lo_r = rate(sum(1 for r in low if r["correct"]), len(low))
    hi_r = rate(sum(1 for r in high if r["correct"]), len(high))

    # Overlapping intervals mean the ordering is not established.
    separated = (lo_r["ci95"][1] < hi_r["ci95"][0]) or (hi_r["ci95"][1] < lo_r["ci95"][0])
    if not separated:
        reading = ("undetermined — the two halves' intervals overlap, so the "
                   "confidence number is not yet shown to carry ordering "
                   "information either way")
    elif hi_r["rate"] > lo_r["rate"]:
        reading = "carries ordering information: higher confidence is right more often"
    else:
        reading = ("INVERTED: higher confidence is right LESS often, and the "
                   "intervals do not overlap")
    return {"measurable": True, "n": len(usable),
            "lower_half": lo_r, "upper_half": hi_r,
            "intervals_separated": separated, "reading": reading}


def summary_line() -> str:
    """One honest sentence, for the world model and the chat prompt."""
    try:
        d = investigate()
    except Exception as e:
        return f"Calibration could not be computed: {e}"
    o = d["overall"]
    if o["rate"] is None:
        return "No resolved predictions yet, so accuracy is not measurable."
    return (f"{o['n']} independent resolved events, {o['rate']:.1%} correct "
            f"(95% CI {o['ci95'][0]:.1%}–{o['ci95'][1]:.1%}) — {o['verdict']}.")
