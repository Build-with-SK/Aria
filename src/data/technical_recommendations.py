"""Provider adapter for the independent technical engine. Read-only analysis."""
from __future__ import annotations

from .technical_engine import (ALIASES, TIMEFRAMES, aggregate_five_hours,
                               closed_candles, score_candles, timeframe_conflicts)

MIN_CONSENSUS_HORIZONS = 3


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


def _provider_identity(symbol, metadata):
    """Provider-reported symbol, venue and unit; never equate a request with a reply."""
    observed = str(metadata.get("symbol") or "").upper()
    if observed and observed != symbol.upper():
        raise ValueError("provider returned a different provider instrument")
    exchange = metadata.get("exchangeName")
    if symbol.endswith(".BO") and exchange and exchange != "BSE":
        raise ValueError("provider returned a different BSE venue")
    if symbol.endswith((".BO", ".NS")) and metadata.get("currency") and metadata["currency"] != "INR":
        raise ValueError("provider returned a different Indian price unit")
    return observed == symbol.upper() and bool(exchange) and bool(metadata.get("currency"))


def _yahoo_frame(symbol, interval, period, cache):
    key = (symbol, interval, period)
    if key not in cache:
        import yfinance as yf
        from src.v5.vendors import _with_deadline, YF_DEADLINE
        def read():
            ticker = yf.Ticker(symbol)
            frame = ticker.history(period=period, interval=interval, auto_adjust=False, raise_errors=True)
            return frame, ticker.history_metadata or {}, ticker
        cache[key] = _with_deadline(read, YF_DEADLINE, f"technical history {symbol}")
    frame, metadata, ticker = cache[key]
    return frame.copy() if frame is not None else None, dict(metadata), ticker


def load_frame(identity, timeframe, *, now=None, _cache=None):
    """Reuse the central daily loader; Yahoo handles existing intraday route.

    Exact futures bypass the central equity resolver and require identity and
    expiry metadata from the same provider before any calculation is published.
    """
    import pandas as pd
    symbol = identity["provider_symbol"]
    cache = _cache if _cache is not None else {}
    interval, period = TIMEFRAMES[timeframe]
    if timeframe == "1d" and identity["kind"] == "provider_instrument":
        from src.v5 import marketdata as md
        df = md.history(identity["requested"], period=period, verify_identity=True)
        meta = md.provenance(identity["requested"]) or {}
        details = {"provider": meta.get("vendor") or meta.get("served_from") or "central marketdata",
                   "provider_symbol": meta.get("provider_symbol"),
                   "adjustment": meta.get("adjustment"), "identity_verified": _provider_identity(symbol, meta.get("identity") or {}),
                   "provider_identity": meta.get("identity") or {}, "provider_bars_returned": meta.get("rows"),
                   "upstream_stale": bool(meta.get("stale", True)),
                   "upstream_stale_reason": meta.get("stale_reason"),
                   "identity_note": "provider-bound central history; only provider-reported metadata confirms identity"}
        if meta.get("provider_symbol") and meta["provider_symbol"].upper() != symbol.upper():
            raise ValueError("central loader returned a different provider instrument")
        if df is not None and isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None:
            # Central daily bars are intentionally date-only; this is its documented
            # format, not permission to guess a timezone for arbitrary naive bars.
            if not df.index.equals(df.index.normalize()):
                raise ValueError("ambiguous daily timestamps; expected date-only central bars")
            from src.markets.sessions import resolve
            from src.markets.venues import VENUES
            venue = resolve(symbol).venue
            if venue not in VENUES:
                raise ValueError("exchange timezone unavailable for central daily bars")
            df = df.copy()
            source_zone = (meta.get("identity") or {}).get("exchangeTimezoneName") or VENUES[venue].tz
            df.index = df.index.tz_localize(source_zone, ambiguous="raise", nonexistent="raise")
            details.update(exchange=venue, exchange_timezone=source_zone,
                           timestamp_basis="central date-only bars localized through exact provider symbol venue")
        return df, details
    df, meta, ticker = _yahoo_frame(symbol, interval, period, cache)
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
                "identity_verified": _provider_identity(symbol, meta), "currency": meta.get("currency"),
                "adjustment": "unadjusted", "vendor_delay_seconds": meta.get("exchangeDataDelayedBy")}


def _recover_history(identity, timeframe, now, cache):
    from .technical_feeds import bse_daily, complete_calendar_bars, fx_daily_from_hourly
    provider = identity["provider_symbol"]
    if identity["kind"] != "provider_instrument" or timeframe not in ("1d", "1w", "1M"):
        raise ValueError("no same-instrument public history recovery is configured for this horizon")
    if provider.endswith(".BO"):
        key = (provider, "official_bse_daily")
        if key not in cache:
            cache[key] = bse_daily(provider, now=now)
        daily, details = cache[key]
        daily, source_validation = closed_candles(daily, "1d", now, provider_symbol=provider)
        frame = complete_calendar_bars(daily, timeframe, provider, now=now)
        if frame.empty:
            raise ValueError(f"official BSE returned {len(daily)} daily bars; no complete {timeframe} history with verified calendar coverage")
        return frame, {**details, "aggregation_source_validation": source_validation,
                       "alignment": "official BSE daily OHLC" if timeframe == "1d" else "OHLC aggregated only from complete verified BSE trading-day coverage"}
    if provider.endswith("=X") and timeframe == "1d":
        hourly, metadata, _ = _yahoo_frame(provider, "60m", "2y", cache)
        if not _provider_identity(provider, metadata):
            raise ValueError("FX hourly source identity is unverified")
        daily, details = fx_daily_from_hourly(hourly, provider, now=now)
        return daily, {**details, "provider": "yfinance hourly reconstruction", "provider_symbol": provider,
                       "identity_verified": True, "exchange": metadata.get("exchangeName"),
                       "currency": metadata.get("currency"), "exchange_timezone": "UTC",
                       "adjustment": "unadjusted; exact same FX pair",
                       "upstream_stale": details["hourly_source_validation"]["data_stale"]}
    raise ValueError("no verified keyless alternative for this instrument/horizon")


def analyze(symbol, timeframe="1d", *, loader=None, now=None, _cache=None):
    tf = ALIASES.get(timeframe, timeframe)
    base = {"symbol": symbol, "timeframe": timeframe}
    cache = _cache if _cache is not None else {}
    provenance = {}
    try:
        if tf not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe: {timeframe}; no daily fallback")
        identity = instrument(symbol)
        base["instrument"] = identity
        base["yahoo"] = identity["provider_symbol"]
        try:
            df, provenance = loader(identity, tf) if loader else load_frame(identity, tf, now=now, _cache=cache)
        except Exception as load_error:
            if loader or isinstance(load_error, ValueError) or tf not in ("1d", "1w", "1M") or not identity["provider_symbol"].endswith((".BO", "=X")):
                raise
            df, provenance = _recover_history(identity, tf, now, cache)
            provenance["recovery_trigger"] = f"primary provider unavailable ({type(load_error).__name__})"
        base["provenance"] = provenance
        if tf == "5h":
            grid_provider = identity["provider_symbol"] if identity["kind"] == "provider_instrument" else None
            df = aggregate_five_hours(df, now, provider_symbol=grid_provider)
            provenance["alignment"] = df.attrs.get("aggregation_alignment")
            provenance["calendar_unknown_days_excluded"] = df.attrs.get("calendar_unknown_days_excluded", 0)
            provenance["aggregation_source_validation"] = df.attrs.get("source_validation")
        try:
            if df is None and provenance.get("provider_bars_returned") is not None:
                raise ValueError(f"provider returned {provenance['provider_bars_returned']} underlying daily bars; fewer than 5 usable bars remain in the requested window")
            df, freshness = closed_candles(df, tf, now, provider_symbol=identity["provider_symbol"] if identity["kind"] == "provider_instrument" else None)
        except ValueError as primary_error:
            recoverable = str(primary_error).startswith(("missing OHLC", "malformed OHLC", "non-finite OHLC", "insufficient closed history", "provider returned"))
            if loader or not recoverable or tf not in ("1d", "1w", "1M") or not identity["provider_symbol"].endswith((".BO", "=X")):
                raise
            try:
                df, recovered = _recover_history(identity, tf, now, cache)
                provenance = {**recovered, "recovery_trigger": str(primary_error)}
                base["provenance"] = provenance
                df, freshness = closed_candles(df, tf, now, provider_symbol=identity["provider_symbol"])
            except ValueError as recovery_error:
                raise ValueError(f"{primary_error}; public recovery: {recovery_error}") from None
        freshness["data_stale"] = freshness["data_stale"] or bool(provenance.get("upstream_stale"))
        result = score_candles(df)
        quality = "complete" if result["evidence"]["warmup_complete"] else "insufficient_warmup"
        if result["evidence"]["indicator_votes"] < 9 and quality == "complete":
            quality = "limited_indicator_coverage"
        if freshness["data_stale"]:
            quality = "stale"
        elif (freshness["history_prefix_excluded"] or (provenance.get("aggregation_source_validation") or {}).get("history_prefix_excluded")) and quality == "complete":
            quality = "valid_history_segment"
        elif freshness["gap_count"] and quality == "complete":
            quality = "missing_scheduled_bars" if freshness["calendar_status"].startswith("verified") else "limited_calendar_unverified"
        result["evidence"]["quality"] = quality
        # Calculated historical direction remains inspectable, but is not a current recommendation.
        identity_unverified = loader is None and not provenance.get("identity_verified")
        if freshness["data_stale"] or not result["evidence"]["warmup_complete"] or identity_unverified:
            result["calculated_summary"] = result["summary"]
            result["summary"] = None
        verification = {"identity": "verified" if provenance.get("identity_verified") else "unverified",
                        "history": "full_warmup" if result["evidence"]["warmup_complete"] else "insufficient_warmup",
                        "ohlc": "valid", "calendar": freshness["calendar_status"],
                        "independent_price_validation": provenance.get("independent_price_validation", "not performed")}
        return {**base, **result, **freshness, "provenance": provenance, "data_quality": "identity_unverified" if identity_unverified else quality,
                "verification": verification,
                "observed": {"close": result["price"], "bar_open": freshness["data_as_of"]},
                "interpretation": "Deterministic description of closed candles; no trade setup or validated edge."}
    except Exception as exc:
        # Provider exceptions can carry URLs and query strings. Publish a safe reason only.
        reason = str(exc) if isinstance(exc, ValueError) else f"provider unavailable ({type(exc).__name__})"
        return {**base, "summary": None, "error": reason[:500], "data_quality": "unavailable",
                "verification": {"identity": "verified" if provenance.get("identity_verified") else "unverified",
                                 "history": "unavailable_or_insufficient", "ohlc": "not_usable",
                                 "independent_price_validation": "not performed"}}


def analyze_all(symbol, timeframes=None, *, loader=None, now=None):
    requested = list(TIMEFRAMES) if timeframes is None else list(dict.fromkeys(timeframes))
    cache = {}
    rows = {tf: analyze(symbol, tf, loader=loader, now=now, _cache=cache) for tf in requested}
    conflicts = timeframe_conflicts(rows)
    used = [tf for tf, r in rows.items() if r.get("summary") and not r.get("data_stale")]
    from .technical_summary import _consensus
    excluded = {tf: (r.get("error") or ("stale" if r.get("data_stale") else r.get("data_quality") or "withheld"))
                for tf, r in rows.items() if tf not in used}
    # A consensus may rest on the horizons that are valid now, as long as it
    # names the ones left out. It never averages a conflict, and it needs at
    # least three valid horizons including one of 1d/1w/1M, so a closed
    # market's stale intraday rows cannot hide it and intraday noise alone
    # cannot make one.
    higher = {"1d", "1w", "1wk", "1M"}
    eligible = not conflicts and len(used) >= MIN_CONSENSUS_HORIZONS and bool(higher & set(used))
    consensus = _consensus([rows[tf]["summary"] for tf in used]) if eligible else None
    coverage = f"{len(used)} of {len(rows)} horizons"
    return {"symbol": symbol, "timeframes": rows, "conflicts": conflicts,
            "yahoo": next((r.get("yahoo") for r in rows.values() if r.get("yahoo")), None),
            "consensus": consensus,
            "consensus_basis": {"used": used, "excluded": excluded, "coverage": coverage,
                                "rule": f"no conflicts; at least {MIN_CONSENSUS_HORIZONS} valid horizons including 1d, 1w or 1M"},
            "assessment": "Timeframe conflict — inspect both horizons." if conflicts else
                          f"Partial multi-timeframe agreement across {coverage}; excluded: {', '.join(excluded)}. No validated trade recommendation." if consensus and excluded else
                          "Incomplete evidence — inspect unavailable/stale timeframes." if excluded else
                          "Descriptive multi-timeframe agreement; no validated trade recommendation.",
            "sentinel": {"status": "NOT_CONSULTED", "position": "NONE",
                         "note": "No independent review obtained. Silence is not agreement."}}
