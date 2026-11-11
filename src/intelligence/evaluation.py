"""
src/intelligence/evaluation.py
==============================
Per-model performance over the canonical dataset — and nothing it cannot
support.

Every metric is reported only above its sample floor; below it the answer is
{"status": "INSUFFICIENT_DATA", "n": k, "n_required": m}, with no number to
quote. Two floors apply to rates, because two things can be too small:

    events     MIN_EVENTS independent graded calls
    clusters   MIN_CLUSTERS distinct information dates. Thirty technical calls
               taken from one session's bars share every market-wide move that
               followed; they are closer to one observation than to thirty.
               The rate is reported with both the Wilson interval (calls as
               independent) and a cluster-robust interval (dates as the unit),
               and the verdict uses the WIDER one.

Directional calls are compared with two baselines, never only with a coin:

    coin           does the interval exclude 50%?
    always long    on the SAME calls, would "up, every time" have done as
                   well? Measured with an exact McNemar test on the calls
                   where the two disagree. In a rising market a model that
                   mostly says "bull" beats a coin and carries no edge.

Probability metrics (Brier, log loss, ECE) read `p` only, which the dataset
fills from STATED / CALIBRATED probabilities alone, and go through
semantics.brier_score / log_loss, which refuse anything else.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Optional

from src.core.calibration import wilson
from src.intelligence import semantics

MIN_EVENTS = 30
MIN_CLUSTERS = 5
MIN_PROBABILITY_EVENTS = 30
MIN_CALIBRATION_EVENTS = 50          # = src/core/calibration.MIN_EVENTS
MIN_BUCKET = 12                      # = src/core/calibration.MIN_BUCKET
MIN_DISCORDANT = 20                  # McNemar needs disagreements to test
CALIBRATION_EDGES = ((0.0, 0.55), (0.55, 0.65), (0.65, 0.75), (0.75, 1.01))
Z95 = 1.96


def insufficient(n: int, required: int, why: str = "", **extra) -> dict:
    return {"status": "INSUFFICIENT_DATA", "n": n, "n_required": required,
            **({"why": why} if why else {}), **extra}


def cluster_se(outcomes: list, clusters: list) -> Optional[float]:
    """Cluster-robust standard error of a mean of 0/1 outcomes.

    Var = C/(C-1) * sum_c (sum_{i in c} (y_i - p))^2 / n^2 — the sandwich
    estimator with one cluster per information date.
    """
    n = len(outcomes)
    C = len(set(clusters))
    if n == 0 or C < 2:
        return None
    p = sum(outcomes) / n
    sums = defaultdict(float)
    for y, c in zip(outcomes, clusters):
        sums[c] += y - p
    var = (C / (C - 1)) * sum(s * s for s in sums.values()) / (n * n)
    return math.sqrt(max(var, 0.0))


def rate_block(rows: list, *, min_events: int = MIN_EVENTS,
               min_clusters: int = MIN_CLUSTERS) -> dict:
    """Hit rate with a Wilson and a cluster-robust interval, or INSUFFICIENT_DATA."""
    n = len(rows)
    clusters = [r.get("information_date") or r.get("created_at", "")[:10] for r in rows]
    n_clusters = len(set(clusters))
    if n < min_events:
        return insufficient(n, min_events, f"{n} graded calls", n_clusters=n_clusters)
    if n_clusters < min_clusters:
        return insufficient(n, min_events,
                            f"{n} calls but only {n_clusters} distinct information "
                            f"dates (need {min_clusters}) — they share one market path",
                            n_clusters=n_clusters, n_clusters_required=min_clusters)
    hits = sum(1 for r in rows if r["outcome"])
    p = hits / n
    lo, hi = wilson(hits, n)
    se = cluster_se([r["outcome"] for r in rows], clusters)
    # With a dozen clusters the sandwich variance is itself noisy and biased
    # low, so the cluster interval uses Student's t on (clusters - 1) degrees
    # of freedom rather than 1.96 — wider exactly when there are few dates.
    crit = _t_crit(n_clusters - 1)
    clo, chi = ((max(0.0, p - crit * se), min(1.0, p + crit * se)) if se is not None
                else (lo, hi))
    wlo, whi = min(lo, clo), max(hi, chi)        # the wider interval decides
    verdict = ("better than chance" if wlo > 0.5 else
               "worse than chance" if whi < 0.5 else
               "indistinguishable from chance")
    se_c = max(math.sqrt(max(p * (1 - p), 1e-12) / n), se or 0.0)
    p_value = _t_two_sided((p - 0.5) / se_c, n_clusters - 1)
    return {"status": "MEASURED", "n": n, "hits": hits, "rate": round(p, 4),
            "n_clusters": n_clusters,
            "ci95_wilson": [round(lo, 4), round(hi, 4)],
            "ci95_cluster": [round(clo, 4), round(chi, 4)],
            "cluster_df": n_clusters - 1,
            "p_value_vs_coin": round(p_value, 5),
            "verdict": verdict}


def _t_crit(df: int) -> float:
    if df < 1:
        return float("inf")
    try:
        from scipy import stats
        return float(stats.t.ppf(0.975, df))
    except Exception:                            # pragma: no cover - scipy present
        return Z95 if df > 120 else 2.0 + 4.0 / df


def _t_two_sided(t: float, df: int) -> float:
    if df < 1:
        return 1.0
    try:
        from scipy import stats
        return float(2 * stats.t.sf(abs(t), df))
    except Exception:                            # pragma: no cover - scipy present
        return math.erfc(abs(t) / math.sqrt(2))


def _binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial p-value against 0.5."""
    if n == 0:
        return 1.0
    try:
        from scipy import stats
        return float(stats.binomtest(k, n, 0.5).pvalue)
    except Exception:                            # pragma: no cover - scipy present
        probs = [math.comb(n, i) * 0.5 ** n for i in range(n + 1)]
        obs = probs[k]
        return float(min(1.0, sum(q for q in probs if q <= obs + 1e-15)))


def vs_always_long(rows: list) -> dict:
    """The same calls against "up, every time" — an exact McNemar test.

    Concordant calls (both right, both wrong) say nothing about which is
    better; only disagreements do. On a bull call the two always agree, so the
    test is really about the bear calls.
    """
    d = [r for r in rows if r["direction"] in ("bull", "bear")
         and r.get("actual_return") is not None]
    up = sum(1 for r in d if r["actual_return"] > 0)
    b = sum(1 for r in d if r["outcome"] and not r["actual_return"] > 0)
    c = sum(1 for r in d if not r["outcome"] and r["actual_return"] > 0)
    base = {"n": len(d), "base_rate_up": round(up / len(d), 4) if d else None,
            "always_long_hit_rate": round(up / len(d), 4) if d else None,
            "model_right_long_wrong": b, "model_wrong_long_right": c}
    if b + c < MIN_DISCORDANT:
        return {**base, "status": "INSUFFICIENT_DATA", "n_discordant": b + c,
                "n_discordant_required": MIN_DISCORDANT,
                "why": "too few calls where the model and always-long disagree"}
    pv = _binom_two_sided(b, b + c)
    return {**base, "status": "MEASURED", "n_discordant": b + c, "p_value": round(pv, 4),
            "verdict": ("beats always-long" if pv < 0.05 and b > c else
                        "worse than always-long" if pv < 0.05 and c > b else
                        "no detectable difference from always-long")}


def probability_block(rows: list) -> dict:
    """Brier, log loss, ECE and mean stated probability — scorable rows only."""
    pts = [(r["p"], r["outcome"], r["probability_type"]) for r in rows
           if r.get("p") is not None]
    n = len(pts)
    if n < MIN_PROBABILITY_EVENTS:
        return insufficient(n, MIN_PROBABILITY_EVENTS,
                            "calls carrying a stated or calibrated probability")
    brier = semantics.brier_score(pts)
    ll = semantics.log_loss(pts)
    mean_p = sum(p for p, _, _ in pts) / n
    realised = sum(1 for _, o, _ in pts if o) / n
    clim = realised * (1 - realised)
    out = {"status": "MEASURED", "n": n, "brier": round(brier, 5),
           "log_loss": round(ll, 5), "log_loss_eps": 1e-6,
           "mean_stated_probability": round(mean_p, 4),
           "realised_frequency": round(realised, 4),
           "brier_skill_vs_coin": round(1 - brier / 0.25, 4),
           "brier_skill_vs_climatology": (round(1 - brier / clim, 4) if clim > 0 else None)}
    if n < MIN_CALIBRATION_EVENTS:
        out["calibration_error"] = insufficient(n, MIN_CALIBRATION_EVENTS,
                                                "a calibration curve")
        return out
    buckets, ece = [], 0.0
    for lo, hi in CALIBRATION_EDGES:
        b = [(p, o) for p, o, _ in pts if lo <= p < hi]
        if len(b) < MIN_BUCKET:
            buckets.append({"range": f"{lo:.2f}-{hi:.2f}", **insufficient(len(b), MIN_BUCKET)})
            continue
        stated = sum(p for p, _ in b) / len(b)
        freq = sum(1 for _, o in b if o) / len(b)
        ece += len(b) / n * abs(stated - freq)
        wl, wh = wilson(sum(1 for _, o in b if o), len(b))
        buckets.append({"range": f"{lo:.2f}-{hi:.2f}", "status": "MEASURED", "n": len(b),
                        "stated": round(stated, 4), "realised": round(freq, 4),
                        "ci95": [round(wl, 4), round(wh, 4)],
                        "outside_interval": not (wl <= stated <= wh)})
    out["calibration_error"] = {"status": "MEASURED", "ece": round(ece, 4),
                                "buckets": buckets}
    return out


def slices(rows: list, key: str) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[r.get(key) or "UNKNOWN"].append(r)
    return {k: rate_block(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def regime_slices(rows: list, key: str) -> dict:
    """Per-regime rates, only when the record genuinely holds two regimes.

    With one label in the data a "breakdown" is a single row formatted as a
    comparison, which invites the conclusion the data cannot support.
    """
    known = [r for r in rows if r.get(key) and r.get(key) != "UNKNOWN"]
    groups = defaultdict(list)
    for r in known:
        groups[r[key]].append(r)
    qualifying = {k: v for k, v in groups.items()
                  if rate_block(v)["status"] == "MEASURED"}
    labels = {k: len(v) for k, v in groups.items()}
    if len(groups) < 2:
        return insufficient(len(known), MIN_EVENTS,
                            f"{len(groups)} regime label(s) in the record "
                            f"({', '.join(sorted(groups)) or 'none'}): a comparison "
                            f"needs at least two",
                            labels=labels, unlabelled=len(rows) - len(known))
    if len(qualifying) < 2:
        return insufficient(len(known), MIN_EVENTS,
                            "fewer than two regimes have enough graded calls "
                            "and dates to measure", labels=labels,
                            unlabelled=len(rows) - len(known))
    return {"status": "MEASURED", "labels": labels,
            "slices": {k: rate_block(v) for k, v in groups.items()}}


def _correct_slices(ev: dict) -> int:
    """Holm across every slice test in one model's evaluation.

    Slicing by horizon, asset class, version and regime is a search: with ten
    slices one clears p<0.05 on noise about two times in five. Each measured
    slice keeps its raw interval but its verdict is re-read on the
    Holm-adjusted p-value, and says so.
    """
    from src.intelligence.multiple_testing import holm
    refs = {}
    for family in ("by_horizon", "by_asset_class", "by_model_version"):
        for k, b in ev[family].items():
            if b.get("status") == "MEASURED":
                refs[f"{family}:{k}"] = b
    for family in ("by_regime_macro", "by_regime_market"):
        blk = ev[family]
        if blk.get("status") == "MEASURED":
            for k, b in blk["slices"].items():
                if b.get("status") == "MEASURED":
                    refs[f"{family}:{k}"] = b
    adj = holm({k: b["p_value_vs_coin"] for k, b in refs.items()})
    for k, b in refs.items():
        b["holm_p"] = adj[k]
        b["verdict_raw"] = b["verdict"]
        if adj[k] >= 0.05:
            b["verdict"] = "indistinguishable from chance after the slice correction"
    return len(refs)


def evaluate_model(rows: list) -> dict:
    """Everything one model's record supports, and the status of what it does not."""
    d = [r for r in rows if r["direction"] in ("bull", "bear")]
    neutral = [r for r in rows if r["direction"] == "neutral"]
    versions = defaultdict(int)
    for r in rows:
        versions[r.get("model_version") or "UNRECORDED"] += 1
    ptypes = defaultdict(int)
    for r in rows:
        ptypes[r["probability_type"]] += 1
    created = sorted(r["created_at"] for r in rows if r.get("created_at"))
    ev = {
        "sample": {"graded_events": len(rows), "directional": len(d),
                   "neutral": len(neutral),
                   "information_dates": len({r.get("information_date") for r in rows}),
                   "first_call": created[0] if created else None,
                   "last_call": created[-1] if created else None},
        "probability_types": dict(ptypes),
        "model_versions": dict(versions),
        "hit_rate_all": rate_block(rows),
        "directional_accuracy": rate_block(d),
        "neutral_band_hit_rate": rate_block(neutral),
        "vs_always_long": vs_always_long(d),
        "probability": probability_block(rows),
        "by_horizon": slices(d, "horizon_bucket"),
        "by_asset_class": slices(d, "asset_class"),
        "by_model_version": slices(d, "model_version"),
        "by_regime_macro": regime_slices(d, "regime_macro"),
        "by_regime_market": regime_slices(d, "regime_market"),
    }
    ev["slice_tests"] = _correct_slices(ev)
    return ev


def evaluate_all(ds) -> dict:
    """{model: evaluate_model(rows)} for every model with rows in the record."""
    out = {}
    for m in ds.models():
        out[m] = evaluate_model(ds.for_model(m))
    return out


def headline(ev: dict) -> str:
    """One sentence a reader can act on, for one model's evaluation."""
    da = ev["directional_accuracy"]
    if da["status"] != "MEASURED":
        return f"insufficient data ({da['n']} directional graded calls)"
    return (f"{da['rate']:.1%} directional over {da['n']} calls on "
            f"{da['n_clusters']} dates — {da['verdict']}")
