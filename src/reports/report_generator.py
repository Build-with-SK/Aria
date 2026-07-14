"""
report_generator.py
===================
Generates a structured daily market intelligence report.

Output formats:
  - Console print (plain text)
  - JSON (for API / programmatic use)
  - Markdown file (for archiving)

The report pulls from all available data:
  - Spot signals (required)
  - Futures signals (optional)
  - Options data (optional)
  - ML predictions (optional)
  - Backtest metrics (optional)
  - Portfolio analysis (optional)
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _score_to_emoji(score: float) -> str:
    if score >= 75:   return "🟢🟢"
    if score >= 40:   return "🟢"
    if score >= 10:   return "🔵"
    if score >= -9:   return "⚪"
    if score >= -40:  return "🟡"
    if score >= -75:  return "🔴"
    return "🔴🔴"


def generate_report(
    signals:        Dict[str, dict],
    futures_data:   Optional[dict] = None,
    options_data:   Optional[dict] = None,
    ml_predictions: Optional[dict] = None,
    portfolio_data: Optional[dict] = None,
    backtest_data:  Optional[dict] = None,
    top_n:          int = 5,
) -> dict:
    """
    Generate the full daily report.

    Returns
    -------
    Dict with sections: regime, top_bullish, top_bearish, market_summary,
    macro_warnings, futures_summary, options_summary, ml_summary,
    portfolio_summary, opportunities, risks
    """
    today_str = str(date.today())

    # --- Market regime detection ---
    bull_count    = sum(1 for s in signals.values() if s.get("composite_score", 0) > 10)
    bear_count    = sum(1 for s in signals.values() if s.get("composite_score", 0) < -10)
    neutral_count = len(signals) - bull_count - bear_count
    total         = max(len(signals), 1)

    bull_pct = bull_count / total
    if bull_pct > 0.60:
        regime = "Risk-On Bull Market"
    elif bull_pct < 0.30:
        regime = "Risk-Off Bear Market"
    elif bull_count > bear_count:
        regime = "Mildly Bullish"
    elif bear_count > bull_count:
        regime = "Mildly Bearish"
    else:
        regime = "Sideways / Uncertain"

    # --- Top bullish and bearish signals ---
    sorted_signals = sorted(signals.values(), key=lambda x: x.get("composite_score", 0))
    top_bearish    = sorted_signals[:top_n]
    top_bullish    = sorted_signals[-top_n:][::-1]

    def signal_summary(s: dict) -> dict:
        return {
            "ticker":     s.get("ticker"),
            "name":       s.get("name"),
            "score":      s.get("composite_score"),
            "action":     s.get("action"),
            "confidence": s.get("confidence"),
            "regime":     s.get("regime"),
            "vol":        s.get("realised_vol"),
        }

    # --- Futures summary ---
    futures_summary = {}
    if futures_data:
        confirming = [(t, f) for t, f in futures_data.items() if f.get("confirmation_score", 0) > 20]
        contracting = [(t, f) for t, f in futures_data.items() if f.get("confirmation_score", 0) < -20]
        futures_summary = {
            "n_contracts":          len(futures_data),
            "confirming_bullish":   [t for t, _ in confirming],
            "confirming_bearish":   [t for t, _ in contracting],
            "avg_confirmation":     round(
                sum(f.get("confirmation_score", 0) for f in futures_data.values()) / max(len(futures_data), 1),
                2,
            ),
        }

    # --- Options summary ---
    options_summary = {}
    if options_data:
        valid     = {t: d for t, d in options_data.items() if d and "error" not in d}
        bearish_o = [(t, d) for t, d in valid.items() if d.get("final_sentiment_score", 0) < -10]
        bullish_o = [(t, d) for t, d in valid.items() if d.get("final_sentiment_score", 0) > 10]
        options_summary = {
            "n_analyzed":           len(valid),
            "bullish_sentiment":    [t for t, _ in bullish_o],
            "bearish_sentiment":    [t for t, _ in bearish_o],
            "avg_sentiment":        round(
                sum(d.get("final_sentiment_score", 0) for d in valid.values()) / max(len(valid), 1),
                2,
            ),
        }

    # --- ML summary ---
    ml_summary = {}
    if ml_predictions:
        trained   = {t: p for t, p in ml_predictions.items() if p.get("models_trained")}
        bullish_m = [(t, p) for t, p in trained.items() if p.get("overall_signal") == "Bullish"]
        bearish_m = [(t, p) for t, p in trained.items() if p.get("overall_signal") == "Bearish"]
        ml_summary = {
            "n_assets_with_models": len(trained),
            "ml_bullish":           [t for t, _ in bullish_m],
            "ml_bearish":           [t for t, _ in bearish_m],
        }

    # --- Portfolio summary ---
    portfolio_summary = {}
    if portfolio_data:
        portfolio_summary = {
            "total_allocated":      portfolio_data.get("exposure", {}).get("total_allocated", 0),
            "diversification_score": portfolio_data.get("diversification_score", 0),
            "portfolio_vol":        portfolio_data.get("portfolio_vol", 0),
            "warnings":             portfolio_data.get("warnings", []),
        }

    # --- Opportunities and risks ---
    opportunities = [
        {"ticker": s.get("ticker"), "score": s.get("composite_score"), "action": s.get("action"),
         "driver": s.get("drivers", [""])[0] if s.get("drivers") else ""}
        for s in top_bullish if s.get("composite_score", 0) > 30
    ]
    risks = [
        {"ticker": s.get("ticker"), "score": s.get("composite_score"), "action": s.get("action"),
         "risk": s.get("risks", [""])[0] if s.get("risks") else ""}
        for s in top_bearish if s.get("composite_score", 0) < -30
    ]

    return {
        "date":              today_str,
        "version":           "Phase 2",
        "market_regime":     regime,
        "regime_stats":      {"bull": bull_count, "bear": bear_count, "neutral": neutral_count},
        "top_bullish":       [signal_summary(s) for s in top_bullish],
        "top_bearish":       [signal_summary(s) for s in top_bearish],
        "futures_summary":   futures_summary,
        "options_summary":   options_summary,
        "ml_summary":        ml_summary,
        "portfolio_summary": portfolio_summary,
        "opportunities":     opportunities,
        "risks":             risks,
    }


def print_report(report: dict) -> None:
    """Pretty-print the report to console."""
    d = report["date"]
    print("\n" + "=" * 70)
    print(f"  DAILY MARKET INTELLIGENCE REPORT — {d}")
    print("=" * 70)
    print(f"\n  MARKET REGIME: {report['market_regime']}")
    s = report["regime_stats"]
    print(f"  Bullish: {s['bull']}  |  Bearish: {s['bear']}  |  Neutral: {s['neutral']}")

    print("\n  TOP BULLISH SIGNALS:")
    for sig in report["top_bullish"]:
        emoji = _score_to_emoji(sig["score"])
        print(f"    {emoji}  {sig['ticker']:<10} {sig['action']:<15} Score: {sig['score']:>+6.1f}")

    print("\n  TOP BEARISH SIGNALS:")
    for sig in report["top_bearish"]:
        emoji = _score_to_emoji(sig["score"])
        print(f"    {emoji}  {sig['ticker']:<10} {sig['action']:<15} Score: {sig['score']:>+6.1f}")

    if report.get("futures_summary"):
        fs = report["futures_summary"]
        print(f"\n  FUTURES: {fs['n_contracts']} contracts analysed. Avg score: {fs['avg_confirmation']:+.1f}")

    if report.get("options_summary"):
        os = report["options_summary"]
        print(f"  OPTIONS: {os['n_analyzed']} chains analysed. Avg sentiment: {os['avg_sentiment']:+.1f}")

    if report.get("portfolio_summary"):
        ps = report["portfolio_summary"]
        print(f"\n  PORTFOLIO: {ps['total_allocated']:.1f}% allocated. "
              f"Diversification: {ps['diversification_score']:.0f}/100. "
              f"Vol: {ps['portfolio_vol']:.0%}")
        for w in ps.get("warnings", []):
            print(f"    ⚠️  {w}")

    print("\n" + "=" * 70)


def save_report(report: dict, output_dir: str = "data/reports") -> str:
    """Save report to JSON and Markdown files. Returns the file path."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    date_str = report["date"]

    # JSON
    json_path = Path(output_dir) / f"report_{date_str}.json"
    json_path.write_text(json.dumps(report, indent=2))

    # Markdown
    md_lines = [
        f"# Daily Market Intelligence Report — {date_str}",
        "",
        f"**Market Regime:** {report['market_regime']}",
        "",
        "## Top Bullish Signals",
        "| Ticker | Action | Score | Confidence |",
        "|--------|--------|-------|------------|",
    ]
    for s in report["top_bullish"]:
        md_lines.append(f"| {s['ticker']} | {s['action']} | {s['score']:+.1f} | {s['confidence']} |")

    md_lines += ["", "## Top Bearish Signals",
                 "| Ticker | Action | Score | Confidence |",
                 "|--------|--------|-------|------------|"]
    for s in report["top_bearish"]:
        md_lines.append(f"| {s['ticker']} | {s['action']} | {s['score']:+.1f} | {s['confidence']} |")

    md_lines += ["", "---", "*Generated by Trading Intelligence System Phase 2*"]
    md_path = Path(output_dir) / f"report_{date_str}.md"
    md_path.write_text("\n".join(md_lines))

    logger.info(f"Report saved to {json_path} and {md_path}")
    return str(json_path)
