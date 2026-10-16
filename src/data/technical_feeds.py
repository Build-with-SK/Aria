"""Public same-instrument recovery feeds. No credential or venue substitution.

BSE's public stock-price report is checked against its own code, ticker and
company name. FX daily candles can be reconstructed from that exact Yahoo
pair's closed hourly OHLC, with complete scheduled UTC-day coverage. Neither
path invents prices or promises history that the supplier does not return.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache" / "technical_feeds"
BSE_HISTORY = "https://api.bseindia.com/BseIndiaAPI/api/StockpricesearchData/w"
BSE_PAGE = "https://www.bseindia.com/markets/equity/EQReports/StockPrcHistori"


def _bse_reference(provider):
    from src.data.universe import get_universe
    manager = get_universe()
    row = manager._lookup(provider)
    if not row:
        with manager._conn() as conn:
            rows = conn.execute("SELECT * FROM symbols WHERE UPPER(yahoo)=?", (provider.upper(),)).fetchall()
        if len(rows) != 1:
            raise ValueError("BSE provider has no unique catalogue identity")
        row = dict(rows[0])
    if row.get("exchange") != "BSE" or row.get("asset_class") != "equity" or not row.get("isin"):
        raise ValueError("BSE exchange/ISIN reference unavailable")
    code = str(row.get("yahoo") or "").removesuffix(".BO")
    if not re.fullmatch(r"\d{6}", code):
        proof = json.loads(manager._meta_get(f"provider_repair:{row['symbol']}") or "{}")
        if not (proof.get("verified") is True and proof.get("isin") == row["isin"] and proof.get("candidate") == provider):
            raise ValueError("BSE numeric code is not bound to this verified ISIN")
        code = str(proof.get("old_provider") or "").removesuffix(".BO")
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("verified six-digit BSE code unavailable")
    return row, code


def bse_daily(provider, *, now=None, reference=None, transport=None):
    import pandas as pd
    from src.data.provider_audit import _company
    if not re.fullmatch(r"[A-Z0-9&._-]+\.BO", provider):
        raise ValueError("not a BSE provider symbol")
    row, code = (reference or _bse_reference)(provider)
    current = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    if current.tzinfo is None:
        raise ValueError("timezone-aware feed as-of required")
    end = current.tz_convert("Asia/Kolkata").normalize() - pd.Timedelta(days=1)
    params = {"MonthDate": (end - pd.DateOffset(years=1) + pd.Timedelta(days=1)).strftime("%d/%m/%Y"), "YearDate": end.strftime("%d/%m/%Y"),
              "pageType": "0", "Scode": code, "Seg": "C", "rbType": "D", "SortOrder": "true"}
    path = CACHE_DIR / f"bse_{provider}.json"
    payload = None
    cache_hit = False
    fetched_at = current.isoformat()
    if transport is None and path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            age = (current - pd.Timestamp(stored["fetched_at"])).total_seconds()
            if (stored.get("format_version") == 2 and 0 <= age < 6 * 3600 and stored["isin"] == row["isin"] and stored["code"] == code
                    and stored["provider_symbol"] == provider and stored["through"] == params["YearDate"]):
                payload, cache_hit = stored["payload"], True
                fetched_at = stored["fetched_at"]
        except (ValueError, KeyError, TypeError):
            pass
    if payload is None:
        # The official report caps each response at 31 daily records. Walk its
        # public date ranges backwards, with a hard request bound. No CSV/login
        # bypass, ticker substitution, or assertion that all history was returned.
        records = []
        page_end = end
        for _ in range(9):
            query = {**params, "YearDate": page_end.strftime("%d/%m/%Y")}
            url = BSE_HISTORY + "?" + urllib.parse.urlencode(query)
            if transport:
                page = transport(url)
            else:
                request = urllib.request.Request(url, headers={"User-Agent": "ARIA/5.0 public market-data research",
                                                             "Referer": "https://www.bseindia.com/", "Accept": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=12) as response:
                        page = json.loads(response.read(8_000_001).decode("utf-8"))
                except Exception as exc:
                    raise ValueError(f"public BSE report unavailable ({type(exc).__name__})") from None
            if (not isinstance(page, dict) or str(page.get("scrip_cd") or "") != code
                    or str(page.get("scrip_id") or "").upper() != row["symbol"].removesuffix(".BO").upper()
                    or not page.get("scripname") or _company(page["scripname"]) != _company(row.get("name"))):
                raise ValueError("BSE response listing identity mismatch")
            batch = page.get("StockData")
            if not isinstance(batch, list):
                raise ValueError("BSE report daily history is not an array")
            if not batch:
                payload = {**page, "StockData": records}
                break
            days = [pd.Timestamp(value["RealDate"]) for value in batch]
            if any(day.tzinfo is not None or day != day.normalize() or day.date() > page_end.date()
                   or day.date() < pd.to_datetime(query["MonthDate"], format="%d/%m/%Y").date() for day in days):
                raise ValueError("BSE report returned dates beyond its requested closed-day range")
            records.extend(batch)
            payload = {**page, "StockData": records}
            if len(records) >= 260:
                break
            page_end = min(days).tz_localize("Asia/Kolkata") - pd.Timedelta(days=1)
            if page_end.date() < pd.to_datetime(params["MonthDate"], format="%d/%m/%Y").date():
                break
    if not isinstance(payload, dict):
        raise ValueError("BSE report is not an object")
    if (str(payload.get("scrip_cd") or "") != code or str(payload.get("scrip_id") or "").upper() != row["symbol"].removesuffix(".BO").upper()
            or not payload.get("scripname") or _company(payload["scripname"]) != _company(row.get("name"))):
        raise ValueError("BSE response listing identity mismatch")
    values = payload.get("StockData")
    if not isinstance(values, list) or not values:
        raise ValueError("BSE report returned no daily OHLC history")
    records, dates = [], []
    for value in values:
        day = pd.Timestamp(value["RealDate"])
        if day.tzinfo is not None or day != day.normalize():
            raise ValueError("BSE report date is not a local date-only candle")
        if day.date() > end.date():
            raise ValueError("BSE report returned dates beyond its requested closed-day range")
        def number(key):
            return float(str(value[key]).replace(",", ""))
        records.append({"Open": number("qe_open"), "High": number("qe_high"),
                        "Low": number("qe_low"), "Close": number("qe_close"),
                        **({"Volume": number("no_of_shrs")} if value.get("no_of_shrs") is not None else {})})
        dates.append(day)
    frame = pd.DataFrame(records, index=pd.DatetimeIndex(dates).tz_localize("Asia/Kolkata")).sort_index()
    if not cache_hit and transport is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"format_version": 2, "provider_symbol": provider, "code": code, "isin": row["isin"],
                                    "fetched_at": current.isoformat(), "through": params["YearDate"], "payload": payload}), encoding="utf-8")
    return frame, {"provider": "BSE public stock-price report", "provider_symbol": provider,
                   "exchange": "BSE", "exchange_timezone": "Asia/Kolkata", "currency": "INR",
                   "identity_verified": True, "identity_basis": "official response code/ticker/full company name matched to catalogue ISIN-bound code",
                   "official_scrip_code": code, "isin": row["isin"], "source_url": BSE_PAGE,
                   "adjustment": "unadjusted; corporate actions not independently verified", "cache_hit": cache_hit,
                   "provider_bars_returned": len(frame), "history_sufficient": len(frame) >= 200,
                   "fetched_at": fetched_at, "history_request_limit": "at most nine public date-range requests within one year; no claim of complete provider history",
                   "upstream_stale": (current - (frame.index[-1] + pd.DateOffset(days=1))).total_seconds() > 5 * 86400,
                   "independent_price_validation": "not performed"}


def complete_calendar_bars(daily, timeframe, symbol, *, now=None):
    """Build weekly/monthly OHLC only from complete verified daily coverage."""
    import pandas as pd
    from src.markets.candle_sessions import session_windows
    from pandas.tseries.offsets import DateOffset
    if timeframe == "1d":
        return daily
    if timeframe not in ("1w", "1M"):
        raise ValueError("unsupported public-feed calendar aggregation")
    rule = "W-MON" if timeframe == "1w" else "MS"
    offset = DateOffset(weeks=1) if timeframe == "1w" else DateOffset(months=1)
    current = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    groups = daily.resample(rule, closed="left", label="left")
    rows, starts = [], []
    for start, group in groups:
        end = start + offset
        if end > current:
            continue
        expected = []
        for day in pd.date_range(start, end - pd.Timedelta(days=1), freq="D"):
            report = session_windows(symbol, day.date())
            if report["status"] == "unknown":
                expected = None
                break
            if report["windows"]:
                expected.append(day.date())
        if not expected or set(group.index.date) != set(expected):
            continue
        rows.append({"Open": group.Open.iloc[0], "High": group.High.max(), "Low": group.Low.min(), "Close": group.Close.iloc[-1],
                     **({"Volume": group.Volume.sum(min_count=len(group))} if "Volume" in group else {})})
        starts.append(start)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(starts, tz=daily.index.tz), columns=daily.columns)


def fx_daily_from_hourly(hourly, provider, *, now=None, session_loader=None):
    """Derive UTC-day bid/quote OHLC without mixing a different pair/vendor."""
    import pandas as pd
    from src.data.technical_engine import closed_candles
    from src.markets.candle_sessions import session_windows
    if not re.fullmatch(r"[A-Z]{6}=X", provider):
        raise ValueError("FX reconstruction requires an explicit six-letter currency pair")
    current = pd.Timestamp(now or pd.Timestamp.now(tz="UTC"))
    source, validation = closed_candles(hourly, "1h", current)
    source = source.tz_convert("UTC")
    loader = session_loader or session_windows
    reports = {}
    rows, starts, excluded, outside_bars = [], [], 0, 0
    for start, group in source.resample("D", closed="left", label="left"):
        end = start + pd.Timedelta(days=1)
        if end > current:
            continue
        expected = []
        for hour in pd.date_range(start, periods=24, freq="h"):
            local_day = hour.tz_convert("America/New_York").date()
            report = reports.setdefault(local_day, loader(provider, local_day)) if local_day not in reports else reports[local_day]
            if report["status"] == "unknown":
                raise ValueError("FX hourly reconstruction calendar is unverified")
            if any(pd.Timestamp(window["open"]) <= hour and hour + pd.Timedelta(hours=1) <= pd.Timestamp(window["close"]) for window in report["windows"]):
                expected.append(hour)
        if not expected:
            if len(group):
                raise ValueError("FX provider returned candles outside its verified trading windows")
            continue
        # Every scheduled hour must be present. A provider bar after the
        # verified close (Yahoo prints Friday's 17:00-18:00 New York hour) is
        # outside the session: left out of the candle and counted, not a
        # reason to drop the whole day. Dropping it would silently remove
        # every Friday from the series.
        if not set(expected) <= set(group.index):
            excluded += 1
            continue
        outside = group.index.difference(pd.DatetimeIndex(expected))
        if len(outside):
            outside_bars += len(outside)
            group = group.drop(outside)
        rows.append({"Open": group.Open.iloc[0], "High": group.High.max(), "Low": group.Low.min(), "Close": group.Close.iloc[-1],
                     **({"Volume": group.Volume.sum(min_count=len(group))} if "Volume" in group else {})})
        starts.append(start)
    result = pd.DataFrame(rows, index=pd.DatetimeIndex(starts, tz="UTC"), columns=source.columns.intersection(["Open", "High", "Low", "Close", "Volume"]))
    return result, {"alignment": "UTC-day OHLC reconstructed from complete scheduled closed hourly candles of the exact same Yahoo pair",
                    "incomplete_days_excluded": excluded, "out_of_session_bars_excluded": outside_bars,
                    "hourly_source_validation": validation,
                    "reconstructed": True, "reconstruction_calendar_verified": True,
                    "independent_price_validation": "not performed"}
