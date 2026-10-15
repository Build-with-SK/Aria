"""Provider adapter for the independent technical engine. Read-only analysis."""
from __future__ import annotations

from .technical_engine import (ALIASES, TIMEFRAMES, aggregate_five_hours,
                               closed_candles, score_candles, timeframe_conflicts)


def instrument(symbol):
    """Never resolve a dated future to a continuous or spot instrument."""
    import re
    value = symbol.strip().upper()
    if value in ("GCZ6", "GCZ26", "GCZ2026", "GCZ26.CMX"):
        return {"requested": symbol, "provider_symbol": "GCZ26.CMX", "kind": "dated_future",
                "exchange": "CMX", "contract_month": "2026-12", "rollover": "none; exact contract only"}
    if re.fullmatch(r"[A-Z]{1,4}[FGHJKMNQUVXZ]\d{1,4}(?:\.[A-Z]+)?", value):
        raise ValueError("dated futures contract is not explicitly mapped; no spot/continuous fallback")
    from .technical_summary import _resolve_yahoo
    yahoo = _resolve_yahoo(symbol)
    return {"requested": symbol, "provider_symbol": yahoo,
            "kind": "continuous_future" if yahoo.endswith("=F") else "provider_instrument",
            "exchange": None, "rollover": "provider-managed; not a dated contract" if yahoo.endswith("=F") else "not applicable"}


def load_frame(identity, timeframe, *, now=None):
    """Reuse the central daily loader; Yahoo handles existing intraday route.

    Exact futures bypass the central equity resolver and require identity and
    expiry metadata from the same provider before any calculation is published.
    """
    import pandas as pd
    symbol = identity["provider_symbol"]
    interval, period = TIMEFRAMES[timeframe]
    if timeframe == "1d" and identity["kind"] == "provider_instrument":
        from src.v5 import marketdata as md
        df = md.history(identity["requested"], period=period)
        meta = md.provenance(identity["requested"]) or {}
        return df, {"provider": meta.get("provider") or meta.get("source") or "central marketdata",
                    "adjustment": meta.get("adjustment"), "identity_verified": False,
                    "identity_note": "central universe resolution; exchange metadata unavailable"}
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False, raise_errors=True)
    meta = ticker.history_metadata or {}
    if identity["kind"] == "dated_future":
        if meta.get("symbol", "").upper() != symbol or meta.get("exchangeName") != identity["exchange"]:
            raise ValueError("requested contract/exchange not confirmed by provider; contract unavailable")
        expiry = meta.get("expireDate")
        if not expiry:
            info = getattr(ticker, "get_info", lambda: {})() or {}
            if info.get("symbol", "").upper() == symbol and info.get("exchange") == identity["exchange"]:
                expiry = info.get("expireDate")
        if not expiry:
            raise ValueError("contract expiration unavailable; identity cannot be fully verified")
        expiration = pd.Timestamp(expiry, unit="s", tz="UTC")
        if expiration.strftime("%Y-%m") != identity["contract_month"]:
            raise ValueError("provider returned the wrong contract month")
        if expiration <= pd.Timestamp(now or pd.Timestamp.now(tz="UTC")):
            raise ValueError("requested futures contract has expired; no rollover substitution")
        identity["expiration"] = expiration.isoformat()
    return df, {"provider": "yfinance", "provider_symbol": meta.get("symbol"),
                "exchange": meta.get("exchangeName"), "exchange_timezone": meta.get("exchangeTimezoneName"),
                "identity_verified": meta.get("symbol", "").upper() == symbol.upper(),
                "adjustment": "unadjusted", "vendor_delay_seconds": meta.get("exchangeDataDelayedBy")}


def analyze(symbol, timeframe="1d", *, loader=None, now=None):
    tf = ALIASES.get(timeframe, timeframe)
    base = {"symbol": symbol, "timeframe": timeframe}
    try:
        if tf not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe: {timeframe}; no daily fallback")
        identity = instrument(symbol)
        base["instrument"] = identity
        base["yahoo"] = identity["provider_symbol"]
        df, provenance = loader(identity, tf) if loader else load_frame(identity, tf, now=now)
        if tf == "5h":
            df = aggregate_five_hours(df, now)
            provenance["alignment"] = "UTC epoch-anchored 5h; five consecutive hourly candles, no partial bins"
        df, freshness = closed_candles(df, tf, now)
        result = score_candles(df)
        quality = "complete" if result["evidence"]["warmup_complete"] else "insufficient_warmup"
        if result["evidence"]["indicator_votes"] < 9 and quality == "complete":
            quality = "limited_indicator_coverage"
        if freshness["data_stale"]:
            quality = "stale"
        elif freshness["gap_count"] and quality == "complete":
            quality = "limited_calendar_unverified"
        result["evidence"]["quality"] = quality
        # Calculated historical direction remains inspectable, but is not a current recommendation.
        if freshness["data_stale"] or not result["evidence"]["warmup_complete"]:
            result["calculated_summary"] = result["summary"]
            result["summary"] = None
        return {**base, **result, **freshness, "provenance": provenance, "data_quality": quality,
                "observed": {"close": result["price"], "bar_open": freshness["data_as_of"]},
                "interpretation": "Deterministic description of closed candles; no trade setup or validated edge."}
    except Exception as exc:
        # Provider exceptions can carry URLs and query strings. Publish a safe reason only.
        reason = str(exc) if isinstance(exc, ValueError) else f"provider unavailable ({type(exc).__name__})"
        return {**base, "summary": None, "error": reason[:250], "data_quality": "unavailable"}


def analyze_all(symbol, timeframes=None, *, loader=None, now=None):
    requested = list(TIMEFRAMES) if timeframes is None else list(dict.fromkeys(timeframes))
    rows = {tf: analyze(symbol, tf, loader=loader, now=now) for tf in requested}
    conflicts = timeframe_conflicts(rows)
    valid = [r["summary"] for r in rows.values() if r.get("summary") and not r.get("data_stale")]
    from .technical_summary import _consensus
    missing = [tf for tf, r in rows.items() if not r.get("summary")]
    return {"symbol": symbol, "timeframes": rows, "conflicts": conflicts,
            "yahoo": next((r.get("yahoo") for r in rows.values() if r.get("yahoo")), None),
            "consensus": None if conflicts or missing or not valid else _consensus(valid),
            "assessment": "Timeframe conflict — inspect both horizons." if conflicts else
                          "Incomplete evidence — inspect unavailable/stale timeframes." if missing else
                          "Descriptive multi-timeframe agreement; no validated trade recommendation.",
            "sentinel": {"status": "NOT_CONSULTED", "position": "NONE",
                         "note": "No independent review obtained. Silence is not agreement."}}
