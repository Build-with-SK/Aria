# src/models/walk_forward.py
"""
Purged Walk-Forward Cross-Validation
Eliminates data leakage via purging and embargo periods.
Based on Lopez de Prado "Advances in Financial Machine Learning".
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FoldResult:
    fold: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_size: int
    test_size: int
    score: float
    feature_importances: dict = field(default_factory=dict)
    predictions: Optional[pd.Series] = None
    actuals: Optional[pd.Series] = None


class PurgedWalkForwardCV:
    """
    Walk-forward cross-validation with:
    - Purging: removes training samples whose forward-looking
      window overlaps with the test period
    - Embargo: adds a gap between train and test to prevent
      leakage from autocorrelated features

    Parameters
    ----------
    n_splits    : number of CV folds
    embargo_pct : fraction of test period to add as embargo gap
                  (0.01 = 1% of test period added as buffer)
    min_train_size: minimum rows in training set
    """

    def __init__(
        self,
        n_splits: int = 5,
        embargo_pct: float = 0.01,
        min_train_size: int = 120,
        expanding: bool = True,   # True = expanding window, False = rolling
        test_size: Optional[int] = None,
    ):
        self.n_splits      = n_splits
        self.embargo_pct   = embargo_pct
        self.min_train_size = min_train_size
        self.expanding     = expanding
        self.test_size     = test_size

    def split(
        self,
        X: pd.DataFrame,
        horizon: int = 1,
    ) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
        """
        Yield (train_indices, test_indices) for each fold.

        Parameters
        ----------
        X       : feature DataFrame with DatetimeIndex
        horizon : prediction horizon in days (used for purging)
        """
        n = len(X)
        if n < self.min_train_size + 10:
            logger.warning(f"Insufficient data for walk-forward CV: {n} rows")
            return

        # Determine test fold size
        test_fold_size = self.test_size or max(
            int((n - self.min_train_size) / self.n_splits), 20
        )
        embargo_size = max(int(test_fold_size * self.embargo_pct), horizon)

        indices = np.arange(n)

        for fold in range(self.n_splits):
            # Test window: walk forward
            test_end   = n - fold * test_fold_size
            test_start = max(test_end - test_fold_size, self.min_train_size + embargo_size)

            if test_start >= test_end:
                continue

            # Train window: expanding or rolling
            train_end_raw = test_start - embargo_size
            if self.expanding:
                train_start = 0
            else:
                train_start = max(0, train_end_raw - self.min_train_size * 3)

            # Purge: remove training samples whose horizon overlaps test
            # A sample at index i has forward window [i, i+horizon]
            # If i + horizon >= test_start: purge it
            purge_cutoff = test_start - horizon
            train_end    = min(train_end_raw, purge_cutoff)

            if train_end - train_start < self.min_train_size:
                continue

            train_idx = indices[train_start:train_end]
            test_idx  = indices[test_start:test_end]

            if len(train_idx) < self.min_train_size or len(test_idx) < 5:
                continue

            yield train_idx, test_idx

    def get_fold_dates(
        self,
        X: pd.DataFrame,
        horizon: int = 1,
    ) -> List[dict]:
        """Return human-readable fold date ranges for inspection."""
        folds = []
        for i, (tr, te) in enumerate(self.split(X, horizon)):
            folds.append({
                "fold": i + 1,
                "train_start": str(X.index[tr[0]])[:10],
                "train_end":   str(X.index[tr[-1]])[:10],
                "test_start":  str(X.index[te[0]])[:10],
                "test_end":    str(X.index[te[-1]])[:10],
                "train_rows":  len(tr),
                "test_rows":   len(te),
            })
        return folds


def combinatorial_purged_cv(
    X: pd.DataFrame,
    n_splits: int = 6,
    n_test_splits: int = 2,
    horizon: int = 1,
    embargo_pct: float = 0.01,
) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
    """
    Combinatorial Purged Cross-Validation (CPCV).
    Uses combinations of folds as test sets — gives more
    unique backtest paths than standard walk-forward.
    For n_splits=6, n_test_splits=2: C(6,2)=15 paths.
    """
    from itertools import combinations

    n = len(X)
    fold_size = n // n_splits
    embargo   = max(int(fold_size * embargo_pct), horizon)

    # Create fold boundaries
    folds = []
    for i in range(n_splits):
        start = i * fold_size
        end   = start + fold_size if i < n_splits - 1 else n
        folds.append((start, end))

    # All combinations of test folds
    for test_combo in combinations(range(n_splits), n_test_splits):
        test_indices = np.concatenate([
            np.arange(folds[i][0], folds[i][1]) for i in test_combo
        ])
        # Train: all folds NOT in test, minus embargo around each test fold
        train_indices = []
        for i in range(n_splits):
            if i in test_combo:
                continue
            s, e = folds[i]
            # Check proximity to any test fold
            too_close = False
            for j in test_combo:
                ts, te = folds[j]
                if abs(e - ts) <= embargo or abs(s - te) <= embargo:
                    too_close = True
                    break
            if not too_close:
                train_indices.extend(range(s, e))

        if len(train_indices) >= 60 and len(test_indices) >= 5:
            yield np.array(train_indices), test_indices


def compute_oof_predictions(
    model,
    X: pd.DataFrame,
    y: pd.Series,
    cv: PurgedWalkForwardCV,
    horizon: int = 1,
    predict_proba: bool = False,
) -> Tuple[pd.Series, List[FoldResult]]:
    """
    Compute out-of-fold predictions across all CV folds.
    Used for stacking (meta-learner training).

    Returns
    -------
    oof_preds   : Series of OOF predictions aligned to X's index
    fold_results: List of FoldResult with per-fold metrics
    """
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.base import clone

    oof_preds    = pd.Series(np.nan, index=X.index)
    fold_results = []

    for fold_i, (tr_idx, te_idx) in enumerate(cv.split(X, horizon)):
        X_train = X.iloc[tr_idx]
        y_train = y.iloc[tr_idx]
        X_test  = X.iloc[te_idx]
        y_test  = y.iloc[te_idx]

        # Drop NaN rows
        mask_train = y_train.notna() & X_train.notna().all(axis=1)
        mask_test  = y_test.notna()  & X_test.notna().all(axis=1)

        if mask_train.sum() < 30 or mask_test.sum() < 3:
            continue

        try:
            m = clone(model)
            m.fit(X_train[mask_train], y_train[mask_train])

            if predict_proba and hasattr(m, "predict_proba"):
                preds = m.predict_proba(X_test[mask_test])[:, 1]
            else:
                preds = m.predict(X_test[mask_test])

            oof_preds.iloc[te_idx[mask_test.values]] = preds

            # Score
            if predict_proba:
                try:
                    score = roc_auc_score(y_test[mask_test], preds)
                except Exception:
                    score = 0.5
            else:
                score = float(accuracy_score(y_test[mask_test], preds))

            # Feature importances
            fi = {}
            if hasattr(m, "feature_importances_"):
                fi = dict(zip(X_train.columns, m.feature_importances_))
            elif hasattr(m, "coef_"):
                fi = dict(zip(X_train.columns, np.abs(m.coef_[0]) if m.coef_.ndim > 1 else np.abs(m.coef_)))

            fold_results.append(FoldResult(
                fold=fold_i + 1,
                train_start=str(X_train.index[0])[:10],
                train_end=str(X_train.index[-1])[:10],
                test_start=str(X_test.index[0])[:10],
                test_end=str(X_test.index[-1])[:10],
                train_size=int(mask_train.sum()),
                test_size=int(mask_test.sum()),
                score=round(score, 4),
                feature_importances=fi,
                predictions=pd.Series(preds, index=X_test.index[mask_test.values]),
                actuals=y_test[mask_test],
            ))

        except Exception as e:
            logger.debug(f"Fold {fold_i + 1} failed: {e}")
            continue

    return oof_preds, fold_results


def mean_fold_score(fold_results: List[FoldResult]) -> float:
    """Average score across all folds."""
    if not fold_results:
        return 0.0
    return float(np.mean([f.score for f in fold_results]))


def aggregate_feature_importance(fold_results: List[FoldResult]) -> pd.Series:
    """Average feature importances across all folds."""
    if not fold_results:
        return pd.Series(dtype=float)
    all_fi = pd.DataFrame([f.feature_importances for f in fold_results])
    return all_fi.mean().sort_values(ascending=False)
