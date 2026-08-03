"""
src/v5/ensemble.py
==================
Ensemble synthesis (spec §4).

The weighting scheme is documented here rather than tuned in private, because
"another analyst with the same data reaches the same answer" (rule 10) requires
the aggregation to be readable:

  1. Each family carries a fixed weight. The ordering reflects how much
     information each family actually carries at a 21-day horizon, not how
     sophisticated it sounds. Price and quant engines dominate; macro and
     behavioural condition rather than decide.

  2. Inside a family, modules are weighted equally, then scaled by their learned
     reliability multiplier (src.v5.learning) — which only ever moves after a
     statistically significant sample.

  3. Abstaining modules are dropped and the remaining weights renormalised. A
     family in which every module abstained redistributes its weight to the
     families that did report, so an absent engine dilutes nothing.

     A family that reports through only one of its eight engines, however, is
     NOT as well measured as one reporting through all eight, so its weight is
     scaled by sqrt(reporting / registered) before renormalisation. Without this
     a single surviving module inherits its whole family's weight and can
     outvote four engines from another family — which is how a data outage
     quietly becomes a concentrated bet on whatever still had data.

  4. Composite confidence falls MECHANICALLY with cross-model dispersion, with
     the average width of the modules' own confidence intervals, and with the
     abstention rate. There is no discretionary override anywhere in this file.

     Confidence is expressed as P(the stated direction is correct), so it lives
     in [0.5, 1.0] and means exactly one thing. The quantity the penalties act
     on is the EDGE — how far the ensemble is from a coin flip, scaled to
     [0, 1] — and confidence is 0.5 + edge/2. A 60% confidence is a claim that
     six of ten such calls should be right, and the learning engine measures
     precisely that. Anything expressed as a percentage that cannot be checked
     against outcomes would be a marketing number.

  5. The dissenting minority is preserved with its strongest evidence attached.
     A losing argument is outweighed, never deleted.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from src.v5.contract import DIRECTION_BAND, ModuleReport

# Documented, reproducible family weights. They sum to 1.0.
FAMILY_WEIGHTS = {
    "price": 0.26,
    "quant": 0.22,
    "fundamental": 0.16,
    "machine": 0.14,
    "volatility": 0.10,
    "macro": 0.08,
    "behavioural": 0.04,
}

# How hard each source of uncertainty bites into composite confidence.
DISPERSION_CAP = 0.60      # max confidence lost to cross-model disagreement
WIDTH_CAP = 0.50           # max confidence lost to wide module intervals
ABSTENTION_CAP = 0.40      # max confidence lost to missing engines


@dataclass
class EnsembleResult:
    ticker: str
    direction: str                  # bull | bear | neutral
    net_score: float                # -100 .. +100, weighted
    p_bull: float                   # 0..1, weighted
    confidence: float               # P(direction correct), 0.5..1.0, after penalties
    edge: float                     # 0..1 distance from a coin flip, after penalties
    edge_raw: float                 # the same, before penalties, for transparency
    dispersion: float               # weighted std of module net scores
    mean_ci_width: float
    abstention_rate: float
    n_modules: int
    n_voting: int
    weights: dict = field(default_factory=dict)
    family_weights: dict = field(default_factory=dict)
    agreement: dict = field(default_factory=dict)
    majority: list = field(default_factory=list)
    dissent: list = field(default_factory=list)
    penalties: dict = field(default_factory=dict)
    weights_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def _module_p(r: ModuleReport) -> float:
    """The module's implied P(bull), WITH its neutral mass intact.

    This used to be `bull / (bull + bear)`, which renormalised the neutral mass
    away and handed back the module's raw point estimate — undoing the one
    mechanism the contract exists to enforce.

    `from_probability` spends the width of a module's interval on neutral mass
    precisely so that a wide interval cannot produce a confident score. Dividing
    it back out recovered the original p exactly: ten modules each reporting
    p=0.80 with a maximally wide interval produced bull=12, bear=3, neutral=85
    and view="neutral" individually, yet `bull/(bull+bear)` = 0.80 for every one
    of them. The ensemble read 0.80, called it BULL at 65% confidence — above
    the 55% trading bar — and the risk gate released the full budget, on ten
    engines that had each said, in as many words, "I cannot say".

    Deriving p from `net` instead keeps the neutral mass in the denominator:
    net = bull - bear over a 200-point range, so p = 0.5 + net/200. The same ten
    modules now give net=9 → p=0.545, a whisker off a coin flip, which is what
    they actually meant. A genuinely confident module is barely touched: p=0.80
    on a tight 0.10 interval gives net=54 → p=0.77.
    """
    return max(0.0, min(1.0, 0.5 + r.net / 200.0))


def synthesise(reports: list[ModuleReport], ticker: str) -> EnsembleResult:
    voting = [r for r in reports if r.votes]
    n_total = len(reports)

    if not voting:
        return EnsembleResult(
            ticker=ticker, direction="neutral", net_score=0.0, p_bull=0.5,
            confidence=0.5, edge=0.0, edge_raw=0.0, dispersion=0.0, mean_ci_width=1.0,
            abstention_rate=1.0, n_modules=n_total, n_voting=0,
            agreement={"note": "every module abstained — no view is possible"},
            penalties={}, weights_version=_version())

    multipliers = _multipliers()

    # Step 1-3: family weight → equal within family → reliability → renormalise.
    fam_present: dict[str, list[ModuleReport]] = {}
    for r in voting:
        fam_present.setdefault(r.family, []).append(r)

    coverage = {fam: math.sqrt(len(members) / max(len(members), _family_size(fam)))
                for fam, members in fam_present.items()}
    present_weight = sum(FAMILY_WEIGHTS.get(f, 0.05) * coverage[f] for f in fam_present)
    weights: dict[str, float] = {}
    fam_weights: dict[str, float] = {}
    for fam, members in fam_present.items():
        fw = FAMILY_WEIGHTS.get(fam, 0.05) * coverage[fam] / present_weight
        fam_weights[fam] = round(fw, 4)
        raw = {m.module: multipliers.get(m.module, 1.0) for m in members}
        tot = sum(raw.values()) or 1.0
        for name, mult in raw.items():
            weights[name] = fw * mult / tot

    total_w = sum(weights.values()) or 1.0
    weights = {k: v / total_w for k, v in weights.items()}

    # Step 4: aggregate.
    net = sum(weights[r.module] * r.net for r in voting)
    p_bull = sum(weights[r.module] * _module_p(r) for r in voting)
    mean_net = net
    dispersion = math.sqrt(sum(weights[r.module] * (r.net - mean_net) ** 2 for r in voting))
    widths = [(weights[r.module], r.ci_width) for r in voting if r.ci_width is not None]
    mean_width = (sum(w * cw for w, cw in widths) / sum(w for w, _ in widths)) if widths else 0.6
    abstention_rate = (n_total - len(voting)) / n_total if n_total else 0.0

    edge_raw = min(1.0, abs(p_bull - 0.5) * 2)
    p_disp = min(DISPERSION_CAP, dispersion / 100.0)
    p_width = min(WIDTH_CAP, mean_width)
    p_abst = min(ABSTENTION_CAP, abstention_rate * 0.5)
    edge = edge_raw * (1 - p_disp) * (1 - p_width) * (1 - p_abst)
    confidence = 0.5 + edge / 2

    # Same band the modules use (contract.DIRECTION_BAND), so the aggregate
    # cannot claim a direction that none of its constituents claimed.
    direction = ("bull" if net > DIRECTION_BAND else
                 "bear" if net < -DIRECTION_BAND else "neutral")
    # The two aggregations must not point opposite ways. Both are now derived
    # from the same signed scores, so a conflict means something is badly wrong
    # rather than merely imprecise — keep the guard.
    if direction != "neutral" and (net > 0) != (p_bull > 0.5):
        direction = "neutral"
        edge, confidence = 0.0, 0.5

    # "neutral" means no view, so it cannot carry a confidence: confidence is
    # defined as P(the stated direction is correct), and there is no stated
    # direction. This used to fire only on a sign conflict, so a net of 4 with
    # p_bull of 0.72 shipped "no position ... confidence 62%", and the learning
    # log then averaged that meaningless number into its calibration stats.
    if direction == "neutral":
        edge, confidence = 0.0, 0.5

    # Step 5: agreement map and preserved dissent.
    bulls = [r for r in voting if r.view == "bull"]
    bears = [r for r in voting if r.view == "bear"]
    neutrals = [r for r in voting if r.view == "neutral"]
    majority_side, minority_side = (bulls, bears) if direction == "bull" else \
                                   (bears, bulls) if direction == "bear" else (neutrals, bulls + bears)

    agreement = {
        # When the ensemble is neutral there is no majority to dissent FROM —
        # the directional modules cancelled each other out, which is a different
        # statement and should not be labelled as dissent.
        "dissent_label": ("dissent" if direction != "neutral"
                          else "directional views that cancelled out"),
        "bull": [r.module for r in bulls],
        "bear": [r.module for r in bears],
        "neutral": [r.module for r in neutrals],
        "abstained": [r.module for r in reports if not r.votes],
        "agreement_ratio": round(len(majority_side) / len(voting), 3) if voting else 0.0,
        "conflicts": _conflicts(voting),
    }

    def _summarise(rs: list[ModuleReport]) -> list[dict]:
        out = []
        for r in sorted(rs, key=lambda x: -weights.get(x.module, 0)):
            ev = (r.bullish_evidence() if r.view == "bull" else r.bearish_evidence()) or r.evidence
            out.append({
                "module": r.module, "family": r.family, "view": r.view,
                "net": r.net, "weight": round(weights.get(r.module, 0), 4),
                "ci": [r.ci_low, r.ci_high],
                "strongest_evidence": ev[0]["claim"] if ev else r.thesis[:160],
                "thesis": r.thesis,
            })
        return out

    return EnsembleResult(
        ticker=ticker,
        direction=direction,
        net_score=round(net, 2),
        p_bull=round(p_bull, 4),
        confidence=round(confidence, 4),
        edge=round(edge, 4),
        edge_raw=round(edge_raw, 4),
        dispersion=round(dispersion, 2),
        mean_ci_width=round(mean_width, 4),
        abstention_rate=round(abstention_rate, 4),
        n_modules=n_total,
        n_voting=len(voting),
        weights={k: round(v, 4) for k, v in sorted(weights.items(), key=lambda kv: -kv[1])},
        family_weights=fam_weights,
        agreement=agreement,
        majority=_summarise(majority_side),
        dissent=_summarise(minority_side),
        penalties={
            "dispersion": round(p_disp, 4),
            "interval_width": round(p_width, 4),
            "abstention": round(p_abst, 4),
            "explanation": (f"Raw edge {edge_raw:.0%} was cut to {edge:.0%} by {p_disp:.0%} "
                            f"cross-model dispersion, {p_width:.0%} mean interval width and "
                            f"{p_abst:.0%} abstention — a stated {confidence:.0%} probability that "
                            f"the direction is right."),
        },
        weights_version=_version(),
    )


def _conflicts(voting: list[ModuleReport]) -> list[str]:
    """Named disagreements worth surfacing rather than averaging away."""
    out = []
    by_name = {r.module: r for r in voting}
    pairs = [("trend_following", "mean_reversion"),
             ("momentum", "mean_reversion"),
             ("value", "momentum"),
             ("technical", "ml_ensemble"),
             ("news_nlp", "sentiment_extremes")]
    for a, b in pairs:
        ra, rb = by_name.get(a), by_name.get(b)
        if ra and rb and ra.view != rb.view and "neutral" not in (ra.view, rb.view):
            out.append(f"{a} is {ra.view} ({ra.net:+.0f}) while {b} is {rb.view} ({rb.net:+.0f})")
    spread = max((r.net for r in voting), default=0) - min((r.net for r in voting), default=0)
    if spread > 80:
        hi = max(voting, key=lambda r: r.net)
        lo = min(voting, key=lambda r: r.net)
        out.append(f"Widest split: {hi.module} {hi.net:+.0f} versus {lo.module} {lo.net:+.0f} "
                   f"({spread:.0f} points apart)")
    return out


def _family_size(family: str) -> int:
    """How many live modules the family is *supposed* to have. Falls back to the
    number actually present when the registry cannot answer (unit tests, or a
    family running entirely on ad-hoc modules)."""
    try:
        from src.v5.registry import REGISTRY
        n = sum(1 for s in REGISTRY.values() if s.family == family and not s.research_only)
        return n or 1
    except Exception:
        return 1


def _multipliers() -> dict:
    try:
        from src.v5.learning import current_multipliers
        return current_multipliers()
    except Exception:
        return {}


def _version() -> int:
    try:
        from src.v5.learning import weights_version
        return weights_version()
    except Exception:
        return 1
