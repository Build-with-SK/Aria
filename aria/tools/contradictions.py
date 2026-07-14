"""
ARIA Tool: check_contradictions
Cross-validates signals across technical, volume, and price action dimensions.
Surfaces conflicts that should reduce conviction.
"""

import asyncio
from tools.ticker import get_ticker_data


async def check_contradictions(ticker: str) -> dict:
    """
    Pull all signals and look for conflicts.
    Returns a structured list of contradictions with severity.
    """
    data = await get_ticker_data(ticker, period="6mo")

    if "error" in data:
        return {"error": data["error"], "ticker": ticker}

    contradictions = []
    confirmations  = []
    warnings       = []

    ind  = data.get("indicators", {})
    sig  = data.get("signal", {})
    ma   = data.get("moving_averages", {})
    price_data = data.get("price", {})
    dq   = data.get("data_quality", {})

    rsi       = ind.get("rsi_14")
    macd_hist = ind.get("macd_histogram")
    bb_pct_b  = ind.get("bb_pct_b")
    vol_ratio = ind.get("volume_ratio_20d")
    ma50      = ma.get("ma50")
    ma200     = ma.get("ma200")
    current   = price_data.get("current")
    day_chg   = price_data.get("day_change_pct", 0)

    # ── DATA QUALITY WARNINGS ────────────────────────────────────────────────
    if not dq.get("ma200_available"):
        warnings.append({
            "type": "data_quality",
            "severity": "high",
            "message": "Less than 200 days of data available — long-term trend is unverifiable. "
                       "Signal score is based on shorter-term data only.",
        })

    if dq.get("rows_available", 0) < 50:
        warnings.append({
            "type": "data_quality",
            "severity": "critical",
            "message": f"Only {dq.get('rows_available')} rows of price data. "
                       "Most indicators are unreliable. Do not rely on signal score.",
        })

    # ── RSI vs MACD ──────────────────────────────────────────────────────────
    if rsi is not None and macd_hist is not None:
        rsi_bullish  = rsi < 50
        macd_bullish = macd_hist > 0
        if rsi_bullish and not macd_bullish:
            contradictions.append({
                "type": "rsi_vs_macd",
                "severity": "medium",
                "message": f"RSI ({rsi}) suggests oversold/recovery bias, "
                           f"but MACD histogram ({macd_hist:.4f}) is negative — momentum is still falling. "
                           "Possible early entry signal but no momentum confirmation.",
            })
        elif not rsi_bullish and macd_bullish:
            contradictions.append({
                "type": "rsi_vs_macd",
                "severity": "medium",
                "message": f"MACD histogram ({macd_hist:.4f}) is positive (momentum building), "
                           f"but RSI ({rsi}) is elevated — late to the move, risk of reversal.",
            })
        else:
            confirmations.append(f"RSI ({rsi}) and MACD histogram ({macd_hist:.4f}) are directionally aligned.")

    # ── PRICE vs MA ALIGNMENT ─────────────────────────────────────────────────
    if current and ma50 and ma200:
        price_above_50  = current > ma50
        price_above_200 = current > ma200
        ma50_above_200  = ma50 > ma200

        if price_above_50 and not price_above_200:
            contradictions.append({
                "type": "ma_structure",
                "severity": "medium",
                "message": f"Price (${current}) is above 50MA (${ma50}) but below 200MA (${ma200}). "
                           "This is a recovery attempt within a longer-term downtrend — not a confirmed uptrend.",
            })

        if not ma50_above_200 and price_above_50:
            contradictions.append({
                "type": "death_cross_risk",
                "severity": "high",
                "message": f"50MA (${ma50}) is below 200MA (${ma200}) — death cross configuration. "
                           "Price above 50MA in this context is a potential bear rally, not trend reversal.",
            })

        if ma50_above_200 and not price_above_50:
            contradictions.append({
                "type": "pullback_in_uptrend",
                "severity": "low",
                "message": f"Golden cross structure intact (50MA ${ma50} > 200MA ${ma200}), "
                           f"but price (${current}) has pulled below 50MA. Potential dip opportunity, but monitor.",
            })

    # ── VOLUME CONFIRMATION ───────────────────────────────────────────────────
    if vol_ratio is not None and day_chg is not None:
        if day_chg > 1.5 and vol_ratio < 0.8:
            contradictions.append({
                "type": "price_volume_divergence",
                "severity": "medium",
                "message": f"Price up {day_chg}% on below-average volume ({vol_ratio:.2f}x). "
                           "Rally may lack conviction — watch for fade.",
            })
        elif day_chg < -1.5 and vol_ratio < 0.8:
            confirmations.append(f"Selloff on below-average volume ({vol_ratio:.2f}x) — weak selling pressure.")
        elif day_chg > 1.5 and vol_ratio > 1.5:
            confirmations.append(f"Price up {day_chg}% on high volume ({vol_ratio:.2f}x) — bullish conviction.")
        elif day_chg < -1.5 and vol_ratio > 1.5:
            contradictions.append({
                "type": "high_volume_selloff",
                "severity": "high",
                "message": f"Significant drop ({day_chg}%) on high volume ({vol_ratio:.2f}x). "
                           "Institutional distribution likely — bearish signal.",
            })

    # ── BOLLINGER BAND EXTREMES ───────────────────────────────────────────────
    if bb_pct_b is not None and rsi is not None:
        if bb_pct_b < 0.1 and rsi > 50:
            contradictions.append({
                "type": "bb_rsi_divergence",
                "severity": "medium",
                "message": f"Price at Bollinger lower band (Pct-B: {bb_pct_b:.2f}) but RSI ({rsi}) is above 50. "
                           "Price compression without oversold reading — unusual, investigate further.",
            })
        elif bb_pct_b > 0.9 and rsi < 50:
            contradictions.append({
                "type": "bb_rsi_divergence",
                "severity": "medium",
                "message": f"Price near Bollinger upper band (Pct-B: {bb_pct_b:.2f}) but RSI ({rsi}) is below 50. "
                           "Check for widening bands or potential breakout rather than overbought.",
            })

    # ── OVERALL ASSESSMENT ────────────────────────────────────────────────────
    high_sev   = [c for c in contradictions if c["severity"] == "high"]
    med_sev    = [c for c in contradictions if c["severity"] == "medium"]
    crit_warn  = [w for w in warnings if w["severity"] == "critical"]

    if crit_warn:
        conviction_impact = "Data quality is critical — do not rely on signals"
    elif len(high_sev) >= 2:
        conviction_impact = "Multiple high-severity contradictions — conviction should be Low"
    elif len(high_sev) == 1 or len(med_sev) >= 2:
        conviction_impact = "Notable contradictions present — conviction should be Medium at best"
    elif len(med_sev) == 1:
        conviction_impact = "Minor contradictions — conviction is possible but note the caveat"
    else:
        conviction_impact = "No major contradictions — signals are relatively clean"

    return {
        "ticker": ticker,
        "signal_score": sig.get("score"),
        "signal_direction": sig.get("direction"),
        "contradictions": contradictions,
        "confirmations": confirmations,
        "warnings": warnings,
        "conviction_impact": conviction_impact,
        "summary": {
            "total_contradictions": len(contradictions),
            "high_severity": len(high_sev),
            "medium_severity": len(med_sev),
            "total_confirmations": len(confirmations),
            "data_warnings": len(warnings),
        },
    }
