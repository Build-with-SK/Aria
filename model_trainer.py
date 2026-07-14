"""
model_trainer.py
================
Trains ML models for each asset and each prediction horizon.

One model set per ticker: Logistic, Random Forest, Gradient Boosting.
Each model set is trained on 80% of available data and tested on the
remaining 20% (chronological split — no shuffling to avoid lookahead bias).

Models are saved to disk with joblib so they can be reloaded without
retraining on every dashboard refresh.

Usage:
    from src.models.model_trainer import train_all_models, load_models
    models = train_all_models(featured_data, config)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import accuracy_score

from src.models.ml_model import (
    ML_FEATURE_COLS,
    ML_MIN_FEATURES,
    XGBOOST_AVAILABLE,
    _prepare_features,
    build_model_pipeline,
)

logger = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")


def train_single_asset(
    ticker:   str,
    df:       pd.DataFrame,
    horizons: list,
    train_split: float = 0.80,
    min_rows:    int   = 120,
) -> Dict:
    """
    Train models for a single asset across all horizons.

    Returns a nested dict:
    {
        horizon_1: {
            "logistic":          trained Pipeline,
            "random_forest":     trained Pipeline,
            "gradient_boosting": trained Pipeline,
            "xgboost":           trained Pipeline (if available),
            "feature_cols":      list of feature column names,
            "test_accuracy":     dict of model_name → accuracy,
            "n_train":           int,
            "n_test":            int,
        },
        ...
    }
    """
    result = {}

    for horizon in horizons:
        X, y = _prepare_features(df, horizon)

        if X.empty or len(X) < min_rows:
            logger.warning(
                f"{ticker} h={horizon}: only {len(X)} rows, need {min_rows}. Skipping."
            )
            continue

        # Skip if too few feature columns available (e.g. forex with no volume)
        if X.shape[1] < ML_MIN_FEATURES:
            logger.warning(f"{ticker} h={horizon}: only {X.shape[1]} features, need {ML_MIN_FEATURES}. Skipping.")
            continue

        # Chronological train/test split
        split_idx = int(len(X) * train_split)
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

        if len(X_train) < 50 or len(X_test) < 10:
            logger.warning(f"{ticker} h={horizon}: train/test sets too small. Skipping.")
            continue

        model_types = ["logistic", "random_forest", "gradient_boosting"]
        if XGBOOST_AVAILABLE:
            model_types.append("xgboost")

        trained_models = {}
        test_accuracies = {}

        for model_type in model_types:
            try:
                pipe = build_model_pipeline(model_type)
                pipe.fit(X_train, y_train)
                y_pred = pipe.predict(X_test)
                acc = float(round(accuracy_score(y_test, y_pred), 4))
                trained_models[model_type]    = pipe
                test_accuracies[model_type]   = acc
                logger.debug(f"{ticker} h={horizon} {model_type}: test acc={acc:.3f}")
            except Exception as e:
                logger.warning(f"{ticker} h={horizon} {model_type} failed: {e}")

        if not trained_models:
            continue

        result[horizon] = {
            "models":       trained_models,
            "feature_cols": [c for c in ML_FEATURE_COLS if c in X.columns],
            "test_accuracy": test_accuracies,
            "n_train":      len(X_train),
            "n_test":       len(X_test),
        }

    return result


def train_all_models(
    featured_data: Dict[str, pd.DataFrame],
    config:        dict,
) -> Dict[str, Dict]:
    """
    Train models for every asset in featured_data.

    Parameters
    ----------
    featured_data : Dict of feature-enriched DataFrames keyed by ticker
    config        : Full config dict (reads ml.prediction_horizons etc.)

    Returns
    -------
    Dict: { ticker: { horizon: { model_name: pipeline, ... } } }
    """
    ml_cfg    = config.get("ml", {})
    horizons  = ml_cfg.get("prediction_horizons", [1, 5, 20])
    min_rows  = ml_cfg.get("min_training_rows", 120)
    split     = ml_cfg.get("train_test_split", 0.80)

    all_models: Dict[str, Dict] = {}
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    for ticker, df in featured_data.items():
        if df is None or df.empty:
            continue

        logger.info(f"Training models for {ticker}...")
        try:
            asset_models = train_single_asset(
                ticker, df, horizons,
                train_split=split,
                min_rows=min_rows,
            )
            if asset_models:
                all_models[ticker] = asset_models
                # Save to disk
                safe_name = ticker.replace("=", "_").replace("^", "_").replace("-", "_")
                model_path = MODEL_DIR / f"{safe_name}_models.joblib"
                joblib.dump(asset_models, model_path)
        except Exception as e:
            logger.error(f"Model training failed for {ticker}: {e}", exc_info=True)

    logger.info(f"Models trained for {len(all_models)} / {len(featured_data)} assets")
    return all_models


def load_models(ticker: str) -> Optional[Dict]:
    """
    Load pre-trained models from disk for one ticker.
    Returns None if no saved models found.
    """
    safe_name  = ticker.replace("=", "_").replace("^", "_").replace("-", "_")
    model_path = MODEL_DIR / f"{safe_name}_models.joblib"

    if not model_path.exists():
        return None

    try:
        return joblib.load(model_path)
    except Exception as e:
        logger.warning(f"Could not load models for {ticker}: {e}")
        return None
