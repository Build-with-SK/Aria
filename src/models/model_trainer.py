# src/models/model_trainer.py
"""
Model Trainer — Phase 6 ML Engine
Orchestrates: feature engineering → target building → purged walk-forward CV
→ stacked ensemble → per-regime weighting → versioned saves → Obsidian export.
"""

from __future__ import annotations
import logging
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd
import joblib

logger = logging.getLogger(__name__)

_MODEL_VERSION = "v6.0"


# ─────────────────────────────────────────────────────────────────────────────
# ModelRegistry — versioned checkpoint management
# ─────────────────────────────────────────────────────────────────────────────

class ModelRegistry:
    """Versioned model storage. Keeps last N checkpoints per ticker/horizon."""

    def __init__(self, base_dir: str = "data/models", keep_versions: int = 3):
        self.base_dir      = Path(base_dir)
        self.keep_versions = keep_versions
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, ticker: str, horizon: int, ensemble, metadata: dict):
        tag  = datetime.now().strftime("%Y%m%d_%H%M")
        name = f"{ticker}_{horizon}d_{tag}.joblib"
        path = self.base_dir / name
        ensemble.save(str(path))

        # Save metadata sidecar
        meta_path = self.base_dir / f"{ticker}_{horizon}d_{tag}.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, default=str)

        # Prune old versions
        self._prune(ticker, horizon)
        logger.debug(f"Saved model: {name}")
        return str(path)

    def load_latest(self, ticker: str, horizon: int):
        """Load the most recent saved ensemble for ticker/horizon."""
        pattern = f"{ticker}_{horizon}d_*.joblib"
        matches = sorted(self.base_dir.glob(pattern), reverse=True)
        if not matches:
            return None, None
        path = matches[0]
        from src.models.ensemble_model import EnsembleTrainer
        ensemble = EnsembleTrainer({})
        if ensemble.load(str(path)):
            meta_path = str(path).replace(".joblib", ".json")
            meta = {}
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            return ensemble, meta
        return None, None

    def _prune(self, ticker: str, horizon: int):
        """Keep only the N most recent versions."""
        pattern = f"{ticker}_{horizon}d_*.joblib"
        matches = sorted(self.base_dir.glob(pattern), reverse=True)
        for old in matches[self.keep_versions:]:
            old.unlink(missing_ok=True)
            meta = str(old).replace(".joblib", ".json")
            if os.path.exists(meta):
                os.remove(meta)


# ─────────────────────────────────────────────────────────────────────────────
# ModelTrainer — main class
# ─────────────────────────────────────────────────────────────────────────────

class ModelTrainer:
    """
    Trains stacked ensemble models per ticker per horizon.
    Integrates feature engine, target builder, purged CV.
    """

    def __init__(self, config: dict):
        self.config   = config
        ml_cfg        = config.get("ml", {})
        self.horizons = list(ml_cfg.get("prediction_horizons", [1, 5, 20]))
        self.min_rows = int(ml_cfg.get("min_training_rows", 120))
        self.cv_splits= int(ml_cfg.get("cv_splits", 5))
        self.target_mode = str(ml_cfg.get("target_mode", "direction"))
        self.use_triple_barrier = bool(ml_cfg.get("use_triple_barrier", False))
        self.use_risk_adjusted  = bool(ml_cfg.get("use_risk_adjusted", True))
        self.registry = ModelRegistry(
            base_dir=ml_cfg.get("model_dir", "data/models"),
            keep_versions=int(ml_cfg.get("keep_versions", 3)),
        )
        self._results: Dict[str, Dict[int, Any]] = {}  # ticker → horizon → EnsembleResult

    def train_and_predict_all(
        self,
        featured_data: Dict[str, pd.DataFrame],
        macro_snapshot=None,
        sentiment_results: Optional[dict] = None,
    ) -> Dict[str, Dict[int, Any]]:
        """
        Train + predict for every equity ticker in featured_data.
        Returns {ticker: {horizon: EnsembleResult}}.
        """
        from src.models.feature_engine import build_features, get_feature_names
        from src.models.targets import build_targets, get_target_col
        from src.models.walk_forward import PurgedWalkForwardCV
        from src.models.ensemble_model import EnsembleTrainer

        skip_suffixes = ["=X", "-USD", "=F"]
        skip_tickers  = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO",
                         "TLT", "IEF", "AGG", "HYG", "LQD", "JNK",
                         "VCIT", "BKLN", "SJNK", "SHYG", "CWB", "TIP"}

        equity_tickers = [
            t for t in featured_data
            if not any(t.endswith(s) for s in skip_suffixes)
            and t not in skip_tickers
            and featured_data[t] is not None
            and len(featured_data[t]) >= self.min_rows
        ]

        logger.info(f"ML Engine: training on {len(equity_tickers)} equities × {len(self.horizons)} horizons")

        for ticker in equity_tickers:
            df = featured_data[ticker]
            self._results[ticker] = {}

            # Build features
            try:
                features = build_features(
                    ticker=ticker,
                    df=df,
                    all_data=featured_data,
                    macro_snapshot=macro_snapshot,
                    sentiment_results=sentiment_results,
                    lag=1,
                )
                if features.empty or len(features) < self.min_rows:
                    logger.debug(f"{ticker}: insufficient features ({len(features)} rows)")
                    continue
            except Exception as e:
                logger.warning(f"{ticker}: feature build failed: {e}")
                continue

            # Build targets
            try:
                df_with_targets = build_targets(
                    df,
                    horizons=self.horizons,
                    use_triple_barrier=self.use_triple_barrier,
                    use_risk_adjusted=self.use_risk_adjusted,
                )
            except Exception as e:
                logger.warning(f"{ticker}: target build failed: {e}")
                continue

            # Train per horizon
            for horizon in self.horizons:
                try:
                    result = self._train_one(
                        ticker=ticker,
                        horizon=horizon,
                        features=features,
                        df_with_targets=df_with_targets,
                    )
                    if result:
                        self._results[ticker][horizon] = result
                except Exception as e:
                    logger.debug(f"{ticker} h={horizon}: {e}")

            if self._results[ticker]:
                h1 = self.horizons[0]
                r  = self._results[ticker].get(h1)
                if r:
                    logger.info(
                        f"  ✓ {ticker}: pred={r.prediction:+d} "
                        f"P(up)={r.probability_up:.2f} "
                        f"conf={r.confidence:.0f} "
                        f"cv={r.cv_mean_score:.3f} "
                        f"regime={r.regime}"
                    )

        logger.info(f"ML Engine complete: {len(self._results)} tickers trained.")
        return self._results

    def _train_one(
        self,
        ticker: str,
        horizon: int,
        features: pd.DataFrame,
        df_with_targets: pd.DataFrame,
    ):
        """Train ensemble for one ticker/horizon pair."""
        from src.models.targets import get_target_col
        from src.models.walk_forward import PurgedWalkForwardCV
        from src.models.ensemble_model import EnsembleTrainer
        from src.models.feature_engine import get_feature_names

        target_col = get_target_col(horizon, self.target_mode)
        if target_col not in df_with_targets.columns:
            return None

        # Align features and targets
        y = df_with_targets[target_col].reindex(features.index)
        feat_cols = get_feature_names(features)
        X = features[feat_cols]

        # Drop rows where target is NaN (last `horizon` rows always NaN)
        valid = y.notna() & X.notna().any(axis=1)
        X_clean = X[valid]
        y_clean = y[valid]

        if len(X_clean) < self.min_rows:
            return None

        # Check if we have a saved model from today
        ensemble, cached_meta = self.registry.load_latest(ticker, horizon)
        if ensemble and cached_meta:
            cached_date = str(cached_meta.get("trained_on", ""))[:10]
            today = datetime.now().strftime("%Y-%m-%d")
            if cached_date == today:
                logger.debug(f"{ticker} h={horizon}: using cached model from today")
                return ensemble.predict(X, ticker, horizon, _MODEL_VERSION)

        # Train fresh
        cv = PurgedWalkForwardCV(
            n_splits=self.cv_splits,
            embargo_pct=0.01,
            min_train_size=self.min_rows,
            expanding=True,
        )

        ensemble = EnsembleTrainer(self.config)
        cv_score = ensemble.train(X_clean, y_clean, cv, horizon)

        # Save to registry
        metadata = {
            "ticker":       ticker,
            "horizon":      horizon,
            "cv_score":     cv_score,
            "n_rows":       len(X_clean),
            "n_features":   len(feat_cols),
            "target_mode":  self.target_mode,
            "trained_on":   datetime.now().isoformat(),
            "model_version": _MODEL_VERSION,
        }
        self.registry.save(ticker, horizon, ensemble, metadata)

        # Generate prediction on most recent features
        return ensemble.predict(X, ticker, horizon, _MODEL_VERSION)

    # ── Convenience accessors ──────────────────────────────────────────────

    def get_signal_scores(self) -> Dict[str, float]:
        """
        Convert ML predictions to signal scores [-100, +100].
        Combines all horizons with recency weighting (1d > 5d > 20d).
        Used to feed back into the main signal pipeline.
        """
        weights = {1: 0.50, 5: 0.30, 20: 0.20}
        scores  = {}

        for ticker, horizon_results in self._results.items():
            weighted_score = 0.0
            total_weight   = 0.0
            for horizon, result in horizon_results.items():
                if result is None:
                    continue
                w = weights.get(horizon, 0.10)
                # Convert probability_up to score: P(up)=0.7 → +40, P(up)=0.3 → -40
                raw = (result.regime_adjusted_prob - 0.5) * 200
                weighted_score += w * raw
                total_weight   += w

            if total_weight > 0:
                scores[ticker] = round(float(weighted_score / total_weight), 2)

        return scores

    def get_top_predictions(self, n: int = 10, direction: str = "long") -> List[dict]:
        """Return top N long or short ML predictions with full detail."""
        all_preds = []
        for ticker, horizon_results in self._results.items():
            result = horizon_results.get(1) or horizon_results.get(5)
            if result is None:
                continue
            all_preds.append({
                "ticker":      ticker,
                "prediction":  result.prediction,
                "prob_up":     result.probability_up,
                "confidence":  result.confidence,
                "regime":      result.regime,
                "cv_score":    result.cv_mean_score,
                "adj_prob":    result.regime_adjusted_prob,
            })

        if direction == "long":
            sorted_preds = sorted(all_preds, key=lambda x: x["adj_prob"], reverse=True)
        else:
            sorted_preds = sorted(all_preds, key=lambda x: x["adj_prob"])

        return sorted_preds[:n]

    def summary_table(self) -> pd.DataFrame:
        """Return a DataFrame summary of all ML predictions."""
        rows = []
        for ticker, horizon_results in self._results.items():
            for horizon, result in horizon_results.items():
                if result is None:
                    continue
                rows.append({
                    "ticker":    ticker,
                    "horizon":   horizon,
                    "pred":      result.prediction,
                    "prob_up":   result.probability_up,
                    "adj_prob":  result.regime_adjusted_prob,
                    "confidence":result.confidence,
                    "regime":    result.regime,
                    "cv_score":  result.cv_mean_score,
                })
        return pd.DataFrame(rows).sort_values("adj_prob", ascending=False)

    def to_obsidian_note(self) -> str:
        """Generate Obsidian-formatted ML model status note."""
        now   = datetime.now().strftime("%Y-%m-%d %H:%M")
        today = datetime.now().strftime("%Y-%m-%d")
        df    = self.summary_table()

        lines = [
            f"# ML Model Status — {today}",
            f"",
            f"#trading #market-intelligence #capitalwithsk #model-status",
            f"",
            f"> **Updated:** {now} | **Model:** {_MODEL_VERSION} | "
            f"**Tickers:** {len(self._results)} | **Engine:** Stacked Ensemble",
            f"",
            f"---",
            f"",
            f"## 🟢 Top Long Signals (ML)",
            f"",
            f"| Ticker | P(Up) | Confidence | Regime | CV Score | Horizon |",
            f"|--------|-------|------------|--------|----------|---------|",
        ]

        top_longs = self.get_top_predictions(10, "long")
        for p in top_longs:
            prob_bar = "█" * int(p["adj_prob"] * 10) + "░" * (10 - int(p["adj_prob"] * 10))
            lines.append(
                f"| **{p['ticker']}** | {prob_bar} {p['adj_prob']:.2f} | "
                f"{p['confidence']:.0f} | {p['regime']} | "
                f"{p['cv_score']:.3f} | 1d |"
            )

        lines += [
            f"",
            f"## 🔴 Top Short Signals (ML)",
            f"",
            f"| Ticker | P(Down) | Confidence | Regime | CV Score |",
            f"|--------|---------|------------|--------|----------|",
        ]

        top_shorts = self.get_top_predictions(10, "short")
        for p in top_shorts:
            prob_dn = 1 - p["adj_prob"]
            lines.append(
                f"| **{p['ticker']}** | {prob_dn:.2f} | "
                f"{p['confidence']:.0f} | {p['regime']} | {p['cv_score']:.3f} |"
            )

        # Regime distribution
        all_regimes = [
            r.get("regime", "neutral")
            for hr in self._results.values()
            for r in hr.values() if r
        ]
        if all_regimes:
            from collections import Counter
            regime_counts = Counter(all_regimes)
            lines += [
                f"",
                f"## 📊 Regime Distribution",
                f"",
            ]
            for regime, count in regime_counts.most_common():
                lines.append(f"- **{regime}**: {count} tickers")

        lines += [
            f"",
            f"---",
            f"",
            f"*Auto-generated by TIS ML Engine {_MODEL_VERSION} | {now}*",
        ]

        return "\n".join(lines)
