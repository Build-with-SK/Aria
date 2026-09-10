"""
src/core/attribution.py
=======================
Why a prediction was right or wrong — Phases 16 and 19.

§16 is explicit that recording WIN or LOSS is not enough. The useful question
is which of these actually happened:

    the direction was right and the move was decisive
    the direction was right but the move was inside the noise
    the direction was wrong but the move was inside the noise
    the direction was wrong and the move was decisive against it

Those four are not the same event. A "wrong" 1-day call where the price moved
0.04% is a coin landing on its edge; a wrong call where it moved 8% against the
thesis is a thesis failure. Scoring them identically is why a directional hit
rate on short horizons carries so little information — most of it is noise
being graded as skill.

THE NOISE BAND
--------------
`src/v5/learning.py::_attribute` already established the right idea in this
codebase: compare the realised move to the asset's own volatility scaled to the
horizon, and treat anything inside half a horizon-sigma as indistinguishable
from drift. This module applies that across every source in the ledger rather
than to V5 alone, and uses the realised return distribution the ledger already
stores instead of re-fetching prices.

WHAT IS NOT MEASURABLE YET, AND WHY THAT IS SAID OUT LOUD
---------------------------------------------------------
§19 asks ARIA to learn WHEN a strategy works, not merely whether. That requires
variance in the conditions. Right now every desk event in the ledger carries
`regime_at = "Expansion (Goldilocks)"` — 61 of 61 — and the technical events
carry no regime at all. There is exactly one regime in the record.

A regime breakdown drawn from that would be a table with one row wearing the
authority of a comparison. So `by_regime` reports UNMEASURABLE and names the
reason. It will start working on its own the first time the market does
something else; nothing needs changing here for that.
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: A move smaller than this fraction of a horizon-sigma is treated as noise.
#: Half a sigma is the same threshold src/v5/learning.py chose.
NOISE_SIGMAS = 0.5

#: Fallback daily volatility when an asset has too little history in the
#: ledger to estimate its own. 1.5% is a broad-equity daily sigma; crypto is
#: far higher, which is why the per-subject estimate is preferred.
DEFAULT_DAILY_VOL = 0.015

#: Minimum resolved events in a slice before its rate is called a finding.
#: Matches src/core/calibration.MIN_EVENTS deliberately — one standard.
from src.core.calibration import MIN_EVENTS, rate  # noqa: E402

REASONS = {
    "DECISIVE_HIT": "direction right and the move cleared the noise band",
    "NOISE_HIT": "direction right but the move stayed inside the noise band",
    "NOISE_MISS": "direction wrong but the move stayed inside the noise band",
    "DECISIVE_MISS": "direction wrong and the move went decisively against it",
    "NEUTRAL_HELD": "neutral call and the price stayed inside its band",
    "NEUTRAL_BROKE": "neutral call and the price broke out of its band",
    "UNSCORABLE": "no reference price or no outcome, so nothing can be attributed",
}


def _horizon_sigma(subject_returns: list[float], horizon_days: int) -> float:
    """Per-horizon sigma from the subject's own realised returns where possible.

    Returns in the ledger are already horizon-scaled (each is the move over
    that prediction's own horizon), so the standard deviation of same-horizon
    returns for a subject IS the horizon sigma — no sqrt-of-time rescaling,
    which would double-count.
    """
    usable = [r for r in subject_returns if r is not None]
    if len(usable) >= 5:
        try:
            sd = statistics.stdev(usable)
            if sd > 0:
                return sd
        except statistics.StatisticsError:
            pass
    return DEFAULT_DAILY_VOL * math.sqrt(max(1, horizon_days))


def attribute_one(pred: dict, sigma: float) -> dict:
    """Reason code for a single resolved prediction."""
    ret = pred.get("actual_return")
    correct = pred.get("correct")
    direction = (pred.get("direction") or "").lower()

    if ret is None or correct is None:
        return {"reason": "UNSCORABLE", "why": REASONS["UNSCORABLE"],
                "sigmas": None, "decisive": None}

    sigmas = abs(ret) / sigma if sigma > 0 else None
    decisive = bool(sigmas is not None and sigmas >= NOISE_SIGMAS)

    if direction == "neutral":
        reason = "NEUTRAL_HELD" if correct else "NEUTRAL_BROKE"
    elif correct:
        reason = "DECISIVE_HIT" if decisive else "NOISE_HIT"
    else:
        reason = "DECISIVE_MISS" if decisive else "NOISE_MISS"

    return {"reason": reason, "why": REASONS[reason],
            "sigmas": round(sigmas, 2) if sigmas is not None else None,
            "decisive": decisive}


def decompose(source: Optional[str] = None) -> dict:
    """Attribution across the whole resolved record.

    Every slice carries its own n and interval, and any slice below the
    threshold is reported as undecided rather than as a small finding.
    """
    from src.core import calibration, ledger

    rows = [r for r in ledger.predictions(limit=2000, source=source, resolved=True)
            if r.get("correct") is not None]
    rows = calibration._dedupe(rows)

    # Per-subject, per-horizon return samples for the sigma estimate.
    samples: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("actual_return") is not None:
            samples[(r.get("subject"), r.get("horizon_days"))].append(r["actual_return"])

    tagged = []
    for r in rows:
        sigma = _horizon_sigma(samples.get((r.get("subject"), r.get("horizon_days")), []),
                               r.get("horizon_days") or 1)
        tagged.append({**r, "attribution": attribute_one(r, sigma)})

    # ── reason mix ──────────────────────────────────────────────────────────
    mix = defaultdict(int)
    for t in tagged:
        mix[t["attribution"]["reason"]] += 1
    total = len(tagged) or 1

    # ── the number that matters: accuracy on DECISIVE moves only ────────────
    # Noise-band events are coin flips by construction; including them dilutes
    # any real signal toward 50% and is most of why short-horizon hit rates
    # look uninformative.
    decisive = [t for t in tagged if t["attribution"]["decisive"]]
    dec_rate = rate(sum(1 for t in decisive if t["correct"]), len(decisive))
    noise = [t for t in tagged if t["attribution"]["decisive"] is False]
    noise_rate = rate(sum(1 for t in noise if t["correct"]), len(noise))

    out = {
        "source": source or "all",
        "n_events": len(tagged),
        "reason_mix": {k: {"n": v, "pct": round(v / total * 100, 1)}
                       for k, v in sorted(mix.items(), key=lambda kv: -kv[1])},
        "reason_glossary": REASONS,
        "decisive_moves": {**dec_rate,
                           "note": ("Accuracy on moves that cleared the noise band. "
                                    "This is the slice where direction could have "
                                    "been informative at all.")},
        "noise_moves": {**noise_rate,
                        "note": ("Moves inside the noise band. These are coin flips "
                                 "by construction; a hit rate here measures nothing "
                                 "about the thesis.")},
        "by_direction": _slice(tagged, lambda t: t.get("direction")),
        "by_horizon": _slice(tagged, lambda t: f"{t.get('horizon_days')}d"),
        "by_strategy": _slice(tagged, lambda t: t.get("strategy")),
        "by_regime": _by_regime(tagged),
    }

    # ── the plain-language conclusion ───────────────────────────────────────
    out["reading"] = _reading(out)
    return out


def _slice(tagged: list[dict], keyfn) -> dict:
    groups = defaultdict(list)
    for t in tagged:
        groups[keyfn(t) or "unknown"].append(t)
    return {str(k): rate(sum(1 for t in v if t["correct"]), len(v))
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


#: A regime needs at least this many observations before it counts as a regime
#: ARIA has actually seen. Two labels where one has three events is not two
#: regimes; it is one regime plus an anecdote.
MIN_PER_REGIME = 20


def regime_learning_eligible(labels) -> tuple[bool, str]:
    """Is regime-conditional analysis available yet? — §19, §5 of the brief.

    Extracted from `_by_regime` so the gate can be tested directly, and so the
    UNLOCK is a property of the data rather than of a code change. Nothing here
    invents a regime: when the market does something other than
    "Expansion (Goldilocks)" and enough observations accumulate under it, this
    returns True on its own and the breakdown starts reporting.

    Returns (eligible, why) — the reason is shown to the reader, so a blocked
    analysis explains itself rather than appearing merely absent.
    """
    from collections import Counter
    counts = Counter(l for l in labels if l and l != "not recorded")
    qualifying = {k: n for k, n in counts.items() if n >= MIN_PER_REGIME}

    if len(counts) < 2:
        only = f" ({next(iter(counts))})" if len(counts) == 1 else ""
        return False, (
            f"The record contains {len(counts)} regime{'' if len(counts) == 1 else 's'}"
            f"{only}. Regime-conditional performance needs at least two regimes to "
            f"compare; with one, any breakdown is a single row wearing the authority "
            f"of a comparison.")

    if len(qualifying) < 2:
        thin = ", ".join(f"{k}={n}" for k, n in sorted(counts.items()))
        return False, (
            f"Two regime labels are present ({thin}) but only {len(qualifying)} has "
            f"at least {MIN_PER_REGIME} observations. A regime with a handful of "
            f"events is too small a sample to compare against; this unlocks on its "
            f"own once the second regime accumulates.")

    return True, (f"{len(qualifying)} regimes with at least {MIN_PER_REGIME} "
                  f"observations each: " + ", ".join(sorted(qualifying)))


def _by_regime(tagged: list[dict]) -> dict:
    """§19 — but only when the record actually contains more than one regime.

    With one regime in the data, a "breakdown" is a single row formatted as a
    comparison. Reporting it that way would invite exactly the conclusion the
    data cannot support: that the strategy works *in this regime specifically*.
    """
    groups = defaultdict(list)
    for t in tagged:
        groups[t.get("regime_at") or "not recorded"].append(t)
    named = {k: v for k, v in groups.items() if k != "not recorded"}

    eligible, why = regime_learning_eligible(
        [t.get("regime_at") for t in tagged])
    if not eligible:
        return {
            "measurable": False,
            "regimes_in_record": sorted(named),
            "unrecorded": len(groups.get("not recorded", [])),
            "note": (why + f" {len(groups.get('not recorded', []))} events carry no "
                           f"regime label at all."),
            "slices": {k: rate(sum(1 for t in v if t["correct"]), len(v))
                       for k, v in named.items()},
        }
    return {"measurable": True,
            "slices": {k: rate(sum(1 for t in v if t["correct"]), len(v))
                       for k, v in named.items()}}


def _reading(out: dict) -> str:
    """One paragraph a human or a model can act on."""
    n = out["n_events"]
    if n < MIN_EVENTS:
        return (f"{n} independent resolved events — below the {MIN_EVENTS} needed "
                f"before any slice of this is worth reading as a finding.")
    dec, noise = out["decisive_moves"], out["noise_moves"]
    parts = [
        f"{n} independent events. "
        f"{noise['n']} of them ({noise['n'] / n:.0%}) moved less than "
        f"{NOISE_SIGMAS} horizon-sigma — coin flips that dilute any hit rate "
        f"toward 50% regardless of the thesis."
    ]
    if dec["n"] >= MIN_EVENTS:
        parts.append(f"On the {dec['n']} decisive moves the record is "
                     f"{dec['rate']:.1%} (95% CI {dec['ci95'][0]:.1%}–"
                     f"{dec['ci95'][1]:.1%}) — {dec['verdict']}.")
    else:
        parts.append(f"Only {dec['n']} events cleared the band, which is below "
                     f"{MIN_EVENTS}; the slice where direction could have "
                     f"mattered is not yet large enough to read.")
    reg = out["by_regime"]
    if not reg.get("measurable"):
        parts.append("Regime-conditional performance is not measurable: "
                     + reg["note"].split(". ")[0].lower() + ".")
    return " ".join(parts)


def explain(prediction_id: str) -> dict:
    """Attribution for one prediction, for the 'why did this fail?' question."""
    from src.core import ledger

    rows = ledger.predictions(limit=2000)
    target = next((r for r in rows if r.get("id") == prediction_id), None)
    if not target:
        return {"error": f"no prediction {prediction_id}"}
    peers = [r["actual_return"] for r in rows
             if r.get("subject") == target.get("subject")
             and r.get("horizon_days") == target.get("horizon_days")
             and r.get("actual_return") is not None]
    sigma = _horizon_sigma(peers, target.get("horizon_days") or 1)
    return {
        "prediction": {k: target.get(k) for k in
                       ("id", "subject", "claim", "direction", "probability",
                        "confidence", "horizon_days", "regime_at", "strategy",
                        "price_at", "price_end", "actual_return", "correct",
                        "created_at", "resolved_at", "rationale", "invalidation")},
        "attribution": attribute_one(target, sigma),
        "horizon_sigma": round(sigma, 5),
        "sigma_basis": ("this subject's own realised returns at this horizon"
                        if len(peers) >= 5 else
                        f"a default {DEFAULT_DAILY_VOL:.1%} daily vol scaled to the "
                        f"horizon — only {len(peers)} same-horizon returns exist for "
                        f"this subject, too few to estimate its own"),
    }
