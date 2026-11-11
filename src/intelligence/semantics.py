"""
src/intelligence/semantics.py
=============================
What a number attached to a prediction actually is.

Five different things have been stored in this system under names like
"probability" and "confidence", and they are not interchangeable:

    STATED_PROBABILITY      the source asserted P(its direction is right).
                            V5's `confidence` is this on a bull or bear call.
    CALIBRATED_PROBABILITY  a stated probability passed through a calibration
                            map that was fitted on EARLIER calls and validated
                            on later ones, with the map's version recorded.
                            None exist today.
    SCORE                   a model output that orders outcomes but has no
                            demonstrated probability meaning: an uncalibrated
                            classifier's predict_proba, V5's pre-review P(bull),
                            a composite signal score.
    CONFIDENCE              a strength or conviction: the desk's 0-100
                            conviction (stored squashed onto 0.5-0.95), a
                            "High / Medium / Low" label.
    NONE                    the source states no number at all (the technical
                            composite is a vote count).
    UNKNOWN                 a number may exist but what it means was never
                            recorded. Unknown stays unknown.

Only the first two may enter a Brier score, a log loss or a calibration curve,
and `brier_score` / `log_loss` below REFUSE anything else rather than score it.
That refusal is the point: in 2026-10 the ledger's `probability` column held
V5's P(bull), the desk's mapped conviction and nothing, all scored as
P(correct), and produced a Brier of 0.288 that measured nothing.

A ledger row carries `probability_kind` (stated | mapped | none, plus
calibrated | score from this revision) and, for new rows, `probability_type`.
`type_of(row)` reads them; it never infers a probability from a bare number.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Iterable, Optional


class ProbabilityType(str, Enum):
    STATED_PROBABILITY = "STATED_PROBABILITY"
    CALIBRATED_PROBABILITY = "CALIBRATED_PROBABILITY"
    SCORE = "SCORE"
    CONFIDENCE = "CONFIDENCE"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


#: The only types a proper scoring rule may read.
SCORABLE = frozenset({ProbabilityType.STATED_PROBABILITY,
                      ProbabilityType.CALIBRATED_PROBABILITY})

#: ledger.probability_kind -> type. "mapped" is the desk's conviction squashed
#: onto 0.5-0.95: it keeps ORDER, it is not a probability.
KIND_TO_TYPE = {
    "stated": ProbabilityType.STATED_PROBABILITY,
    "calibrated": ProbabilityType.CALIBRATED_PROBABILITY,
    "mapped": ProbabilityType.CONFIDENCE,
    "score": ProbabilityType.SCORE,
    "none": ProbabilityType.NONE,
}
TYPE_TO_KIND = {v: k for k, v in KIND_TO_TYPE.items()}

#: Why a row's number did not enter a probability metric. Kept as codes so the
#: exclusions are countable and auditable.
NOT_DIRECTIONAL = "NEUTRAL_CALL_STATES_NO_PROBABILITY"
NOT_A_PROBABILITY = "NUMBER_IS_NOT_A_PROBABILITY"
NO_NUMBER = "NO_PROBABILITY_STATED"
KIND_UNRECORDED = "PROBABILITY_KIND_UNRECORDED"
OUT_OF_RANGE = "PROBABILITY_OUT_OF_RANGE"
NO_CALIBRATION_VERSION = "CALIBRATED_WITHOUT_CALIBRATION_VERSION"


class NotAProbability(ValueError):
    """A score, a confidence, nothing, or an unknown was offered to a metric
    that is only defined for probabilities."""


def parse(value) -> ProbabilityType:
    """A ProbabilityType from its name or an enum; UNKNOWN for anything else."""
    if isinstance(value, ProbabilityType):
        return value
    try:
        return ProbabilityType(str(value).strip().upper())
    except (ValueError, AttributeError):
        return ProbabilityType.UNKNOWN


def type_of(row: dict) -> ProbabilityType:
    """The probability type a ledger row carries — from what was RECORDED.

    An explicit `probability_type` wins; otherwise `probability_kind` maps
    one-to-one. A row with neither is UNKNOWN, even when it holds a number:
    guessing what an unlabelled number meant is how the 0.288 Brier happened.
    """
    explicit = row.get("probability_type")
    if explicit:
        t = parse(explicit)
        if t is not ProbabilityType.UNKNOWN:
            return t
    kind = row.get("probability_kind")
    if kind in KIND_TO_TYPE:
        return KIND_TO_TYPE[kind]
    return ProbabilityType.UNKNOWN


def scorable_probability(row: dict) -> tuple[Optional[float], Optional[str]]:
    """(P(the stated direction is right), None) or (None, why it is not one).

    Directional calls only: a neutral call states no probability of staying
    flat. A calibrated probability must name the calibration that produced it.
    """
    t = type_of(row)
    if t is ProbabilityType.UNKNOWN:
        return None, KIND_UNRECORDED
    if t is ProbabilityType.NONE:
        return None, NO_NUMBER
    if t not in SCORABLE:
        return None, NOT_A_PROBABILITY
    if (row.get("direction") or "") not in ("bull", "bear"):
        return None, NOT_DIRECTIONAL
    if t is ProbabilityType.CALIBRATED_PROBABILITY and not row.get("calibration_version"):
        return None, NO_CALIBRATION_VERSION
    p = row.get("p_correct")
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None, NO_NUMBER
    if not (math.isfinite(p) and 0.0 <= p <= 1.0):
        return None, OUT_OF_RANGE
    return p, None


def require_probability(value, ptype) -> float:
    """`value` as a probability, or NotAProbability. The guard every proper
    scoring rule in this package calls first."""
    t = parse(ptype)
    if t not in SCORABLE:
        raise NotAProbability(
            f"a {t.value} is not a probability and may not be scored as one")
    try:
        p = float(value)
    except (TypeError, ValueError):
        raise NotAProbability(f"{value!r} is not a number") from None
    if not (math.isfinite(p) and 0.0 <= p <= 1.0):
        raise NotAProbability(f"{p} is outside [0, 1]")
    return p


def brier_score(points: Iterable[tuple]) -> Optional[float]:
    """Mean squared error of (p, outcome, probability_type) triples.

    Refuses — raises NotAProbability — on any point whose type is not a
    probability. A confidence value can never silently enter a Brier score.
    """
    pts = [(require_probability(p, t), 1.0 if o else 0.0) for p, o, t in points]
    if not pts:
        return None
    return sum((p - o) ** 2 for p, o in pts) / len(pts)


def log_loss(points: Iterable[tuple], eps: float = 1e-6) -> Optional[float]:
    """Mean negative log likelihood; the same refusal as brier_score.

    `eps` clips 0 and 1 so a single confident miss is a large finite penalty
    rather than infinity; it is reported, not hidden, by the caller.
    """
    pts = [(require_probability(p, t), 1.0 if o else 0.0) for p, o, t in points]
    if not pts:
        return None
    total = 0.0
    for p, o in pts:
        q = min(1 - eps, max(eps, p))
        total -= o * math.log(q) + (1 - o) * math.log(1 - q)
    return total / len(pts)


def describe() -> dict:
    """The vocabulary, for the API and the documentation."""
    return {
        "types": {
            "STATED_PROBABILITY": "the source asserted P(its direction is right)",
            "CALIBRATED_PROBABILITY": ("a stated probability mapped by a calibration "
                                       "fitted on earlier calls and validated on later "
                                       "ones; carries calibration_version"),
            "SCORE": "orders outcomes; no demonstrated probability meaning",
            "CONFIDENCE": "a strength or conviction; keeps order, is not a probability",
            "NONE": "the source states no number",
            "UNKNOWN": "a number may exist but its meaning was not recorded",
        },
        "scorable": sorted(t.value for t in SCORABLE),
        "kind_to_type": {k: v.value for k, v in KIND_TO_TYPE.items()},
    }
