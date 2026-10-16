from __future__ import annotations

from datetime import datetime, time, timedelta
import json
from urllib.parse import parse_qs, urlparse
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from src.data.technical_engine import aggregate_five_hours, closed_candles
from src.data.technical_feeds import bse_daily, complete_calendar_bars, fx_daily_from_hourly


def candles(index):
    close = 100 + np.arange(len(index)) * .05
    return pd.DataFrame({"Open": close - .01, "High": close + .1, "Low": close - .1,
                         "Close": close, "Volume": 100.}, index=index)


def market_source(zone, opening, hours=7, days=60, close_minutes=390):
    dates = pd.bdate_range("2026-01-05", periods=days)
    indexes = []
    for day in dates:
        start = pd.Timestamp(datetime.combine(day.date(), opening), tz=zone)
        indexes.extend(pd.date_range(start, periods=hours, freq="h"))
    def schedule(_, day):
        start = pd.Timestamp(datetime.combine(day, opening), tz=zone)
        return {"status": "verified", "windows": [{"open": start, "close": start + pd.Timedelta(minutes=close_minutes)}]}
    return candles(pd.DatetimeIndex(indexes)), schedule


@pytest.mark.parametrize("symbol,zone,opening,hours,minutes", [
    ("AAPL", "America/New_York", time(9, 30), 7, 390),
    ("SAIL.BO", "Asia/Kolkata", time(9, 15), 7, 375),
    ("RELIANCE.NS", "Asia/Kolkata", time(9, 15), 7, 375),
    ("HSBA.L", "Europe/London", time(8), 9, 510),
])
def test_session_5h_handles_offset_open_and_one_complete_bin_per_day(symbol, zone, opening, hours, minutes):
    source, schedule = market_source(zone, opening, hours, close_minutes=minutes)
    result = aggregate_five_hours(source, source.index[-1] + pd.Timedelta(days=1), provider_symbol=symbol, session_loader=schedule)
    assert len(result) == 60
    assert result.index[0].hour == opening.hour and result.index[0].minute == opening.minute
    assert result.iloc[0].Close == source.iloc[4].Close
    assert result.iloc[0].Volume == 500  # closing half/quarter hour never counts
    _, facts = closed_candles(result, "5h", result.index[-1] + pd.Timedelta(hours=6))
    assert facts["last_closed_at"] == (result.index[-1] + pd.Timedelta(hours=5)).isoformat()


def test_5h_missing_hour_drops_whole_bucket():
    source, schedule = market_source("America/New_York", time(9, 30))
    missing_day = source.index[2].date()
    result = aggregate_five_hours(source.drop(source.index[2]), source.index[-1] + pd.Timedelta(days=1),
                                  provider_symbol="AAPL", session_loader=schedule)
    assert missing_day not in result.index.date and len(result) == 59


def test_short_sessions_and_unknown_calendars_do_not_become_5h_prices():
    source, schedule = market_source("America/New_York", time(9, 30), hours=4, close_minutes=210)
    with pytest.raises(ValueError, match="session shorter"):
        aggregate_five_hours(source, source.index[-1] + pd.Timedelta(days=1), provider_symbol="AAPL", session_loader=schedule)
    with pytest.raises(ValueError, match="unverified"):
        aggregate_five_hours(source, source.index[-1] + pd.Timedelta(days=1), provider_symbol="AAPL",
                             session_loader=lambda *_: {"status": "unknown", "windows": []})


def test_continuous_5h_grid_is_independent_of_pandas_timestamp_precision():
    index = pd.date_range("2026-01-01", periods=1300, freq="h", tz="UTC").as_unit("ns")
    source = candles(index)
    result = aggregate_five_hours(source, index[-1] + pd.Timedelta(hours=1))
    assert len(result) >= 259
    closed, _ = closed_candles(result, "5h", index[-1] + pd.Timedelta(hours=1))
    assert len(closed) == len(result)


@pytest.mark.parametrize("timeframe", ["1d", "1w", "1M"])
def test_single_in_progress_quote_reports_count_before_alignment(timeframe):
    source = candles(pd.DatetimeIndex([pd.Timestamp("2026-10-16", tz="Asia/Kolkata")]))
    with pytest.raises(ValueError, match="provider returned 1 bars, 0 closed"):
        closed_candles(source, timeframe, pd.Timestamp("2026-10-16T10:00:00Z"))


def test_old_corruption_restarts_all_indicators_on_a_contiguous_valid_segment():
    source = candles(pd.date_range("2020-01-01", periods=300, freq="h", tz="UTC"))
    source.iloc[20, source.columns.get_loc("High")] = 0
    result, facts = closed_candles(source, "1h", source.index[-1] + pd.Timedelta(hours=1))
    assert result.equals(source.iloc[21:])
    assert facts["invalid_closed_bars"] == 1 and facts["history_prefix_excluded"] == 21
    source.iloc[-2, source.columns.get_loc("High")] = 0
    with pytest.raises(ValueError, match="only 1 consecutive valid bars"):
        closed_candles(source, "1h", source.index[-1] + pd.Timedelta(hours=1))


def test_open_quote_cannot_poison_closed_history():
    source = candles(pd.date_range("2026-01-01", periods=260, freq="h", tz="UTC"))
    source.iloc[-1, source.columns.get_loc("High")] = np.nan
    result, facts = closed_candles(source, "1h", source.index[-1] + pd.Timedelta(minutes=30))
    assert len(result) == 259 and facts["open_bars_excluded"] == 1


def bse_reference(_):
    return {"symbol": "SAIL.BO", "name": "STEEL AUTHORITY OF INDIA LTD.", "isin": "INE114A01011"}, "500113"


def bse_payload(**changes):
    rows = [{"RealDate": str(day.date()) + "T00:00:00", "qe_open": "100.0", "qe_high": "101.0", "qe_low": "99.0", "qe_close": "100.5", "no_of_shrs": "1,23,456"}
            for day in pd.bdate_range("2026-09-01", periods=31)]
    return {"scrip_cd": "500113", "scrip_id": "SAIL", "scripname": "Steel Authority of India Ltd", "StockData": rows, **changes}


def report_transport(payload):
    def fetch(url):
        end = pd.to_datetime(parse_qs(urlparse(url).query)["YearDate"][0], format="%d/%m/%Y")
        return {**payload, "StockData": [row for row in payload["StockData"] if pd.Timestamp(row["RealDate"]) <= end][-31:]}
    return fetch


def test_bse_public_report_verifies_listing_without_claiming_history_sufficiency():
    frame, details = bse_daily("SAIL.BO", now=pd.Timestamp("2026-10-16T10:00Z"), reference=bse_reference, transport=report_transport(bse_payload()))
    assert len(frame) == 31 and frame.Volume.iloc[-1] == 123456
    assert details["identity_verified"] is True and details["history_sufficient"] is False
    assert str(frame.index.tz) == "Asia/Kolkata"
    assert details["provider_symbol"] == "SAIL.BO" and "unadjusted" in details["adjustment"]


@pytest.mark.parametrize("changes", [{"scrip_cd": "500325"}, {"scrip_id": "RELIANCE"}, {"scripname": "SailPoint Inc"}])
def test_bse_public_report_refuses_different_identity(changes):
    with pytest.raises(ValueError, match="identity mismatch"):
        bse_daily("SAIL.BO", now=pd.Timestamp("2026-10-16T10:00Z"), reference=bse_reference, transport=lambda _: bse_payload(**changes))


def test_bse_report_future_dates_are_refused():
    payload = bse_payload()
    payload["StockData"][-1]["RealDate"] = "2026-10-17T00:00:00"
    with pytest.raises(ValueError, match="beyond"):
        bse_daily("SAIL.BO", now=pd.Timestamp("2026-10-16T10:00Z"), reference=bse_reference, transport=lambda _: payload)


def fx_source():
    from src.markets.candle_sessions import session_windows
    starts = []
    for start in pd.date_range("2026-01-01", "2026-02-10", freq="h", tz="UTC", inclusive="left"):
        day = start.tz_convert("America/New_York").date()
        report = session_windows("EURUSD=X", day)
        if any(window["open"] <= start and start + pd.Timedelta(hours=1) <= window["close"] for window in report["windows"]):
            starts.append(start)
    return candles(pd.DatetimeIndex(starts))


def test_fx_daily_uses_all_scheduled_hourly_prices_and_drops_incomplete_days():
    source = fx_source()
    now = pd.Timestamp("2026-02-10T00:00Z")
    daily, details = fx_daily_from_hourly(source, "EURUSD=X", now=now)
    target = pd.Timestamp("2026-01-06", tz="UTC")
    group = source.loc[target:target + pd.Timedelta(hours=23)]
    assert daily.loc[target, "Open"] == group.Open.iloc[0]
    assert daily.loc[target, "Close"] == group.Close.iloc[-1]
    assert daily.loc[target, "Volume"] == 2400
    assert not any(daily.index.dayofweek == 5)
    missing = source.drop(target + pd.Timedelta(hours=4))
    broken, facts = fx_daily_from_hourly(missing, "EURUSD=X", now=now)
    assert target not in broken.index and facts["incomplete_days_excluded"] >= 1
    assert details["independent_price_validation"] == "not performed"


def test_fx_friday_kept_when_provider_prints_an_hour_after_the_close():
    # Yahoo's EURUSD=X hourly feed carries Friday's 17:00-18:00 New York bar,
    # one hour past the verified FX week close. Requiring an exact match
    # dropped every Friday; the extra bar must be left out, not the day.
    source = fx_source()
    friday = pd.Timestamp("2026-01-09", tz="UTC")
    scheduled = source.loc[friday:friday + pd.Timedelta(hours=23)]
    late = scheduled.index[-1] + pd.Timedelta(hours=1)
    extra = scheduled.iloc[[-1]].copy()
    extra.index = pd.DatetimeIndex([late])
    extra[["Open", "High", "Low", "Close"]] = [500., 900., 1., 2.]
    daily, details = fx_daily_from_hourly(pd.concat([source, extra]).sort_index(), "EURUSD=X",
                                          now=pd.Timestamp("2026-02-10T00:00Z"))
    assert friday in daily.index
    assert daily.loc[friday, "High"] == scheduled.High.max()
    assert daily.loc[friday, "Close"] == scheduled.Close.iloc[-1]
    assert details["out_of_session_bars_excluded"] == 1
    assert {0, 1, 2, 3, 4} <= set(daily.index.dayofweek)


def test_fx_unknown_hours_are_refused():
    with pytest.raises(ValueError, match="calendar is unverified"):
        fx_daily_from_hourly(fx_source(), "EURUSD=X", now=pd.Timestamp("2026-02-10T00:00Z"),
                             session_loader=lambda *_: {"status": "unknown", "windows": []})


def test_weekly_aggregation_never_fills_a_missing_trading_day(monkeypatch):
    from src.markets import candle_sessions
    def weekdays(_, day):
        return {"status": "verified" if day.weekday() < 5 else "closed", "windows": [1] if day.weekday() < 5 else []}
    monkeypatch.setattr(candle_sessions, "session_windows", weekdays)
    source = candles(pd.bdate_range("2026-01-05", periods=40, tz="Asia/Kolkata"))
    now = source.index[-1] + pd.Timedelta(days=10)
    complete = complete_calendar_bars(source, "1w", "SAIL.BO", now=now)
    assert len(complete) == 8
    broken = complete_calendar_bars(source.drop(source.index[2]), "1w", "SAIL.BO", now=now)
    assert source.index[0] not in broken.index and len(broken) == 7


def test_daily_identity_refetch_skips_unbound_cache_then_reuses_bound_cache(tmp_path, monkeypatch):
    from src.v5 import marketdata as md, vendors
    monkeypatch.setattr(md, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(md, "RAW_DIR", tmp_path / "raw")
    for name in ("_MEM", "_MISSES", "_PROVENANCE", "_FETCH_LOCKS"):
        monkeypatch.setattr(md, name, {})
    monkeypatch.setattr(md, "resolve", lambda _: "AAPL")
    frame = candles(pd.date_range("2026-01-01", periods=260, freq="D"))
    calls = []
    def fetch(*_):
        calls.append(1)
        return frame, {"vendor": "yfinance", "is_primary": True, "adjustment": "split+dividend", "attempts": [],
                       "fetched_at": "2026-10-16T00:00:00", "identity": {"symbol": "AAPL", "exchangeName": "NMS", "currency": "USD"}}
    monkeypatch.setattr(vendors, "fetch", fetch)
    md.CACHE_DIR.mkdir()
    old = frame.copy(); old.Close += 10
    old.to_csv(md._cache_path("AAPL", "1d"))
    assert md.history("AAPL", verify_identity=True).Close.iloc[-1] == frame.Close.iloc[-1]
    assert md.history("AAPL", verify_identity=True) is not None and len(calls) == 1
    md._MEM.clear(); md._PROVENANCE.clear()
    assert md.history("AAPL", verify_identity=True) is not None and len(calls) == 1
    assert md.provenance("AAPL")["identity"]["symbol"] == "AAPL"


def test_yahoo_wrong_reply_identity_never_enters_central_cache(monkeypatch):
    import sys
    from src.v5 import vendors
    frame = candles(pd.date_range("2026-01-01", periods=40, freq="D"))
    fake = SimpleNamespace(history=lambda **_: frame, history_metadata={"symbol": "WRONG", "currency": "USD", "exchangeName": "NMS"})
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda _: fake))
    result = vendors.fetch_yfinance("AAPL")
    assert not result.ok and "different instrument" in result.error


def test_bse_walks_bounded_public_ranges_and_rechecks_every_listing():
    payload = bse_payload()
    payload['StockData'] = [{**payload['StockData'][0], 'RealDate': str(day.date()) + 'T00:00:00'}
                            for day in pd.bdate_range('2025-10-16', '2026-10-15')]
    calls = []
    fetch = report_transport(payload)
    def transport(url):
        calls.append(url)
        return fetch(url)
    frame, details = bse_daily('SAIL.BO', now=pd.Timestamp('2026-10-16T10:00Z'), reference=bse_reference, transport=transport)
    assert len(calls) <= 9 and len(frame) >= 250 and not frame.index.has_duplicates
    assert details['history_sufficient'] is True
    calls.clear()
    def switched(url):
        calls.append(url)
        return {**fetch(url), **({'scrip_cd': '500325'} if len(calls) == 2 else {})}
    with pytest.raises(ValueError, match='identity mismatch'):
        bse_daily('SAIL.BO', now=pd.Timestamp('2026-10-16T10:00Z'), reference=bse_reference, transport=switched)


def test_multi_bin_session_5h_and_missing_volume_are_not_joined_or_invented():
    source, schedule = market_source('America/New_York', time(0), hours=12, close_minutes=720)
    source.loc[source.index[2], 'Volume'] = np.nan
    result = aggregate_five_hours(source, source.index[-1] + pd.Timedelta(days=1), provider_symbol='AAPL', session_loader=schedule)
    assert len(result) == 120 and list(result.index[:2].hour) == [0, 5]
    assert result.iloc[0].Close == source.iloc[4].Close
    assert result.iloc[1].Open == source.iloc[5].Open
    assert pd.isna(result.iloc[0].Volume) and result.iloc[1].Volume == 500
    assert result.attrs['session_grid_verified'] is True
    # attrs must survive the validator copies on supported pandas versions.
    checked, _ = closed_candles(result, '5h', source.index[-1] + pd.Timedelta(days=1))
    assert checked.attrs['session_grid_verified'] is True


@pytest.mark.parametrize('sidecar', [{}, {'provider_symbol': 'WRONG', 'identity': {'symbol': 'AAPL'}}])
def test_failed_vendor_never_serves_unbound_stale_cache_to_identity_request(tmp_path, monkeypatch, sidecar):
    from src.v5 import marketdata as md, vendors
    monkeypatch.setattr(md, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(md, 'resolve', lambda _: 'AAPL')
    for name in ('_MEM', '_MISSES', '_PROVENANCE', '_FETCH_LOCKS'):
        monkeypatch.setattr(md, name, {})
    monkeypatch.setattr(vendors, 'fetch', lambda *_: (None, {'attempts': []}))
    candles(pd.date_range('2026-01-01', periods=260, freq='D')).to_csv(md._cache_path('AAPL', '1d'))
    md._cache_meta_path('AAPL', '1d').write_text(json.dumps(sidecar))
    assert md.history('AAPL', verify_identity=True) is None
    assert md.provenance('AAPL')['served_from'] is None


def test_bound_stale_cache_remains_inspectable_and_stale_after_vendor_failure(tmp_path, monkeypatch):
    from src.v5 import marketdata as md, vendors
    monkeypatch.setattr(md, 'CACHE_DIR', tmp_path)
    monkeypatch.setattr(md, 'resolve', lambda _: 'AAPL')
    monkeypatch.setattr(md, '_age_days', lambda _: 20)
    monkeypatch.setattr(vendors, 'fetch', lambda *_: (None, {'attempts': []}))
    candles(pd.date_range('2026-01-01', periods=260, freq='D')).to_csv(md._cache_path('AAPL', '1d'))
    md._cache_meta_path('AAPL', '1d').write_text(json.dumps({'provider_symbol': 'AAPL', 'identity': {'symbol': 'AAPL'}}))
    df, meta = md._from_vendors('AAPL', '1d', {'provider_symbol': 'AAPL'}, verify_identity=True)
    assert df is not None and meta['served_from'] == 'stale_cache' and meta['degraded']


def test_unverified_provider_reply_never_becomes_current_recommendation(monkeypatch):
    from src.data import technical_recommendations as technical
    source = candles(pd.date_range('2025-01-01', periods=260, freq='D', tz='UTC'))
    monkeypatch.setattr(technical, 'instrument', lambda symbol: {'provider_symbol': 'BTC-USD', 'requested': symbol, 'kind': 'provider_instrument'})
    monkeypatch.setattr(technical, 'load_frame', lambda *a, **k: (source, {'identity_verified': False}))
    result = technical.analyze('BTC-USD', '1d', now=source.index[-1] + pd.Timedelta(days=1))
    assert not result.get('error') and result['summary'] is None
    assert result['data_quality'] == 'identity_unverified' and result['verification']['identity'] == 'unverified'


def test_identity_error_never_triggers_public_recovery(monkeypatch):
    from src.data import technical_recommendations as technical
    monkeypatch.setattr(technical, 'instrument', lambda symbol: {'provider_symbol': 'SAIL.BO', 'requested': symbol, 'kind': 'provider_instrument'})
    def wrong(*a, **k):
        raise ValueError('provider returned a different provider instrument')
    monkeypatch.setattr(technical, 'load_frame', wrong)
    def recovery(*a, **k):
        pytest.fail('identity mismatch must refuse without recovery')
    monkeypatch.setattr(technical, '_recover_history', recovery)
    result = technical.analyze('SAIL.BO', '1d')
    assert result['summary'] is None and 'different provider instrument' in result['error']
