"""
ml_model.py  —  v2 (hardened)
==============================
Machine learning models for directional price prediction.

Fixes vs v1
-----------
[BUG]  No walk-forward validation — single 80/20 split is optimistically biased
       on financial time series. Now uses sklearn TimeSeriesSplit (5 folds).
[BUG]  No prediction confidence gate — all model outputs treated equally.
       Now requires model agreement ≥ agreement_threshold before flagging signal.

New features
------------
[NEW]  predict_with_confidence()  — returns direction + ensemble probability +
       model agreement score. Caller can gate on agreement.
[NEW]  get_feature_importance()   — extracts feature weights/importances from
       every trained model in a horizon set, returns ranked DataFrame.
[NEW]  walk_forward_accuracy()    — rolling out-of-sample accuracy over 5 folds;
       much more honest than a single holdout split.
[NEW]  ML_MIN_FEATURES guard raised from 8 → 10 for tighter quality control.
"""

from __future__ import annotations

import logging
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score
import joblib

warnings.filterwarnings("ignore", category=UserWarning)
logger = logging.getLogger(__name__)

try:
    from xgboost import XGBClassifier
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    logger.info("XGBoost not installed — using Gradient Boosting instead")


# ===========================================================================
# Feature columns
# ===========================================================================

ML_FEATURE_COLS = [
    # Returns — available for all asset classes including forex
    "return_1d", "return_5d", "return_20d", "return_60d", "log_return_1d",
    # Trend
    "dist_sma_20", "dist_sma_50", "dist_sma_200",
    # Momentum (no volume dependency)
    "rsi", "macd", "macd_histogram", "roc", "momentum",
    # Volatility
    "realised_vol", "bb_width", "bb_pct_b", "atr_pct",
    # Regime flags
    "bull_regime", "bear_regime", "high_vol_regime", "low_vol_regime",
    # Volume — optional, skipped automatically when absent (e.g. forex)
    "volume_trend",
]

ML_MIN_FEATURES = 10   # v1 was 8; raised to enforce tighter quality gate


# ===========================================================================
# Feature preparation
# ===========================================================================

def _prepare_features(df: pd.DataFrame, horizon: int) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Build feature matrix X and target vector y for a prediction horizon.

    Target: 1 if close(t+horizon) > close(t), else 0.
    Features: all available ML_FEATURE_COLS at time t — strictly no lookahead.

    Returns (X, y). Either may be empty if data is insufficient.
    """
    available = [c for c in ML_FEATURE_COLS if c in df.columns]
    if len(available) < ML_MIN_FEATURES:
        logger.warning(
            f"Only {len(available)} feature cols available (need {ML_MIN_FEATURES}). "
            "Skipping."
        )
        return pd.DataFrame(), pd.Series(dtype=int)

    df = df.copy()
    # shift(-horizon) computes future return — this is the label only,
    # never a feature input, so there is no lookahead in X.
    df["_target"] = (df["Close"].shift(-horizon) > df["Close"]).astype(int)

    data = df[available + ["_target"]].dropna()

    if len(data) < 60:
        return pd.DataFrame(), pd.Series(dtype=int)

    return data[available], data["_target"]


# ===========================================================================
# Pipeline factory
# ===========================================================================

def build_model_pipeline(model_type: str = "random_forest") -> Pipeline:
    """
    Build a sklearn Pipeline: StandardScaler + classifier.

    Using a Pipeline is critical — it prevents data leakage during
    cross-validation (scaler is fit only on the training fold).
    """
    if model_type == "logistic":
        clf = LogisticRegression(
            max_iter=500, class_weight="balanced", C=0.1,
        )

    elif model_type == "random_forest":
        clf = RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            min_samples_leaf=10,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )

    elif model_type == "gradient_boosting":
        clf = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )

    elif model_type == "xgboost" and XGBOOST_AVAILABLE:
        clf = XGBClassifier(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            eval_metric="logloss",
            verbosity=0,
            random_state=42,
        )

    else:
        logger.warning(f"Unknown model_type '{model_type}', falling back to logistic")
        clf = LogisticRegression(max_iter=500, class_weight="balanced")

    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    clf),
    ])


# ===========================================================================
# Walk-forward cross-validation
# ===========================================================================

def walk_forward_accuracy(
    X: pd.DataFrame,
    y: pd.Series,
    model_type: str = "random_forest",
    n_splits: int   = 5,
) -> Dict:
    """
    Compute rolling out-of-sample accuracy via TimeSeriesSplit.

    TimeSeriesSplit never shuffles — each fold trains on the past only,
    which is the correct regime for financial time series.

    Returns
    -------
    {
        "mean_accuracy": float,
        "std_accuracy":  float,
        "fold_scores":   list[float],
        "model_type":    str,
    }
    """
    if len(X) < n_splits * 20:
        return {"mean_accuracy": float("nan"), "std_accuracy": float("nan"),
                "fold_scores": [], "model_type": model_type}

    tscv   = TimeSeriesSplit(n_splits=n_splits)
    pipe   = build_model_pipeline(model_type)
    scores = cross_val_score(pipe, X, y, cv=tscv, scoring="accuracy", n_jobs=-1)

    return {
        "mean_accuracy": round(float(scores.mean()), 4),
        "std_accuracy":  round(float(scores.std()),  4),
        "fold_scores":   [round(float(s), 4) for s in scores],
        "model_type":    model_type,
    }


# ===========================================================================
# Prediction with confidence
# ===========================================================================

def predict_with_confidence(
    feature_row:        pd.DataFrame,
    trained_models:     Dict[str, Pipeline],
    feature_cols:       List[str],
    agreement_threshold: float = 0.60,
) -> Dict:
    """
    Generate an ensemble prediction with a confidence score.

    Confidence is defined as the fraction of models agreeing on the
    predicted direction. A caller should gate on this before acting:

        if pred["agreement"] < 0.60:
            # models disagree — skip or reduce size

    Parameters
    ----------
    feature_row         : Single-row DataFrame of features (latest bar)
    trained_models      : Dict of { model_name: fitted Pipeline }
    feature_cols        : Ordered list of feature names used at training time
    agreement_threshold : Minimum fraction of models that must agree

    Returns
    -------
    {
        "direction":       "up" | "down" | "uncertain"
        "bull_prob":       float   # average predicted P(up)
        "bear_prob":       float   # average predicted P(down)
        "agreement":       float   # fraction of models agreeing
        "n_models":        int
        "above_threshold": bool    # agreement >= agreement_threshold
    }
    """
    available = [c for c in feature_cols if c in feature_row.columns]
    if not available:
        return _uncertain_pred(0)

    X = feature_row[available].copy()

    bull_probs:  List[float] = []
    directions:  List[int]   = []

    for name, pipe in trained_models.items():
        try:
            if hasattr(pipe, "predict_proba"):
                proba = pipe.predict_proba(X)[0]
                # class index 1 = bullish (up)
                p_bull = float(proba[1]) if len(proba) > 1 else 0.5
            else:
                pred   = int(pipe.predict(X)[0])
                p_bull = 1.0 if pred == 1 else 0.0

            bull_probs.append(p_bull)
            directions.append(1 if p_bull >= 0.5 else 0)
        except Exception as e:
            logger.debug(f"Prediction failed for {name}: {e}")

    if not bull_probs:
        return _uncertain_pred(0)

    avg_bull  = float(np.mean(bull_probs))
    avg_bear  = 1.0 - avg_bull
    majority  = 1 if avg_bull >= 0.5 else 0
    agreement = float(np.mean([d == majority for d in directions]))

    if agreement < agreement_threshold:
        direction = "uncertain"
    else:
        direction = "up" if majority == 1 else "down"

    return {
        "direction":       direction,
        "bull_prob":       round(avg_bull, 4),
        "bear_prob":       round(avg_bear, 4),
        "agreement":       round(agreement, 4),
        "n_models":        len(bull_probs),
        "above_threshold": agreement >= agreement_threshold,
    }


def _uncertain_pred(n: int) -> Dict:
    return {
        "direction": "uncertain", "bull_prob": 0.5, "bear_prob": 0.5,
        "agreement": 0.0, "n_models": n, "above_threshold": False,
    }


# ===========================================================================
# Feature importance
# ===========================================================================

def get_feature_importance(
    trained_models: Dict[str, Pipeline],
    feature_cols:   List[str],
) -> Optional[pd.DataFrame]:
    """
    Extract feature importances from tree-based models and coefficients
    from linear models. Returns a ranked DataFrame.

    Columns: feature | logistic_coef | rf_importance | gb_importance | mean_rank
    """
    importance_records: Dict[str, Dict[str, float]] = {}

    for name, pipe in trained_models.items():
        clf = pipe.named_steps.get("clf")
        if clf is None:
            continue

        cols = feature_cols  # assume scaler preserves column order

        try:
            if hasattr(clf, "feature_importances_"):
                imp = clf.feature_importances_
            elif hasattr(clf, "coef_"):
                imp = np.abs(clf.coef_[0])
            else:
                continue

            for feat, val in zip(cols, imp):
                if feat not in importance_records:
                    importance_records[feat] = {}
                importance_records[feat][name] = round(float(val), 6)

        except Exception as e:
            logger.debug(f"Feature importance extraction failed for {name}: {e}")

    if not importance_records:
        return None

    df = pd.DataFrame(importance_records).T.reset_index().rename(columns={"index": "feature"})

    # Compute mean rank across models
    numeric_cols = [c for c in df.columns if c != "feature"]
    for col in numeric_cols:
        df[col] = df[col].fillna(0)

    df["mean_importance"] = df[numeric_cols].mean(axis=1)
    df = df.sort_values("mean_importance", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))

    return df
