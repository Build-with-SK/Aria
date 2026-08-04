"""
src/v5/contract.py
==================
The single output type every research module must return.

Spec §3: every module outputs, every time — bull score, bear score, neutral
score, a confidence INTERVAL (not a point estimate dressed up as one),
supporting evidence citing real data, and the known weaknesses of that specific
model in that specific context.

Spec §3, final clause: a module that cannot produce a genuine confidence
interval reports "insufficient data for calibrated confidence" rather than
inventing one. `insufficient()` is that path, and it is not a failure mode —
it is the honest answer, and the ensemble treats it as an abstention.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Optional

# ── evidence ─────────────────────────────────────────────────────────────────


def Evidence(claim: str, value: Any, source: str, lean: str = "neutral") -> dict:
    """One cited fact. `source` names the file/API the number came from — an
    evidence item without a real source is a violation of Law 2.

    Shape-compatible with src.desk.opinion.ev so desk and V5 evidence can be
    concatenated and the debate engine can consume either.
    """
    if lean not in ("bull", "bear", "neutral"):
        lean = "neutral"
    return {"claim": claim, "value": value, "source": source, "lean": lean}


# ── confidence intervals ─────────────────────────────────────────────────────

# How far from zero a net score must sit before it counts as a direction rather
# than "no view". Shared with the ensemble deliberately: when every module reads
# neutral, the ensemble must not read bull. The two used to disagree — modules
# banded at ±10, the ensemble at ±5 — so a net of 9 was simultaneously "neutral"
# on every constituent and "bull" on the aggregate.
DIRECTION_BAND = 10.0


def _finite(x) -> bool:
    """True only for a real, usable number. NaN and infinity are neither."""
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def wilson_interval(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion — well-behaved at small n and at
    p near 0 or 1, where the normal approximation is not. Returns (lo, hi)."""
    if n <= 0:
        return (0.0, 1.0)
    p = max(0.0, min(1.0, successes / n))
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def bootstrap_interval(values: list[float], z: float = 1.96) -> tuple[float, float]:
    """Normal-theory interval on the mean of a sample. Used where a module's
    signal is an average of independent observations (e.g. per-horizon
    probabilities) rather than a count of successes."""
    n = len(values)
    if n < 2:
        return (0.0, 1.0)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    return (round(max(0.0, mean - z * se), 4), round(min(1.0, mean + z * se), 4))


# ── the report ───────────────────────────────────────────────────────────────

@dataclass
class ModuleReport:
    """What one research module concluded about one instrument.

    Score convention: `bull`, `bear` and `neutral` are percentages that sum to
    100. Neutral is not "no opinion" — it is the probability mass the module
    honestly cannot assign to either side, and it grows mechanically with the
    width of the confidence interval (see `from_probability`).
    """

    module: str
    family: str
    ticker: str

    bull: float = 0.0
    bear: float = 0.0
    neutral: float = 100.0

    # Confidence interval on P(bullish over the module's horizon), 0..1.
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None

    thesis: str = ""
    evidence: list = field(default_factory=list)
    weaknesses: list = field(default_factory=list)

    insufficient_data: bool = False
    reason: str = ""            # why, when insufficient_data is True

    horizon_days: int = 21
    n_obs: int = 0              # observations behind the estimate

    # What kind of process produced this score — "statistical", "model" or
    # "narrative". Stamped by the registry from the module's declaration, so a
    # module cannot claim a provenance it was not registered with. See
    # src/v5/registry.py:PROVENANCE for what each one is worth.
    provenance: str = "statistical"
    as_of: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    error: str = ""             # set when the module raised; still a valid abstention

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def net(self) -> float:
        """+100 (max bull) .. -100 (max bear)."""
        return round(self.bull - self.bear, 2)

    @property
    def view(self) -> str:
        if self.insufficient_data:
            return "abstain"
        if self.net > DIRECTION_BAND:
            return "bull"
        if self.net < -DIRECTION_BAND:
            return "bear"
        return "neutral"

    @property
    def ci_width(self) -> Optional[float]:
        if self.ci_low is None or self.ci_high is None:
            return None
        return round(self.ci_high - self.ci_low, 4)

    @property
    def votes(self) -> bool:
        """Whether the ensemble should count this module at all."""
        return not self.insufficient_data

    def bullish_evidence(self) -> list:
        return [e for e in self.evidence if e.get("lean") == "bull"]

    def bearish_evidence(self) -> list:
        return [e for e in self.evidence if e.get("lean") == "bear"]

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update({"net": self.net, "view": self.view, "ci_width": self.ci_width})
        return d

    # ── constructors ─────────────────────────────────────────────────────────

    @classmethod
    def from_probability(
        cls,
        module: str,
        family: str,
        ticker: str,
        p_bull: float,
        ci: tuple[float, float],
        *,
        thesis: str,
        evidence: list,
        weaknesses: list,
        horizon_days: int = 21,
        n_obs: int = 0,
    ) -> "ModuleReport":
        """Build a report from a bullish probability and its interval.

        The mapping is fixed and documented so it is reproducible:

            neutral      = 100 * clamp(ci_width, 0.10, 0.85)
            conviction   = 100 - neutral
            bull         = conviction * p
            bear         = conviction * (1 - p)

        A wide interval therefore *cannot* produce a confident score, no matter
        how extreme the point estimate. That is the mechanism behind spec §2's
        "confidence is a statement about uncertainty".

        A non-finite input (NaN or infinity, almost always a division by zero
        upstream) becomes an abstention rather than a score. A number nobody can
        interpret must never reach the ensemble.
        """
        if not all(_finite(v) for v in (p_bull, ci[0], ci[1])):
            return insufficient(
                module, family, ticker,
                "the estimator produced a non-finite value (division by zero or "
                "missing data upstream), so no calibrated score is possible")
        p = max(0.0, min(1.0, float(p_bull)))
        lo, hi = (max(0.0, min(1.0, float(ci[0]))), max(0.0, min(1.0, float(ci[1]))))
        if hi < lo:
            lo, hi = hi, lo
        width = hi - lo
        neutral = 100.0 * max(0.10, min(0.85, width))
        conviction = 100.0 - neutral
        return cls(
            module=module, family=family, ticker=ticker,
            bull=round(conviction * p, 2),
            bear=round(conviction * (1 - p), 2),
            neutral=round(neutral, 2),
            ci_low=round(lo, 4), ci_high=round(hi, 4),
            thesis=thesis, evidence=evidence, weaknesses=weaknesses,
            horizon_days=horizon_days, n_obs=n_obs,
        )


    def widened_to(self, min_width: float) -> "ModuleReport":
        """This report with its confidence interval widened to at least
        `min_width`, and its scores recomputed through the same documented
        mapping `from_probability` uses.

        Used to enforce the narrative-provenance floor (registry.run): a
        language model has no observation count behind it, so it is not entitled
        to a narrow interval no matter how decisive its prose was. Widening the
        interval and re-deriving the scores is the honest correction, because it
        moves the removed conviction into `neutral` rather than deleting the
        module's view outright.

        Abstentions and intervals already wide enough pass through untouched.
        """
        if self.insufficient_data or self.ci_low is None or self.ci_high is None:
            return self
        width = self.ci_high - self.ci_low
        if width >= min_width:
            return self

        # Widen symmetrically about the midpoint, then clip to [0, 1]. Clipping
        # can shrink the width again at the boundaries; shifting the interval
        # back inside the unit range keeps the requested width where possible.
        mid = (self.ci_low + self.ci_high) / 2.0
        lo, hi = mid - min_width / 2.0, mid + min_width / 2.0
        if lo < 0.0:
            lo, hi = 0.0, min(1.0, min_width)
        elif hi > 1.0:
            lo, hi = max(0.0, 1.0 - min_width), 1.0

        conviction_p = self.bull / (self.bull + self.bear) if (self.bull + self.bear) > 0 else 0.5
        neutral = 100.0 * max(0.10, min(0.85, hi - lo))
        conviction = 100.0 - neutral
        return replace(
            self,
            bull=round(conviction * conviction_p, 2),
            bear=round(conviction * (1 - conviction_p), 2),
            neutral=round(neutral, 2),
            ci_low=round(lo, 4), ci_high=round(hi, 4),
            weaknesses=list(self.weaknesses) + [
                "Confidence interval widened to the floor required of a language-model "
                "score: fluent reasoning is not an observation count."],
        )


def insufficient(module: str, family: str, ticker: str, reason: str,
                 *, error: str = "") -> ModuleReport:
    """The honest abstention. Spec §3: report insufficient data rather than
    inventing a confidence interval."""
    return ModuleReport(
        module=module, family=family, ticker=ticker,
        bull=0.0, bear=0.0, neutral=100.0,
        ci_low=None, ci_high=None,
        thesis=f"Insufficient data for calibrated confidence: {reason}",
        evidence=[], weaknesses=["No calibrated output — this module abstained."],
        insufficient_data=True, reason=reason, error=error,
    )


def validate(report: ModuleReport) -> list[str]:
    """Contract violations, empty list when clean. Used by the test suite and
    by the registry in strict mode."""
    problems = []
    # Non-finite first: every check below is meaningless against a NaN, and a
    # NaN reaching the ensemble would poison the weighted average silently.
    for name, v in (("bull", report.bull), ("bear", report.bear),
                    ("neutral", report.neutral), ("ci_low", report.ci_low),
                    ("ci_high", report.ci_high)):
        if v is not None and not _finite(v):
            problems.append(f"{report.module}: {name} is not a finite number ({v})")
    if problems:
        return problems

    total = report.bull + report.bear + report.neutral
    if abs(total - 100.0) > 0.5:
        problems.append(f"{report.module}: scores sum to {total:.2f}, not 100")
    for name, v in (("bull", report.bull), ("bear", report.bear), ("neutral", report.neutral)):
        if v < -0.001 or v > 100.001:
            problems.append(f"{report.module}: {name} score {v} outside 0..100")
    if not report.insufficient_data:
        if report.ci_low is None or report.ci_high is None:
            problems.append(f"{report.module}: no confidence interval and did not abstain")
        elif report.ci_high < report.ci_low:
            problems.append(f"{report.module}: inverted confidence interval")
        if not report.evidence:
            problems.append(f"{report.module}: a scored view with no cited evidence")
        if not report.weaknesses:
            problems.append(f"{report.module}: no stated weaknesses")
        for e in report.evidence:
            if not e.get("source"):
                problems.append(f"{report.module}: evidence item without a source")
                break
    return problems
