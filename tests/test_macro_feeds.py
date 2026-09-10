"""
tests/test_macro_feeds.py
=========================
The macro feeds, and the date semantics that keep them honest.

These are offline: every HTTP call is stubbed, so the suite does not depend on
the Treasury, the BLS or the NY Fed being reachable. What is being tested is
the parsing and — more importantly — the REFUSALS:

  * a dead feed yields an Observation with an error, never a zero and never a
    stale carry-forward, because a feed that fails silently is the exact bug
    that left three macro fields at None for 85 days;
  * observation_date and release_date stay distinct, because collapsing them
    is how look-ahead bias enters a backtest;
  * a monthly statistic reports the age of the DATA, not the age of the fetch.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from src.macro import feeds


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, *, text="", payload=None, status=200):
        self.text = text if payload is None else json.dumps(payload)
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


TREASURY_CSV = (
    'Date,"1 Mo","2 Mo","1 Yr","2 Yr","10 Yr","30 Yr"\n'
    '08/21/2026,3.80,3.80,4.03,4.24,4.74,5.27\n'
    '08/20/2026,3.79,3.79,4.02,4.22,4.71,5.24\n'
)


def _bls(series_rows):
    return {"status": "REQUEST_SUCCEEDED",
            "Results": {"series": [{"data": series_rows}]}}


def _months(values, year=2026, start_month=7):
    """Newest-first monthly rows, the way BLS returns them."""
    names = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
    rows, m, y = [], start_month, year
    for v in values:
        rows.append({"year": str(y), "periodName": names[m - 1],
                     "period": f"M{m:02d}", "value": str(v)})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return rows


# ── Treasury ────────────────────────────────────────────────────────────────

def test_treasury_parses_the_newest_row(monkeypatch):
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(text=TREASURY_CSV))
    out = feeds.treasury_yields()
    assert out["10 Yr"].value == 4.74
    assert out["2 Yr"].value == 4.24
    # Daily market series: observation and release are the same day.
    assert out["10 Yr"].observation_date == "2026-08-21"
    assert out["10 Yr"].release_date == "2026-08-21"


def test_treasury_failure_reports_error_not_zero(monkeypatch):
    def boom(url):
        raise ConnectionError("treasury unreachable")
    monkeypatch.setattr(feeds, "_get", boom)
    out = feeds.treasury_yields()
    obs = out["_error"]
    assert obs.value is None          # NOT 0.0
    assert obs.ok is False
    assert "ConnectionError" in obs.error


def test_the_2y_is_the_real_2y_not_a_bill(monkeypatch):
    """The bug this replaced: `treasury_2y` was ^IRX, the 13-week bill, so the
    '10y2y' spread was really 10Y-3M. On this row that is 0.50 versus 0.94."""
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(text=TREASURY_CSV))
    out = feeds.treasury_yields()
    spread = out["10 Yr"].value - out["2 Yr"].value
    assert spread == pytest.approx(0.50, abs=1e-9)
    bill_spread = out["10 Yr"].value - out["2 Mo"].value
    assert bill_spread != pytest.approx(spread, abs=1e-6)


# ── BLS ─────────────────────────────────────────────────────────────────────

def test_cpi_yoy_is_computed_from_the_index(monkeypatch):
    # 13 months so a year-over-year is possible; +10% over the year.
    rows = _months([110.0] + [100.0] * 12)
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(payload=_bls(rows)))
    obs = feeds.cpi_yoy()
    assert obs.value == pytest.approx(10.0, abs=0.01)
    assert obs.unit == "percent"


def test_cpi_refuses_with_too_few_points(monkeypatch):
    """Fewer than 13 monthly points cannot make a year-over-year, and inventing
    one from a shorter window would be a different statistic under the same
    name."""
    rows = _months([100.0] * 5)
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(payload=_bls(rows)))
    obs = feeds.cpi_yoy()
    assert obs.value is None
    assert "13" in obs.error


def test_bls_error_status_is_surfaced(monkeypatch):
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(
        payload={"status": "REQUEST_NOT_PROCESSED", "message": ["daily cap"]}))
    obs = feeds.unemployment_rate()
    assert obs.value is None
    assert "REQUEST_NOT_PROCESSED" in obs.error


def test_monthly_observation_date_is_the_month_end(monkeypatch):
    rows = _months([4.1] + [4.0] * 12)
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(payload=_bls(rows)))
    obs = feeds.unemployment_rate()
    # July 2026 → the observation covers the month, so month-end is the honest
    # single date for it.
    assert obs.observation_date == "2026-07-31"
    # BLS does not return a release date here, and guessing one would defeat
    # the field's purpose.
    assert obs.release_date is None


def test_age_measures_the_data_not_the_fetch(monkeypatch):
    """A number retrieved a second ago that describes last quarter is stale
    information freshly delivered."""
    rows = _months([4.1] + [4.0] * 12, year=2020, start_month=1)
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(payload=_bls(rows)))
    obs = feeds.unemployment_rate()
    assert obs.retrieved_at.startswith(str(date.today().year))   # fetched now
    assert obs.age_days() > 365 * 5                              # data is old


# ── EFFR ────────────────────────────────────────────────────────────────────

def test_effr_parses(monkeypatch):
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(
        payload={"refRates": [{"effectiveDate": "2026-08-21", "percentRate": 3.63,
                               "type": "EFFR"}]}))
    obs = feeds.effective_fed_funds()
    assert obs.value == 3.63
    assert obs.observation_date == "2026-08-21"


def test_effr_empty_response_is_an_error(monkeypatch):
    monkeypatch.setattr(feeds, "_get", lambda url: _Resp(payload={"refRates": []}))
    obs = feeds.effective_fed_funds()
    assert obs.value is None and "no rates" in obs.error


# ── the whole picture ───────────────────────────────────────────────────────

def test_one_dead_source_degrades_one_field(monkeypatch):
    """A dead BLS must not take the Treasury legs down with it. The macro score
    is allowed to be computed from fewer inputs; it is not allowed to be
    computed from wrong ones."""
    def route(url):
        if "treasury" in url:
            return _Resp(text=TREASURY_CSV)
        if "bls.gov" in url:
            raise ConnectionError("bls down")
        return _Resp(payload={"refRates": [{"effectiveDate": "2026-08-21",
                                            "percentRate": 3.63}]})
    monkeypatch.setattr(feeds, "_get", route)
    out = feeds.fetch_all()
    assert out["treasury_10y"]["value"] == 4.74
    assert out["fed_funds_rate"]["value"] == 3.63
    assert out["cpi_yoy"]["value"] is None
    assert "cpi_yoy" in out["_meta"]["failed"]
    assert "treasury_10y" in out["_meta"]["ok"]


def test_spread_is_absent_when_a_leg_is(cache_dir, monkeypatch):
    """Half a curve is not a spread. Returning 0.0 here would read as a flat
    curve, which is a claim, not a gap.

    Takes `cache_dir` so the scenario is actually the one named: a dead source
    AND no prior observation. Without it this passed alone and failed in the
    suite, because a Treasury leg cached by an earlier test legitimately
    survived the outage — which is the §2 behaviour, not a bug. The two cases
    are separated: this test owns "no cache", and
    `test_a_cached_treasury_leg_survives_a_dead_source` owns "valid cache".
    """
    def route(url):
        if "treasury" in url:
            raise ConnectionError("treasury down")
        if "bls.gov" in url:
            return _Resp(payload=_bls(_months([110.0] + [100.0] * 12)))
        return _Resp(payload={"refRates": []})
    monkeypatch.setattr(feeds, "_get", route)
    out = feeds.fetch_all()
    assert out["yield_spread_10y2y"]["value"] is None
    assert out["yield_spread_10y2y"]["error"]


def test_a_cached_treasury_leg_survives_a_dead_source(cache_dir, monkeypatch):
    """The other half of §2: a valid observation must NOT be destroyed by a
    failed refresh. Yesterday's 10Y is still yesterday's 10Y when the Treasury
    endpoint times out, and the spread built from it is real — it just has to
    say it came from cache and that the refresh failed."""
    for key, value in (("treasury_10y", 4.74), ("treasury_2y", 4.24)):
        feeds._cache_write(key, {
            "series": key, "value": value, "unit": "percent",
            "observation_date": "2026-08-21", "release_date": "2026-08-21",
            "retrieved_at": "2026-08-24T18:40:42",
            "source": "US Treasury", "url": "u", "error": None,
            "frequency": "daily"})

    def dead(url):
        if "treasury" in url:
            raise ConnectionError("treasury down")
        if "bls.gov" in url:
            return _Resp(payload=_bls(_months([110.0] + [100.0] * 12)))
        return _Resp(payload={"refRates": []})
    monkeypatch.setattr(feeds, "_get", dead)
    # Force the refetch to be ATTEMPTED. With the default 8h TTL the cache is
    # fresh and the code never touches the network at all — correct, and the
    # quota protection working, but it means the failure path is untested
    # unless the TTL is expired deliberately.
    monkeypatch.setitem(feeds.FREQUENCY["daily"], "refetch_after_hours", 0)

    out = feeds.fetch_all()
    assert out["treasury_10y"]["value"] == 4.74, "a valid observation was destroyed"
    assert out["treasury_10y"]["observation_date"] == "2026-08-21", "re-dated"
    assert out["treasury_10y"]["from_cache"] is True
    assert out["treasury_10y"]["last_attempt_status"] == feeds.FETCH_FAILED
    # The spread is computable again, and is honest about its provenance.
    assert out["yield_spread_10y2y"]["value"] == pytest.approx(0.50, abs=1e-9)
    assert out["yield_spread_10y2y"]["derived"] is True
    assert out["yield_spread_10y2y"]["from_cache"] is True


def test_fred_without_a_key_says_so(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    obs = feeds.fred_series("FEDFUNDS")
    assert obs.value is None
    assert "FRED_API_KEY" in obs.error
    assert feeds.fred_available() is False


# ── the monthly cache (Phases 10, 49, 51) ───────────────────────────────────

@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Point the feed cache at a throwaway file."""
    path = tmp_path / "macro_feeds.json"
    monkeypatch.setattr(feeds, "_cache_path", lambda: path)
    return path


def test_a_rate_limited_monthly_series_falls_back_to_cache(cache_dir, monkeypatch):
    """The live failure: BLS allows 25 keyless requests per day per IP, and a
    burst of testing exhausted it. July's CPI does not stop being July's CPI
    because today's re-fetch was refused — losing it to None is the wrong
    failure for a monthly statistic."""
    feeds._cache_write("cpi_yoy", {
        "series": "CPI-U YoY", "value": 3.365, "unit": "percent",
        "observation_date": "2026-07-31", "release_date": None,
        "retrieved_at": "2026-08-24T17:03:07", "source": "BLS", "url": "u",
        "error": None})

    def rate_limited():
        return feeds._fail("cpi_yoy", "BLS", "u", "percent", "REQUEST_NOT_PROCESSED")

    got = feeds._monthly("cpi_yoy", rate_limited, max_age_hours=0)
    assert got.value == 3.365
    assert got.observation_date == "2026-07-31"     # the ORIGINAL period
    assert "cached" in got.source


def test_a_cached_value_does_not_claim_a_fresh_retrieval(cache_dir):
    """Carrying the value forward is honest; carrying the retrieval time
    forward would be the same lie in a smaller place."""
    feeds._cache_write("unemployment_rate", {
        "series": "U-3", "value": 4.1, "unit": "percent",
        "observation_date": "2026-07-31", "release_date": None,
        "retrieved_at": "2026-08-24T17:03:07", "source": "BLS", "url": "u",
        "error": None})
    got = feeds._from_cache("unemployment_rate", "")
    assert got.retrieved_at == "2026-08-24T17:03:07"
    assert got.retrieved_at != feeds._now()


def test_a_fresh_cache_skips_the_call_entirely(cache_dir):
    """A monthly number cannot have changed since this morning, so spending a
    scarce keyless quota to re-read it is waste (§51)."""
    feeds._cache_write("cpi_yoy", {
        "series": "CPI-U YoY", "value": 3.365, "unit": "percent",
        "observation_date": "2026-07-31", "release_date": None,
        "retrieved_at": feeds._now(), "source": "BLS", "url": "u", "error": None})
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return feeds._fail("cpi_yoy", "BLS", "u", "percent", "should not be called")

    got = feeds._monthly("cpi_yoy", counted, max_age_hours=12)
    assert calls["n"] == 0
    assert got.value == 3.365


def test_a_stale_cache_is_not_used_and_the_error_survives(cache_dir, monkeypatch):
    """Cache fallback must not become an unbounded carry-forward. With no
    usable cache, the live error is what the caller sees."""
    def failing():
        return feeds._fail("cpi_yoy", "BLS", "u", "percent", "network down")
    got = feeds._monthly("cpi_yoy", failing, max_age_hours=12)
    assert got.value is None
    assert "network down" in got.error


def test_a_successful_fetch_refreshes_the_cache(cache_dir):
    def good():
        return feeds.Observation(series="CPI-U YoY", value=3.5, unit="percent",
                                 observation_date="2026-08-31", release_date=None,
                                 retrieved_at=feeds._now(), source="BLS", url="u")
    got = feeds._monthly("cpi_yoy", good, max_age_hours=0)
    assert got.value == 3.5
    assert feeds._cache_read("cpi_yoy", 24)["value"] == 3.5


def test_a_failed_fetch_never_poisons_the_cache(cache_dir):
    feeds._cache_write("cpi_yoy", {"series": "x", "value": None, "unit": "percent",
                                   "observation_date": None, "release_date": None,
                                   "retrieved_at": feeds._now(), "source": "BLS",
                                   "url": "u", "error": "boom"})
    assert feeds._cache_read("cpi_yoy", 24) is None
