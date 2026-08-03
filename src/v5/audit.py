"""
src/v5/audit.py
===============
The self-audit block (spec §7) that closes every substantive output.

Nothing in here is generated prose. Every line is assembled from what the
modules actually reported: what they cited, what they could not get, what they
declared as their own weaknesses, and how wide their intervals were. An audit
that was written rather than computed would be a decoration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from src.v5.contract import ModuleReport
from src.v5.ensemble import EnsembleResult
from src.v5.meta import MetaResult


@dataclass
class SelfAudit:
    what_i_know: list = field(default_factory=list)
    what_i_do_not_know: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)     # [{assumption, confidence, basis}]
    what_would_change_this: list = field(default_factory=list)
    confidence_range: dict = field(default_factory=dict)
    blind_spots: list = field(default_factory=list)
    track_record: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _weighted_interval(reports: list[ModuleReport], ens: EnsembleResult) -> tuple[float, float]:
    lo_num = hi_num = wsum = 0.0
    for r in reports:
        if not r.votes or r.ci_low is None:
            continue
        w = ens.weights.get(r.module, 0.0)
        lo_num += w * r.ci_low
        hi_num += w * r.ci_high
        wsum += w
    if wsum <= 0:
        return (0.0, 1.0)
    return (round(lo_num / wsum, 4), round(hi_num / wsum, 4))


def build(ticker: str, ens: EnsembleResult, reports: list[ModuleReport],
          meta: MetaResult, risk) -> SelfAudit:
    voting = [r for r in reports if r.votes]
    abstained = [r for r in reports if not r.votes]

    # ── what I know: the highest-weighted cited facts, with their sources ────
    facts = []
    for r in sorted(voting, key=lambda x: -ens.weights.get(x.module, 0))[:8]:
        for e in r.evidence[:2]:
            facts.append(f"{e['claim']}  [{r.module} · source: {e['source']}]")
    know = facts[:12]
    know.append(f"{len(voting)} of {len(reports)} research modules produced a calibrated view; "
                f"the weighted net score is {ens.net_score:+.1f} on a -100..+100 scale.")

    # ── what I do not know: every abstention, stated with its reason ─────────
    dont = [f"{r.module} ({r.family}): {r.reason}" for r in abstained]
    if ens.mean_ci_width > 0.35:
        dont.append(f"The modules' own confidence intervals average {ens.mean_ci_width:.0%} wide — "
                    f"most of this system's uncertainty is irreducible with the data it has.")
    if not any(r.family == "fundamental" and r.votes for r in reports):
        dont.append("No fundamental engine reported: this view rests entirely on price, flow and "
                    "statistical structure, with no claim about the underlying business.")
    if not any(r.module == "earnings" and r.votes for r in reports):
        dont.append("No earnings calendar or analyst revision data is available anywhere in this "
                    "system — a scheduled report inside the horizon is an unmodelled event.")

    # ── assumptions, each with its own confidence ───────────────────────────
    assumptions = [
        {"assumption": "The historical base rates measured here transfer to the current regime.",
         "confidence": "moderate",
         "basis": f"Most modules estimate from 2-5 years of history; the regime module reads "
                  f"{next((r.view for r in reports if r.module == 'market_regime'), 'unavailable')}."},
        {"assumption": "The modules are independent enough that agreement between them is "
                       "informative.",
         "confidence": "low to moderate",
         "basis": f"They share one price series and one data vendor. Measured dispersion is "
                  f"{ens.dispersion:.0f} points, which is the only direct evidence on this."},
        {"assumption": "Vendor fundamentals and prices are accurate and current.",
         "confidence": "moderate",
         "basis": "Yahoo Finance, unaudited, occasionally stale or restated without notice."},
        {"assumption": f"A {max((r.horizon_days for r in voting), default=21)}-day horizon is the "
                       f"right frame for this decision.",
         "confidence": "stated, not tested",
         "basis": "Modules were built to that horizon; a different holding period would re-rank them."},
    ]
    if ens.weights_version > 1:
        assumptions.append({
            "assumption": f"The learned module weights (version {ens.weights_version}) reflect real "
                          f"skill rather than a fortunate sample.",
            "confidence": "guarded",
            "basis": "Weights only move on a significant binomial test over at least 25 resolved "
                     "calls per module, and every version is reversible."})

    # ── what would change this ──────────────────────────────────────────────
    change = list(meta.falsification_tests)
    if getattr(risk, "invalidation", ""):
        change.insert(0, f"Invalidation condition: {risk.invalidation}")
    for path in meta.alternative_paths:
        if not path.agrees:
            change.append(f"The {path.name} path already disagrees ({path.direction}); if the primary "
                          f"weighting were equal-weighted instead, this call would change.")

    # ── confidence, as a range ──────────────────────────────────────────────
    lo_p, hi_p = _weighted_interval(reports, ens)
    half = ens.mean_ci_width / 2
    c = meta.confidence_after
    # Widen the EDGE, not the probability, so the band cannot cross 50% and
    # silently imply the opposite direction.
    conf_lo = 0.5 + meta.edge_after * (1 - half) / 2
    conf_hi = min(1.0, 0.5 + meta.edge_after * (1 + half) / 2)
    confidence_range = {
        "direction": ens.direction,
        "p_bull_range": [lo_p, hi_p],
        "confidence_range": [round(conf_lo, 4), round(conf_hi, 4)],
        "point": round(c, 4),
        "derivation": (f"P(bull) range is the weight-averaged interval across the {len(voting)} "
                       f"reporting modules. Confidence is stated as the probability the direction "
                       f"is correct: the post-meta figure {c:.1%}, with its edge widened by half "
                       f"the mean module interval ({half:.0%}). A point estimate here would be "
                       f"false precision."),
    }

    # ── blind spots: the modules' own stated weaknesses, weight-ranked ───────
    blind = []
    for r in sorted(voting, key=lambda x: -ens.weights.get(x.module, 0))[:6]:
        for w in r.weaknesses[:2]:
            if w and w not in blind:
                blind.append(f"{r.module}: {w}")
    if ens.abstention_rate > 0.25:
        blind.append(f"{ens.abstention_rate:.0%} of the module set could not report — the ensemble is "
                     f"narrower than its module count suggests.")
    if len(meta.contradictions):
        blind.append(f"{len(meta.contradictions)} unresolved internal contradiction(s) remain in the "
                     f"evidence base.")

    # ── track record: state it before making another call ───────────────────
    track = {}
    try:
        from src.v5.learning import bias_snapshot
        track = bias_snapshot()
    except Exception:
        track = {}

    return SelfAudit(
        what_i_know=know,
        what_i_do_not_know=dont,
        assumptions=assumptions,
        what_would_change_this=change,
        confidence_range=confidence_range,
        blind_spots=blind,
        track_record=track,
    )
