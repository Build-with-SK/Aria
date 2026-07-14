"""
ARIA Tool: get_macro_data
Fetches macroeconomic indicators from FRED and Yahoo Finance.
"""

import asyncio
from datetime import datetime
from typing import List


FRED_SERIES = {
    "fed_rate":     {"id": "FEDFUNDS",  "name": "Fed Funds Rate", "unit": "%"},
    "cpi":          {"id": "CPIAUCSL",  "name": "CPI YoY",        "unit": "%", "transform": "yoy"},
    "unemployment": {"id": "UNRATE",    "name": "Unemployment",   "unit": "%"},
    "gdp":          {"id": "GDP",       "name": "GDP Growth",     "unit": "B USD", "transform": "yoy"},
    "yield_2y":     {"id": "GS2",       "name": "2Y Treasury",    "unit": "%"},
    "yield_10y":    {"id": "GS10",      "name": "10Y Treasury",   "unit": "%"},
    "yield_30y":    {"id": "GS30",      "name": "30Y Treasury",   "unit": "%"},
    "m2":           {"id": "M2SL",      "name": "M2 Money Supply","unit": "B USD"},
}

YAHOO_TICKERS = {
    "dxy":  {"ticker": "DX-Y.NYB", "name": "US Dollar Index (DXY)"},
    "vix":  {"ticker": "^VIX",     "name": "CBOE VIX"},
    "gold": {"ticker": "GC=F",     "name": "Gold Futures"},
    "oil":  {"ticker": "CL=F",     "name": "WTI Crude Oil"},
    "tnx":  {"ticker": "^TNX",     "name": "10Y Yield (CBOE)"},
}


async def _fetch_fred_series(series_id: str, fred_key: str, limit: int = 2) -> list:
    """Fetch latest observations from FRED."""
    if not fred_key:
        return []
    try:
        import httpx
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": fred_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            data = r.json()
            return data.get("observations", [])
    except Exception:
        return []


async def _fetch_yahoo_price(ticker_symbol: str) -> dict:
    """Fetch current price from yfinance."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: yf.Ticker(ticker_symbol).info)
        price = info.get("regularMarketPrice") or info.get("previousClose")
        change = info.get("regularMarketChangePercent", 0)
        return {"value": price, "change_pct": round(change, 2) if change else None}
    except Exception:
        return {"value": None, "change_pct": None}


async def get_macro_data(indicators: List[str], fred_key: str = "") -> dict:
    """
    Fetch macro indicators.
    indicators: list of keys or ['all']
    """
    if "all" in indicators:
        fred_keys  = list(FRED_SERIES.keys())
        yahoo_keys = list(YAHOO_TICKERS.keys())
    else:
        fred_keys  = [i for i in indicators if i in FRED_SERIES]
        yahoo_keys = [i for i in indicators if i in YAHOO_TICKERS]

        # Add yield curve if requested
        if "yield_curve" in indicators:
            fred_keys += ["yield_2y", "yield_10y"]

    results = {}
    tasks = []

    # FRED tasks
    for key in fred_keys:
        series = FRED_SERIES[key]
        tasks.append(("fred", key, series, _fetch_fred_series(series["id"], fred_key, 2)))

    # Yahoo tasks
    for key in yahoo_keys:
        yticker = YAHOO_TICKERS[key]
        tasks.append(("yahoo", key, yticker, _fetch_yahoo_price(yticker["ticker"])))

    # Also fetch yield curve by default
    if not yahoo_keys and "yield_curve" not in (indicators or []):
        tasks.append(("fred", "yield_2y", FRED_SERIES["yield_2y"], _fetch_fred_series(FRED_SERIES["yield_2y"]["id"], fred_key, 1)))
        tasks.append(("fred", "yield_10y", FRED_SERIES["yield_10y"], _fetch_fred_series(FRED_SERIES["yield_10y"]["id"], fred_key, 1)))

    # Run async
    gathered = await asyncio.gather(*[t[3] for t in tasks], return_exceptions=True)

    for i, (source, key, meta, _) in enumerate(tasks):
        result = gathered[i]
        if isinstance(result, Exception):
            results[key] = {"name": meta.get("name", key), "error": str(result)}
            continue

        if source == "fred":
            obs = result
            if obs:
                latest = obs[0]
                value  = latest.get("value", ".")
                if value == ".":
                    results[key] = {"name": meta["name"], "value": None, "note": "FRED returned missing value"}
                else:
                    results[key] = {
                        "name": meta["name"],
                        "value": float(value),
                        "unit": meta["unit"],
                        "date": latest.get("date"),
                        "source": "FRED",
                    }
            else:
                results[key] = {
                    "name": meta["name"],
                    "value": None,
                    "note": "No data from FRED — check API key" if not fred_key else "No data returned",
                }
        else:
            results[key] = {
                "name": meta["name"],
                "value": result.get("value"),
                "change_pct": result.get("change_pct"),
                "source": "Yahoo Finance",
            }

    # Yield curve calculation
    y2 = results.get("yield_2y", {}).get("value")
    y10 = results.get("yield_10y", {}).get("value")
    if y2 and y10:
        spread = round(y10 - y2, 3)
        results["yield_curve"] = {
            "name": "Yield curve (10Y-2Y spread)",
            "value": spread,
            "unit": "%",
            "inverted": spread < 0,
            "note": "Inverted — historically a recession leading indicator" if spread < 0 else "Normal slope",
        }

    # Regime assessment
    regime_signals = []
    vix = results.get("vix", {}).get("value")
    if vix:
        regime_signals.append("high-vol risk-off" if vix > 25 else "low-vol risk-on" if vix < 15 else "neutral vol")

    yc = results.get("yield_curve", {})
    if yc.get("inverted"):
        regime_signals.append("inverted curve — recession risk elevated")

    fed = results.get("fed_rate", {}).get("value")
    if fed:
        regime_signals.append(f"Fed funds at {fed}%")

    return {
        "timestamp": datetime.now().isoformat(),
        "indicators": results,
        "regime_signals": regime_signals,
        "data_quality": {
            "fred_available": bool(fred_key),
            "indicators_requested": len(indicators),
            "indicators_returned": len(results),
        },
    }
