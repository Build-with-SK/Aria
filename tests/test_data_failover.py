"""
tests/test_data_failover.py
===========================
PHASE 2 — data reliability.

Two failures were possible before this, and they looked the same from outside:
the primary vendor goes down and the system serves whatever was left on disk,
or the primary vendor goes down and the system falls over. Neither told anyone.

So every test here removes the primary vendor and asserts on what the system
DOES and what it SAYS. A test that only proves "it did not crash" would have
passed against the old silent-stale-cache behaviour, which is exactly the bug.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.v5 import marketdata as md, vendors     # noqa: E402


def _frame(days: int = 400, end: datetime | None = None) -> pd.DataFrame:
    """A plausible daily OHLCV series ending `end` (default: today)."""
    end = end or datetime.now()
    idx = pd.bdate_range(end=end.date(), periods=days)
    close = pd.Series(range(days), dtype=float) + 100.0
    return pd.DataFrame({"Open": close.values, "High": close.values + 1,
                         "Low": close.values - 1, "Close": close.values,
                         "Volume": [1e6] * days}, index=idx)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    """No shared memo, no shared cache directory, no real network."""
    md._MEM.clear()
    md._MISSES.clear()
    md._PROVENANCE.clear()
    monkeypatch.setattr(md, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(md, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(md, "resolve", lambda s: s)
    yield
    md._MEM.clear()
    md._PROVENANCE.clear()


def kill(*vendor_names, working: dict | None = None, monkeypatch=None):
    """Replace the vendor chain: named vendors fail, `working` ones return data."""
    working = working or {}

    def make(name):
        def fn(symbol, interval="1d", period="10y"):
            if name in working:
                return vendors.VendorResult(name, df=working[name])
            return vendors.VendorResult(name, error=f"{name} is down (simulated)")
        return fn

    chain = [(name, make(name)) for name, _ in vendors.CHAIN]
    monkeypatch.setattr(vendors, "CHAIN", chain)


# ── the chain does what it claims ────────────────────────────────────────────

def test_primary_is_used_when_it_works(monkeypatch):
    kill(working={"yfinance": _frame()}, monkeypatch=monkeypatch)
    df, report = vendors.fetch("AAPL")
    assert df is not None
    assert report["vendor"] == "yfinance"
    assert report["is_primary"] is True


def test_failover_reaches_the_second_vendor(monkeypatch):
    """THE test the phase exists for: primary dead, data still arrives."""
    kill(working={"alphavantage": _frame()}, monkeypatch=monkeypatch)
    df, report = vendors.fetch("AAPL")
    assert df is not None and not df.empty
    assert report["vendor"] == "alphavantage"
    assert report["is_primary"] is False


def test_failover_reaches_the_third_vendor(monkeypatch):
    """The second needs an API key. The third does not — which is what makes
    the fallback real on a machine where nobody configured anything."""
    kill(working={"stooq": _frame()}, monkeypatch=monkeypatch)
    df, report = vendors.fetch("AAPL")
    assert df is not None
    assert report["vendor"] == "stooq"


def test_every_vendor_failure_is_recorded_not_just_the_last(monkeypatch):
    kill(working={"stooq": _frame()}, monkeypatch=monkeypatch)
    _, report = vendors.fetch("AAPL")
    attempts = {a["vendor"]: a for a in report["attempts"]}
    assert attempts["yfinance"]["ok"] is False and attempts["yfinance"]["error"]
    assert attempts["alphavantage"]["ok"] is False
    assert attempts["stooq"]["ok"] is True


def test_total_outage_returns_no_data_and_says_so(monkeypatch):
    kill(monkeypatch=monkeypatch)
    df, report = vendors.fetch("AAPL")
    assert df is None
    assert report["vendor"] is None
    assert len(report["attempts"]) == len(vendors.CHAIN)


def test_the_chain_is_not_decorative():
    """A fallback list with one real entry is a single point of failure with
    extra steps."""
    assert len(vendors.CHAIN) >= 3
    assert vendors.PRIMARY == "yfinance"
    names = [n for n, _ in vendors.CHAIN]
    assert len(set(names)) == len(names)
    # At least one fallback must need no credentials, or a fresh deployment has
    # no fallback at all.
    keyless = [n for n in names if n in ("yfinance", "stooq")]
    assert keyless, "every fallback requires an API key"


# ── failover through the real loader, with labelling ─────────────────────────

def test_failover_data_is_served_and_labelled_as_fallback(monkeypatch):
    """Fresh data from a fallback vendor is still degraded: the primary is
    down, and that is an operational fact somebody has to be able to see."""
    kill(working={"stooq": _frame()}, monkeypatch=monkeypatch)

    df = md.history("AAPL", period="1y")
    assert df is not None and len(df) > 100

    prov = md.provenance("AAPL")
    assert prov["served_from"] == "vendor:stooq"
    assert prov["vendor"] == "stooq"
    assert prov["degraded"] is True
    assert prov["stale"] is True
    assert "yfinance" in prov["stale_reason"] or "FALLBACK" in prov["detail"]
    assert "Stooq" in md.source_label("AAPL")


def test_fresh_primary_data_is_not_labelled_stale(monkeypatch):
    """The counterpart. A flag that is always on is not a flag."""
    kill(working={"yfinance": _frame()}, monkeypatch=monkeypatch)
    assert md.history("AAPL", period="1y") is not None
    prov = md.provenance("AAPL")
    assert prov["stale"] is False
    assert prov["degraded"] is False
    assert md.is_stale("AAPL") is False
    assert "STALE" not in md.source_label("AAPL")


def test_old_data_from_a_working_primary_is_still_stale(monkeypatch):
    """Staleness is about the data, not only about the plumbing. A vendor that
    happily serves last month's bars has not given us current prices."""
    kill(working={"yfinance": _frame(end=datetime.now() - timedelta(days=30))},
         monkeypatch=monkeypatch)
    assert md.history("AAPL", period="1y") is not None
    prov = md.provenance("AAPL")
    assert prov["stale"] is True
    assert prov["last_bar_age_days"] > md.STALE_AFTER_DAYS
    assert "days old" in prov["stale_reason"]


def test_a_weekend_does_not_make_friday_stale(monkeypatch):
    """Friday's close read on Monday is normal, not a fault. A staleness rule
    that cries wolf every Monday gets switched off."""
    kill(working={"yfinance": _frame(end=datetime.now() - timedelta(days=3))},
         monkeypatch=monkeypatch)
    assert md.history("AAPL", period="1y") is not None
    assert md.provenance("AAPL")["stale"] is False


def test_stale_cache_is_used_but_never_passed_off_as_fresh(monkeypatch, tmp_path):
    """The original finding. Old data is still allowed — this is a research
    system that cites its dates — but it comes back labelled."""
    # Seed the on-disk cache with something old, then take every vendor down.
    md.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    old = _frame(end=datetime.now() - timedelta(days=9))
    old.to_csv(md._cache_path("AAPL", "1d"))
    import os
    stamp = (datetime.now() - timedelta(days=9)).timestamp()
    os.utime(md._cache_path("AAPL", "1d"), (stamp, stamp))

    kill(monkeypatch=monkeypatch)

    df = md.history("AAPL", period="1y")
    assert df is not None, "usable cached data was discarded"

    prov = md.provenance("AAPL")
    assert prov["served_from"] == "stale_cache"
    assert prov["stale"] is True
    assert "every vendor failed" in prov["detail"]
    label = md.source_label("AAPL")
    assert "STALE" in label


def test_no_data_anywhere_is_not_silently_ok(monkeypatch):
    kill(monkeypatch=monkeypatch)
    assert md.history("NOSUCH", period="1y") is None
    assert md.is_stale("NOSUCH") is True


def test_is_stale_treats_unknown_as_stale():
    """A symbol nobody loaded is not fresh — it is unknown, and unknown must
    not read as fine."""
    assert md.is_stale("NEVER-LOADED-SYMBOL") is True


# ── the label reaches the signal ─────────────────────────────────────────────

def test_stale_data_cuts_confidence_in_the_meta_layer():
    """Propagation, not decoration: the penalty is applied where the risk gate
    and the prediction log will both inherit it."""
    from src.v5 import meta as meta_mod
    from src.v5.contract import ModuleReport
    from src.v5.ensemble import synthesise

    reports = [ModuleReport(module=f"m{i}", family="technical", ticker="AAPL",
                            bull=70, bear=10, neutral=20, horizon_days=21,
                            ci_low=0.55, ci_high=0.80, thesis="up")
               for i in range(6)]
    ens = synthesise(reports, "AAPL")

    fresh = meta_mod.review("AAPL", ens, reports, data_quality={"stale": False})
    stale = meta_mod.review("AAPL", ens, reports,
                            data_quality={"stale": True,
                                          "stale_reason": "cache is 9 days old"})

    assert stale.confidence_after < fresh.confidence_after
    assert "not current" in stale.adjustment_reason
    assert "9 days old" in stale.adjustment_reason
    # The penalty acts on the edge, so it can never flip the direction.
    assert stale.confidence_after >= 0.5
    assert stale.edge_after < fresh.edge_after


def test_the_api_response_carries_the_data_block():
    """A caller must be able to see the source without reading server logs."""
    from src.v5 import pipeline
    block = pipeline._data_block("AAPL", {
        "served_from": "stale_cache", "vendor": None, "stale": True,
        "last_bar": "2026-07-28", "last_bar_age_days": 9.0,
        "stale_reason": "every vendor failed; cache is 9.0 days old"})
    assert block["stale"] is True
    assert block["last_bar"] == "2026-07-28"
    assert "not current" in block["warning"]

    fresh = pipeline._data_block("AAPL", {
        "served_from": "vendor:yfinance", "vendor": "yfinance", "stale": False,
        "last_bar": "2026-08-06", "last_bar_age_days": 0.4, "stale_reason": ""})
    assert fresh["stale"] is False
    assert "warning" not in fresh


def test_a_stale_recommendation_does_not_read_like_a_fresh_one():
    """The headline is what gets quoted. It has to carry the caveat itself."""
    from src.v5 import pipeline

    class Ens:
        ticker, direction, n_voting, n_modules = "AAPL", "bull", 6, 41
        net_score, mean_ci_width = 40.0, 0.3
        dissent = []

    class Risk:
        verdict, position_size_pct = "OK", 1.5
        stop_price, target_price, entry_price = 95.0, 120.0, 100.0
        max_loss_pct, invalidation = 0.5, ""

    class Meta:
        confidence_after, edge_after = 0.62, 0.24

    fresh = pipeline._recommendation(Ens(), Risk(), Meta(), {"stale": False})
    stale = pipeline._recommendation(Ens(), Risk(), Meta(),
                                     {"stale": True, "last_bar": "2026-07-28"})
    assert fresh["headline"] != stale["headline"]
    assert stale["headline"].startswith("[STALE DATA")
    assert "2026-07-28" in stale["headline"]
    assert stale["data_stale"] is True and fresh["data_stale"] is False


def test_outage_is_reported_as_an_outage_not_as_a_bad_ticker(monkeypatch):
    """Telling someone their symbol does not exist during a vendor outage
    sends them to fix the wrong thing."""
    from src.v5 import pipeline
    kill(monkeypatch=monkeypatch)
    out = pipeline.analyze("AAPL", log=False)
    assert out.get("data_outage") is True
    assert "outage" in out["error"]
    assert "not a listed symbol" not in out["error"]
