"""
src/brain/cognitive/novelty.py
==============================
THE NOVELTY SENSE — noticing that something is not like what she usually sees,
before knowing why.

This is the sense the other organs cannot supply. `perception.py` reads what is
there. The 41 modules answer questions somebody thought to ask. Alerts fire on
a threshold somebody chose in advance — `|score| > 30` — which means they can
only catch the surprises that were anticipated well enough to be given a
number. A market does its most interesting work in the shapes nobody wrote a
rule for.

So this one asks a different question, and only one: **how often have I seen a
day like this?**

WHAT IT IS NOT
--------------
Not a forecast. Novelty is not direction and carries no opinion about what
happens next — a strange day can resolve into nothing at all. It exists to
make her *look*, and to say out loud that she is looking. Anything that turned
this score into a position would be inventing an edge nobody has measured.

HOW IT DECIDES
--------------
Each cycle reduces the snapshot to a handful of numbers — breadth, dispersion,
conviction, alert count, macro level, ML disagreement — and compares them
against her own recent history, not against a constant somebody typed.

Two readings, because they catch different things:

  per-feature   a robust z-score (median and MAD, not mean and standard
                deviation — one crash in the history would inflate an SD and
                blind the sense for months afterwards). This catches "breadth
                has never been this negative".

  joint         distance to her nearest historical days in that same robust
                space. This catches the day where every single number is
                individually unremarkable and the COMBINATION has never
                occurred: quiet vol with collapsing breadth and rising
                conviction. That is the one a threshold can never find, and
                the reason this module is not just another alert rule.

THE REFUSAL THAT MAKES IT HONEST
---------------------------------
With six days of history, everything is unprecedented. A novelty sense that
starts crying on day two teaches its owner to ignore it by day four, and then
it is worse than absent — it is a silenced alarm. So below MIN_HISTORY
observations it reports `not_enough_history` and says how many days it has
seen and how many it needs. It does not scale its confidence down and report
anyway.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent.parent
HISTORY_FILE = ROOT / "data" / "brain_memory" / "novelty_history.jsonl"

#: Below this she says she cannot tell. An observation is one distinct market
#: state, not one look — see `remember()`. With the desk refreshing roughly
#: daily that makes thirty observations about six weeks: enough for a median
#: and a MAD to mean something, nowhere near enough for a tail. Both facts are
#: reported rather than one of them being quietly assumed.
MIN_HISTORY = 30

#: Keep about a year of distinct states. Older than that and "usual" describes
#: a market that no longer exists.
MAX_HISTORY = 260

#: Robust z above which one feature is worth naming.
FEATURE_UNUSUAL = 3.0

#: Joint-distance percentile above which the COMBINATION is worth naming.
JOINT_UNUSUAL = 0.95

#: ...and how many times more isolated than a typical day it must also be.
#: The percentile on its own is scale-free: on a history whose days are all
#: nearly identical, a trivially small deviation still ranks above 95% of
#: them, and the sense would report a rounding difference as a strange day.
#: Both gates, for the same reason the adapter gate needs significance AND a
#: material margin — a rank without a magnitude is a threshold anyone clears.
JOINT_MIN_RATIO = 2.0

FAMILIAR = "familiar"
UNUSUAL = "unusual"
UNPRECEDENTED = "unprecedented"
UNKNOWN = "not_enough_history"

#: How many nearest neighbours the joint reading looks at. One neighbour is
#: noise; the whole history is an average and stops being local.
K_NEIGHBOURS = 5


@dataclass
class Driver:
    feature: str
    value: float
    median: float
    robust_z: float
    rarer_than: float = 0.0     # share of history less extreme than today

    def describe(self) -> str:
        direction = "high" if self.value >= self.median else "low"
        return (f"{self.feature} is unusually {direction} "
                f"({self.value:+.2f} against a usual {self.median:+.2f}, "
                f"{self.robust_z:.1f} robust deviations — more extreme than "
                f"{self.rarer_than:.0%} of the days I remember)")

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Novelty:
    state: str = UNKNOWN
    score: float = 0.0              # 0-1, the joint reading's percentile
    isolation_ratio: float = 0.0    # how many times more isolated than usual
    observations: int = 0
    drivers: list[Driver] = field(default_factory=list)
    combination_novel: bool = False
    at: str = ""
    features: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {"state": self.state, "score": round(self.score, 4),
                "isolation_ratio": self.isolation_ratio,
                "observations": self.observations,
                "combination_novel": self.combination_novel,
                "drivers": [d.to_dict() for d in self.drivers],
                "features": self.features, "at": self.at, "note": self.note,
                "speak": self.speak()}

    def speak(self) -> str:
        """One sentence she can volunteer, or nothing.

        Silent on a familiar day. A sense that comments every fifteen minutes
        is furniture, and the day it has something to say nobody reads it.
        """
        if self.state == UNKNOWN:
            return ""
        if self.state == FAMILIAR:
            return ""
        lead = ("I have not seen a day like this before"
                if self.state == UNPRECEDENTED else
                "Today does not look like what I usually see")
        parts = [d.describe() for d in self.drivers[:2]]
        if self.combination_novel and not parts:
            parts.append("no single number is strange, but I have no day in "
                         "memory where they occurred together")
        elif self.combination_novel:
            parts.append("and that combination is one I have no close match for")
        tail = "; ".join(parts)
        return (f"{lead}: {tail}. I do not know what it means — it is a reason "
                f"to look, not a signal.")


# ── the features ─────────────────────────────────────────────────────────────

def features_from(snapshot) -> dict:
    """Reduce a PerceptionSnapshot to the numbers novelty is measured on.

    Deliberately few and deliberately structural. Adding a feature per ticker
    would make every day unprecedented — in a space of 700 dimensions
    everything is far from everything else, and the sense would fire
    constantly while measuring nothing. These six describe the SHAPE of a day.
    """
    signals = list((getattr(snapshot, "signals", None) or {}).values())
    scores = [float(getattr(s, "composite_score", 0.0) or 0.0) for s in signals]
    n = len(scores)

    if n:
        mean = sum(scores) / n
        dispersion = math.sqrt(sum((s - mean) ** 2 for s in scores) / n)
        breadth = (sum(1 for s in scores if s > 0) - sum(1 for s in scores if s < 0)) / n
        conviction = sum(abs(s) for s in scores) / n
    else:
        mean = dispersion = breadth = conviction = 0.0

    ml = list((getattr(snapshot, "ml", None) or {}).values())
    bullish = [float(getattr(m, "overall_bullish", 0.5) or 0.5) for m in ml]
    # How split the ML models are. 0 = unanimous, 0.5 = maximally divided.
    ml_disagreement = (sum(abs(b - 0.5) for b in bullish) / len(bullish)
                       if bullish else 0.0)

    macro = getattr(snapshot, "macro", None)
    return {
        "breadth": round(breadth, 4),
        "dispersion": round(dispersion, 4),
        "conviction": round(conviction, 4),
        "mean_score": round(mean, 4),
        "alerts": float(len(getattr(snapshot, "alerts", None) or [])),
        "macro_score": round(float(getattr(macro, "macro_score", 0.0) or 0.0), 4),
        "ml_split": round(0.5 - ml_disagreement, 4),
    }


# ── robust statistics ────────────────────────────────────────────────────────

def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def mad(values: list[float], centre: float | None = None) -> float:
    """Median absolute deviation, scaled to be comparable to a standard
    deviation on normal data. Robust: one crash in the history cannot inflate
    it and blind the sense for months, which is exactly what an SD does."""
    if not values:
        return 0.0
    centre = median(values) if centre is None else centre
    return 1.4826 * median([abs(v - centre) for v in values])


def robust_z(value: float, history: list[float]) -> tuple[float, float, float]:
    """(z, median, share of history less extreme). Zero z when a feature has
    never moved — a constant is not evidence of anything."""
    if not history:
        return 0.0, 0.0, 0.0
    centre = median(history)
    spread = mad(history, centre)
    if spread <= 1e-9:
        # No variation in memory. Report novelty only if today actually
        # differs, and cap it — an infinite z from a degenerate history is a
        # measurement artefact, not a discovery.
        #
        # The tolerance is RELATIVE. An absolute 1e-9 meant that a feature
        # sitting at a constant 14.0 for sixty days and arriving at
        # 14.0000001 was reported as unprecedented: floating-point noise
        # dressed as an event, and precisely the kind of false alarm that
        # trains an owner to stop reading this sense.
        tolerance = max(1e-9, abs(centre) * 1e-6)
        z = 0.0 if abs(value - centre) <= tolerance else FEATURE_UNUSUAL
    else:
        z = (value - centre) / spread
    less_extreme = sum(1 for h in history if abs(h - centre) < abs(value - centre))
    return z, centre, less_extreme / len(history)


def numeric_features(vector: dict) -> list[str]:
    """The measurable keys, in a stable order.

    Remembered rows carry an `at` timestamp beside the numbers, and booleans
    can arrive from a future feature. Both would end up in the distance
    arithmetic if the keys were simply iterated — a timestamp string raised
    here the first time this ran.
    """
    return sorted(k for k, v in vector.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool))


def _stats(history: list[dict], names: list[str]) -> tuple[dict, dict]:
    """Per-feature median and MAD, computed once for a whole assessment."""
    centres, spreads = {}, {}
    for name in names:
        column = [h[name] for h in history
                  if isinstance(h.get(name), (int, float))
                  and not isinstance(h.get(name), bool)]
        centre = median(column)
        centres[name] = centre
        spreads[name] = mad(column, centre)
    return centres, spreads


def _scaled(vector: dict, names: list[str], centres: dict, spreads: dict
            ) -> list[float]:
    """One point in robust-z space, so features with different units
    contribute comparably to the distance. Capped, so a single wild feature
    cannot dominate the shape of a day."""
    out = []
    for name in names:
        value = float(vector.get(name, 0.0) or 0.0)
        spread = spreads.get(name, 0.0)
        z = 0.0 if spread <= 1e-9 else (value - centres.get(name, 0.0)) / spread
        out.append(max(-10.0, min(10.0, z)))
    return out


def _knn_distance(point: list[float], cloud: list[list[float]], k: int,
                  skip: int = -1) -> float:
    """Mean distance from `point` to its k nearest neighbours in `cloud`.

    `skip` excludes one index — used when the point IS one of the cloud, so a
    day is never counted as its own nearest neighbour at distance zero.
    """
    distances = []
    for i, other in enumerate(cloud):
        if i == skip:
            continue
        distances.append(math.sqrt(sum((a - b) ** 2
                                       for a, b in zip(point, other))))
    if not distances:
        return 0.0
    distances.sort()
    take = min(k, len(distances))
    return sum(distances[:take]) / take


def joint_novelty(vector: dict, history: list[dict], k: int = K_NEIGHBOURS
                  ) -> tuple[float, float, float]:
    """(today's k-NN distance, its percentile among normal days).

    THE PERCENTILE IS THE POINT. A raw distance is uncalibrated — nobody can
    say whether 1.8 is far. So today's isolation is ranked against how
    isolated each REMEMBERED day is from its own neighbours. "More isolated
    than 97% of the days I remember" is a statement with a meaning.

    The first version of this ranked today's distance against its own distance
    distribution, which returns approximately k/n no matter what the day looks
    like — a score that could never fire. Its own test caught it.
    """
    if len(history) < k + 2:
        return 0.0, 0.0, 0.0
    names = numeric_features(vector)
    if not names:
        return 0.0, 0.0, 0.0
    centres, spreads = _stats(history, names)

    cloud = [_scaled(h, names, centres, spreads) for h in history]
    today = _knn_distance(_scaled(vector, names, centres, spreads), cloud, k)
    reference = [_knn_distance(point, cloud, k, skip=i)
                 for i, point in enumerate(cloud)]
    if not reference:
        return today, 0.0, 0.0
    rank = sum(1 for r in reference if r < today) / len(reference)
    typical = median(reference)
    ratio = today / typical if typical > 1e-9 else (0.0 if today <= 1e-9
                                                    else JOINT_MIN_RATIO)
    return today, rank, ratio


# ── history ──────────────────────────────────────────────────────────────────

def load_history(path: Path = HISTORY_FILE, limit: int = MAX_HISTORY) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue        # one bad line must not blind the sense
    return rows[-limit:]


def remember(features: dict, path: Path = HISTORY_FILE,
             at: str = "") -> bool:
    """Append this market state to what she considers usual. True if it was
    new.

    ONE OBSERVATION MEANS ONE MARKET STATE, NOT ONE LOOK.
    The brain cycle runs every fifteen minutes; `signals.json` changes when the
    desk refreshes it, which is far less often — and was 49 hours stale the day
    this was written. Remembering every look would fill her memory with the
    same day repeated forty times: the spread would collapse toward zero, the
    next genuine change would read as unprecedented, and "usual" would mean
    "the last two days". So an unchanged state is not a new observation.

    Every state she does see is remembered, including the strange ones. A
    sense that only kept the ordinary days would find each repetition of a new
    regime just as shocking as the first, and would never learn that the world
    had changed.
    """
    path = Path(path)
    history = load_history(path, limit=1)
    if history:
        last = history[-1]
        if all(abs(float(last.get(k, 0.0) or 0.0) - float(v or 0.0)) <= 1e-9
               for k, v in features.items()):
            return False

    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": at or datetime.now().isoformat(timespec="seconds"), **features}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return True


# ── the sense ────────────────────────────────────────────────────────────────

def assess(features: dict, history: list[dict] | None = None,
           *, path: Path = HISTORY_FILE) -> Novelty:
    """How unusual is this, given everything she remembers?"""
    history = load_history(path) if history is None else history
    n = Novelty(at=datetime.now().isoformat(timespec="seconds"),
                features=features, observations=len(history))

    if len(history) < MIN_HISTORY:
        n.state = UNKNOWN
        n.note = (f"I have {len(history)} days in memory and need {MIN_HISTORY} "
                  f"before I can tell you what unusual looks like. Until then "
                  f"everything would look unprecedented, which is the same as "
                  f"saying nothing.")
        return n

    for name, value in features.items():
        column = [h[name] for h in history if name in h]
        if len(column) < MIN_HISTORY:
            continue
        z, centre, rarer = robust_z(float(value), column)
        if abs(z) >= FEATURE_UNUSUAL:
            n.drivers.append(Driver(feature=name, value=float(value),
                                    median=centre, robust_z=round(z, 2),
                                    rarer_than=round(rarer, 4)))
    n.drivers.sort(key=lambda d: abs(d.robust_z), reverse=True)

    _, rank, ratio = joint_novelty(features, history)
    n.score = rank
    n.isolation_ratio = round(ratio, 2)
    n.combination_novel = rank >= JOINT_UNUSUAL and ratio >= JOINT_MIN_RATIO

    if n.combination_novel and not n.drivers:
        n.state = UNUSUAL
        n.note = ("every number is ordinary on its own; the combination is "
                  "one I have no close match for")
    elif n.drivers and (n.combination_novel or abs(n.drivers[0].robust_z) >= 2 * FEATURE_UNUSUAL):
        n.state = UNPRECEDENTED
    elif n.drivers:
        n.state = UNUSUAL
    else:
        n.state = FAMILIAR
        n.note = (f"within the range of the {len(history)} days I remember")
    return n


def observe(snapshot=None, *, path: Path = HISTORY_FILE,
            record: bool = True) -> Novelty:
    """Assess this moment, then remember it. The whole sense, one call.

    Assessment happens BEFORE the day is remembered — otherwise every day is
    compared against a history that already contains it, and the strangest day
    on record quietly makes itself look one observation less strange.
    """
    if snapshot is None:
        from src.brain.cognitive.perception import MarketPerception
        snapshot = MarketPerception().perceive()

    features = features_from(snapshot)
    result = assess(features, path=path)
    if record:
        remember(features, path=path,
                 at=getattr(snapshot, "timestamp", datetime.now()).isoformat(
                     timespec="seconds"))
    return result


def status(path: Path = HISTORY_FILE) -> dict:
    history = load_history(path)
    return {
        "observations": len(history),
        "needs": MIN_HISTORY,
        "ready": len(history) >= MIN_HISTORY,
        "remembers_from": history[0].get("at") if history else None,
        "note": ("She can tell an unusual day from an ordinary one."
                 if len(history) >= MIN_HISTORY else
                 f"{MIN_HISTORY - len(history)} more observations before this "
                 f"sense says anything. It reports nothing rather than "
                 f"reporting everything as unprecedented."),
    }
