"""
src/intelligence/recorders.py
=============================
Recording, at creation, predictions that used to leave no falsifiable trace.

The ML stacked ensemble (src/models) writes data/ml_predictions.json and
nothing else: no row of it has ever been graded, so nothing can be said about
whether it works. `record_ml_predictions` writes each (ticker, horizon) call
into the ledger the moment the pipeline produces it, with:

    probability_type  SCORE — an uncalibrated classifier's P(up), shifted by
                      a hard-coded regime rule; it orders, it is not a
                      probability, and it never enters a Brier score
    direction         the model's own dead zone: bull above 0.55, bear below
                      0.45 (ensemble_model.EnsembleTrainer.predict). Inside
                      the dead zone the model makes no call and nothing is
                      recorded
    information       the last feature bar the model saw (data_as_of and the
                      information session), so the grader anchors on that
                      close and never on a later one

It is forward-only: the May 2026 file carries no per-row timestamp, and a
creation time cannot be proved for any of it, so none of it is back-filled.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

ML_SOURCE = "ml-ensemble"
BULL_ABOVE, BEAR_BELOW = 0.55, 0.45


def ml_direction(p_up) -> Optional[str]:
    try:
        p = float(p_up)
    except (TypeError, ValueError):
        return None
    return "bull" if p > BULL_ABOVE else "bear" if p < BEAR_BELOW else None


def record_ml_predictions(payload: dict, *, data_as_of: Optional[dict] = None,
                          last_close: Optional[dict] = None,
                          trainer_version: Optional[str] = None,
                          created_at: Optional[str] = None) -> dict:
    """Record every directional (ticker, horizon) call in `payload` — the
    data/ml_predictions.json contract. Returns counts. Never raises."""
    from src.core import ledger
    from src.core.versioning import model_version
    from src.intelligence.dataset import model_hash
    created = created_at or datetime.now().isoformat(timespec="seconds")
    try:
        version = model_version(ML_SOURCE, "src/models", trainer=trainer_version)
    except Exception:                            # pragma: no cover - defensive
        version = None
    h_ = model_hash(version)
    data_as_of = data_as_of or {}
    last_close = last_close or {}
    try:
        from src.macro.regime import stamp
        regime = stamp()                         # the regime known NOW, with its basis
    except Exception as e:
        regime = {"regime_at": None, "regime_basis": f"unavailable: {e}"[:200]}
    recorded = skipped = 0
    for ticker, entry in (payload or {}).items():
        for h, hd in ((entry or {}).get("horizons") or {}).items():
            p_up = (hd or {}).get("bullish_prob")
            direction = ml_direction(p_up)
            if direction is None:
                skipped += 1
                continue
            asof = data_as_of.get(ticker)
            try:
                pid = ledger.record_prediction(
                    subject=ticker, source=ML_SOURCE,
                    claim=f"{direction} over {int(hd.get('horizon_days', h))} trading days "
                          f"(ML stacked ensemble score {float(p_up):.3f})",
                    direction=direction, probability=float(p_up), confidence=None,
                    probability_type="SCORE",
                    horizon_days=int(hd.get("horizon_days", h)),
                    price_at=last_close.get(ticker), data_as_of=asof,
                    information_session=asof,
                    regime_at=regime.get("regime_at"),
                    regime_basis=regime.get("regime_basis"),
                    strategy=f"ml-ensemble:{hd.get('model_version') or trainer_version}",
                    rationale=(f"per-model scores {hd.get('per_model_probs')}; regime flag "
                               f"{hd.get('regime')}; in-sample CV {hd.get('model_agreement')}"),
                    created_at=created,
                    external_ref=f"ml:{ticker}:{h}:{asof or created[:10]}",
                    model_version=version,
                    feature_version=f"src/models@{h_}" if h_ else None,
                    routing_decision="DIRECT: recorded by its own source (not routed)",
                    calibration_version="none", announce=False)
                recorded += int(bool(pid))
            except Exception as e:               # one bad row costs one row
                logger.debug("ml prediction not recorded for %s h=%s: %s", ticker, h, e)
    return {"recorded": recorded, "no_call_dead_zone": skipped}
