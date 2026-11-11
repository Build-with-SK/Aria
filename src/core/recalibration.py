"""
src/core/recalibration.py
=========================
Should a stated probability be recalibrated? An experiment, not a default.

A recalibration map (isotonic, Platt) is a MODEL, fitted on outcomes. Fit it
on the same rows it is judged on and it will always look like an improvement —
that is the in-sample trap, and it is the reason this module exists rather
than a one-line `IsotonicRegression().fit()` in the prediction path.

`evaluate(rows)` splits CHRONOLOGICALLY (fit on the earlier calls, judge on
the later ones), fits each candidate on the fit split only, and compares them
on the held-out split with Brier, ECE, log-loss and AUC (discrimination — a
monotone map cannot change it, so a "better" AUC is a bug). It recommends a
map only when:

  * both splits hold at least MIN_FIT / MIN_EVAL events, and
  * the held-out Brier improves on the raw probability by more than 1.96
    standard errors of the paired difference.

Otherwise the recommendation is "keep raw", and the reason is stated.

Nothing here touches a live prediction. Deploying a map is a separate,
deliberate change that must cite an `evaluate()` result.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

MIN_FIT = 100
MIN_EVAL = 100
EPS = 1e-6


def _brier(p, y) -> float:
    return float(np.mean((p - y) ** 2))


def _logloss(p, y) -> float:
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _ece(p, y, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def _auc(p, y) -> Optional[float]:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = np.argsort(np.argsort(np.concatenate([pos, neg]))) + 1.0
    # average ranks for ties
    allp = np.concatenate([pos, neg])
    for v in np.unique(allp):
        m = allp == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2)
                 / (len(pos) * len(neg)))


def _metrics(p, y) -> dict:
    return {"brier": round(_brier(p, y), 5), "ece": round(_ece(p, y), 5),
            "log_loss": round(_logloss(p, y), 5),
            "auc": None if _auc(p, y) is None else round(_auc(p, y), 5)}


def _fit_isotonic(p, y):
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(y_min=EPS, y_max=1 - EPS, out_of_bounds="clip")
    iso.fit(p, y)
    return iso.predict


def _fit_platt(p, y):
    from sklearn.linear_model import LogisticRegression
    z = np.log(np.clip(p, EPS, 1 - EPS) / (1 - np.clip(p, EPS, 1 - EPS))).reshape(-1, 1)
    lr = LogisticRegression(C=1e6)
    lr.fit(z, y)

    def predict(q):
        zq = np.log(np.clip(q, EPS, 1 - EPS) / (1 - np.clip(q, EPS, 1 - EPS))).reshape(-1, 1)
        return lr.predict_proba(zq)[:, 1]
    return predict


def evaluate(rows: list[dict], *, fit_fraction: float = 0.6) -> dict:
    """rows: [{created_at, p_correct, correct}] — stated probabilities only.

    Returns per-method held-out metrics and a recommendation."""
    usable = sorted((r for r in rows if r.get("p_correct") is not None
                     and r.get("correct") is not None and r.get("created_at")),
                    key=lambda r: r["created_at"])
    n = len(usable)
    cut = int(n * fit_fraction)
    fit, held = usable[:cut], usable[cut:]
    out: dict = {"n": n, "n_fit": len(fit), "n_eval": len(held),
                 "fit_period": [fit[0]["created_at"], fit[-1]["created_at"]] if fit else None,
                 "eval_period": [held[0]["created_at"], held[-1]["created_at"]] if held else None}
    if len(fit) < MIN_FIT or len(held) < MIN_EVAL:
        out.update(recommendation="keep raw",
                   why=(f"insufficient data: {len(fit)} fit / {len(held)} held-out "
                        f"events; at least {MIN_FIT} / {MIN_EVAL} are needed before "
                        f"any recalibration can be judged out of sample"))
        return out

    pf = np.array([float(r["p_correct"]) for r in fit])
    yf = np.array([1.0 if r["correct"] else 0.0 for r in fit])
    pe = np.array([float(r["p_correct"]) for r in held])
    ye = np.array([1.0 if r["correct"] else 0.0 for r in held])

    methods = {"raw": lambda q: q}
    try:
        methods["isotonic"] = _fit_isotonic(pf, yf)
    except Exception as e:                       # pragma: no cover - sklearn missing
        out.setdefault("errors", []).append(f"isotonic: {e}")
    try:
        methods["platt"] = _fit_platt(pf, yf)
    except Exception as e:                       # pragma: no cover
        out.setdefault("errors", []).append(f"platt: {e}")

    raw_err = (pe - ye) ** 2
    results, best, best_gain = {}, "raw", 0.0
    for name, fn in methods.items():
        q = np.clip(np.asarray(fn(pe), dtype=float), EPS, 1 - EPS)
        m = _metrics(q, ye)
        if name != "raw":
            diff = (q - ye) ** 2 - raw_err           # paired, per event
            se = float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else float("inf")
            gain = -float(diff.mean())
            m.update(brier_gain=round(gain, 5), brier_gain_se=round(se, 5),
                     significant=bool(gain > 1.96 * se))
            if m["significant"] and gain > best_gain:
                best, best_gain = name, gain
        results[name] = m
    out["held_out"] = results
    out["recommendation"] = best if best != "raw" else "keep raw"
    out["why"] = (f"{best} improves held-out Brier by {best_gain:.4f}, beyond 1.96 SE"
                  if best != "raw" else
                  "no recalibration improves held-out Brier beyond sampling noise")
    return out
