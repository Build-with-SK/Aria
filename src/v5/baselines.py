"""
src/v5/baselines.py
===================
The question the track record could not answer.

`src/v5/track_record.py` reports whether ARIA's stated confidence is calibrated
— when it says 62%, does it happen 62% of the time. That is a real measurement
and most systems never make it. But it is not the question a sceptical reader
actually asks, which is:

    Would a rule I could write on a napkin have done the same thing?

Calibration is a property of the *confidence numbers*. Skill is a property of
the *calls*. A system can be beautifully calibrated and still carry no edge:
predict "bull" on every US equity at 55% confidence and, in a market that rises
55% of the time over 21 days, you will be perfectly calibrated and perfectly
worthless. Brier score against a coin flip does not catch this, because the coin
flip is not the competitor. Buy-and-hold is the competitor.

So this module re-runs the naive strategies over EXACTLY the calls ARIA made —
same tickers, same dates, same horizons, graded against the same realised
returns — and compares them on the paired sample. The pairing matters: if ARIA
and the SMA rule are both measured on 40 calls but not the *same* 40, the
difference between them is mostly a difference in which stocks got picked. Only
the paired comparison isolates the decision.

The test is McNemar's, which is the correct one for two classifiers on a shared
sample: it looks only at the calls where the two DISAGREED, because the ones
they both got right (or both got wrong) carry no information about which is
better. Ten agreements and one disagreement is not evidence of anything, and
this module says so rather than reporting a percentage-point gap.

Every statistic here refuses to report below its minimum sample, on the same
principle as the rest of the track record: an unmeasurable quantity is reported
as unmeasurable.

Read the output the pessimistic way. `beats_baseline: false` is the expected
result for a young system, and "no measurable edge over buy-and-hold yet" is an
honest, useful thing for the deck to say. It is what this file exists to be able
to say.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MIN_RESOLVED = 20       # paired calls before any comparison is reported
MIN_DISCORDANT = 10     # disagreements before a McNemar p-value is reported
SIGNIFICANCE = 0.05


# ── the naive competitors ────────────────────────────────────────────────────
#
# Each takes (ticker, as_of) and returns "bull", "bear", or None to abstain —
# the same three answers a research module may give. They are deliberately
# stupid. A baseline that needed tuning would not be a baseline, it would be a
# forty-second strategy with its own overfitting risk, and beating it would
# prove nothing.
#
# They see only closes strictly BEFORE `as_of`. That is not a formality: every
# one of these rules becomes an oracle if it is allowed to see the bar it is
# predicting, and an oracle baseline would make ARIA look bad for the wrong
# reason. `_closes_before` is the only door to price data in this file.


def _closes_before(ticker: str, as_of: datetime, lookback: str = "1y"):
    """Closes strictly before `as_of`. None when there is no usable history."""
    import pandas as pd

    from src.v5 import marketdata as md

    c = md.closes(ticker, period=lookback)
    if c is None or c.empty:
        return None
    try:
        cutoff = pd.Timestamp(as_of)
        idx = c.index
        if getattr(idx, "tz", None) is not None:
            cutoff = cutoff.tz_localize(idx.tz) if cutoff.tz is None else cutoff.tz_convert(idx.tz)
        before = c[idx < cutoff]
    except Exception:
        return None
    return before if len(before) else None


def _always_bull(ticker: str, as_of: datetime) -> Optional[str]:
    """Buy and hold. The baseline that matters most for equities, because it is
    what the reader's money would have been doing anyway."""
    return "bull"


def _sma_cross(ticker: str, as_of: datetime) -> Optional[str]:
    """The oldest trend rule there is: 20-day above 50-day is bull."""
    c = _closes_before(ticker, as_of)
    if c is None or len(c) < 50:
        return None
    fast = float(c.iloc[-20:].mean())
    slow = float(c.iloc[-50:].mean())
    if not (math.isfinite(fast) and math.isfinite(slow)) or fast == slow:
        return None
    return "bull" if fast > slow else "bear"


def _momentum_60d(ticker: str, as_of: datetime) -> Optional[str]:
    """Sixty-day price momentum, sign only."""
    c = _closes_before(ticker, as_of)
    if c is None or len(c) < 61:
        return None
    then, now = float(c.iloc[-61]), float(c.iloc[-1])
    if not (math.isfinite(then) and math.isfinite(now)) or then <= 0:
        return None
    return "bull" if now > then else "bear"


BASELINES: dict[str, tuple[Callable[[str, datetime], Optional[str]], str]] = {
    "buy_and_hold": (_always_bull, "Always bullish — what the money was doing anyway."),
    "sma_20_50": (_sma_cross, "20-day SMA above 50-day SMA, measured before the call."),
    "momentum_60d": (_momentum_60d, "Sign of the trailing 60-day return, measured before the call."),
}


# ── statistics ───────────────────────────────────────────────────────────────

def _binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial p against a fair-coin null. Used for McNemar on
    the discordant pairs, where the null is 'each system wins half the
    disagreements'."""
    if n <= 0:
        return 1.0
    try:
        from scipy import stats
        return float(stats.binomtest(k, n, 0.5).pvalue)
    except Exception:
        # Normal approximation with a continuity correction. Only reached when
        # scipy is absent; MIN_DISCORDANT keeps it in the range where it holds.
        z = (abs(k - n / 2) - 0.5) / math.sqrt(n * 0.25)
        return float(math.erfc(max(0.0, z) / math.sqrt(2)))


def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    from src.v5.contract import wilson_interval
    return wilson_interval(successes, n, z)


def _correct(direction: str, ret: float) -> Optional[bool]:
    """Was this directional call right? None for calls that took no side —
    a neutral read is not a prediction and must not be graded as one."""
    if direction == "bull":
        return ret > 0
    if direction == "bear":
        return ret < 0
    return None


# ── the comparison ───────────────────────────────────────────────────────────

def compare(*, min_resolved: int = MIN_RESOLVED) -> dict:
    """ARIA against each naive baseline, on the paired sample of resolved calls.

    Returns a dict that is safe to render directly. When the sample is too
    small the payload carries `measurable: false` and a note explaining what is
    missing, exactly as `track_record.calibration()` does — the deck must never
    have to guess whether a number is real.
    """
    from src.v5.learning import load_predictions

    resolved = [
        e for e in load_predictions()
        if e.get("resolved")
        and e.get("realised_return") is not None
        and e.get("direction") in ("bull", "bear")
    ]

    if len(resolved) < min_resolved:
        return {
            "measurable": False,
            "n_resolved": len(resolved),
            "n_required": min_resolved,
            "note": (f"{len(resolved)} directional calls have resolved. Baseline comparison "
                     f"is not reported below {min_resolved}: on a handful of calls the gap "
                     f"between ARIA and a coin is mostly which stocks happened to come up."),
            "baselines": [],
        }

    aria_right = sum(1 for e in resolved if _correct(e["direction"], e["realised_return"]))
    n = len(resolved)
    lo, hi = _wilson(aria_right, n)

    out = []
    for name, (fn, description) in BASELINES.items():
        out.append(_one_baseline(name, fn, description, resolved))

    # The headline is deliberately the WEAKEST result, not the best one. Picking
    # the baseline ARIA happens to beat is the same error as picking the
    # backtest window that happens to work.
    tested = [b for b in out if b["measurable"]]
    beaten = [b for b in tested if b["verdict"] == "ahead"]
    if not tested:
        headline = ("No baseline has enough overlapping calls to compare against yet. "
                    "Edge over a naive rule is unmeasured.")
        beats_all = None
    elif len(beaten) == len(tested):
        headline = (f"ARIA is ahead of all {len(tested)} naive baselines at p<{SIGNIFICANCE}. "
                    f"This is evidence of edge on this sample, not proof of edge in general.")
        beats_all = True
    else:
        names = ", ".join(b["baseline"] for b in tested if b["verdict"] != "ahead")
        headline = (f"No measurable edge over: {names}. On the calls where they disagreed, "
                    f"ARIA did not win significantly more often than the naive rule.")
        beats_all = False

    return {
        "measurable": True,
        "n_resolved": n,
        "aria": {
            "n": n,
            "right": aria_right,
            "hit_rate": round(aria_right / n, 4),
            "hit_rate_ci": [lo, hi],
        },
        "baselines": out,
        "beats_all_baselines": beats_all,
        "headline": headline,
        "method": ("Each baseline is re-run on the same tickers and dates ARIA called, using "
                   "only price history from before each call, and graded against the same "
                   "realised returns. Compared with McNemar's test on the discordant pairs."),
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def _one_baseline(name: str, fn: Callable, description: str, resolved: list[dict]) -> dict:
    """Score one baseline over the resolved calls and pair it against ARIA."""
    paired = []       # (aria_correct, baseline_correct)
    n_abstained = 0

    for e in resolved:
        try:
            as_of = datetime.fromisoformat(str(e["at"]))
        except Exception:
            continue
        ret = float(e["realised_return"])

        a = _correct(e["direction"], ret)
        if a is None:
            continue

        try:
            view = fn(e["ticker"], as_of)
        except Exception as ex:            # a baseline must never break the page
            logger.debug(f"baseline {name} failed on {e.get('ticker')}: {ex}")
            view = None

        if view is None:
            n_abstained += 1
            continue
        b = _correct(view, ret)
        if b is None:
            n_abstained += 1
            continue
        paired.append((a, b))

    n = len(paired)
    if n < MIN_RESOLVED:
        return {
            "baseline": name,
            "description": description,
            "measurable": False,
            "n_paired": n,
            "n_abstained": n_abstained,
            "note": (f"only {n} calls overlap with this baseline "
                     f"({MIN_RESOLVED} needed) — it had no usable history for the rest"),
        }

    aria_right = sum(1 for a, _ in paired if a)
    base_right = sum(1 for _, b in paired if b)

    # McNemar: only the disagreements carry information.
    aria_only = sum(1 for a, b in paired if a and not b)
    base_only = sum(1 for a, b in paired if b and not a)
    discordant = aria_only + base_only

    if discordant < MIN_DISCORDANT:
        verdict, p = "undetermined", None
        note = (f"ARIA and this baseline disagreed on only {discordant} of {n} calls "
                f"({MIN_DISCORDANT} needed). They are making nearly the same decisions, "
                f"so which one is better cannot be told apart yet.")
    else:
        p = _binom_two_sided(min(aria_only, base_only), discordant)
        # Rounding is presentational only; the comparison uses the exact p.
        if p >= SIGNIFICANCE:
            verdict = "tied"
            note = (f"On the {discordant} calls where they disagreed ARIA won {aria_only}. "
                    f"That is not distinguishable from chance (p={p:.3f}).")
        elif aria_only > base_only:
            verdict = "ahead"
            note = (f"ARIA won {aria_only} of the {discordant} disagreements (p={p:.3f}). "
                    f"Measured edge over this baseline on this sample.")
        else:
            verdict = "behind"
            note = (f"The baseline won {base_only} of the {discordant} disagreements "
                    f"(p={p:.3f}). ARIA is significantly WORSE than this rule here.")

    return {
        "baseline": name,
        "description": description,
        "measurable": True,
        "n_paired": n,
        "n_abstained": n_abstained,
        "aria_hit_rate": round(aria_right / n, 4),
        "baseline_hit_rate": round(base_right / n, 4),
        "edge_pp": round(100.0 * (aria_right - base_right) / n, 2),
        "aria_only_right": aria_only,
        "baseline_only_right": base_only,
        "n_discordant": discordant,
        "p_value": round(p, 4) if p is not None else None,
        "verdict": verdict,
        "note": note,
    }


# ── confidence-weighted comparison ───────────────────────────────────────────

def brier_comparison() -> dict:
    """ARIA's Brier score against the two constant-probability strategies.

    `track_record.calibration()` already reports Brier against always-50%. The
    addition here is always-BASE-RATE: a forecaster that ignores every input and
    states the historical frequency of a correct call. Beating 50% is easy in a
    market with drift. Beating the base rate means the inputs did something.
    """
    from src.v5.learning import load_predictions

    rows = []
    for e in load_predictions():
        if not e.get("resolved") or e.get("correct") is None:
            continue
        if e.get("direction") not in ("bull", "bear"):
            continue
        p = float(e.get("confidence") or 0.5)
        rows.append((max(0.5, min(1.0, p)), 1.0 if e["correct"] else 0.0))

    if len(rows) < MIN_RESOLVED:
        return {"measurable": False, "n_resolved": len(rows), "n_required": MIN_RESOLVED,
                "note": "not enough resolved calls to score the probabilities"}

    n = len(rows)
    base_rate = sum(o for _, o in rows) / n
    aria = sum((p - o) ** 2 for p, o in rows) / n
    coin = sum((0.5 - o) ** 2 for _, o in rows) / n
    constant = sum((base_rate - o) ** 2 for _, o in rows) / n

    # Skill against the base rate is the honest one. It can be negative, and a
    # negative value means the confidence numbers are actively misleading —
    # worse than stating the same number every time.
    skill_vs_coin = 1 - aria / coin if coin > 0 else None
    skill_vs_base = 1 - aria / constant if constant > 0 else None

    if skill_vs_base is None:
        verdict = "The base rate is degenerate on this sample; skill is undefined."
    elif skill_vs_base > 0:
        verdict = ("The confidence numbers carry information beyond the base rate — "
                   "ARIA knows which calls are the good ones, not just how often it is right.")
    else:
        verdict = ("The confidence numbers carry no information beyond the base rate. "
                   "Stating the same number on every call would score as well or better.")

    return {
        "measurable": True,
        "n_resolved": n,
        "base_rate": round(base_rate, 4),
        "brier_aria": round(aria, 4),
        "brier_coin_flip": round(coin, 4),
        "brier_constant_base_rate": round(constant, 4),
        "skill_vs_coin_flip": round(skill_vs_coin, 4) if skill_vs_coin is not None else None,
        "skill_vs_base_rate": round(skill_vs_base, 4) if skill_vs_base is not None else None,
        "verdict": verdict,
    }


def report() -> dict:
    """Everything this module can say, in one payload for the Track Record page."""
    return {"directional": compare(), "probabilistic": brier_comparison()}
