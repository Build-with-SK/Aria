"""
src/intelligence/walkforward.py
===============================
Train, validation and test windows in time order — and a record of every trial.

Financial outcomes are never shuffled here. A random split lets a fit learn
from next month to score last month, and with overlapping horizons it does so
even when the rows themselves are distinct, because a 45-day call made on
Monday and one made on Tuesday share 44 days of the same price path.

So the windows are anchored and expanding, in creation order, and PURGED on
the label: a training row is used only if its outcome was knowable (its
`resolve_after` date had passed) before the first validation call was made,
and likewise validation before test. That is the same rule the ML purged
walk-forward applies (src/models/walk_forward.py) restated for ledger rows.

Every fit or evaluation run records a trial (data/intelligence/trials.jsonl):
model, purpose, hyperparameters, the windows, the sample, the score. The count
of trials for a purpose is what the multiple-testing charge reads — a result
that was the best of twelve attempts is not reported as if it were the only one.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
TRIALS_FILE = ROOT / "data" / "intelligence" / "trials.jsonl"
_lock = threading.Lock()


@dataclass(frozen=True)
class Window:
    fold: int
    train: tuple                 # (first created_at, last created_at)
    validation: Optional[tuple]
    test: tuple
    n_train: int
    n_validation: int
    n_test: int
    purged: int                  # training/validation rows dropped by the label purge

    def to_dict(self) -> dict:
        return asdict(self)


def _key(r: dict) -> str:
    return str(r.get("created_at") or "")


def _known_before(r: dict, start: str) -> bool:
    """Was this row's outcome knowable before `start` (a created_at)?"""
    ra = str(r.get("resolve_after") or "")[:10]
    return bool(ra) and ra < start[:10]


def chronological_splits(rows: list, *, n_folds: int = 3, initial_fraction: float = 0.5,
                         validation_fraction: float = 0.25,
                         min_train: int = 1) -> list[tuple[Window, list, list, list]]:
    """Anchored expanding windows over rows sorted by `created_at`.

    The first `initial_fraction` of rows is history only; the remainder is cut
    into `n_folds` consecutive test blocks. For each block, everything before
    it is train + validation (the last `validation_fraction` of that span is
    validation), purged so no training label is still open when validation
    begins and no validation label is open when the test begins.

    Returns [(window, train_rows, validation_rows, test_rows), ...]. Order of
    the input does not matter; equal timestamps keep their input order.
    """
    ordered = sorted(rows, key=_key)
    n = len(ordered)
    start = int(n * initial_fraction)
    if n_folds < 1 or n - start < n_folds:
        return []
    size = (n - start) // n_folds
    out = []
    for k in range(n_folds):
        lo = start + k * size
        hi = n if k == n_folds - 1 else lo + size
        test = ordered[lo:hi]
        if not test:
            continue
        before = ordered[:lo]
        cut = int(len(before) * (1 - validation_fraction))
        train_all, val_all = before[:cut], before[cut:]
        t0 = _key(test[0])
        if val_all:
            v0 = _key(val_all[0])
            train = [r for r in train_all if _known_before(r, v0)]
            val = [r for r in val_all if _known_before(r, t0)]
        else:
            train = [r for r in train_all if _known_before(r, t0)]
            val = []
        purged = (len(train_all) - len(train)) + (len(val_all) - len(val))
        if len(train) < min_train:
            continue
        w = Window(fold=k,
                   train=(_key(train[0]), _key(train[-1])) if train else None,
                   validation=(_key(val[0]), _key(val[-1])) if val else None,
                   test=(_key(test[0]), _key(test[-1])),
                   n_train=len(train), n_validation=len(val), n_test=len(test),
                   purged=purged)
        out.append((w, train, val, test))
    return out


def holdout_split(rows: list, *, holdout_fraction: float = 0.3) -> tuple[list, list]:
    """(earlier, later): the most recent `holdout_fraction` of calls is held out,
    and earlier rows whose labels were still open when it began are dropped."""
    ordered = sorted(rows, key=_key)
    cut = int(len(ordered) * (1 - holdout_fraction))
    later = ordered[cut:]
    if not later:
        return ordered, []
    earlier = [r for r in ordered[:cut] if _known_before(r, _key(later[0]))]
    return earlier, later


# ── the trial record ────────────────────────────────────────────────────────

def record_trial(*, model_id: str, purpose: str, hyperparameters: Optional[dict] = None,
                 windows: Optional[list] = None, n: Optional[int] = None,
                 metric: Optional[str] = None, value: Optional[float] = None,
                 model_version: Optional[str] = None, note: str = "") -> dict:
    """Append one trial. Never rewritten: a trial that went badly still counts."""
    entry = {"at": datetime.now().isoformat(timespec="seconds"),
             "model_id": model_id, "model_version": model_version,
             "purpose": purpose, "hyperparameters": hyperparameters or {},
             "windows": [w.to_dict() if isinstance(w, Window) else w
                         for w in (windows or [])],
             "n": n, "metric": metric, "value": value, "note": note}
    with _lock:
        TRIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with TRIALS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    return entry


def trials(model_id: Optional[str] = None, purpose: Optional[str] = None) -> list[dict]:
    if not TRIALS_FILE.exists():
        return []
    out = []
    for line in TRIALS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            t = json.loads(line)
        except ValueError:
            continue
        if model_id and t.get("model_id") != model_id:
            continue
        if purpose and t.get("purpose") != purpose:
            continue
        out.append(t)
    return out


def trial_count(model_id: Optional[str] = None, purpose: Optional[str] = None) -> int:
    return len(trials(model_id, purpose))
