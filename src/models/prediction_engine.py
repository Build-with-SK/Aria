"""
prediction_engine.py
====================
Uses trained ML models to generate predictions for each asset.

For each asset and each horizon (1d, 5d, 20d), this module:
  - Loads the feature vector from the latest row
  - Runs inference through each trained model
  - Aggregates predictions into a consensus (ensemble)
  - Returns bullish probability, bearish probability, and confidence

Output: ML predictions per ticker, saved to data/ml_predictions.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.models.ml_model import ML_FEATURE_COLS
from src.models.model_trainer import load_models

logger = logging.getLogger(__name__)


@dataclass
class HorizonPrediction:
    """ML prediction for one asset at one time horizon."""
    horizon_days:    int
    bullish_prob:    float       # 0.0 to 1.0
    bearish_prob:    float       # 0.0 to 1.0
    direction:       str         # "Bullish" or "Bearish"
    confidence:      str         # "Low" / "Medium" / "High"
    model_agreement: float       # 0.0 to 1.0 (1 = all models agree)
    per_model_probs: Dict[str, float] = field(default_factory=dict)


@dataclass
class AssetPrediction:
    """All ML predictions for one asset."""
    ticker:          str
    predictions:     Dict[int, HorizonPrediction]   # {1: ..., 5: ..., 20: ...}
    overall_bullish: float   # Weighted average bullish prob across horizons
    overall_signal:  str     # "Bullish" / "Bearish" / "Neutral"
    models_trained:  bool    # False if no models were found
    warning:         str = ""


def _get_latest_features(df: pd.DataFrame, feature_cols: List[str]) -> Optional[pd.DataFrame]:
    """
    Extract the latest complete feature row from a DataFrame.
    Returns None if the row has too many NaN values.
    """
    available = [c for c in feature_cols if c in df.columns]
    if not available:
        return None

    row = df[available].dropna(how="all").tail(1)
    if row.empty:
        return None

    # Fill remaining NaN with column median (conservative imputation)
    row = row.fillna(df[available].median())
    return row


def predict_single_asset(
    ticker:       str,
    df:           pd.DataFrame,
    horizons:     List[int] = None,
) -> AssetPrediction:
    """
    Generate ML predictions for one asset using pre-trained models.

    Parameters
    ----------
    ticker   : Asset ticker
    df       : Feature-enriched DataFrame
    horizons : List of horizon days to predict (default: [1, 5, 20])

    Returns
    -------
    AssetPrediction — always returns a result; models_trained=False if no models
    """
    if horizons is None:
        horizons = [1, 5, 20]

    # Load saved models
    asset_models = load_models(ticker)

    if not asset_models:
        return AssetPrediction(
            ticker=ticker,
            predictions={},
            overall_bullish=0.5,
            overall_signal="Neutral",
            models_trained=False,
            warning="No trained models found. Run python main.py first.",
        )

    predictions: Dict[int, HorizonPrediction] = {}

    for horizon in horizons:
        if horizon not in asset_models:
            continue

        horizon_data = asset_models[horizon]
        feature_cols = horizon_data.get("feature_cols", ML_FEATURE_COLS)
        models_dict  = horizon_data.get("models", {})

        if not models_dict:
            continue

        # Get latest features
        X_latest = _get_latest_features(df, feature_cols)
        if X_latest is None:
            logger.warning(f"{ticker} h={horizon}: Could not extract features")
            continue

        # Run each model
        per_model_probs: Dict[str, float] = {}

        for model_name, pipeline in models_dict.items():
            try:
                proba = pipeline.predict_proba(X_latest)[0]
                # proba[1] = probability of class 1 = "up"
                per_model_probs[model_name] = float(round(proba[1], 4))
            except Exception as e:
                logger.debug(f"{ticker} h={horizon} {model_name}: {e}")

        if not per_model_probs:
            continue

        # Ensemble: simple average of all model probabilities
        bull_prob = float(np.mean(list(per_model_probs.values())))
        bear_prob = 1.0 - bull_prob

        # Confidence from model agreement
        # std dev of probabilities: low std = high agreement
        std_dev = float(np.std(list(per_model_probs.values()))) if len(per_model_probs) > 1 else 0.0
        model_agreement = max(0.0, 1.0 - std_dev * 4)

        if abs(bull_prob - 0.5) < 0.05:
            confidence = "Low"
        elif abs(bull_prob - 0.5) < 0.12 or model_agreement < 0.5:
            confidence = "Medium"
        else:
            confidence = "High"

        direction = "Bullish" if bull_prob > 0.5 else "Bearish"

        predictions[horizon] = HorizonPrediction(
            horizon_days=horizon,
            bullish_prob=round(bull_prob, 4),
            bearish_prob=round(bear_prob, 4),
            direction=direction,
            confidence=confidence,
            model_agreement=round(model_agreement, 4),
            per_model_probs=per_model_probs,
        )

    # Overall signal: weight short horizons more
    if not predictions:
        overall_bull = 0.5
        overall_sig  = "Neutral"
    else:
        weights  = {1: 0.5, 5: 0.35, 20: 0.15}
        weighted_sum = sum(
            predictions[h].bullish_prob * weights.get(h, 0.33)
            for h in predictions
        )
        total_weight = sum(weights.get(h, 0.33) for h in predictions)
        overall_bull = weighted_sum / total_weight if total_weight > 0 else 0.5
        overall_sig  = "Bullish" if overall_bull > 0.55 else "Bearish" if overall_bull < 0.45 else "Neutral"

    return AssetPrediction(
        ticker=ticker,
        predictions=predictions,
        overall_bullish=round(overall_bull, 4),
        overall_signal=overall_sig,
        models_trained=True,
    )


def predict_all_assets(
    featured_data: Dict[str, pd.DataFrame],
    config:        dict,
) -> Dict[str, AssetPrediction]:
    """
    Generate predictions for all assets.

    Returns
    -------
    Dict: { ticker: AssetPrediction }
    """
    horizons = config.get("ml", {}).get("prediction_horizons", [1, 5, 20])
    all_preds: Dict[str, AssetPrediction] = {}

    for ticker, df in featured_data.items():
        if df is None or df.empty:
            continue
        try:
            pred = predict_single_asset(ticker, df, horizons)
            all_preds[ticker] = pred
        except Exception as e:
            logger.error(f"Prediction failed for {ticker}: {e}", exc_info=True)

    logger.info(f"Generated predictions for {len(all_preds)} assets")
    return all_preds


def predictions_to_json(all_preds: Dict[str, AssetPrediction]) -> dict:
    """Serialise predictions to JSON-safe dict."""
    out = {}
    for ticker, pred in all_preds.items():
        horizons_out = {}
        for h, hp in pred.predictions.items():
            horizons_out[str(h)] = {
                "horizon_days":    hp.horizon_days,
                "bullish_prob":    hp.bullish_prob,
                "bearish_prob":    hp.bearish_prob,
                "direction":       hp.direction,
                "confidence":      hp.confidence,
                "model_agreement": hp.model_agreement,
                "per_model_probs": hp.per_model_probs,
            }
        out[ticker] = {
            "ticker":          pred.ticker,
            "horizons":        horizons_out,
            "overall_bullish": pred.overall_bullish,
            "overall_signal":  pred.overall_signal,
            "models_trained":  pred.models_trained,
            "warning":         pred.warning,
        }
    return out
