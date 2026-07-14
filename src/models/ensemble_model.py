# src/models/ensemble_model.py
"""
Stacked Ensemble Model
Base learners: XGBoost, LightGBM, RandomForest, GradientBoosting, Ridge
Meta-learner: ElasticNet on OOF predictions
Platt scaling for probability calibration.
Per-regime model weighting.
"""

from __future__ import annotations
import logging
import os
import joblib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import accuracy_score, roc_auc_score, f1_score
from sklearn.impute import SimpleImputer

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Base learner definitions
# ─────────────────────────────────────────────────────────────────────────────

def _make_base_learners(config: dict) -> Dict[str, object]:
    """Instantiate all base learner pipelines."""
    learners = {}

    # Random Forest
    learners["random_forest"] = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("model",   RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=20,
            max_features="sqrt", n_jobs=-1, random_state=42,
            class_weight="balanced",
        )),
    ])

    # Gradient Boosting
    learners["gradient_boost"] = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("model",   GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, min_samples_leaf=20, random_state=42,
        )),
    ])

    # Logistic Regression (linear baseline — good for stability)
    learners["logistic"] = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("model",   LogisticRegression(
            C=0.1, max_iter=500, solver="saga",
            class_weight="balanced", random_state=42,
        )),
    ])

    # XGBoost (optional — graceful fallback if not installed)
    try:
        from xgboost import XGBClassifier
        learners["xgboost"] = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler",  StandardScaler()),
            ("model",   XGBClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                min_child_weight=20, eval_metric="logloss",
                use_label_encoder=False, random_state=42,
                verbosity=0, n_jobs=-1,
            )),
        ])
    except ImportError:
        logger.info("XGBoost not installed — skipping.")

    # LightGBM (optional)
    try:
        from lightgbm import LGBMClassifier
        learners["lightgbm"] = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler",  StandardScaler()),
            ("model",   LGBMClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                min_child_samples=20, random_state=42,
                verbose=-1, n_jobs=-1,
            )),
        ])
    except ImportError:
        logger.info("LightGBM not installed — skipping.")

    return learners


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EnsembleResult:
    ticker: str
    horizon: int
    prediction: int             # +1, 0, -1
    probability_up: float       # calibrated P(up)
    confidence: float           # 0-100
    base_learner_probs: Dict[str, float]   # each learner's P(up)
    meta_score: float           # meta-learner raw score
    regime: str                 # "bull" | "bear" | "high_vol" | "neutral"
    regime_adjusted_prob: float
    cv_mean_score: float
    feature_importances: Dict[str, float]  # top features
    model_version: str
    notes: List[str] = field(default_factory=list)

    def _to_json(self) -> dict:
        return {
            "ticker": self.ticker,
            "horizon": self.horizon,
            "prediction": self.prediction,
            "probability_up": round(self.probability_up, 4),
            "confidence": round(self.confidence, 1),
            "base_learner_probs": {k: round(v, 4) for k, v in self.base_learner_probs.items()},
            "meta_score": round(self.meta_score, 4),
            "regime": self.regime,
            "regime_adjusted_prob": round(self.regime_adjusted_prob, 4),
            "cv_mean_score": round(self.cv_mean_score, 4),
            "feature_importances": {k: round(v, 6) for k, v in list(self.feature_importances.items())[:20]},
            "model_version": self.model_version,
            "notes": self.notes,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Regime detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_regime(features: pd.DataFrame) -> str:
    """Detect current market regime from features."""
    try:
        last = features.iloc[-1]
        bull   = float(last.get("bull_market", 0))
        hi_vol = float(last.get("high_vol_regime", 0))
        gc     = float(last.get("golden_cross", 0))

        if hi_vol:
            return "high_vol"
        elif bull and gc:
            return "bull"
        elif not bull and not gc:
            return "bear"
        else:
            return "neutral"
    except Exception:
        return "neutral"


def _regime_adjust(prob: float, regime: str, base_regime: str = "neutral") -> float:
    """
    Adjust probability based on regime.
    In bull market: shift probabilities slightly toward up.
    In bear market: shift toward down.
    In high_vol: widen uncertainty toward 0.5.
    """
    adjustment = {
        "bull":     0.03,   # +3% toward up
        "bear":    -0.03,   # +3% toward down
        "high_vol": 0.0,    # no adjustment — compress toward 0.5
        "neutral":  0.0,
    }
    adj = adjustment.get(regime, 0.0)
    if regime == "high_vol":
        return 0.5 + (prob - 0.5) * 0.7   # compress by 30%
    return float(np.clip(prob + adj, 0.05, 0.95))


# ─────────────────────────────────────────────────────────────────────────────
# Stacking
# ─────────────────────────────────────────────────────────────────────────────

def _train_meta_learner(oof_matrix: pd.DataFrame, y: pd.Series) -> object:
    """
    Train ElasticNet meta-learner on OOF predictions from base learners.
    Uses logistic regression (ElasticNet penalty) for binary classification.
    """
    mask = y.notna() & oof_matrix.notna().all(axis=1)
    if mask.sum() < 30:
        return None

    meta = Pipeline([
        ("imputer", SimpleImputer(strategy="mean")),
        ("model",   LogisticRegression(
            C=1.0, penalty="elasticnet", solver="saga",
            l1_ratio=0.5, max_iter=300, random_state=42,
        )),
    ])
    try:
        meta.fit(oof_matrix[mask], y[mask])
        return meta
    except Exception as e:
        logger.debug(f"Meta-learner training failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main ensemble trainer
# ─────────────────────────────────────────────────────────────────────────────

class EnsembleTrainer:
    """
    Full stacked ensemble pipeline.
    Call train() to fit, predict() to get EnsembleResult.
    """

    def __init__(self, config: dict):
        self.config    = config
        self.learners  = _make_base_learners(config)
        self.meta      = None
        self.scaler    = StandardScaler()
        self._fitted   = False
        self._cv_score = 0.0
        self._fi: Dict[str, float] = {}
        self._fitted_learners: Dict[str, object] = {}

    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        cv,         # PurgedWalkForwardCV instance
        horizon: int = 1,
    ) -> float:
        """
        Full training pipeline:
        1. Purged walk-forward OOF for each base learner
        2. Train meta-learner on OOF predictions
        3. Refit all base learners on full dataset
        4. Compute CV score

        Returns mean CV accuracy.
        """
        from src.models.walk_forward import compute_oof_predictions, mean_fold_score

        # Clean data
        mask = y.notna() & X.notna().all(axis=1)
        Xc   = X[mask].copy()
        yc   = y[mask].copy()

        if len(Xc) < cv.min_train_size:
            logger.warning(f"Insufficient data: {len(Xc)} rows")
            return 0.0

        # Binary target for OOF (predict_proba needs 0/1)
        y_binary = (yc > 0).astype(int)

        # Step 1: OOF predictions from each base learner
        oof_cols   = {}
        fold_scores = []

        for name, learner in self.learners.items():
            logger.debug(f"  OOF: {name}")
            oof_preds, folds = compute_oof_predictions(
                learner, Xc, y_binary, cv, horizon, predict_proba=True
            )
            oof_cols[name] = oof_preds
            if folds:
                fold_scores.extend([f.score for f in folds])
                # Accumulate feature importances
                from src.models.walk_forward import aggregate_feature_importance
                fi = aggregate_feature_importance(folds)
                for feat, imp in fi.items():
                    self._fi[feat] = self._fi.get(feat, 0.0) + float(imp)

        self._cv_score = float(np.mean(fold_scores)) if fold_scores else 0.0

        # Step 2: Build OOF matrix and train meta-learner
        oof_matrix = pd.DataFrame(oof_cols, index=Xc.index)
        self.meta  = _train_meta_learner(oof_matrix, y_binary)

        # Step 3: Refit all base learners on FULL dataset
        for name, learner in self.learners.items():
            try:
                from sklearn.base import clone
                fitted = clone(learner)
                fitted.fit(Xc, y_binary)
                self._fitted_learners[name] = fitted
            except Exception as e:
                logger.debug(f"  Refit {name} failed: {e}")

        # Normalise feature importances
        if self._fi:
            total = sum(self._fi.values())
            if total > 0:
                self._fi = {k: v / total for k, v in self._fi.items()}

        self._fitted = True
        logger.info(f"  Ensemble trained. CV score: {self._cv_score:.4f}, "
                    f"base learners: {list(self._fitted_learners.keys())}")
        return self._cv_score

    def predict(
        self,
        X_latest: pd.DataFrame,
        ticker: str,
        horizon: int,
        model_version: str = "v1",
    ) -> Optional[EnsembleResult]:
        """
        Generate prediction for the most recent row of features.
        Returns EnsembleResult with calibrated probability.
        """
        if not self._fitted or not self._fitted_learners:
            return None

        try:
            # Use the last non-NaN row
            valid_rows = X_latest.dropna()
            if valid_rows.empty:
                return None

            X_pred = valid_rows.iloc[[-1]]   # single row

            # Base learner probabilities
            base_probs: Dict[str, float] = {}
            for name, learner in self._fitted_learners.items():
                try:
                    if hasattr(learner, "predict_proba"):
                        prob = float(learner.predict_proba(X_pred)[0, 1])
                    else:
                        prob = float(learner.predict(X_pred)[0])
                    base_probs[name] = round(prob, 4)
                except Exception:
                    base_probs[name] = 0.5

            # Meta-learner prediction
            if self.meta is not None:
                oof_row = pd.DataFrame([base_probs])
                try:
                    meta_prob = float(self.meta.predict_proba(oof_row)[0, 1])
                except Exception:
                    meta_prob = float(np.mean(list(base_probs.values())))
            else:
                meta_prob = float(np.mean(list(base_probs.values())))

            # Regime detection and adjustment
            regime = _detect_regime(X_latest)
            adj_prob = _regime_adjust(meta_prob, regime)

            # Direction prediction
            if adj_prob > 0.55:
                prediction = 1
            elif adj_prob < 0.45:
                prediction = -1
            else:
                prediction = 0

            # Confidence: distance from 0.5, scaled to 0-100
            confidence = float(abs(adj_prob - 0.5) * 200)

            # Top features by importance
            top_fi = dict(sorted(self._fi.items(), key=lambda x: x[1], reverse=True)[:15])

            return EnsembleResult(
                ticker=ticker,
                horizon=horizon,
                prediction=prediction,
                probability_up=round(meta_prob, 4),
                confidence=round(confidence, 1),
                base_learner_probs=base_probs,
                meta_score=round(meta_prob, 4),
                regime=regime,
                regime_adjusted_prob=round(adj_prob, 4),
                cv_mean_score=round(self._cv_score, 4),
                feature_importances=top_fi,
                model_version=model_version,
                notes=[f"Base learners: {len(base_probs)} | Regime: {regime}"],
            )

        except Exception as e:
            logger.debug(f"Prediction failed for {ticker}: {e}")
            return None

    def save(self, path: str):
        """Persist the fitted ensemble to disk."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump({
            "fitted_learners": self._fitted_learners,
            "meta":            self.meta,
            "cv_score":        self._cv_score,
            "fi":              self._fi,
        }, path)
        logger.debug(f"Ensemble saved: {path}")

    def load(self, path: str) -> bool:
        """Load a previously saved ensemble."""
        try:
            data = joblib.load(path)
            self._fitted_learners = data["fitted_learners"]
            self.meta             = data["meta"]
            self._cv_score        = data["cv_score"]
            self._fi              = data["fi"]
            self._fitted          = True
            return True
        except Exception as e:
            logger.debug(f"Load failed: {e}")
            return False
