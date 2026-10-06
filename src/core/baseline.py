"""
src/core/baseline.py
====================
The measured "before" — and the only way ARIA may claim an "after".

THE RULE THIS MODULE ENFORCES
-----------------------------
"Calibration improved" / "the hit rate went up" is a claim, and it is only
allowed when all three hold:

  1. OUT OF SAMPLE. The comparison is made on predictions CREATED AFTER the
     baseline's cutoff. Re-measuring the rows a change was tuned against is
     not evidence that the change works; it is the tuning reported back.
  2. ENOUGH DATA. Below MIN_N scored events on either side, the verdict is
     "insufficient data", whatever the point estimates say.
  3. OUTSIDE NOISE. Hit rates differ by a two-proportion z-test at 95%; Brier
     scores differ by more than 1.96 standard errors of the difference.
     Anything else is "no detectable change".

Probabilities are read from `p_correct` on rows whose source STATED one
(probability_kind == "stated", directional calls only). Hit rates are
reported for directional calls separately from neutral ones, because a neutral
call's "correct" means the price stayed in a band — a different claim.

Everything here is a pure function of ledger rows, so it is tested on
synthetic data with known answers.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable, Optional

from src.core.calibration import wilson

MIN_N = 30
EDGES = [(0.0, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.80), (0.80, 1.01)]


def _rate(k: int, n: int) -> dict:
    ci = wilson(k, n) if n else None
    return {"n": n, "hits": k, "rate": round(k / n, 4) if n else None,
            "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None}


def _stated(r: dict) -> Optional[float]:
    if (r.get("probability_kind") == "stated" and r.get("p_correct") is not None
            and (r.get("direction") or "") in ("bull", "bear")):
        return float(r["p_correct"])
    return None


def _brier_block(rows: list[dict]) -> dict:
    pts = [(_stated(r), 1.0 if r["correct"] else 0.0) for r in rows]
    pts = [(p, o) for p, o in pts if p is not None]
    n = len(pts)
    if n == 0:
        return {"n": 0, "brier": None, "brier_se": None, "skill": None, "ece": None,
                "buckets": []}
    errs = [(p - o) ** 2 for p, o in pts]
    brier = sum(errs) / n
    var = sum((e - brier) ** 2 for e in errs) / (n - 1) if n > 1 else 0.0
    ece, buckets = 0.0, []
    for lo, hi in EDGES:
        b = [(p, o) for p, o in pts if lo <= p < hi]
        if not b:
            continue
        stated = sum(p for p, _ in b) / len(b)
        realised = sum(o for _, o in b) / len(b)
        ece += len(b) / n * abs(stated - realised)
        buckets.append({"range": f"{lo:.2f}-{hi:.2f}", "n": len(b),
                        "stated": round(stated, 4),
                        **{k: v for k, v in _rate(int(sum(o for _, o in b)), len(b)).items()
                           if k in ("rate", "ci95")}})
    return {"n": n, "brier": round(brier, 5), "brier_se": round(math.sqrt(var / n), 5),
            "skill": round(1 - brier / 0.25, 4), "ece": round(ece, 4), "buckets": buckets}


def measure(rows: Iterable[dict], *, created_after: Optional[str] = None) -> dict:
    """Metrics over graded ledger rows, per source and overall."""
    graded = [r for r in rows if r.get("resolved") and r.get("correct") is not None
              and (created_after is None or (r.get("created_at") or "") > created_after)]
    by: dict[str, list] = {}
    for r in graded:
        by.setdefault(r.get("source") or "unknown", []).append(r)

    def block(rs: list[dict]) -> dict:
        d = [r for r in rs if (r.get("direction") or "") in ("bull", "bear")]
        nn = [r for r in rs if (r.get("direction") or "") not in ("bull", "bear")]
        return {
            "scored": len(rs),
            "directional": _rate(sum(1 for r in d if r["correct"]), len(d)),
            "neutral": _rate(sum(1 for r in nn if r["correct"]), len(nn)),
            "stated_probability": _brier_block(rs),
            "kinds": {k: sum(1 for r in rs if r.get("probability_kind") == k)
                      for k in ("stated", "mapped", "none")},
            "regimes": sorted({r.get("regime_at") for r in rs if r.get("regime_at")}),
            "created": [min((r.get("created_at") or "") for r in rs) if rs else None,
                        max((r.get("created_at") or "") for r in rs) if rs else None],
        }

    return {"measured_at": datetime.now().isoformat(timespec="seconds"),
            "created_after": created_after,
            "cutoff": max((r.get("created_at") or "") for r in graded) if graded else None,
            "overall": block(graded),
            "by_source": {k: block(v) for k, v in sorted(by.items())}}


def _z_two_props(k1: int, n1: int, k2: int, n2: int) -> Optional[float]:
    if not n1 or not n2:
        return None
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return None if se == 0 else (k2 / n2 - k1 / n1) / se


def compare(baseline: dict, current: dict) -> dict:
    """Verdicts for current vs baseline. `current` MUST be measured with
    created_after=baseline["cutoff"]; anything else is refused."""
    if baseline.get("cutoff") and current.get("created_after") != baseline["cutoff"]:
        return {"verdict": "refused",
                "why": ("current must be measured on predictions created after the "
                        f"baseline cutoff {baseline['cutoff']} (out of sample)")}
    out = {}
    for src in sorted(set(baseline["by_source"]) | set(current["by_source"])):
        b, c = baseline["by_source"].get(src), current["by_source"].get(src)
        if not b or not c:
            out[src] = {"hit_rate": "insufficient data", "brier": "insufficient data"}
            continue
        bd, cd = b["directional"], c["directional"]
        if bd["n"] < MIN_N or cd["n"] < MIN_N:
            hv = "insufficient data"
        else:
            z = _z_two_props(bd["hits"], bd["n"], cd["hits"], cd["n"])
            hv = ("improved" if z is not None and z > 1.96 else
                  "worse" if z is not None and z < -1.96 else "no detectable change")
        bb, cb = b["stated_probability"], c["stated_probability"]
        if bb["n"] < MIN_N or cb["n"] < MIN_N:
            bv = "insufficient data"
        else:
            diff = cb["brier"] - bb["brier"]
            se = math.sqrt(bb["brier_se"] ** 2 + cb["brier_se"] ** 2)
            bv = ("improved" if diff < -1.96 * se else
                  "worse" if diff > 1.96 * se else "no detectable change")
        out[src] = {"hit_rate": hv, "brier": bv,
                    "n": {"baseline": bd["n"], "current": cd["n"]}}
    return {"verdict": "compared", "by_source": out}
