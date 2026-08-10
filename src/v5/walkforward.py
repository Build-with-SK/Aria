"""
src/v5/walkforward.py
=====================
Historical validation for all 41 research modules — and an honest record of
which ones have never been through it.

WHAT THIS MEASURES, AND WHAT IT DOES NOT
----------------------------------------
Each module is a function from "everything known on date T" to a bull/bear
score. This harness replays history: it stands on date T, asks the module what
it thinks, and then looks at what the instrument actually did over the module's
own horizon. Repeat across dates and tickers; count how often the direction was
right, and how well the score's magnitude ranked the outcomes.

Leakage control is the whole game, and it comes from two places:

  * `marketdata.as_of` truncates every series to date T, so a module physically
    cannot read a bar from the future. This is enforced in the data layer, not
    trusted to 41 separate implementations.
  * Folds come from `PurgedWalkForwardCV` (Lopez de Prado) with the module's
    own horizon as the purge parameter, so an evaluation window never overlaps
    the forward window of the one before it.

An honest caveat, stated because the alternative is implying more rigour than
exists: most of these modules are rule-based, not fitted. For those, there is
no training set to leak from, and "walk-forward" means "evaluated out-of-sample
across time" rather than "trained here, tested there". That is still the number
that matters — it is measured on data the rule never saw when it was written —
but it is a weaker claim than a fitted model surviving the same procedure, and
the two should not be quoted as if they were the same thing.

WHY THE UNVALIDATED FLAG EXISTS
-------------------------------
Before this, some modules had been checked and most had not, and the API output
looked identical either way. A module's report now carries
`walk_forward_validated`, so an unvalidated engine's opinion is visibly an
unvalidated engine's opinion at the point where somebody reads it.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
RESULTS_FILE = ROOT / "data" / "v5" / "walk_forward.json"

# The evaluation universe. Deliberately small, liquid and diverse: this runs 41
# modules at every evaluation date, so breadth costs hours. Names are spread
# across sectors so a single sector's decade does not become "the result".
DEFAULT_UNIVERSE = ["AAPL", "MSFT", "JPM", "XOM", "JNJ", "WMT", "CAT", "NVDA"]

# A directional call needs a score meaningfully off neutral to count. Grading
# every near-zero score as a "call" measures noise and reports it as skill.
CALL_THRESHOLD = 10.0        # |net| on the -100..+100 scale

MIN_CALLS_FOR_VERDICT = 30   # below this, a hit rate is an anecdote


# ── scoring one module ───────────────────────────────────────────────────────

def _forward_return(closes: pd.Series, at: pd.Timestamp, horizon_days: int) -> Optional[float]:
    """Realised return from the close on/just before `at`, `horizon_days` on.

    Trading days, not calendar days — a 21-day horizon is a month of trading,
    and mixing the two silently shortens every horizon by a third.
    """
    idx = closes.index.searchsorted(at, side="right") - 1
    if idx < 0 or idx + horizon_days >= len(closes):
        return None
    start = float(closes.iloc[idx])
    end = float(closes.iloc[idx + horizon_days])
    if start <= 0 or not math.isfinite(start) or not math.isfinite(end):
        return None
    return (end - start) / start


def uses_wall_clock(name: str) -> bool:
    """True if the module reads today's date rather than the data's date.

    Found by this harness rather than reasoned about: with the data layer
    pinned to February 2024, one module still reported on "calendar month 8".
    It was asking `datetime.now()`. Under normal use that is harmless — today
    IS the as-of date — but it means the module's historical replay is not
    strictly point-in-time, and a validation result is worth exactly as much as
    the honesty of its caveats. Detected, recorded, and reported next to the
    number rather than quietly folded into it.
    """
    from src.v5 import registry
    import inspect
    spec = registry.REGISTRY.get(name)
    if spec is None:
        return False
    try:
        src = inspect.getsource(spec.fn)
    except (OSError, TypeError):
        return False
    return any(token in src for token in
               ("datetime.now(", "date.today(", "datetime.today(", "time.time("))


def effective_sample_size(calls: list[dict]) -> tuple[int, float]:
    """(n_effective, average cross-sectional correlation) for a set of calls.

    THE REASON THIS EXISTS. The raw count is not the sample size. Forty tickers
    evaluated on the same date are forty observations of one market day: in
    2020-03 every one of them fell together, and counting that as forty
    independent pieces of evidence is how a backtest manufactures significance
    it has not earned. `_util.conditional_hit_rate` already refuses to do this
    with overlapping windows; the walk-forward harness was doing it with the
    cross-section.

    The standard discount applies. For N correlated observations with average
    pairwise correlation rho:

        n_eff = N / (1 + (N - 1) * rho)

    With forty names at rho ~ 0.5, 240 calls are worth about 5 independent
    ones — which is the honest reason a 27% "edge" in this file's first run
    should never have been read as a finding.

    Overlap within a ticker is handled by construction: evaluation dates inside
    a fold are spaced far wider than the horizon.
    """
    if len(calls) < 3:
        return max(0, len(calls)), 0.0

    by_date: dict[str, list[float]] = {}
    for c in calls:
        by_date.setdefault(c["at"], []).append(c["fwd_return"])

    # How alike are names measured on the SAME date? Decompose the outcome
    # variance into the part that survives within a date and the total:
    #
    #     rho = 1 - (mean within-date variance / total variance)
    #
    # Names that move together leave almost no within-date variance, so rho
    # approaches 1. Names whose outcomes are unrelated scatter as much inside a
    # date as across the whole sample, so rho approaches 0.
    #
    # Then interpolate the sample size between its two honest extremes: when
    # every name on a date tells the same story, the DATE is the observation
    # and the sample is the number of dates; when they are independent, every
    # call counts. An earlier attempt used the textbook
    # n / (1 + (n-1)*rho) with an intraclass estimator, which is degenerate
    # when there is only one date — it reported 40 correlated names as 40
    # independent observations, the exact overstatement this function exists to
    # prevent.
    n = len(calls)
    n_dates = len(by_date)
    all_vals = [x for g in by_date.values() for x in g]
    grand = sum(all_vals) / len(all_vals)
    total_var = sum((x - grand) ** 2 for x in all_vals) / max(1, len(all_vals) - 1)
    if total_var <= 0:
        return 1, 1.0                       # every outcome identical: one fact

    groups = [g for g in by_date.values() if len(g) > 1]
    if not groups:
        return n, 0.0                       # one name per date: nothing shared

    within = sum(sum((x - (sum(g) / len(g))) ** 2 for x in g) / max(1, len(g) - 1)
                 for g in groups) / len(groups)
    rho = max(0.0, min(1.0, 1.0 - (within / total_var)))

    n_eff = n_dates + (n - n_dates) * (1.0 - rho)
    return max(1, int(round(n_eff))), round(rho, 3)


def _binomial_p(successes: int, n: int, p0: float) -> Optional[float]:
    """Two-sided normal-approximation p-value for a hit rate against a base
    rate. Approximate on purpose — the uncertainty in n_eff dwarfs the
    difference between this and an exact test."""
    if n < 5 or not (0 < p0 < 1):
        return None
    se = math.sqrt(p0 * (1 - p0) / n)
    if se <= 0:
        return None
    z = (successes / n - p0) / se
    # Two-sided tail of the standard normal via erfc.
    return round(math.erfc(abs(z) / math.sqrt(2)), 4)


def _spearman(a: list[float], b: list[float]) -> Optional[float]:
    """Rank correlation without pulling in scipy."""
    if len(a) < 8:
        return None
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(ra.corr(rb))


def evaluate_module(name: str, *, universe: list[str] | None = None,
                    n_splits: int = 5, dates_per_fold: int = 6,
                    lookback_period: str = "5y") -> dict:
    """Replay one module across the universe and score its calls."""
    from src.models.walk_forward import PurgedWalkForwardCV
    from src.v5 import marketdata as md, registry

    registry.load_modules()
    spec = registry.REGISTRY.get(name)
    if spec is None:
        return {"module": name, "error": "not registered", "validated": False}

    horizon = max(1, int(spec.horizon_days or 21))
    universe = universe or DEFAULT_UNIVERSE
    cv = PurgedWalkForwardCV(n_splits=n_splits, embargo_pct=0.02,
                             min_train_size=120)

    scores: list[float] = []
    returns: list[float] = []
    calls: list[dict] = []
    folds: list[dict] = []
    errors = 0
    abstentions = 0
    evaluations = 0

    for ticker in universe:
        # NOTE: fetched outside any as_of block on purpose — this is the
        # scoring series, the thing the module is graded against. The module
        # itself never sees it.
        full = md.history(ticker, period=lookback_period)
        if full is None or len(full) < 260:
            continue
        closes = full["Close"].dropna()

        # Fold over the GRADABLE part only. The last `horizon` bars have no
        # forward window yet, and the newest walk-forward fold lands exactly
        # there — so folding over the whole series produces a run whose most
        # recent fold silently scores nothing.
        gradable = full.iloc[:-horizon] if len(full) > horizon else full

        for fold_i, (_, test_idx) in enumerate(cv.split(gradable, horizon=horizon)):
            test_dates = gradable.index[test_idx]
            if len(test_dates) == 0:
                continue
            # Sample within the fold: consecutive days produce near-identical
            # overlapping calls, which inflates n without adding information.
            step = max(1, len(test_dates) // dates_per_fold)
            sampled = list(test_dates[::step])[:dates_per_fold]

            fold_hits, fold_calls = 0, 0
            for when in sampled:
                fwd = _forward_return(closes, when, horizon)
                if fwd is None:
                    continue
                evaluations += 1
                try:
                    with md.as_of(when):
                        report = registry.run(name, ticker)
                except Exception:                       # registry already isolates
                    errors += 1
                    continue
                if report.insufficient_data:
                    abstentions += 1
                    continue
                net = float(report.net)
                scores.append(net)
                returns.append(fwd)
                if abs(net) >= CALL_THRESHOLD:
                    correct = (net > 0 and fwd > 0) or (net < 0 and fwd < 0)
                    fold_calls += 1
                    fold_hits += int(correct)
                    calls.append({"ticker": ticker,
                                  "at": pd.Timestamp(when).date().isoformat(),
                                  "net": round(net, 2), "fwd_return": round(fwd, 4),
                                  "correct": bool(correct)})
            if fold_calls:
                folds.append({"fold": fold_i + 1, "ticker": ticker,
                              "calls": fold_calls,
                              "hit_rate": round(fold_hits / fold_calls, 4),
                              "test_start": str(test_dates[0])[:10],
                              "test_end": str(test_dates[-1])[:10]})

    n_calls = len(calls)
    hits = sum(1 for c in calls if c["correct"])
    hit_rate = round(hits / n_calls, 4) if n_calls else None
    ic = _spearman(scores, returns)

    # Split by side, because the aggregate is misleading without it. Over a
    # rising sample the base rate is 60-80%, so a module that mostly says
    # "bear" scores terribly on hit rate even when its bearish calls are better
    # than chance FOR BEARISH CALLS. One number cannot carry both facts.
    bulls = [c for c in calls if c["net"] > 0]
    bears = [c for c in calls if c["net"] < 0]
    by_side = {
        "bull_calls": len(bulls),
        "bull_hit_rate": (round(sum(c["correct"] for c in bulls) / len(bulls), 4)
                          if bulls else None),
        "bear_calls": len(bears),
        "bear_hit_rate": (round(sum(c["correct"] for c in bears) / len(bears), 4)
                          if bears else None),
    }

    # Base rate: how often the instrument simply went up over the same windows.
    # A 60% hit rate in a decade that rose 60% of the time is not skill, and
    # reporting it without this number would be the most flattering possible lie.
    base_up = round(sum(1 for r in returns if r > 0) / len(returns), 4) if returns else None

    # Significance, computed on the DISCOUNTED sample. Without this the file
    # reports "27% edge over base" from eleven correlated observations, and
    # somebody rewrites a module because of it.
    n_eff, rho = effective_sample_size(calls)
    # Scale the SUCCESSES with the discounted sample, exactly as
    # _util.conditional_hit_rate does. Passing a raw success count alongside a
    # discounted n computes the hit rate as hits/n_eff — which for 624 hits in
    # 1039 calls discounted to 824 reads as 76% instead of 60%, and duly
    # returned p = 0.0 for every module including one whose edge was NEGATIVE.
    # Every "significant" flag in the first run of this code was wrong.
    succ_eff = int(round(hit_rate * n_eff)) if hit_rate is not None else 0
    p_value = (_binomial_p(succ_eff, n_eff, base_up)
               if (hit_rate is not None and base_up is not None) else None)
    # An IC's standard error is ~1/sqrt(n_eff); anything inside two of those is
    # indistinguishable from zero.
    ic_se = (1.0 / math.sqrt(n_eff)) if n_eff > 1 else None
    ic_significant = bool(ic is not None and ic_se and abs(ic) > 2 * ic_se)
    significant = bool(p_value is not None and p_value < 0.05)

    fold_rates = [f["hit_rate"] for f in folds]
    result = {
        "module": name,
        "family": spec.family,
        "horizon_days": horizon,
        "universe": universe,
        "n_evaluations": evaluations,
        "n_abstentions": abstentions,
        "n_observations": len(scores),
        "n_calls": n_calls,
        # Point-in-time integrity of THIS result, next to the result.
        "point_in_time": not uses_wall_clock(name),
        "point_in_time_note": (
            "" if not uses_wall_clock(name) else
            "this module reads the wall clock, so its historical replay is not "
            "strictly point-in-time; treat the figures below as indicative"),
        "hit_rate": hit_rate,
        "base_rate_up": base_up,
        "edge_over_base": (round(hit_rate - base_up, 4)
                           if hit_rate is not None and base_up is not None else None),
        "information_coefficient": round(ic, 4) if ic is not None else None,
        "n_effective": n_eff,
        "cross_sectional_correlation": rho,
        "p_value": p_value,
        "significant": significant,
        "ic_standard_error": round(ic_se, 4) if ic_se else None,
        "ic_significant": ic_significant,
        **by_side,
        # The IC is the fairer headline for a module that mostly calls one way:
        # it asks whether the SCORE ranked the outcomes, which no base rate can
        # flatter or punish.
        "folds": folds,
        "fold_hit_rate_std": (round(float(np.std(fold_rates)), 4)
                              if len(fold_rates) > 1 else None),
        "module_errors": errors,
        "validated": n_calls >= MIN_CALLS_FOR_VERDICT,
        "verdict": _verdict(n_calls, hit_rate, base_up, ic,
                            n_eff=n_eff, p_value=p_value,
                            significant=significant),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    return result


def _verdict(n_calls: int, hit_rate: Optional[float], base: Optional[float],
             ic: Optional[float], *, n_eff: int = 0,
             p_value: Optional[float] = None, significant: bool = False) -> str:
    """The sentence a human reads. It must not describe noise as a finding.

    The first version of this called anything past a 3-percentage-point edge
    "positive" or "NEGATIVE" on the raw call count. With forty correlated names
    that count overstates the evidence by an order of magnitude, so the words
    were far more confident than the data — the exact failure the rest of this
    codebase is careful about. Significance is now required before either
    word is used, and the discounted sample is quoted so the reader can see how
    thin the evidence actually is.
    """
    if n_calls < MIN_CALLS_FOR_VERDICT:
        return (f"insufficient — {n_calls} directional calls, "
                f"{MIN_CALLS_FOR_VERDICT} needed before a hit rate means anything")
    if hit_rate is None:
        return "insufficient — no gradable calls"
    edge = (hit_rate - base) if base is not None else None
    if edge is None:
        return f"hit rate {hit_rate:.1%} over {n_calls} calls; no base rate available"

    sample = (f"{n_calls} calls, but only ~{n_eff} independent after "
              f"discounting for names that move together")
    # p is the Benjamini-Hochberg adjusted value once run_all has seen the whole
    # family of 41 tests; before that it is the raw one.
    if not significant:
        return (f"not distinguishable from the base rate — {hit_rate:.1%} vs "
                f"{base:.1%} ({edge:+.1%}), {sample}"
                + (f", adjusted p={p_value}" if p_value is not None else "")
                + ". Do not act on this number.")
    if edge > 0:
        return (f"positive and significant — {hit_rate:.1%} vs a {base:.1%} base "
                f"rate ({edge:+.1%}), adjusted p={p_value}, IC {ic:+.3f} ({sample})")
    return (f"NEGATIVE and significant — {hit_rate:.1%} vs a {base:.1%} base "
            f"rate ({edge:+.1%}), adjusted p={p_value}; this module has been "
            f"worse than the coin ({sample})")


# ── the whole registry ───────────────────────────────────────────────────────

def run_all(*, universe: list[str] | None = None, modules: list[str] | None = None,
            n_splits: int = 5, dates_per_fold: int = 6,
            progress: bool = True) -> dict:
    """Walk-forward every registered module. Slow by nature — this is a batch
    job (scripts/run_walkforward.py), not an API call."""
    from src.v5 import registry
    registry.load_modules()
    names = modules or sorted(registry.REGISTRY.keys())

    results = {}
    for i, name in enumerate(names, 1):
        if progress:
            logger.info("walk-forward %d/%d: %s", i, len(names), name)
        try:
            results[name] = evaluate_module(name, universe=universe,
                                            n_splits=n_splits,
                                            dates_per_fold=dates_per_fold)
        except Exception as e:
            logger.exception("walk-forward failed for %s", name)
            results[name] = {"module": name, "error": str(e)[:300],
                             "validated": False,
                             "at": datetime.now().isoformat(timespec="seconds")}

    # MULTIPLE COMPARISONS. Forty-one modules are forty-one simultaneous tests,
    # so at p<0.05 roughly two of them clear by chance under a null where
    # nothing works at all. Reporting the raw count as "12 significant modules"
    # would be the single most likely thing in this file to embarrass anyone who
    # quoted it.
    #
    # Benjamini-Hochberg, borrowed from src/v5/tiers.py rather than
    # reimplemented, so the platform has ONE convention for this rather than two
    # that can drift apart.
    from src.v5.tiers import _bh_adjust

    raw_p = {name: r["p_value"] for name, r in results.items()
             if r.get("p_value") is not None}
    adjusted = _bh_adjust(raw_p)
    for name, r in results.items():
        adj = adjusted.get(name)
        r["p_value_adjusted"] = adj
        r["significant_uncorrected"] = bool(r.get("significant"))
        r["significant"] = bool(adj is not None and adj < 0.05)
        # The verdict sentence has to be recomputed: it is written inside
        # evaluate_module, which cannot know the size of the family it belongs to.
        if r.get("n_calls"):
            r["verdict"] = _verdict(
                r["n_calls"], r.get("hit_rate"), r.get("base_rate_up"),
                r.get("information_coefficient"),
                n_eff=r.get("n_effective", 0), p_value=adj,
                significant=r["significant"])

    validated = [r for r in results.values() if r.get("validated")]
    bases = [r["base_rate_up"] for r in results.values()
             if r.get("base_rate_up") is not None]
    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "universe": universe or DEFAULT_UNIVERSE,
        # Read these before reading any number below them.
        "caveats": [
            f"The evaluation universe rose in {min(bases):.0%}-{max(bases):.0%} "
            f"of the measured windows. A module that mostly calls BEAR is "
            f"penalised by hit-rate-versus-base-rate on a sample like this even "
            f"when its bearish calls beat chance — read bear_hit_rate and the "
            f"information coefficient for those, not edge_over_base."
            if bases else "no base rates were computable",
            "Eight large-cap US names over roughly ten years is one market and "
            "one decade. These figures do not transfer to small caps, to other "
            "geographies, or to a decade that falls.",
            "Most modules are rule-based rather than fitted, so this is "
            "out-of-sample across time, not trained-here-tested-there. The "
            "rules were written by someone who had lived through this period, "
            "which no walk-forward can undo.",
            "Modules flagged point_in_time=false read the wall clock and so "
            "were not perfectly replayed; their figures are indicative only.",
        ],
        "n_splits": n_splits,
        "dates_per_fold": dates_per_fold,
        "modules_total": len(results),
        "modules_validated": len(validated),
        "modules_unvalidated": len(results) - len(validated),
        "call_threshold": CALL_THRESHOLD,
        "min_calls_for_verdict": MIN_CALLS_FOR_VERDICT,
        "results": results,
    }


# ── the stored record, and the flag every module report carries ──────────────

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MTIME: float = 0.0


def save(report: dict, path: Path | None = None) -> Path:
    p = path or RESULTS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    with _CACHE_LOCK:
        _CACHE.clear()
    return p


def load(path: Path | None = None) -> dict:
    """The stored results, reloaded when the file changes. {} if never run."""
    global _CACHE_MTIME
    p = path or RESULTS_FILE
    if not p.exists():
        return {}
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    with _CACHE_LOCK:
        if _CACHE and mtime == _CACHE_MTIME:
            return _CACHE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("walk-forward results unreadable: %s", e)
        return {}
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE.update(data)
        _CACHE_MTIME = mtime
    return data


def status(module: str) -> dict:
    """The validation status of one module, for its report.

    Never-run and never-validated both come back `validated: False`. Absence of
    evidence is reported as absence of evidence — not omitted, which is how it
    ends up read as evidence of absence of a problem.
    """
    stored = (load().get("results") or {}).get(module)
    if not stored:
        return {"walk_forward_validated": False,
                "walk_forward": {"status": "never run",
                                 "note": "this module has not been walk-forward "
                                         "validated; its opinion is untested"}}
    measured = bool(stored.get("validated"))
    skill = bool(stored.get("significant"))
    return {
        # "validated" means MEASURED — a large enough sample to report on.
        # It has never meant "works", and a reader who assumes it does will
        # trust a module with 1,200 calls and no edge whatsoever. The second
        # field is the one that answers "does it work", and it is separate
        # precisely because the answer is usually no.
        "walk_forward_validated": measured,
        "walk_forward_skill_demonstrated": skill,
        "walk_forward": {
            "status": ("skill demonstrated" if skill else
                       "measured, no demonstrable skill" if measured else
                       "insufficient sample"),
            "hit_rate": stored.get("hit_rate"),
            "base_rate_up": stored.get("base_rate_up"),
            "edge_over_base": stored.get("edge_over_base"),
            "information_coefficient": stored.get("information_coefficient"),
            "n_calls": stored.get("n_calls"),
            "n_effective": stored.get("n_effective"),
            "p_value": stored.get("p_value"),
            "significant": skill,
            "verdict": stored.get("verdict"),
            "as_of": stored.get("at"),
        },
    }


def summary() -> dict:
    """Coverage across the registry — what the API exposes at /api/v5/walk-forward."""
    from src.v5 import registry
    registry.load_modules()
    data = load()
    results = data.get("results") or {}
    per_module = []
    for name in sorted(registry.REGISTRY):
        r = results.get(name) or {}
        per_module.append({
            "module": name,
            "family": registry.REGISTRY[name].family,
            "walk_forward_validated": bool(r.get("validated")),
            "walk_forward_skill_demonstrated": bool(r.get("significant")),
            "hit_rate": r.get("hit_rate"),
            "edge_over_base": r.get("edge_over_base"),
            "information_coefficient": r.get("information_coefficient"),
            "n_calls": r.get("n_calls", 0),
            "n_effective": r.get("n_effective"),
            "p_value": r.get("p_value"),
            "verdict": r.get("verdict", "never run"),
        })
    validated = [m for m in per_module if m["walk_forward_validated"]]
    with_skill = [m for m in per_module if m["walk_forward_skill_demonstrated"]]
    return {
        "ran_at": data.get("at"),
        "universe": data.get("universe"),
        "modules_total": len(per_module),
        "modules_validated": len(validated),
        "modules_unvalidated": len(per_module) - len(validated),
        "modules_with_demonstrated_skill": len(with_skill),
        "coverage_pct": (round(100 * len(validated) / len(per_module), 1)
                         if per_module else 0.0),
        "note": ("Rule-based modules are not fitted, so 'walk-forward' here means "
                 "evaluated out-of-sample across time with the data layer pinned "
                 "to each evaluation date — not trained-here-tested-there."),
        "modules": per_module,
    }
