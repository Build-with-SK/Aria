"""
src/training/evaluate.py
========================
ONE METRIC, SCORED OUT-OF-SAMPLE, AGAINST WHAT IS ALREADY RUNNING
(docs/ARIA_NEXT_SESSION.md step 6).

The question is never "is the new adapter any good". It is "is it better than
the one already serving" — a much harder bar, and the only one that decides
anything. `src/v5/weightfit.py` states the same rule for ensemble weights;
this is that rule applied to model weights.

The metric is the Brier score on P(the direction was up), which is what the
track record already reports. One metric everywhere means an improvement
cannot be shopped for by picking the framing that flatters the candidate —
that failure has happened three times in this project's own measurement code,
and each time the number that got quoted was the flattering one.

WHY A BOOTSTRAP AND NOT JUST "LOWER IS BETTER"
----------------------------------------------
On forty held-out rows, a Brier difference of 0.01 is noise. Promoting on
"the new number is smaller" would adopt a new adapter roughly half the time
even if the two models were identical. So the comparison is PAIRED — both
models score the same rows — and the improvement has to survive resampling
those rows before it counts. Two gates, both required:

  - the 95% bootstrap interval on the improvement excludes zero, and
  - the improvement is at least MIN_MATERIAL_IMPROVEMENT

The second exists because a real but microscopic improvement is not worth a
model swap, and because significance on its own is a threshold anyone can
reach by collecting more data.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

#: Below this the comparison cannot separate better from luckier.
MIN_EVAL_ROWS = 40

#: Brier improvement worth swapping a model for. 0.01 on a score that ranges
#: 0-1 and sits near 0.25 for a coin flip.
MIN_MATERIAL_IMPROVEMENT = 0.01

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260812      # fixed: a promotion decision must be re-runnable


@dataclass
class Score:
    n: int = 0
    brier: float | None = None
    accuracy: float | None = None
    abstained: int = 0
    unparseable: int = 0

    def to_dict(self) -> dict:
        return {"n": self.n, "brier": self.brier, "accuracy": self.accuracy,
                "abstained": self.abstained, "unparseable": self.unparseable}


@dataclass
class Comparison:
    candidate: Score = field(default_factory=Score)
    incumbent: Score = field(default_factory=Score)
    improvement: float | None = None        # incumbent brier - candidate brier
    ci_low: float | None = None
    ci_high: float | None = None
    n_paired: int = 0
    verdict: str = "insufficient"           # better | worse | indistinguishable | insufficient
    reasons: list[str] = field(default_factory=list)

    @property
    def candidate_wins(self) -> bool:
        return self.verdict == "better"

    def to_dict(self) -> dict:
        return {"candidate": self.candidate.to_dict(),
                "incumbent": self.incumbent.to_dict(),
                "improvement": self.improvement,
                "ci_low": self.ci_low, "ci_high": self.ci_high,
                "n_paired": self.n_paired, "verdict": self.verdict,
                "reasons": self.reasons}

    def describe(self) -> str:
        head = self.verdict.upper()
        if self.improvement is not None:
            head += (f" — Brier {self.incumbent.brier:.4f} → "
                     f"{self.candidate.brier:.4f} "
                     f"(improvement {self.improvement:+.4f}, 95% CI "
                     f"[{self.ci_low:+.4f}, {self.ci_high:+.4f}], "
                     f"n={self.n_paired})")
        return "\n".join([head, *(f"  - {r}" for r in self.reasons)])


# ── reading a probability out of a model's reply ─────────────────────────────

_UP = re.compile(r"\bUP\b", re.IGNORECASE)
_DOWN = re.compile(r"\bDOWN\b", re.IGNORECASE)
_ABSTAIN = re.compile(r"\bABSTAIN|NO[_ -]?TRADE|INSUFFICIENT\b", re.IGNORECASE)
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_PROB = re.compile(r"\b(?:p|probability|confidence)\D{0,12}?(0?\.\d+|1\.0+)\b",
                   re.IGNORECASE)


def parse_probability(text: str) -> float | None:
    """P(up) from a model's reply, or None if it did not give one.

    None means UNPARSEABLE OR ABSTAINED, and the caller must not fill it in
    with 0.5. A model that declines to answer is not a model that said
    "coin flip" — scoring it as one hands it the Brier score of a hedge it
    never made, which flatters exactly the model that has learned to say
    nothing.
    """
    if not text:
        return None
    if _ABSTAIN.search(text):
        return None
    up, down = bool(_UP.search(text)), bool(_DOWN.search(text))
    if up == down:                      # both or neither: no direction stated
        return None

    confidence = None
    m = _PROB.search(text)
    if m:
        confidence = float(m.group(1))
    else:
        m = _PCT.search(text)
        if m:
            value = float(m.group(1))
            if 0 <= value <= 100:
                confidence = value / 100.0
    if confidence is None:
        confidence = 0.75               # a direction with no number stated
    confidence = min(max(confidence, 0.5), 0.99)
    return confidence if up else 1.0 - confidence


def outcome_of(row: dict) -> int | None:
    """1 if the realised return was up, 0 if down, None if unresolved.

    An exactly-zero return is dropped rather than counted: it is neither, and
    assigning it to a side is a free half-point for whichever model guessed
    that side.
    """
    realised = (row.get("meta") or row).get("realised_return")
    if not isinstance(realised, (int, float)) or realised == 0:
        return None
    return 1 if realised > 0 else 0


# ── scoring ──────────────────────────────────────────────────────────────────

def score(probabilities: list[float | None], rows: list[dict]) -> Score:
    """Brier and accuracy over the rows where the model actually committed."""
    if len(probabilities) != len(rows):
        raise ValueError(f"{len(probabilities)} predictions for {len(rows)} rows")
    s = Score()
    losses, hits = [], 0
    for p, row in zip(probabilities, rows):
        truth = outcome_of(row)
        if truth is None:
            continue
        if p is None:
            s.abstained += 1
            continue
        losses.append((p - truth) ** 2)
        hits += int((p >= 0.5) == bool(truth))
    s.n = len(losses)
    if s.n:
        s.brier = round(sum(losses) / s.n, 6)
        s.accuracy = round(hits / s.n, 4)
    return s


def _paired_losses(cand: list[float | None], inc: list[float | None],
                   rows: list[dict]) -> tuple[list[float], list[float]]:
    """Squared errors on the rows BOTH models committed to.

    Paired on purpose. If the candidate abstains on the hard names and is
    scored on what is left, it wins by choosing its own exam.
    """
    a, b = [], []
    for pc, pi, row in zip(cand, inc, rows):
        truth = outcome_of(row)
        if truth is None or pc is None or pi is None:
            continue
        a.append((pc - truth) ** 2)
        b.append((pi - truth) ** 2)
    return a, b


def bootstrap_improvement(cand_losses: list[float], inc_losses: list[float], *,
                          resamples: int = BOOTSTRAP_RESAMPLES,
                          seed: int = BOOTSTRAP_SEED
                          ) -> tuple[float, float, float]:
    """(point estimate, 2.5th, 97.5th) of the paired Brier improvement."""
    n = len(cand_losses)
    point = (sum(inc_losses) - sum(cand_losses)) / n
    rng = random.Random(seed)
    diffs = [i - c for c, i in zip(cand_losses, inc_losses)]
    means = []
    for _ in range(resamples):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * (resamples - 1))]
    hi = means[int(0.975 * (resamples - 1))]
    return round(point, 6), round(lo, 6), round(hi, 6)


def compare(candidate_probs: list[float | None], incumbent_probs: list[float | None],
            rows: list[dict], *, min_rows: int = MIN_EVAL_ROWS,
            min_improvement: float = MIN_MATERIAL_IMPROVEMENT) -> Comparison:
    """Is the candidate better than what is already running, on the same rows?"""
    c = Comparison()
    c.candidate = score(candidate_probs, rows)
    c.incumbent = score(incumbent_probs, rows)

    cand_losses, inc_losses = _paired_losses(candidate_probs, incumbent_probs, rows)
    c.n_paired = len(cand_losses)

    if c.n_paired < min_rows:
        c.verdict = "insufficient"
        c.reasons.append(
            f"{c.n_paired} rows both models committed to, against a floor of "
            f"{min_rows}. Below that the comparison cannot tell a better "
            f"adapter from a luckier one.")
        return c

    point, lo, hi = bootstrap_improvement(cand_losses, inc_losses)
    c.improvement, c.ci_low, c.ci_high = point, lo, hi

    significant = lo > 0
    material = point >= min_improvement
    if significant and material:
        c.verdict = "better"
        c.reasons.append(
            f"Brier improved by {point:.4f} and the 95% interval "
            f"[{lo:+.4f}, {hi:+.4f}] excludes zero.")
    elif hi < 0:
        c.verdict = "worse"
        c.reasons.append(
            f"the candidate is WORSE by {-point:.4f}, and significantly so.")
    else:
        c.verdict = "indistinguishable"
        if not significant:
            c.reasons.append(
                f"the 95% interval [{lo:+.4f}, {hi:+.4f}] includes zero — this "
                f"difference is what {c.n_paired} rows of noise looks like.")
        if not material:
            c.reasons.append(
                f"improvement {point:.4f} is below the {min_improvement} worth "
                f"swapping a model for.")

    if c.candidate.abstained > c.incumbent.abstained:
        c.reasons.append(
            f"note: the candidate abstained on {c.candidate.abstained} rows "
            f"against the incumbent's {c.incumbent.abstained}. Scoring is "
            f"paired, so it gained nothing by it — but a model that answers "
            f"less is a different product, not just a better-scoring one.")
    return c
