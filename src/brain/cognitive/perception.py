"""
src/brain/cognitive/perception.py
=================================
PERCEPTION LAYER — reads all available data files and produces a
structured snapshot of what the market looks like RIGHT NOW.
Pure data, no opinions.

"Right now" includes the ML ensemble: a 20-day call made in May says nothing
about October. Each row is aged by its own `generated_at` (src/models/ml_store
.made_at — the file's mtime only for a file in which no row is stamped), and a
horizon whose window has elapsed (technical_agent.ml_horizon_live) is not
perceived. A row with no horizon left in force goes to `ml_expired`, not `ml`.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.desk.analysts.technical_agent import ml_horizon_live
from src.models.ml_store import made_at

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent.parent
DATA = ROOT / "data"

# Perception remembers the last regime it saw so it can flag shifts
_LAST_REGIME_FILE = DATA / "brain_memory" / "last_regime.json"

ALERT_SCORE_THRESHOLD = 30.0


@dataclass
class SignalData:
    """One ticker's signal snapshot — mapped from signals.json."""
    ticker:            str
    name:              str = ""
    asset_class:       str = "equities"
    current_price:     float = 0.0
    composite_score:   float = 0.0
    action:            str = "Hold"
    confidence:        str = "Low"
    bullish_prob:      float = 0.5
    bearish_prob:      float = 0.5
    trend_score:       float = 0.0
    momentum_score:    float = 0.0
    volatility_score:  float = 0.0
    regime:            str = ""
    realised_vol:      float = 0.0
    stop_loss:         float = 0.0
    take_profit:       float = 0.0
    invalidation:      float = 0.0
    position_size_pct: float = 0.0
    drivers:           list = field(default_factory=list)
    risks:             list = field(default_factory=list)
    explanation:       str = ""

    @classmethod
    def from_json(cls, ticker: str, d: dict) -> "SignalData":
        return cls(
            ticker=d.get("ticker", ticker),
            name=d.get("name", ""),
            asset_class=d.get("asset_class", "equities"),
            current_price=d.get("current_price") or 0.0,
            composite_score=d.get("composite_score") or 0.0,
            action=d.get("action", "Hold"),
            confidence=d.get("confidence", "Low"),
            bullish_prob=d.get("bullish_prob") or 0.5,
            bearish_prob=d.get("bearish_prob") or 0.5,
            trend_score=d.get("trend_score") or 0.0,
            momentum_score=d.get("momentum_score") or 0.0,
            volatility_score=d.get("volatility_score") or 0.0,
            regime=d.get("regime", ""),
            realised_vol=d.get("realised_vol") or 0.0,
            stop_loss=d.get("stop_loss") or 0.0,
            take_profit=d.get("take_profit") or 0.0,
            invalidation=d.get("invalidation") or 0.0,
            position_size_pct=d.get("position_size_pct") or 0.0,
            drivers=d.get("drivers") or [],
            risks=d.get("risks") or [],
            explanation=d.get("explanation", ""),
        )


@dataclass
class MacroState:
    """Macro environment snapshot — mapped from macro_data.json."""
    regime:       str = "Unknown"
    macro_score:  float = 0.0
    vix:          Optional[float] = None
    dxy:          Optional[float] = None
    treasury_10y: Optional[float] = None
    data_date:    str = ""

    def __str__(self):
        return (f"Regime: {self.regime} | Macro score: {self.macro_score:.1f} | "
                f"VIX: {self.vix} | DXY: {self.dxy} | 10Y: {self.treasury_10y}")


@dataclass
class MLPrediction:
    """ML ensemble prediction — mapped from ml_predictions.json.

    In `PerceptionSnapshot.ml`, `horizons` holds only the horizons still in
    force and `overall_*` summarise those: the file's own values when none has
    elapsed, otherwise the writer's summary (main._ml_to_json: plain mean,
    Bullish > 0.55, Bearish < 0.45) of the ones left. In `ml_expired`,
    `overall_*` are what the file said, kept for reference only."""
    ticker:           str
    overall_bullish:  float = 0.5
    overall_signal:   str = "Neutral"
    horizons:         dict = field(default_factory=dict)  # {"1": {...}, "5": {...}, "20": {...}}
    made_at:          Optional[datetime] = None  # latest instant it could have been made; None = unknown
    expired_horizons: dict = field(default_factory=dict)  # horizons whose window has elapsed


@dataclass
class PerceptionSnapshot:
    """Everything the brain can see this instant."""
    signals:           dict            # {ticker: SignalData}
    macro:             MacroState
    ml:                dict            # {ticker: MLPrediction} — rows with a horizon still in force
    top_opportunities: list            # top 10 SignalData by |composite_score|
    regime_shift:      bool
    alerts:            list            # tickers with |score| > 30 AND High confidence
    timestamp:         datetime
    ml_expired:        dict = field(default_factory=dict)  # {ticker: MLPrediction} — nothing left in force


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Perception could not parse {path.name}: {e}")
        return {}


def _mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _ml_row(t: str, d: dict, body: dict, file_mtime: Optional[datetime],
            now: datetime) -> tuple[MLPrediction, bool]:
    """One ml_predictions.json row as perceived at `now`, and whether any of
    its horizons is still in force."""
    made = made_at(body, t, file_mtime)
    live, expired = {}, {}
    for h, hd in (d.get("horizons") or {}).items():
        days = hd.get("horizon_days", h) if isinstance(hd, dict) else h
        (live if ml_horizon_live(days, made, now) else expired)[h] = hd
    pred = MLPrediction(
        ticker=d.get("ticker", t),
        overall_bullish=d.get("overall_bullish") or 0.5,
        overall_signal=d.get("overall_signal", "Neutral"),
        horizons=live,
        made_at=made,
        expired_horizons=expired,
    )
    if not expired:
        return pred, bool(live)
    # The file's overall blends horizons that have elapsed; summarise only
    # the ones left, by the writer's own rule.
    probs = [float(p) for p in (hd.get("bullish_prob") for hd in live.values()
                                if isinstance(hd, dict))
             if isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p)]
    if not probs:
        return pred, False
    mean_p = sum(probs) / len(probs)
    pred.overall_bullish = round(mean_p, 4)
    pred.overall_signal = ("Bullish" if mean_p > 0.55 else
                           "Bearish" if mean_p < 0.45 else "Neutral")
    return pred, True


class MarketPerception:
    """
    Reads all available data and produces a structured snapshot of
    what the market looks like RIGHT NOW. Pure data, no opinions.
    """

    def perceive(self, now: Optional[datetime] = None) -> PerceptionSnapshot:
        now = now or datetime.now()
        raw_signals = _load_json(DATA / "signals.json")
        raw_macro   = _load_json(DATA / "macro_data.json")
        # mtime BEFORE the read: a file replaced in between is then aged as
        # older than it is, never as newer.
        ml_mtime    = _mtime(DATA / "ml_predictions.json")
        raw_ml      = _load_json(DATA / "ml_predictions.json")

        signals = {t: SignalData.from_json(t, d)
                   for t, d in raw_signals.items() if isinstance(d, dict)}

        macro = MacroState(
            regime=raw_macro.get("regime", "Unknown"),
            macro_score=raw_macro.get("macro_score") or 0.0,
            vix=raw_macro.get("vix"),
            dxy=raw_macro.get("dxy"),
            treasury_10y=raw_macro.get("treasury_10y"),
            data_date=raw_macro.get("data_date", ""),
        )

        ml, ml_expired = {}, {}
        for t, d in raw_ml.items():
            if not isinstance(d, dict):
                continue
            pred, in_force = _ml_row(t, d, raw_ml, ml_mtime, now)
            (ml if in_force else ml_expired)[t] = pred

        top_opportunities = sorted(
            signals.values(), key=lambda s: abs(s.composite_score), reverse=True
        )[:10]

        alerts = [
            s.ticker for s in signals.values()
            if abs(s.composite_score) > ALERT_SCORE_THRESHOLD and s.confidence == "High"
        ]

        return PerceptionSnapshot(
            signals=signals,
            macro=macro,
            ml=ml,
            top_opportunities=top_opportunities,
            regime_shift=self._detect_regime_shift(macro.regime),
            alerts=alerts,
            timestamp=now,
            ml_expired=ml_expired,
        )

    def _detect_regime_shift(self, current_regime: str) -> bool:
        """True if the macro regime changed since the last perception run."""
        _LAST_REGIME_FILE.parent.mkdir(parents=True, exist_ok=True)
        last = None
        if _LAST_REGIME_FILE.exists():
            try:
                last = json.loads(_LAST_REGIME_FILE.read_text(encoding="utf-8")).get("regime")
            except Exception:
                last = None
        try:
            _LAST_REGIME_FILE.write_text(
                json.dumps({"regime": current_regime, "seen_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Could not persist last regime: {e}")
        return last is not None and last != current_regime
