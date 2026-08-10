"""
src/v5/weightfit.py
===================
The harness that will replace the hand-set family weights — once, and only
once, there is enough resolved evidence to justify it.

THE HONEST STATE OF THIS FILE
------------------------------
`ensemble.FAMILY_WEIGHTS` are seven numbers somebody chose. They are documented
and reproducible, which is better than most, but they are not fitted to
anything: nobody has ever checked whether weighting `price` at 0.26 beats
weighting it at 0.10, because checking requires resolved calls and there have
not been enough of them.

This module is that check, written in advance. It will refuse to produce a
number today — deliberately, loudly, with the count it wanted and the count it
has — and it will start working on its own the moment the paper loop has
accumulated the sample. Writing it now means the first fit happens the day the
data arrives rather than the day somebody remembers this was a to-do.

THE PROTOCOL, AND WHY IT IS NOT "FIT THE WEIGHTS"
-------------------------------------------------
Fitting seven weights to a few hundred correlated financial outcomes will
produce an in-sample improvement every single time, and that improvement is
worth nothing. So the fit is never scored on the data it was fitted to:

  1. Resolved calls are ordered by TIME, never shuffled. A random split lets
     the fit learn from next month to trade last month.
  2. Expanding-window walk-forward: fit on everything up to date T, score the
     block after T, roll forward. Every score is out-of-sample by construction.
  3. The fitted weights are scored against the CURRENT hand-set weights on the
     same out-of-sample blocks. The question is never "are the fitted weights
     any good" — it is "are they better than what is already running", which is
     a much harder bar and the only one that matters.
  4. Brier score on P(direction correct), the same quantity the track record
     reports. One metric, everywhere, so improvements cannot be shopped for.

NOTHING HERE APPLIES ANYTHING. `propose()` returns a recommendation; a human
looks at it and decides. The weights on disk are changed by
`src/v5/learning.py`'s versioned mechanism, never by this file.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Seven free parameters against noisy, correlated outcomes. 200 is not a
# comfortable sample for that — it is the point below which the exercise is
# obviously meaningless rather than merely fragile. The out-of-sample
# comparison in (3) above is what actually protects against overfitting;
# this is the floor beneath which it cannot even be attempted.
MIN_RESOLVED = 200
MIN_PER_FAMILY = 20        # a family with fewer resolved calls keeps its prior
MIN_OOS_BLOCKS = 3         # fewer blocks than this is one lucky quarter
BLOCK_SIZE = 40            # resolved calls per out-of-sample block

# How far a fitted weight may move from the hand-set prior in one step. An
# unconstrained fit on 200 points routinely wants to zero out a whole family;
# shrinking towards the prior is what keeps one bad quarter from deleting an
# engine that took months to build.
MAX_SHRINK = 0.5           # fitted = prior + MAX_SHRINK * (raw_fit - prior)


# ── evidence ─────────────────────────────────────────────────────────────────

def resolved_calls() -> list[dict]:
    """Every resolved prediction that carries per-module scores, oldest first.

    A prediction without module detail cannot be re-scored under different
    weights, so it is not evidence here even though it counts elsewhere.
    """
    from src.v5 import learning
    out = []
    for e in learning.load_predictions():
        if not e.get("resolved"):
            continue
        if e.get("correct") is None:
            continue
        mods = [m for m in (e.get("modules") or [])
                if not m.get("abstained") and m.get("family")
                and isinstance(m.get("net"), (int, float))]
        if not mods:
            continue
        out.append({"at": e.get("at", ""), "ticker": e.get("ticker"),
                    "direction": e.get("direction"),
                    "correct": bool(e.get("correct")),
                    "modules": mods})
    out.sort(key=lambda e: str(e.get("at")))
    return out


def family_counts(calls: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for c in calls:
        for fam in {m["family"] for m in c["modules"]}:
            counts[fam] = counts.get(fam, 0) + 1
    return counts


# ── scoring a set of weights ─────────────────────────────────────────────────

def _p_correct(call: dict, weights: dict) -> float:
    """P(the call's stated direction is correct) under `weights`.

    A faithful-but-simplified reconstruction of ensemble.synthesise: family
    weight, split equally within the family, renormalised over the families
    that actually reported. The dispersion/width/abstention penalties are
    deliberately left out — they scale confidence but do not change which
    direction wins, and it is the direction being scored here.
    """
    by_family: dict[str, list[float]] = {}
    for m in call["modules"]:
        by_family.setdefault(m["family"], []).append(float(m["net"]))
    total = sum(weights.get(f, 0.0) for f in by_family) or 1.0
    net = sum(weights.get(fam, 0.0) / total * (sum(nets) / len(nets))
              for fam, nets in by_family.items())
    # net is -100..100; map to a probability with a gentle slope so that a
    # 40-point net is a 60/40 call, not a certainty.
    p_bull = 1.0 / (1.0 + math.exp(-net / 50.0))
    if call["direction"] == "bear":
        return 1.0 - p_bull
    if call["direction"] == "neutral":
        return 0.5
    return p_bull


def brier(calls: list[dict], weights: dict) -> Optional[float]:
    """Mean squared error of P(correct). Lower is better; 0.25 is a coin."""
    if not calls:
        return None
    total = 0.0
    for c in calls:
        p = _p_correct(c, weights)
        total += (p - (1.0 if c["correct"] else 0.0)) ** 2
    return round(total / len(calls), 5)


def _normalise(w: dict) -> dict:
    total = sum(max(0.0, v) for v in w.values()) or 1.0
    return {k: round(max(0.0, v) / total, 4) for k, v in w.items()}


def _fit(calls: list[dict], prior: dict, *, rounds: int = 40) -> dict:
    """Coordinate descent on the simplex, shrunk towards the prior.

    Plain numpy/stdlib on purpose — scipy is not a dependency of this project,
    and a seven-parameter fit does not need it.
    """
    best = dict(prior)
    best_score = brier(calls, best)
    if best_score is None:
        return dict(prior)

    step = 0.08
    for _ in range(rounds):
        improved = False
        for fam in list(best):
            for delta in (step, -step):
                trial = _normalise({**best, fam: max(0.0, best[fam] + delta)})
                score = brier(calls, trial)
                if score is not None and score < best_score - 1e-6:
                    best, best_score, improved = trial, score, True
        if not improved:
            step /= 2
            if step < 0.005:
                break

    # Shrink towards the prior: the fit gets a vote, not a veto.
    return _normalise({fam: prior.get(fam, 0.0)
                       + MAX_SHRINK * (best.get(fam, 0.0) - prior.get(fam, 0.0))
                       for fam in prior})


# ── the public entry point ───────────────────────────────────────────────────

def readiness() -> dict:
    """Can this run yet, and if not, exactly what is missing."""
    from src.v5.ensemble import FAMILY_WEIGHTS
    calls = resolved_calls()
    counts = family_counts(calls)
    thin = {f: counts.get(f, 0) for f in FAMILY_WEIGHTS
            if counts.get(f, 0) < MIN_PER_FAMILY}
    blocks = max(0, (len(calls) - MIN_RESOLVED) // BLOCK_SIZE + 1) if calls else 0
    ready = (len(calls) >= MIN_RESOLVED and not thin
             and blocks >= MIN_OOS_BLOCKS)
    return {
        "ready": ready,
        "resolved_calls": len(calls),
        "required": MIN_RESOLVED,
        "shortfall": max(0, MIN_RESOLVED - len(calls)),
        "per_family_counts": counts,
        "families_below_minimum": thin,
        "min_per_family": MIN_PER_FAMILY,
        "estimated_oos_blocks": blocks,
        "min_oos_blocks": MIN_OOS_BLOCKS,
        "reason": ("ready" if ready else
                   f"{len(calls)} resolved calls with module detail; "
                   f"{MIN_RESOLVED} needed"
                   + (f"; families still thin: {thin}" if thin else "")),
    }


def propose() -> dict:
    """Fit family weights out-of-sample and report whether they beat the
    hand-set ones. Applies nothing.
    """
    from src.v5.ensemble import FAMILY_WEIGHTS
    prior = dict(FAMILY_WEIGHTS)
    state = readiness()
    base = {"at": datetime.now().isoformat(timespec="seconds"),
            "current_weights": prior, "readiness": state}

    if not state["ready"]:
        return {**base, "fitted": False,
                "recommendation": "keep the current weights",
                "why": ("Not enough resolved evidence to fit anything. This is "
                        "the expected answer until the paper loop has run long "
                        "enough — " + state["reason"] + ".")}

    calls = resolved_calls()

    # Expanding-window walk-forward. Every score below is out-of-sample.
    blocks, cut = [], MIN_RESOLVED
    while cut + BLOCK_SIZE <= len(calls):
        train, test = calls[:cut], calls[cut:cut + BLOCK_SIZE]
        fitted = _fit(train, prior)
        blocks.append({
            "train_n": len(train),
            "test_n": len(test),
            "test_from": test[0]["at"][:10], "test_to": test[-1]["at"][:10],
            "brier_prior": brier(test, prior),
            "brier_fitted": brier(test, fitted),
            "weights": fitted,
        })
        cut += BLOCK_SIZE

    if len(blocks) < MIN_OOS_BLOCKS:
        return {**base, "fitted": False,
                "recommendation": "keep the current weights",
                "why": f"only {len(blocks)} out-of-sample block(s) available; "
                       f"{MIN_OOS_BLOCKS} needed"}

    prior_mean = sum(b["brier_prior"] for b in blocks) / len(blocks)
    fitted_mean = sum(b["brier_fitted"] for b in blocks) / len(blocks)
    wins = sum(1 for b in blocks if b["brier_fitted"] < b["brier_prior"])
    improvement = prior_mean - fitted_mean

    # The bar: better on average AND better in most blocks. One spectacular
    # block carrying a mean is how an overfit gets adopted.
    beats = improvement > 0.002 and wins > len(blocks) / 2
    final = _fit(calls, prior)

    return {
        **base,
        "fitted": True,
        "proposed_weights": final,
        "oos_blocks": blocks,
        "oos_brier_current": round(prior_mean, 5),
        "oos_brier_proposed": round(fitted_mean, 5),
        "oos_improvement": round(improvement, 5),
        "blocks_won": f"{wins}/{len(blocks)}",
        "beats_current": beats,
        "recommendation": ("adopt the proposed weights" if beats
                           else "keep the current weights"),
        "why": ("The fitted weights beat the hand-set ones out-of-sample in "
                f"{wins} of {len(blocks)} blocks (mean Brier "
                f"{fitted_mean:.4f} vs {prior_mean:.4f})." if beats else
                "The fitted weights did not clear the bar out-of-sample. The "
                "hand-set weights stand — an unbeaten prior is a result, not a "
                "failure."),
        "applies_anything": False,
        "note": "Nothing has been changed. Adoption is a human decision.",
    }
