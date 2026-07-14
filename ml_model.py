"""
ml_model.py
===========
Machine learning models for directional price prediction.

Models trained:
  - Logistic Regression (directional prediction — fast and interpretable)
  - Random Forest (handles non-linear relationships well)
  - Gradient Boosting (often strongest, slower to train)
  - XGBoost (optional — only used if installed)

Target labels:
  - 1-day direction  (1 = up, 0 = down)
  - 5-day direction
  - 20-day direction

Outputs:
  - Predicted direction
  - Bullish probability
  - Bearish probability
  - Model confidence (agreement between models)

IMPORTANT: ML models trained on price data overfit easily.
We use strict train/test splits and limit features to prevent data leakage.
Never look ahead: all features use only past data.
"""

from __future__ import annotations

import logging
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score
import joblib

warnings.filterwarnings("ignore", category=UserWarning)
logger = logging.getLogger(__name__)

# Try to import XGBoost — it's optional
try:
    from xgboost import XGBClassifier
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    logger.info("XGBoost not installed — will use Gradient Boosting instead")


# ===========================================================================
# Feature columns used for ML (these must all exist in the feature DataFrame)
# ===========================================================================

ML_FEATURE_COLS = [
    # Returns — available for ALL asset classes including forex
    "return_1d", "return_5d", "return_20d", "return_60d", "log_return_1d",
    # Trend
    "dist_sma_20", "dist_sma_50", "dist_sma_200",
    # Momentum (no volume dependency)
    "rsi", "macd", "macd_histogram", "roc", "momentum",
    # Volatility
    "realised_vol", "bb_width", "bb_pct_b", "atr_pct",
    # Regime flags
    "bull_regime", "bear_regime", "high_vol_regime", "low_vol_regime",
    # Volume — optional, only used when present (forex lacks it)
    "volume_trend",
]
ML_MIN_FEATURES = 8  # Skip training if fewer features available


def _prepare_features(df: pd.DataFrame, horizon: int) -> Tuple[pd.DataFrame, pd.Series]:
    """
    Build feature matrix X and target vector y for a given prediction horizon.

    Target: 1 if close(t+horizon) > close(t), else 0.
    Features: all ML_FEATURE_COLS at time t (no lookahead).

    Parameters
    ----------
    df      : Feature-enriched DataFrame for one asset
    horizon : Prediction horizon in days

    Returns
    -------
    (X, y) tuple — may be empty if insufficient data
    """
    # Only use columns that actually exist in this DataFrame
    available_cols = [c for c in ML_FEATURE_COLS if c in df.columns]
    if not available_cols:
        return pd.DataFrame(), pd.Series(dtype=int)

    # Forward return — this is the target we want to predict
    df = df.copy()
    df["target"] = (df["Close"].shift(-horizon) > df["Close"]).astype(int)

    # Drop rows with any NaN in features or target
    data = df[available_cols + ["target"]].dropna()

    if len(data) < 60:
        return pd.DataFrame(), pd.Series(dtype=int)

    X = data[available_cols]
    y = data["target"]
    return X, y


def build_model_pipeline(model_type: str = "random_forest") -> Pipeline:
    """
    Build a sklearn Pipeline with StandardScaler + classifier.

    All numeric models benefit from scaling even if they don't strictly need it.
    Using a Pipeline prevents data leakage during cross-validation.
    """
    if model_type == "logistic":
        clf = LogisticRegression(max_iter=500, class_weight="balanced", C=0.1)

    elif model_type == "random_forest":
        clf = RandomForestClassifier(
            n_estimators=100,
            max_depth=5,           # Shallow trees reduce overfitting
            min_samples_leaf=10,   # Require meaningful sample size
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
        # Fallback to logistic regression
        logger.warning(f"Unknown model type '{model_type}', using logistic regression")
        clf = LogisticRegression(max_iter=500, class_weight="balanced")

    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    clf),
    ])
