"""
src/intelligence/ensemble.py
============================
Do two models add independent information, or is one an echo of the other?

Averaging models is only an improvement when their errors are not the same
errors. Two correlated models averaged together are one model counted twice:
the combined "conviction" rises while the information does not. So for every
pair of sources this measures, on the calls they made about the same
instrument from the same day's data:

    agreement            how often they point the same way (all calls, graded
                         or not — agreement needs no outcome)
    prediction corr.     phi coefficient of their directions
    error correlation    phi of their right/wrong, on calls both had graded
    conditional          P(B right | A right) and P(B right | A wrong)
    incremental info     does B's direction improve a model of the outcome
                         that already has A's? A likelihood-ratio test of
                         logit(up) ~ A  vs  ~ A + B. Only on a COMMON target:
                         the same instrument, the same anchor date and the
                         same horizon bucket, or the outcomes are different
                         questions and the test is refused.

Every block reports its n and refuses (INSUFFICIENT_DATA) below its floor.
The verdict "adds information" requires the likelihood-ratio test to clear
p<0.05; "redundant" requires high agreement AND an insignificant test.
"""
from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations
from typing import Optional

import numpy as np

MIN_OVERLAP = 30          # paired directional calls before agreement means anything
MIN_GRADED_PAIRS = 30     # paired graded calls for error correlation
MIN_LR_PAIRS = 50         # paired graded calls on a common target for the LR test
REDUNDANT_AGREEMENT = 0.85


def _sign(direction: str) -> int:
    return 1 if direction == "bull" else -1 if direction == "bear" else 0


def _key(r: dict) -> tuple:
    from src.intelligence.dataset import information_date
    sym = r.get("symbol") or r.get("provider_symbol") or r.get("subject")
    date = r.get("information_date") or information_date(r)
    return (str(sym).upper(), date)


def _index(rows: list) -> dict:
    out = {}
    for r in sorted(rows, key=lambda x: x.get("created_at") or ""):
        out.setdefault(_key(r), r)            # first call per instrument per date
    return out


def _phi(a: list, b: list) -> Optional[float]:
    if len(a) < 3:
        return None
    x, y = np.asarray(a, float), np.asarray(b, float)
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _loglik(X: np.ndarray, y: np.ndarray) -> float:
    """Max log-likelihood of a logistic regression (Newton, with an intercept)."""
    Xi = np.column_stack([np.ones(len(y)), X]) if X.size else np.ones((len(y), 1))
    w = np.zeros(Xi.shape[1])
    for _ in range(50):
        z = Xi @ w
        p = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        g = Xi.T @ (y - p)
        H = Xi.T @ (Xi * (p * (1 - p))[:, None]) + 1e-8 * np.eye(len(w))
        step = np.linalg.solve(H, g)
        w += step
        if np.max(np.abs(step)) < 1e-9:
            break
    p = np.clip(1 / (1 + np.exp(-np.clip(Xi @ w, -30, 30))), 1e-12, 1 - 1e-12)
    return float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))


def incremental_information(a_dirs: list, b_dirs: list, up: list) -> dict:
    """LR test: does B's direction add to A's in explaining the move?"""
    n = len(up)
    if n < MIN_LR_PAIRS:
        return {"status": "INSUFFICIENT_DATA", "n": n, "n_required": MIN_LR_PAIRS}
    y = np.asarray(up, float)
    if y.min() == y.max():
        return {"status": "INSUFFICIENT_DATA", "n": n,
                "why": "every outcome went the same way; nothing to explain"}
    A = np.asarray(a_dirs, float).reshape(-1, 1)
    AB = np.column_stack([a_dirs, b_dirs]).astype(float)
    ll_a, ll_ab = _loglik(A, y), _loglik(AB, y)
    lr = max(0.0, 2 * (ll_ab - ll_a))
    p = math.erfc(math.sqrt(lr / 2))          # chi-square(1) survival
    return {"status": "MEASURED", "n": n, "lr_statistic": round(lr, 4),
            "p_value": round(p, 5), "adds_information": p < 0.05}


def pair(a_name: str, a_rows: list, b_name: str, b_rows: list,
         graded_a: list, graded_b: list) -> dict:
    """Everything measurable about one pair of sources."""
    ia, ib = _index(a_rows), _index(b_rows)
    both = [k for k in ia if k in ib]
    dirs = [(ia[k], ib[k]) for k in both
            if _sign(ia[k].get("direction")) and _sign(ib[k].get("direction"))]
    out = {"pair": [a_name, b_name], "overlap": len(both), "both_directional": len(dirs)}
    if len(dirs) < MIN_OVERLAP:
        out["agreement"] = {"status": "INSUFFICIENT_DATA", "n": len(dirs),
                            "n_required": MIN_OVERLAP}
    else:
        agree = sum(1 for a, b in dirs if a["direction"] == b["direction"])
        out["agreement"] = {"status": "MEASURED", "n": len(dirs),
                            "rate": round(agree / len(dirs), 4),
                            "phi_directions": _phi([_sign(a["direction"]) for a, _ in dirs],
                                                   [_sign(b["direction"]) for _, b in dirs])}

    ga, gb = _index(graded_a), _index(graded_b)
    gboth = [(ga[k], gb[k]) for k in ga if k in gb
             and _sign(ga[k]["direction"]) and _sign(gb[k]["direction"])]
    if len(gboth) < MIN_GRADED_PAIRS:
        out["errors"] = {"status": "INSUFFICIENT_DATA", "n": len(gboth),
                         "n_required": MIN_GRADED_PAIRS}
    else:
        ca = [a["outcome"] for a, _ in gboth]
        cb = [b["outcome"] for _, b in gboth]
        a_right = [b for a, b in zip(ca, cb) if a]
        a_wrong = [b for a, b in zip(ca, cb) if not a]
        out["errors"] = {
            "status": "MEASURED", "n": len(gboth),
            "error_correlation": _phi(ca, cb),
            "p_b_right_given_a_right": round(sum(a_right) / len(a_right), 4) if a_right else None,
            "p_b_right_given_a_wrong": round(sum(a_wrong) / len(a_wrong), 4) if a_wrong else None,
        }
    common = [(a, b) for a, b in gboth
              if a.get("horizon_bucket") == b.get("horizon_bucket")
              and a.get("actual_return") is not None]
    if not common and gboth:
        out["incremental"] = {"status": "NOT_TESTABLE",
                              "why": "the two sources grade different horizons, so their "
                                     "outcomes answer different questions"}
    else:
        out["incremental"] = incremental_information(
            [_sign(a["direction"]) for a, _ in common],
            [_sign(b["direction"]) for _, b in common],
            [1 if a["actual_return"] > 0 else 0 for a, _ in common])
    ag = out["agreement"]
    inc = out["incremental"]
    if inc.get("status") == "MEASURED" and inc["adds_information"]:
        out["verdict"] = f"{b_name} adds information beyond {a_name}"
    elif (ag.get("status") == "MEASURED" and ag["rate"] >= REDUNDANT_AGREEMENT
          and inc.get("status") == "MEASURED"):
        out["verdict"] = f"{b_name} is largely redundant with {a_name}"
    else:
        out["verdict"] = "insufficient overlap to say whether these add independent information"
    return out


def independence(ledger_rows: list, dataset) -> dict:
    """Every pair of ledger sources: agreement on all calls, errors on graded ones."""
    from src.intelligence.dataset import information_date
    by_src = defaultdict(list)
    for r in ledger_rows:
        if r.get("source"):
            by_src[r["source"]].append({**r, "symbol": r.get("provider_symbol") or r.get("subject"),
                                        "information_date": information_date(r)})
    graded = defaultdict(list)
    for r in dataset.rows:
        graded[r["model"]].append(r)
    pairs = [pair(a, by_src[a], b, by_src[b], graded[a], graded[b])
             for a, b in combinations(sorted(by_src), 2)]
    return {"pairs": pairs,
            "method": "pairs aligned on (instrument, information date); first call per "
                      "key; incremental test only on a common horizon bucket",
            "floors": {"agreement": MIN_OVERLAP, "errors": MIN_GRADED_PAIRS,
                       "incremental": MIN_LR_PAIRS}}
