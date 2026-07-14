"""
alerts_engine.py
================
Phase 3 — Alert generation system.

Scans all signals, macro data, and sentiment to produce actionable alerts.

Alert types:
  - SIGNAL_CHANGE    : Asset crossed a signal threshold (e.g. Buy → Strong Buy)
  - HIGH_VOLATILITY  : Asset vol exceeds threshold
  - MACRO_WARNING    : Macro indicator crossed danger level (yield inversion, VIX spike)
  - SENTIMENT_SHIFT  : News sentiment suddenly turned bearish/bullish
  - DRAWDOWN_WARNING : Asset in significant drawdown from 52w high
  - REGIME_CHANGE    : Market regime shifted (bull → bear or vice versa)
  - OPPORTUNITY      : Strong signal with high confidence
  - RISK_OFF         : Multiple risk-off indicators firing simultaneously

Alerts are saved to data/alerts.json and displayed in the dashboard.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """A single alert."""
    alert_type:  str           # e.g. "MACRO_WARNING"
    severity:    str           # "INFO" / "WARNING" / "CRITICAL"
    ticker:      Optional[str] # None for market-wide alerts
    title:       str
    message:     str
    value:       Optional[float] = None   # The value that triggered the alert
    threshold:   Optional[float] = None   # The threshold that was crossed
    date:        str = ""

    def __post_init__(self):
        if not self.date:
            self.date = str(date.today())


def _sev_rank(s: str) -> int:
    return {"CRITICAL": 3, "WARNING": 2, "INFO": 1}.get(s, 0)


def generate_signal_alerts(signals: Dict[str, dict]) -> List[Alert]:
    """Generate alerts based on spot signal strength and risk."""
    alerts = []

    for ticker, sig in signals.items():
        score   = sig.get("composite_score", 0)
        risk    = sig.get("risk_level", "")
        vol     = sig.get("realised_vol", 0)
        drawdown = sig.get("drawdown", 0) or 0

        # Strong buy/sell opportunities
        if score >= 75:
            alerts.append(Alert(
                alert_type="OPPORTUNITY", severity="INFO", ticker=ticker,
                title=f"Strong Buy Signal — {ticker}",
                message=f"{ticker} composite score: {score:+.1f}. "
                        f"Confidence: {sig.get('confidence')}. Vol: {vol:.0%}.",
                value=score,
            ))
        elif score <= -75:
            alerts.append(Alert(
                alert_type="OPPORTUNITY", severity="WARNING", ticker=ticker,
                title=f"Strong Sell Signal — {ticker}",
                message=f"{ticker} composite score: {score:+.1f}. Consider reducing exposure.",
                value=score,
            ))

        # High volatility warning
        if vol > 0.60:
            alerts.append(Alert(
                alert_type="HIGH_VOLATILITY", severity="WARNING", ticker=ticker,
                title=f"Very High Volatility — {ticker}",
                message=f"Realised vol: {vol:.0%} (annualised). Reduce position size significantly.",
                value=vol, threshold=0.60,
            ))
        elif vol > 0.40:
            alerts.append(Alert(
                alert_type="HIGH_VOLATILITY", severity="INFO", ticker=ticker,
                title=f"Elevated Volatility — {ticker}",
                message=f"Realised vol: {vol:.0%}. Consider smaller position.",
                value=vol, threshold=0.40,
            ))

        # Drawdown warning
        if drawdown < -0.30:
            alerts.append(Alert(
                alert_type="DRAWDOWN_WARNING", severity="WARNING", ticker=ticker,
                title=f"Significant Drawdown — {ticker}",
                message=f"{ticker} is {abs(drawdown):.0%} below its 52-week high.",
                value=drawdown, threshold=-0.30,
            ))

    return alerts


def generate_macro_alerts(macro: dict) -> List[Alert]:
    """Generate alerts from macro indicators."""
    alerts = []
    if not macro:
        return alerts

    # VIX spike
    vix = macro.get("vix")
    if vix is not None:
        if vix > 35:
            alerts.append(Alert(
                alert_type="MACRO_WARNING", severity="CRITICAL", ticker=None,
                title=f"VIX Spike — Fear Index at {vix:.1f}",
                message=f"VIX at {vix:.1f} indicates extreme market fear. "
                        "Expect high volatility. Consider reducing risk exposure.",
                value=vix, threshold=35,
            ))
        elif vix > 25:
            alerts.append(Alert(
                alert_type="MACRO_WARNING", severity="WARNING", ticker=None,
                title=f"Elevated VIX — {vix:.1f}",
                message=f"VIX at {vix:.1f} — above-average fear. Monitor positions carefully.",
                value=vix, threshold=25,
            ))

    # Yield curve inversion
    spread = macro.get("yield_spread_10y2y")
    if spread is not None:
        if spread < -0.5:
            alerts.append(Alert(
                alert_type="MACRO_WARNING", severity="CRITICAL", ticker=None,
                title=f"Yield Curve Deeply Inverted — {spread:.2f}%",
                message=f"10Y-2Y spread: {spread:.2f}%. Deep inversion is a historical "
                        "recession indicator. Defensive positioning advised.",
                value=spread, threshold=-0.5,
            ))
        elif spread < 0:
            alerts.append(Alert(
                alert_type="MACRO_WARNING", severity="WARNING", ticker=None,
                title=f"Yield Curve Inverted — {spread:.2f}%",
                message=f"10Y-2Y spread: {spread:.2f}%. Inversion signals potential slowdown.",
                value=spread, threshold=0,
            ))

    # CPI inflation
    cpi = macro.get("cpi_yoy")
    if cpi is not None and cpi > 4.0:
        alerts.append(Alert(
            alert_type="MACRO_WARNING", severity="WARNING", ticker=None,
            title=f"High Inflation — CPI {cpi:.1f}% YoY",
            message=f"CPI at {cpi:.1f}% YoY. Fed likely to maintain restrictive policy. "
                    "Rate-sensitive assets at risk.",
            value=cpi, threshold=4.0,
        ))

    # Macro regime
    regime = macro.get("regime", "")
    if "Recession" in regime:
        alerts.append(Alert(
            alert_type="REGIME_CHANGE", severity="CRITICAL", ticker=None,
            title=f"Macro Regime: {regime}",
            message="Multiple macro indicators point to recession risk. "
                    "Consider defensive positioning: bonds, gold, cash.",
        ))
    elif "Stagflation" in regime:
        alerts.append(Alert(
            alert_type="REGIME_CHANGE", severity="WARNING", ticker=None,
            title=f"Macro Regime: {regime}",
            message="Stagflation risk: high inflation + low growth. "
                    "Equities and bonds may both struggle. Gold historically outperforms.",
        ))

    return alerts


def generate_sentiment_alerts(sentiment: dict) -> List[Alert]:
    """Generate alerts from news sentiment shifts."""
    alerts = []
    if not sentiment:
        return alerts

    for ticker, s in sentiment.items():
        score = s.get("sentiment_score", 0)
        n     = s.get("n_articles", 0)

        if n < 3:
            continue   # Not enough articles to be meaningful

        if score <= -50:
            alerts.append(Alert(
                alert_type="SENTIMENT_SHIFT", severity="WARNING", ticker=ticker,
                title=f"Very Negative News Sentiment — {ticker}",
                message=f"Sentiment score: {score:+.1f} from {n} articles. "
                        f"Top headline: {s.get('top_headlines', [''])[0][:80]}",
                value=score, threshold=-50,
            ))
        elif score >= 50:
            alerts.append(Alert(
                alert_type="SENTIMENT_SHIFT", severity="INFO", ticker=ticker,
                title=f"Very Positive News Sentiment — {ticker}",
                message=f"Sentiment score: {score:+.1f} from {n} articles. "
                        f"Top headline: {s.get('top_headlines', [''])[0][:80]}",
                value=score, threshold=50,
            ))

    return alerts


def generate_portfolio_alerts(portfolio: dict) -> List[Alert]:
    """Generate portfolio-level risk alerts."""
    alerts = []
    if not portfolio:
        return alerts

    for w in portfolio.get("warnings", []):
        alerts.append(Alert(
            alert_type="RISK_OFF", severity="WARNING", ticker=None,
            title="Portfolio Risk Warning",
            message=w,
        ))

    div_score = portfolio.get("diversification_score", 100)
    if div_score < 30:
        alerts.append(Alert(
            alert_type="RISK_OFF", severity="WARNING", ticker=None,
            title=f"Low Diversification — Score: {div_score:.0f}/100",
            message="Portfolio assets are highly correlated. A single shock could affect all positions.",
            value=div_score, threshold=30,
        ))

    return alerts


def generate_all_alerts(
    signals:   Dict[str, dict],
    macro:     Optional[dict] = None,
    sentiment: Optional[dict] = None,
    portfolio: Optional[dict] = None,
) -> List[Alert]:
    """
    Run all alert generators and return sorted list.
    CRITICAL alerts first, then WARNING, then INFO.
    """
    all_alerts: List[Alert] = []

    all_alerts.extend(generate_signal_alerts(signals))
    all_alerts.extend(generate_macro_alerts(macro or {}))
    all_alerts.extend(generate_sentiment_alerts(sentiment or {}))
    all_alerts.extend(generate_portfolio_alerts(portfolio or {}))

    # Sort: CRITICAL first, then WARNING, then INFO
    all_alerts.sort(key=lambda a: _sev_rank(a.severity), reverse=True)

    logger.info(f"Generated {len(all_alerts)} alerts: "
                f"{sum(1 for a in all_alerts if a.severity=='CRITICAL')} critical, "
                f"{sum(1 for a in all_alerts if a.severity=='WARNING')} warning, "
                f"{sum(1 for a in all_alerts if a.severity=='INFO')} info")
    return all_alerts


def alerts_to_json(alerts: List[Alert]) -> list:
    """Serialise alerts list to JSON."""
    return [
        {
            "alert_type": a.alert_type,
            "severity":   a.severity,
            "ticker":     a.ticker,
            "title":      a.title,
            "message":    a.message,
            "value":      a.value,
            "threshold":  a.threshold,
            "date":       a.date,
        }
        for a in alerts
    ]


def save_alerts(alerts: List[Alert], path: str = "data/alerts.json") -> None:
    """Save alerts to JSON file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(alerts_to_json(alerts), indent=2))
    logger.info(f"Saved {len(alerts)} alerts to {path}")
