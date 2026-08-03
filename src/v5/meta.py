"""
src/v5/meta.py
==============
The meta-reasoning engine (spec §6).

Before any recommendation is finalised, ARIA turns on itself:

  - builds the strongest available counterargument to its own conclusion, from
    real dissenting evidence rather than a rhetorical gesture,
  - actively collects contradictory evidence instead of confirming evidence,
  - states falsification tests — what would have to be observed for the thesis
    to be wrong, phrased so it can actually be checked,
  - runs two INDEPENDENT alternative reasoning paths that do not use the primary
    weighting scheme at all, and
  - lowers confidence mechanically when those paths disagree with the primary.

Everything here is deterministic. Meta-reasoning that produced a different
answer on every run would defeat rule 10 (reproducibility).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from statistics import median

from src.v5.contract import ModuleReport
from src.v5.ensemble import EnsembleResult

DISAGREE_PENALTY = 0.60     # confidence multiplier when a path contradicts the primary
STRAIN_PENALTY = 0.85       # when paths agree on direction but not on strength


@dataclass
class AltPath:
    name: str
    method: str
    direction: str
    p_bull: float
    strength: float
    agrees: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetaResult:
    ticker: str
    primary_direction: str
    counterargument: str
    counter_evidence: list = field(default_factory=list)
    contradictions: list = field(default_factory=list)
    falsification_tests: list = field(default_factory=list)
    alternative_paths: list = field(default_factory=list)
    confidence_before: float = 0.5      # P(direction correct) as the ensemble left it
    confidence_after: float = 0.5       # after the penalties below
    edge_before: float = 0.0
    edge_after: float = 0.0
    adjustment_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["alternative_paths"] = [p.to_dict() if isinstance(p, AltPath) else p
                                  for p in self.alternative_paths]
        return d


def _module_p(r: ModuleReport) -> float:
    side = r.bull + r.bear
    return 0.5 if side <= 0 else r.bull / side


# ── alternative reasoning paths ─────────────────────────────────────────────

def _path_median(reports: list[ModuleReport], primary: str) -> AltPath:
    """Path 1 — unweighted median of module probabilities. Deliberately ignores
    family weights, learned multipliers and every module's confidence interval,
    so it shares no machinery with the primary aggregation."""
    ps = [_module_p(r) for r in reports if r.votes]
    if not ps:
        return AltPath("unweighted median", "median of module P(bull), no weights",
                       "neutral", 0.5, 0.0, primary == "neutral", "no voting modules")
    m = median(ps)
    srt = sorted(ps)
    q1 = srt[len(srt) // 4]
    q3 = srt[(3 * len(srt)) // 4]
    iqr = q3 - q1
    direction = "bull" if m > 0.55 else "bear" if m < 0.45 else "neutral"
    strength = max(0.0, abs(m - 0.5) * 2 * (1 - min(1.0, iqr * 2)))
    return AltPath(
        "unweighted median", "median of module P(bull), no weighting scheme applied",
        direction, round(m, 4), round(strength, 4), direction == primary,
        f"Median P(bull) across {len(ps)} modules is {m:.0%} with an interquartile range of "
        f"{iqr:.2f}. Equal-weighted, this path is {direction}.")


def _path_evidence(reports: list[ModuleReport], primary: str) -> AltPath:
    """Path 2 — count the evidence, not the models. Every cited fact gets one
    vote regardless of which module produced it, so a single module with many
    supporting facts cannot dominate by being weighted heavily."""
    bull = sum(len(r.bullish_evidence()) for r in reports if r.votes)
    bear = sum(len(r.bearish_evidence()) for r in reports if r.votes)
    n = bull + bear
    if n < 5:
        return AltPath("evidence count", "one vote per cited fact", "neutral", 0.5, 0.0,
                       primary == "neutral", f"only {n} directional facts cited — too few to count")
    p = bull / n
    z = (p - 0.5) / math.sqrt(0.25 / n)
    direction = "bull" if p > 0.55 else "bear" if p < 0.45 else "neutral"
    strength = min(1.0, abs(z) / 4.0)
    return AltPath(
        "evidence count", "one vote per cited fact, module identity discarded",
        direction, round(p, 4), round(strength, 4), direction == primary,
        f"{bull} bullish facts against {bear} bearish ({p:.0%} bullish, z={z:+.1f}). "
        f"Fact-counted, this path is {direction}.")


# ── counterargument and falsification ───────────────────────────────────────

def _counterargument(reports: list[ModuleReport], ens: EnsembleResult) -> tuple[str, list]:
    """The best case against the conclusion, assembled from the evidence that
    actually opposes it — not a softened restatement of the primary view."""
    opposing = "bear" if ens.direction == "bull" else "bull"
    items = []
    for r in reports:
        if not r.votes:
            continue
        ev = r.bearish_evidence() if opposing == "bear" else r.bullish_evidence()
        for e in ev:
            items.append({**e, "module": r.module, "family": r.family,
                          "module_view": r.view,
                          "weight": ens.weights.get(r.module, 0.0)})
    # Evidence from a module that disagrees outright is stronger than a caveat
    # buried inside a module that agrees.
    items.sort(key=lambda x: (x["module_view"] == opposing, x["weight"]), reverse=True)
    top = items[:6]

    if not top:
        return ("No module produced evidence against this conclusion. That is not corroboration — "
                "it more likely means the module set is too narrow or too correlated to disagree, "
                "which is itself a reason to hold the view more loosely.", [])

    dissenting = [r.module for r in reports if r.votes and r.view == opposing]
    lead = (f"{len(dissenting)} module(s) — {', '.join(dissenting)} — reach the opposite conclusion. "
            if dissenting else
            "No module reaches the opposite conclusion outright, but the following evidence cuts "
            "against the call. ")
    body = " ".join(f"{i + 1}) {e['claim']} [{e['module']}]." for i, e in enumerate(top))
    close = (f" If the {ens.direction} case is wrong, this is the shape of how: the same data that "
             f"supports it is being read in a regime where these {len(top)} facts dominate instead.")
    return (lead + body + close, top)


def _contradictions(reports: list[ModuleReport]) -> list[str]:
    """Where the same number is being cited to opposite ends, and where modules
    that measure the same thing disagree."""
    out = []
    seen: dict[tuple, list] = {}
    for r in reports:
        if not r.votes:
            continue
        for e in r.evidence:
            if e.get("value") is None:
                continue
            key = (e["source"], round(float(e["value"]), 6)) if isinstance(e["value"], (int, float)) \
                else (e["source"], str(e["value"]))
            seen.setdefault(key, []).append((r.module, e.get("lean"), e["claim"]))
    for key, uses in seen.items():
        leans = {u[1] for u in uses}
        if "bull" in leans and "bear" in leans:
            mods = ", ".join(sorted({u[0] for u in uses}))
            out.append(f"The same figure ({key[1]} from {key[0]}) is cited both bullishly and "
                       f"bearishly by {mods} — one of those readings is wrong.")

    # Structural disagreements between engines measuring the same phenomenon.
    by = {r.module: r for r in reports if r.votes}
    for a, b, what in [("trend_following", "mean_reversion", "the same price series"),
                       ("technical", "ml_ensemble", "the same technical features"),
                       ("momentum", "crowding", "the same extension from trend"),
                       ("news_nlp", "sentiment_extremes", "sentiment")]:
        ra, rb = by.get(a), by.get(b)
        if ra and rb and ra.view != rb.view and "neutral" not in (ra.view, rb.view):
            out.append(f"{a} ({ra.view}, {ra.net:+.0f}) and {b} ({rb.view}, {rb.net:+.0f}) read "
                       f"{what} in opposite directions.")
    return out


def _falsification_tests(reports: list[ModuleReport], ens: EnsembleResult) -> list[str]:
    """Checks that could actually be run, phrased so a wrong thesis fails them."""
    tests = []
    supporting = sorted([r for r in reports if r.votes and r.view == ens.direction],
                        key=lambda r: -abs(r.net))[:3]
    for r in supporting:
        if r.n_obs and r.ci_low is not None:
            tests.append(
                f"Re-run {r.module} on a held-out earlier half of the sample. Its claimed "
                f"{r.ci_low:.0%}-{r.ci_high:.0%} interval came from {r.n_obs} observations; if the "
                f"out-of-sample rate falls outside that interval, the module is not measuring what "
                f"it claims.")
    tests.append(
        f"Drop the three highest-weighted modules ({', '.join(list(ens.weights)[:3])}) and re-synthesise. "
        f"If the direction flips, this recommendation rests on three engines, not on {ens.n_voting}.")
    if ens.dispersion > 30:
        tests.append(
            f"Cross-model dispersion is {ens.dispersion:.0f} points. Sample the disagreeing modules' "
            f"historical accuracy from the prediction log: if the dissenters have the better record on "
            f"this ticker, the weighting is backwards.")
    tests.append(
        "Check whether the same conclusion is reached with the 5-year window replaced by the last "
        "12 months. A conclusion that only survives on decade-long data is a claim about a regime "
        "that may no longer exist.")
    if ens.abstention_rate > 0.2:
        tests.append(
            f"{ens.abstention_rate:.0%} of modules abstained. Establish whether those abstentions are "
            f"random or systematically concentrated in the engines that would have disagreed.")
    return tests


# ── entry point ─────────────────────────────────────────────────────────────

def review(ticker: str, ens: EnsembleResult, reports: list[ModuleReport]) -> MetaResult:
    counter, counter_ev = _counterargument(reports, ens)
    paths = [_path_median(reports, ens.direction), _path_evidence(reports, ens.direction)]

    # The penalties act on the EDGE (distance from a coin flip), never on the
    # probability itself — halving a 60% confidence to 30% would assert the
    # opposite direction, which is not what a disagreement means.
    edge = ens.edge
    reasons = []
    for path in paths:
        if path.direction == "neutral" and ens.direction != "neutral":
            edge *= STRAIN_PENALTY
            reasons.append(f"the {path.name} path finds no direction at all")
        elif not path.agrees:
            edge *= DISAGREE_PENALTY
            reasons.append(f"the {path.name} path reaches {path.direction}, contradicting the primary")
        elif abs(path.strength - ens.edge) > 0.35:
            edge *= STRAIN_PENALTY
            reasons.append(f"the {path.name} path agrees on direction but at very different strength "
                           f"({path.strength:.0%} versus {ens.edge:.0%})")

    contradictions = _contradictions(reports)
    if contradictions:
        edge *= max(0.7, 1 - 0.06 * len(contradictions))
        reasons.append(f"{len(contradictions)} internal contradiction(s) found in the evidence")

    conf = 0.5 + edge / 2
    reason = ("Independent paths corroborate the primary aggregation and no contradictions were "
              "found; confidence is unchanged." if not reasons else
              "Edge cut from {:.0%} to {:.0%} (confidence {:.0%} → {:.0%}) because ".format(
                  ens.edge, edge, ens.confidence, conf)
              + "; ".join(reasons) + ".")

    return MetaResult(
        ticker=ticker,
        primary_direction=ens.direction,
        counterargument=counter,
        counter_evidence=counter_ev,
        contradictions=contradictions,
        falsification_tests=_falsification_tests(reports, ens),
        alternative_paths=paths,
        confidence_before=round(ens.confidence, 4),
        confidence_after=round(conf, 4),
        edge_before=round(ens.edge, 4),
        edge_after=round(edge, 4),
        adjustment_reason=reason,
    )
