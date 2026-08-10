"""
src/v5/tiers.py
===============
Which of the 41 engines have actually earned a vote.

A flat list of forty-one modules is a liability presented as a feature. It reads
as breadth without depth, and it invites the obvious objection: more knobs means
more ways to fit history without noticing. The objection is correct as long as
every module is presented as equally trustworthy, because then the reader has no
way to tell the three that work from the thirty-eight that are along for the
ride — and neither does the author.

The answer is not to delete modules. Research code should be allowed to be
speculative; that is what research is. The answer is that a module's standing
must be EARNED FROM ITS OWN TRACK RECORD and recomputed from outcomes, never
declared in a config file by the person who wrote it.

    experimental   the default and the honest starting point for all 41.
                   Not enough resolved calls to say anything. Runs, reports,
                   appears in the UI, and is labelled as unproven.

    provisional    enough calls to measure, hit rate not distinguishable from
                   chance. This is where a good module lives for a long time,
                   and it is not a criticism — most real signals are weak.

    core           enough calls, hit rate significantly better than a coin at
                   p<0.05. The only tier that has evidence behind it.

    benched        significantly WORSE than a coin. Kept running and kept
                   visible, because a module that is reliably wrong is
                   information, but excluded from live recommendations.

Nothing here is hand-set. `classify()` reads `learning.module_scorecard()`, which
reads resolved outcomes, and that is the only input. A module cannot be promoted
by editing this file — there is no list to edit.

One honest caveat, stated here because it belongs next to the code and not only
in the README: with 41 modules tested at p<0.05, roughly two are expected to
clear the bar by chance alone. `classify()` therefore also reports a
Benjamini-Hochberg adjusted verdict across the whole family of tests, and the
`core_bh` tier is the one to trust when someone asks whether the promotions are
real. Reporting the naive count without the correction would be the exact
multiple-comparisons error this system exists to avoid.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

MIN_RESOLVED = 25       # calls before a module can leave `experimental`
SIGNIFICANCE = 0.05

TIERS = ("core", "provisional", "experimental", "benched")

TIER_NOTES = {
    "core": "Measured edge over a coin flip on this module's own resolved calls.",
    "provisional": "Enough calls to measure; not distinguishable from chance.",
    "experimental": "Not enough resolved calls to say anything. Unproven.",
    "benched": "Significantly worse than chance. Excluded from live recommendations.",
}


def _bh_adjust(pvalues: dict[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg adjusted p-values (controls false discovery rate).

    With 41 simultaneous tests at p<0.05, about two false positives are the
    EXPECTED outcome under a null where nothing works. Any promotion scheme that
    ignores this will reliably manufacture two 'validated' modules from noise
    and then weight them.
    """
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    if m == 0:
        return {}
    adjusted: dict[str, float] = {}
    prev = 1.0
    # Walk from the largest p downwards, enforcing monotonicity.
    for rank in range(m, 0, -1):
        name, p = items[rank - 1]
        val = min(prev, p * m / rank)
        adjusted[name] = round(min(1.0, val), 6)
        prev = val
    return adjusted


def classify() -> dict:
    """Current tier of every registered module, computed from outcomes.

    Every registered module appears in the output, including ones with no
    resolved calls at all — a module missing from the scorecard is
    `experimental`, not absent. Silently dropping unmeasured modules would make
    the tier table look far more validated than the system is.
    """
    from src.v5.learning import module_scorecard
    from src.v5.registry import REGISTRY, load_modules

    load_modules()
    card = module_scorecard()

    # First pass: gather p-values for the modules that are measurable at all,
    # so the multiple-comparisons correction sees the whole family of tests.
    measurable_p: dict[str, float] = {}
    for name, rec in card.items():
        if (rec.get("n") or 0) >= MIN_RESOLVED and rec.get("p_value") is not None:
            measurable_p[name] = float(rec["p_value"])
    adjusted = _bh_adjust(measurable_p)

    out: dict[str, dict] = {}
    for name, spec in REGISTRY.items():
        rec = card.get(name) or {}
        n = int(rec.get("n") or 0)
        hit = rec.get("hit_rate")
        p = rec.get("p_value")

        if n < MIN_RESOLVED:
            tier, reason = "experimental", (
                f"{n} resolved calls; {MIN_RESOLVED} needed before this module's "
                f"hit rate means anything")
        elif p is not None and p < SIGNIFICANCE and hit is not None and hit > 0.5:
            tier, reason = "core", (
                f"{hit:.0%} on {n} calls, p={p:.3f} against a coin flip")
        elif p is not None and p < SIGNIFICANCE and hit is not None and hit < 0.5:
            tier, reason = "benched", (
                f"{hit:.0%} on {n} calls, p={p:.3f} — reliably wrong, not just unlucky")
        else:
            tier, reason = "provisional", (
                f"{hit:.0%} on {n} calls" + (f", p={p:.3f}" if p is not None else "") +
                " — measurable, but not distinguishable from chance")

        p_adj = adjusted.get(name)
        survives_bh = (tier == "core" and p_adj is not None and p_adj < SIGNIFICANCE)

        out[name] = {
            "module": name,
            "family": spec.family,
            "provenance": getattr(spec, "provenance", "statistical"),
            "research_only": spec.research_only,
            "tier": tier,
            "tier_note": TIER_NOTES[tier],
            "reason": reason,
            "n_resolved": n,
            "hit_rate": hit,
            "p_value": p,
            "p_value_bh_adjusted": p_adj,
            # The honest version of "core": still significant after correcting
            # for the fact that 41 tests were run.
            "core_after_correction": survives_bh,
        }
    return out


def summary() -> dict:
    """Tier counts plus the one sentence a reader actually needs."""
    tiers = classify()
    counts = {t: 0 for t in TIERS}
    for rec in tiers.values():
        counts[rec["tier"]] += 1
    n_core_bh = sum(1 for r in tiers.values() if r["core_after_correction"])
    total = len(tiers)

    if counts["core"] == 0:
        headline = (f"None of the {total} research modules has enough resolved calls to "
                    f"show an edge yet. Every one is experimental until it does.")
    elif n_core_bh == 0:
        headline = (f"{counts['core']} of {total} modules clear p<0.05 individually, but none "
                    f"survives correction for having run {len(tiers)} tests. Treat the "
                    f"promotions as unproven.")
    else:
        headline = (f"{n_core_bh} of {total} modules show an edge that survives correction "
                    f"for multiple comparisons. {counts['benched']} are benched.")

    return {
        "counts": counts,
        "n_core_after_correction": n_core_bh,
        "n_modules": total,
        "min_resolved_to_leave_experimental": MIN_RESOLVED,
        "headline": headline,
        "tier_definitions": TIER_NOTES,
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def tier_of(module_name: str) -> str:
    return (classify().get(module_name) or {}).get("tier", "experimental")


def modules_in_tier(tier: str) -> list[str]:
    return sorted(n for n, r in classify().items() if r["tier"] == tier)


def live_eligible() -> set[str]:
    """Modules allowed to contribute to a live recommendation.

    Benched modules are excluded — a module measured as reliably wrong should
    not be quietly averaged into a number a human is asked to approve.
    Experimental and provisional modules DO vote: excluding everything unproven
    would leave the ensemble empty on day one, and the ensemble already
    discounts weak modules through the learned reliability multipliers. The tier
    system's job is to tell the reader what they are looking at, not to silently
    prune the research surface.
    """
    return {n for n, r in classify().items()
            if r["tier"] != "benched" and not r["research_only"]}


def report() -> dict:
    """Full payload for the deck: the summary plus every module's standing."""
    tiers = classify()
    return {
        "summary": summary(),
        "modules": sorted(tiers.values(),
                          key=lambda r: (TIERS.index(r["tier"]), r["family"], r["module"])),
    }
