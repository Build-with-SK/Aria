"""
tests/test_data_quality.py
==========================
Series-level checks, and the adjustment convention travelling with the data.

Found 2026-10-06: the V5 vendor chain mixes conventions — yfinance is split-
and dividend-adjusted, Alpha Vantage's free TIME_SERIES_DAILY is as traded —
and nothing recorded which one a series used. Worse, the disk cache stored a
fallback vendor's series with no record of who filled it and served it for
six hours as clean, un-degraded data.
"""
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5 import dataquality as DQ


def _series(n=60, split_at=None, factor=4.0, crash_at=None, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=n)
    close = np.linspace(100, 110, n)
    if split_at is not None:
        close[split_at:] = close[split_at:] / factor
    if crash_at is not None:
        close[crash_at:] = close[crash_at:] * 0.6
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close}, index=idx)


def test_a_clean_adjusted_series_is_ok():
    assert DQ.assess(_series(), adjustment="split+dividend")["status"] == "ok"


def test_an_unadjusted_split_is_bad():
    q = DQ.assess(_series(split_at=30, factor=4), adjustment="none")
    assert q["status"] == "bad"
    assert "probable unadjusted split" in q["issues"][0]


def test_the_same_jump_on_an_adjusted_series_is_only_a_warning():
    """A crash happens. On an adjusted series a big bar is information, not
    an error — flagging legitimate events as bad data is its own failure."""
    q = DQ.assess(_series(crash_at=30), adjustment="split+dividend")
    assert q["status"] == "warn"
    assert q["jumps"][0]["date"]


def test_reverse_splits_are_recognised():
    assert DQ._split_factor(10.0) == 10
    assert DQ._split_factor(0.25) == pytest.approx(0.25)
    assert DQ._split_factor(0.6) is None              # a 40% fall is not a split ratio


def test_non_positive_and_future_bars_are_bad():
    df = _series()
    df.iloc[5, df.columns.get_loc("Close")] = 0.0
    assert DQ.assess(df, adjustment="split+dividend")["status"] == "bad"
    future = _series(start=(datetime.now() + timedelta(days=30)).date().isoformat(), n=10)
    assert "future" in " ".join(DQ.assess(future, adjustment="split+dividend")["issues"])


def test_gaps_and_ohlc_inconsistency_warn():
    df = _series()
    df = df.drop(df.index[20:30])                      # a ten-business-day hole
    df.iloc[3, df.columns.get_loc("High")] = df.iloc[3]["Low"] * 0.9
    q = DQ.assess(df, adjustment="split+dividend")
    assert q["status"] == "warn"
    assert any("gap" in w for w in q["warnings"])
    assert any("High below Low" in w for w in q["warnings"])


def test_unknown_adjustment_is_never_assumed_adjusted():
    q = DQ.assess(_series(), adjustment="unknown")
    assert "adjustment convention unknown" in q["warnings"]


# ── the vendor refuses a raw series with a split in it ──────────────────────

def test_alphavantage_refuses_a_series_spanning_a_split(monkeypatch):
    from src.v5 import vendors
    df = _series(split_at=30, factor=2)
    series = {d.date().isoformat(): {"1. open": c, "2. high": c, "3. low": c,
                                     "4. close": c, "5. volume": 1}
              for d, c in df["Close"].items()}
    monkeypatch.setattr(vendors, "alphavantage_key", lambda: "k")
    monkeypatch.setattr(vendors, "_get", lambda url, timeout=12: json.dumps(
        {"Time Series (Daily)": series}))
    res = vendors.fetch_alphavantage("XYZ")
    assert not res.ok
    assert "probable split" in res.error


def test_the_chain_reports_the_adjustment_of_whoever_served(monkeypatch):
    from src.v5 import vendors
    ok = vendors.VendorResult("stooq", df=_series())
    monkeypatch.setattr(vendors, "CHAIN", [
        ("yfinance", lambda *a: vendors.VendorResult("yfinance", error="down")),
        ("stooq", lambda *a: ok)])
    df, report = vendors.fetch("XYZ")
    assert report["vendor"] == "stooq" and report["adjustment"] == "split"


# ── the cache keeps what the vendor fetch knew ──────────────────────────────

def test_a_cached_fallback_series_stays_degraded(tmp_path, monkeypatch):
    from src.v5 import marketdata as md, vendors
    monkeypatch.setattr(md, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(md, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(md, "resolve", lambda s: s)
    md._MEM.clear(); md._MISSES.clear(); md._PROVENANCE.clear()
    monkeypatch.setattr(vendors, "fetch", lambda *a, **k: (_series(), {
        "vendor": "alphavantage", "adjustment": "none", "is_primary": False,
        "attempts": [], "fetched_at": "2026-10-06T10:00:00"}))
    md.history("XYZ")
    first = md.provenance("XYZ")
    assert first["adjustment"] == "none" and first["degraded"] is True

    md._MEM.clear()                                   # next read comes from disk
    md.history("XYZ")
    again = md.provenance("XYZ")
    assert again["served_from"] == "disk_cache"
    assert again["vendor"] == "alphavantage"
    assert again["adjustment"] == "none"
    assert again["degraded"] is True and again["stale"] is True
    md._MEM.clear(); md._PROVENANCE.clear()


def test_quality_reaches_the_analysis_data_block():
    from src.v5.pipeline import _data_block
    block = _data_block("XYZ", {"served_from": "vendor:yfinance", "stale": False,
                                "adjustment": "split+dividend",
                                "quality": {"status": "warn", "issues": [],
                                            "warnings": ["1 gap"]}})
    assert block["adjustment"] == "split+dividend"
    assert block["quality"] == "warn" and block["quality_notes"] == ["1 gap"]


# ── namespaces: raw files are looked up by the resolved provider symbol ─────

def test_raw_files_are_read_by_provider_symbol_not_display_symbol(tmp_path, monkeypatch):
    """data/raw/BA.csv is the signals pipeline's BOEING file; resolve("BA") in
    the master namespace is BA.L (BAE Systems, pence). Reading raw by the bare
    symbol would hand V5 Boeing on days the pipeline ran and BAE otherwise."""
    from src.v5 import marketdata as md, vendors
    raw = tmp_path / "raw"; raw.mkdir()
    _series().to_csv(raw / "BA.csv")                    # Boeing, from the pipeline
    monkeypatch.setattr(md, "RAW_DIR", raw)
    monkeypatch.setattr(md, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(md, "resolve", lambda s: "BA.L" if s == "BA" else s)
    md._MEM.clear(); md._MISSES.clear(); md._PROVENANCE.clear()
    seen = []
    monkeypatch.setattr(vendors, "fetch", lambda sym, *a, **k: (seen.append(sym) or _series(), {
        "vendor": "yfinance", "adjustment": "split+dividend", "is_primary": True,
        "attempts": [], "fetched_at": "2026-10-06T10:00:00"}))
    md.history("BA")
    prov = md.provenance("BA")
    assert prov["served_from"] == "vendor:yfinance"     # not Boeing's raw file
    assert prov["provider_symbol"] == "BA.L"
    md._MEM.clear(); md._PROVENANCE.clear()
