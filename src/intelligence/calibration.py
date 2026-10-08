"""
src/intelligence/calibration.py
===============================
Calibration status per model — and the refusal to fit one on too little.

A calibration map is a model fitted on outcomes. Fitted and judged on the same
calls it always looks like an improvement; that is the in-sample trap, and
src/core/recalibration.py already exists to refuse it. This module decides,
per model, whether calibration is even a question yet, and if it is, runs it
on strictly temporal train / validation / test windows:

    train        fit each candidate map
    validation   choose between them (and raw)
    test         judge the chosen map against raw, once, on calls it has
                 never seen; recommended only if the paired Brier gain is
                 beyond 1.96 standard errors

The method is chosen by the size of the TRAINING window, before any fit:

    < 100        nothing — CALIBRATION_INSUFFICIENT_DATA
    100 - 999    Platt (two parameters; isotonic regression memorises
                 a sample this size)
    >= 1000      Platt or isotonic

Beta calibration is not implemented; it is listed so its absence is visible.

Statuses:

    CALIBRATION_NOT_APPLICABLE     the model states no probability (its
                                   numbers are CONFIDENCE, SCORE or NONE)
    CALIBRATION_INSUFFICIENT_DATA  too few probability observations
    RAW_RETAINED                   enough data; no map beat raw out of sample
    CALIBRATION_CANDIDATE          a map beat raw out of sample. NOT applied:
                                   deploying it is an owner decision that must
                                   cite this result, and produces a
                                   calibration_version.

`calibration_version` is "none" for every model today.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from src.intelligence import walkforward

MIN_TRAIN = 100
MIN_VALIDATION = 50
MIN_TEST = 50
ISOTONIC_MIN_TRAIN = 1000
TRAIN, VALIDATION, TEST = 0.6, 0.2, 0.2
NOT_IMPLEMENTED = ("beta",)

NOT_APPLICABLE = "CALIBRATION_NOT_APPLICABLE"
INSUFFICIENT = "CALIBRATION_INSUFFICIENT_DATA"
RAW_RETAINED = "RAW_RETAINED"
CANDIDATE = "CALIBRATION_CANDIDATE"
CURRENT_VERSION = "none"


def methods_for(n_train: int) -> list[str]:
    if n_train < MIN_TRAIN:
        return []
    if n_train < ISOTONIC_MIN_TRAIN:
        return ["platt"]
    return ["platt", "isotonic"]


def _split(rows: list) -> tuple[list, list, list, int]:
    """60 / 20 / 20 in creation order, purged on the label."""
    ordered = sorted(rows, key=lambda r: r.get("created_at") or "")
    n = len(ordered)
    a, b = int(n * TRAIN), int(n * (TRAIN + VALIDATION))
    train, val, test = ordered[:a], ordered[a:b], ordered[b:]
    purged = 0
    if val:
        keep = [r for r in train if walkforward._known_before(r, val[0]["created_at"])]
        purged += len(train) - len(keep)
        train = keep
    if test:
        keep = [r for r in val if walkforward._known_before(r, test[0]["created_at"])]
        purged += len(val) - len(keep)
        val = keep
    return train, val, test, purged


def status(rows: list, *, model_id: str = "", declared_probability: bool = True,
           record: bool = False) -> dict:
    """Calibration status for one model's dataset rows."""
    probs = [r for r in rows if r.get("p") is not None]
    base = {"model_id": model_id, "calibration_version": CURRENT_VERSION,
            "n_probability_observations": len(probs),
            "methods_not_implemented": list(NOT_IMPLEMENTED)}
    if not probs and not declared_probability:
        return {**base, "status": NOT_APPLICABLE,
                "why": "this model states no probability: its numbers are "
                       "confidence, score or nothing, and cannot be calibrated "
                       "as probabilities"}
    train, val, test, purged = _split(probs)
    base.update(n_train=len(train), n_validation=len(val), n_test=len(test),
                purged=purged,
                windows={"train": [train[0]["created_at"], train[-1]["created_at"]] if train else None,
                         "validation": [val[0]["created_at"], val[-1]["created_at"]] if val else None,
                         "test": [test[0]["created_at"], test[-1]["created_at"]] if test else None})
    if len(train) < MIN_TRAIN or len(val) < MIN_VALIDATION or len(test) < MIN_TEST:
        return {**base, "status": INSUFFICIENT,
                "why": (f"{len(train)} train / {len(val)} validation / {len(test)} test "
                        f"probability observations; at least {MIN_TRAIN} / "
                        f"{MIN_VALIDATION} / {MIN_TEST} are needed before any map "
                        f"can be fitted and judged out of sample"),
                "candidate_methods": methods_for(len(train))}
    return {**base, **_fit_and_judge(train, val, test, model_id=model_id, record=record)}


def _arr(rows):
    return (np.array([float(r["p"]) for r in rows]),
            np.array([1.0 if r["outcome"] else 0.0 for r in rows]))


def _fit_and_judge(train, val, test, *, model_id: str, record: bool) -> dict:
    from src.core import recalibration as rc
    pt, yt = _arr(train)
    pv, yv = _arr(val)
    pe, ye = _arr(test)
    fitters = {"platt": rc._fit_platt, "isotonic": rc._fit_isotonic}
    candidates = {"raw": (lambda q: q)}
    for name in methods_for(len(train)):
        try:
            candidates[name] = fitters[name](pt, yt)
        except Exception as e:                   # pragma: no cover - sklearn present
            candidates[f"{name}_failed"] = None
            _ = e
    val_brier = {}
    for name, fn in candidates.items():
        if fn is None:
            continue
        q = np.clip(np.asarray(fn(pv), dtype=float), rc.EPS, 1 - rc.EPS)
        val_brier[name] = float(np.mean((q - yv) ** 2))
    chosen = min(val_brier, key=val_brier.get)
    trials = len(val_brier)
    if record:
        walkforward.record_trial(model_id=model_id, purpose="calibration_selection",
                                 hyperparameters={"candidates": sorted(val_brier)},
                                 n=len(val), metric="validation_brier",
                                 value=round(val_brier[chosen], 6),
                                 note=f"chose {chosen}")
    raw_test = rc._metrics(np.clip(pe, rc.EPS, 1 - rc.EPS), ye)
    if chosen == "raw":
        return {"status": RAW_RETAINED, "chosen_on_validation": "raw",
                "validation_brier": {k: round(v, 5) for k, v in val_brier.items()},
                "test_raw": raw_test, "trials": trials,
                "why": "no map beat the raw probabilities on the validation window"}
    q = np.clip(np.asarray(candidates[chosen](pe), dtype=float), rc.EPS, 1 - rc.EPS)
    diff = (q - ye) ** 2 - (pe - ye) ** 2
    gain = -float(diff.mean())
    se = float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else float("inf")
    significant = gain > 1.96 * se
    return {"status": CANDIDATE if significant else RAW_RETAINED,
            "chosen_on_validation": chosen,
            "validation_brier": {k: round(v, 5) for k, v in val_brier.items()},
            "test_raw": raw_test, "test_mapped": rc._metrics(q, ye),
            "test_brier_gain": round(gain, 6), "test_brier_gain_se": round(se, 6),
            "trials": trials,
            "why": (f"{chosen} improved held-out Brier by {gain:.4f} (> 1.96 SE); "
                    f"NOT applied — deployment is an owner decision"
                    if significant else
                    f"{chosen} won on validation but its test gain {gain:.4f} is "
                    f"within 1.96 SE ({se:.4f}) of raw")}


def summary(dataset, declared: Optional[dict] = None) -> dict:
    """{model: status} over a canonical dataset. `declared` maps model ->
    whether the model claims to state probabilities at all."""
    declared = declared or {}
    return {m: status(dataset.for_model(m), model_id=m,
                      declared_probability=declared.get(m, True))
            for m in dataset.models()}
