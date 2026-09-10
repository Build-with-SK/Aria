"""
src/macro/feeds.py
==================
Keyless macro feeds with honest date semantics — Phases 10 and 13.

WHY THIS EXISTS
---------------
`data/macro_data.json` had not been rewritten since 2026-05-31, and three of
its fields — `fed_funds_rate`, `cpi_yoy`, `unemployment_rate` — had been `None`
for the whole of that time. The cause was one broken import:

    >>> import pandas_datareader
    TypeError: deprecate_kwarg() missing 1 required positional argument

`src/macro/macro_data.py` catches that at import, sets `FRED_AVAILABLE = False`,
and every `_fetch_fred()` call returns `None` forever after. The warning it
emits ("install pandas-datareader") is misleading: the package IS installed, it
is incompatible with the installed pandas. Nothing downstream ever noticed,
because a `None` macro field is indistinguishable from a macro field that has
not been published yet.

Meanwhile the values ARIA *did* have were badly wrong. The stale file carried a
2-year yield of 3.588 and a 10y–2y spread of 0.865. The actual Treasury par
curve for 2026-08-21 was 2Y 4.24, 10Y 4.74 — a spread of 0.50. ARIA had been
reasoning about the yield curve with a number 73% too large.

WHAT THIS MODULE DOES
---------------------
Fetches the same series from sources that need no API key and no extra
dependency (`requests` is already in the stack):

    US Treasury    par yield curve, daily      → 10Y, 2Y, spread
    BLS            CPI-U index, monthly        → CPI YoY, computed here
    BLS            unemployment rate, monthly  → UNRATE
    NY Fed         effective fed funds, daily  → EFFR

FRED itself stays available and preferred when a key is configured — this is a
fallback, not a replacement, so nothing is lost if the owner adds
`FRED_API_KEY` later.

DATE SEMANTICS (Phase 10)
-------------------------
Every value carries three distinct dates, because collapsing them is how a
backtest acquires look-ahead bias:

    observation_date  the period the number DESCRIBES (July CPI)
    release_date      when it was published (mid-August, for July CPI)
    retrieved_at      when ARIA fetched it

`release_date` is `None` where the source does not publish one, and that is
reported rather than guessed. For daily market series observation and release
are the same day; for monthly statistics they are weeks apart, and using the
observation date as if the number had been knowable then is exactly the error
Phase 24 forbids.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

UA = {"User-Agent": "ARIA/1.0 (open finance research; https://github.com/)"}
TIMEOUT = 25

# BLS publishes CPI-U and the unemployment rate on a roughly mid-month
# schedule for the prior month. The public v1 API allows 25 requests per day
# per IP with no key, which is ample for a daily macro refresh.
BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
SERIES_CPI = "CUUR0000SA0"      # CPI-U, all items, US city average, NSA
SERIES_UNRATE = "LNS14000000"   # unemployment rate, seasonally adjusted

TREASURY_CSV = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)
NYFED_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"

# ── freshness, derived from how often a series is actually published ────────
# One table, two uses: how long a cached value may be REUSED before a refetch
# is worth attempting, and how old an OBSERVATION may be before it stops being
# current. They are different questions and were previously conflated.
#
# Applying a short TTL to a monthly statistic is what exhausted the BLS keyless
# quota (25/day/IP) — refetching a number that cannot have changed since this
# morning. Applying a monthly staleness rule to a daily series would hide a
# dead Treasury feed for six weeks.
FREQUENCY = {
    "daily":   {"refetch_after_hours": 8,   "stale_after_days": 5},
    "monthly": {"refetch_after_hours": 24,  "stale_after_days": 45},
}

# Observation status vocabulary (§2). Explicit, because "value is None" cannot
# distinguish "never published" from "the refresh failed this morning".
VALID = "VALID"                          # current, within its publication cadence
STALE = "STALE"                          # a real observation, but older than expected
MISSING = "MISSING"                      # no observation exists at all
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"  # no observation AND the source is down

FETCH_OK = "OK"
FETCH_FAILED = "FAILED"
FETCH_SKIPPED = "SKIPPED_CACHE_VALID"


@dataclass
class Observation:
    """One macro number, with everything needed to know whether to trust it."""
    series: str
    value: Optional[float]
    unit: str
    observation_date: Optional[str]   # what period the number describes
    release_date: Optional[str]       # when it became public; None if unknown
    retrieved_at: str                 # when the VALUE was fetched
    source: str
    url: str
    error: Optional[str] = None

    # §2 — four separate facts that were previously collapsed into "value or
    # None". A monthly statistic must not disappear because today's refetch
    # failed, and the reader must be able to see that that is what happened.
    frequency: str = "daily"          # daily | monthly
    status: str = "VALID"             # VALID | STALE | MISSING | SOURCE_UNAVAILABLE
    from_cache: bool = False
    last_attempt_at: Optional[str] = None       # when a refresh was last TRIED
    last_attempt_status: Optional[str] = None   # OK | FAILED | SKIPPED_CACHE_VALID
    last_attempt_error: Optional[str] = None
    derived: bool = False             # computed from other series, not observed

    @property
    def ok(self) -> bool:
        return self.value is not None and self.error is None

    def classify(self) -> str:
        """Derive `status` from the observation's own age and its frequency."""
        if self.value is None:
            return (SOURCE_UNAVAILABLE if (self.error or self.last_attempt_error)
                    else MISSING)
        age = self.age_days()
        limit = FREQUENCY.get(self.frequency, FREQUENCY["daily"])["stale_after_days"]
        if age is None:
            return VALID
        return STALE if age > limit else VALID

    def age_days(self) -> Optional[float]:
        """How old the OBSERVATION is — not how old the fetch is.

        A number retrieved a second ago that describes last quarter is stale
        information freshly delivered, and the distinction matters.
        """
        if not self.observation_date:
            return None
        try:
            d = datetime.fromisoformat(self.observation_date[:10]).date()
            return (date.today() - d).days
        except Exception:
            return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["age_days"] = self.age_days()
        d["status"] = self.classify()
        return d


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── the monthly-series cache ────────────────────────────────────────────────
# The BLS public API allows 25 requests per day per IP with no key. A 6-hourly
# refresh of two series is 8 — comfortable — but any burst of testing exhausts
# it, and then CPI and unemployment go None until midnight.
#
# Going None is the WRONG failure here, and the distinction matters. Carrying a
# daily series forward as if it were current is the bug this whole module was
# written to fix. But July's CPI does not stop being July's CPI because today's
# re-fetch was rate-limited: the value travels with its own observation_date,
# so a carried-forward monthly statistic stays true and simply ages. What must
# never be carried forward is the CLAIM that it is current — and it never is,
# because observation_date and age_days are on every row.



def _cache_path():
    """Where the monthly-series cache lives.

    ARIA_MACRO_CACHE redirects it, and tests/conftest.py sets that for the whole
    session. Without the override, any test touching fetch_all() reads and
    writes the real data/cache/ — the same leak that let test fixtures into the
    production event log, and it showed up the same way: a test asserting a
    dead feed yields None started passing back a cached 3.365.
    """
    from pathlib import Path as _P
    override = os.environ.get("ARIA_MACRO_CACHE")
    p = (_P(override) if override
         else _P(__file__).resolve().parent.parent.parent / "data" / "cache" / "macro_feeds.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _cache_read(series: str, max_age_hours: float = 24.0) -> Optional[dict]:
    import json
    try:
        blob = json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    row = blob.get(series)
    if not isinstance(row, dict) or row.get("value") is None:
        return None
    try:
        cached_at = datetime.fromisoformat(row.get("_cached_at", ""))
    except Exception:
        return None
    if (datetime.now() - cached_at).total_seconds() / 3600 > max_age_hours:
        return None
    return row


def _cache_write(series: str, row: dict) -> None:
    import json
    if row.get("value") is None:
        return
    try:
        path = _cache_path()
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            blob = {}
        blob[series] = {**row, "_cached_at": _now()}
        path.write_text(json.dumps(blob, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.debug("macro feed cache write failed for %s: %s", series, e)


def _from_cache(series: str, live_error: str) -> Optional[Observation]:
    """Rebuild an Observation from cache, keeping its ORIGINAL dates.

    `retrieved_at` is the cached fetch time, not now — claiming a fresh
    retrieval for a cached value would be the same lie in a smaller place.
    """
    row = _cache_read(series)
    if not row:
        return None
    return Observation(
        series=row.get("series") or series, value=row.get("value"),
        unit=row.get("unit") or "", observation_date=row.get("observation_date"),
        release_date=row.get("release_date"), retrieved_at=row.get("retrieved_at") or "",
        source=(row.get("source") or "") + " (cached)", url=row.get("url") or "",
        error=None)


def _fail(series: str, source: str, url: str, unit: str, err: Exception | str) -> Observation:
    """A failed fetch is an Observation with an error, never a zero.

    Returning 0.0 or falling back to a previous value here is how a dead feed
    becomes an invisible one — the failure mode this whole module exists to
    correct.
    """
    return Observation(series=series, value=None, unit=unit,
                       observation_date=None, release_date=None,
                       retrieved_at=_now(), source=source, url=url,
                       error=f"{type(err).__name__}: {err}"[:200] if isinstance(err, Exception) else str(err)[:200])


def _get(url: str):
    import requests
    return requests.get(url, headers=UA, timeout=TIMEOUT)


# ── US Treasury: the par yield curve ────────────────────────────────────────

def treasury_yields(year: Optional[int] = None) -> dict[str, Observation]:
    """Latest published par yields. Daily series: observation == release date.

    Returns a dict keyed by tenor label ("10 Yr", "2 Yr", …). The Treasury
    publishes the whole year in one CSV, newest first.
    """
    yr = year or date.today().year
    url = TREASURY_CSV.format(year=yr)
    try:
        r = _get(url)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows:
            # Early January: this year's file can be empty. Fall back once.
            if year is None:
                return treasury_yields(yr - 1)
            return {"_error": _fail("treasury", "US Treasury", url, "percent",
                                    "no rows in the published CSV")}
        latest = rows[0]
        obs = latest.get("Date")
        iso = None
        if obs:
            try:
                iso = datetime.strptime(obs, "%m/%d/%Y").date().isoformat()
            except ValueError:
                iso = obs
        out: dict[str, Observation] = {}
        for tenor, raw in latest.items():
            if tenor == "Date":
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            out[tenor] = Observation(
                series=f"UST {tenor}", value=val, unit="percent",
                observation_date=iso, release_date=iso,   # daily: same day
                retrieved_at=_now(), source="US Treasury", url=url)
        return out
    except Exception as e:
        logger.warning("treasury yields fetch failed: %s", e)
        return {"_error": _fail("treasury", "US Treasury", url, "percent", e)}


# ── BLS: CPI and unemployment ───────────────────────────────────────────────

_MONTHS = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
           "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
           "December": 12}


def _bls_series(series_id: str) -> list[dict]:
    url = BLS_V1 + series_id
    r = _get(url)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS said {body.get('status')}: {body.get('message')}")
    return body["Results"]["series"][0]["data"]


def _obs_date(row: dict) -> Optional[str]:
    """BLS reports year + period name; the observation is the whole month, so
    the month END is the honest single date for it."""
    try:
        y, m = int(row["year"]), _MONTHS[row["periodName"]]
        nxt = date(y + (m == 12), (m % 12) + 1, 1)
        return (nxt - timedelta(days=1)).isoformat()
    except Exception:
        return None


def cached_fetch(series_key: str, fetch, *, frequency: str = "monthly",
                 force: bool = False) -> Observation:
    """Cache-first retrieval with the §3 request flow, made explicit:

        REQUEST -> CHECK VALID CACHE -> USE IT
                -> otherwise NETWORK -> STORE -> RETURN
                -> on failure, FALL BACK to the cached observation

    Two rules this enforces, both learned the hard way:

    1. A restart is not a reason to make a network request. The TTL comes from
       `FREQUENCY`, not from process lifetime, so booting the app repeatedly
       costs nothing. Exhausting the BLS keyless quota (25/day/IP) is what
       made CPI and unemployment go None.

    2. A failed refresh must not destroy a valid observation. July's CPI does
       not stop being July's CPI because this morning's refetch was refused.
       The cached value is returned with its ORIGINAL observation_date and its
       ORIGINAL retrieved_at, and `last_attempt_*` records that the refresh was
       tried and failed. The reader can therefore see all four facts at once:
       value, when it was measured, when we last tried, and whether that worked.
    """
    cfg = FREQUENCY.get(frequency, FREQUENCY["monthly"])
    ttl = cfg["refetch_after_hours"]

    if not force:
        cached_row = _cache_read(series_key, ttl)
        if cached_row:
            obs = _from_cache(series_key, "")
            if obs:
                obs.frequency = frequency
                obs.from_cache = True
                obs.last_attempt_at = cached_row.get("_cached_at")
                obs.last_attempt_status = FETCH_SKIPPED
                obs.status = obs.classify()
                return obs

    attempted_at = _now()
    obs = fetch()
    obs.frequency = frequency

    if obs.ok:
        obs.from_cache = False
        obs.last_attempt_at = attempted_at
        obs.last_attempt_status = FETCH_OK
        obs.status = obs.classify()
        _cache_write(series_key, obs.to_dict())
        return obs

    # Live failure. Prefer a real cached observation over None — but say so.
    fallback = _from_cache(series_key, obs.error or "")
    if fallback:
        fallback.frequency = frequency
        fallback.from_cache = True
        fallback.last_attempt_at = attempted_at
        fallback.last_attempt_status = FETCH_FAILED
        fallback.last_attempt_error = obs.error
        fallback.status = fallback.classify()
        logger.info("%s: refresh failed (%s) — keeping the observation from %s "
                    "(status %s)", series_key, obs.error,
                    fallback.observation_date, fallback.status)
        return fallback

    # No cache either: this is genuinely absent, and the source is down.
    obs.last_attempt_at = attempted_at
    obs.last_attempt_status = FETCH_FAILED
    obs.last_attempt_error = obs.error
    obs.status = obs.classify()
    return obs


# Kept as the previous name so nothing outside this module has to change.
def _monthly(series_key: str, fetch, *, max_age_hours: float = 24.0) -> Observation:
    """Backwards-compatible wrapper around `cached_fetch` for monthly series."""
    cfg = FREQUENCY["monthly"]
    prev = cfg["refetch_after_hours"]
    try:
        cfg["refetch_after_hours"] = max_age_hours
        return cached_fetch(series_key, fetch, frequency="monthly")
    finally:
        cfg["refetch_after_hours"] = prev


def cpi_yoy() -> Observation:
    """Year-over-year CPI-U inflation, computed from the index level.

    BLS publishes the index, not the rate, so the twelve-month change is
    computed here from the two index values — which is also why the
    observation date is the LATER month's, not today's.
    """
    url = BLS_V1 + SERIES_CPI
    try:
        rows = _bls_series(SERIES_CPI)
        monthly = [r for r in rows if r.get("periodName") in _MONTHS]
        if len(monthly) < 13:
            return _fail("cpi_yoy", "BLS", url, "percent",
                         f"only {len(monthly)} monthly points; need 13 for a YoY")
        latest, year_ago = monthly[0], monthly[12]
        v_now, v_then = float(latest["value"]), float(year_ago["value"])
        if v_then == 0:
            return _fail("cpi_yoy", "BLS", url, "percent", "prior-year index is zero")
        return Observation(
            series="CPI-U YoY", value=round((v_now / v_then - 1) * 100, 3),
            unit="percent", observation_date=_obs_date(latest),
            # BLS does not return the release date in this response. Guessing
            # it would defeat the purpose of the field.
            release_date=None, retrieved_at=_now(),
            source="US Bureau of Labor Statistics", url=url)
    except Exception as e:
        logger.warning("BLS CPI fetch failed: %s", e)
        return _fail("cpi_yoy", "BLS", url, "percent", e)


def unemployment_rate() -> Observation:
    """Headline U-3 unemployment rate, seasonally adjusted."""
    url = BLS_V1 + SERIES_UNRATE
    try:
        rows = _bls_series(SERIES_UNRATE)
        monthly = [r for r in rows if r.get("periodName") in _MONTHS]
        if not monthly:
            return _fail("unemployment_rate", "BLS", url, "percent", "no monthly points")
        latest = monthly[0]
        return Observation(
            series="Unemployment rate (U-3)", value=float(latest["value"]),
            unit="percent", observation_date=_obs_date(latest),
            release_date=None, retrieved_at=_now(),
            source="US Bureau of Labor Statistics", url=url)
    except Exception as e:
        logger.warning("BLS unemployment fetch failed: %s", e)
        return _fail("unemployment_rate", "BLS", url, "percent", e)


# ── NY Fed: the effective fed funds rate ────────────────────────────────────

def effective_fed_funds() -> Observation:
    """EFFR — what the fed funds market actually cleared at.

    This is the realised rate, not the FOMC target band. It is the closer
    analogue to FRED's FEDFUNDS series, which is also an effective rate.
    """
    try:
        r = _get(NYFED_EFFR)
        r.raise_for_status()
        rows = r.json().get("refRates") or []
        if not rows:
            return _fail("fed_funds_rate", "NY Fed", NYFED_EFFR, "percent", "no rates returned")
        row = rows[0]
        d = row.get("effectiveDate")
        return Observation(
            series="Effective fed funds rate", value=float(row["percentRate"]),
            unit="percent", observation_date=d, release_date=d,
            retrieved_at=_now(), source="Federal Reserve Bank of New York",
            url=NYFED_EFFR)
    except Exception as e:
        logger.warning("NY Fed EFFR fetch failed: %s", e)
        return _fail("fed_funds_rate", "NY Fed", NYFED_EFFR, "percent", e)


# ── FRED, when a key is configured ──────────────────────────────────────────

def fred_available() -> bool:
    return bool((os.environ.get("FRED_API_KEY") or "").strip())


def fred_series(series_id: str) -> Observation:
    """FRED via its JSON API. Preferred when a key exists, because FRED carries
    revision history the fallbacks do not."""
    key = (os.environ.get("FRED_API_KEY") or "").strip()
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&file_type=json&sort_order=desc&limit=1")
    if not key:
        return _fail(series_id, "FRED", url, "", "FRED_API_KEY is not set")
    try:
        r = _get(url + f"&api_key={key}")
        r.raise_for_status()
        obs = r.json().get("observations") or []
        obs = [o for o in obs if o.get("value") not in (".", "", None)]
        if not obs:
            return _fail(series_id, "FRED", url, "", "no non-empty observations")
        o = obs[0]
        return Observation(series=series_id, value=float(o["value"]), unit="",
                           observation_date=o.get("date"), release_date=None,
                           retrieved_at=_now(), source="FRED", url=url)
    except Exception as e:
        return _fail(series_id, "FRED", url, "", e)


# ── the whole picture ───────────────────────────────────────────────────────

def _cached_treasury() -> dict[str, Observation]:
    """Treasury legs behind the daily cache, so a restart is not a request.

    The whole par curve arrives in one CSV, so both legs are cached from a
    single fetch — which is also why they are cached together rather than as
    two independent series.
    """
    cfg = FREQUENCY["daily"]
    c10 = _cache_read("treasury_10y", cfg["refetch_after_hours"])
    c2 = _cache_read("treasury_2y", cfg["refetch_after_hours"])
    if c10 and c2:
        o10, o2 = _from_cache("treasury_10y", ""), _from_cache("treasury_2y", "")
        if o10 and o2:
            for o, key in ((o10, "treasury_10y"), (o2, "treasury_2y")):
                o.frequency = "daily"
                o.from_cache = True
                o.last_attempt_status = FETCH_SKIPPED
                o.status = o.classify()
            return {"10 Yr": o10, "2 Yr": o2}

    attempted = _now()
    ust = treasury_yields()
    out: dict[str, Observation] = {}
    for tenor, key in (("10 Yr", "treasury_10y"), ("2 Yr", "treasury_2y")):
        obs = ust.get(tenor)
        if obs is not None and obs.ok:
            obs.frequency = "daily"
            obs.last_attempt_at = attempted
            obs.last_attempt_status = FETCH_OK
            obs.status = obs.classify()
            _cache_write(key, obs.to_dict())
            out[tenor] = obs
        else:
            fallback = _from_cache(key, "")
            err = (ust.get("_error").error if ust.get("_error") else "tenor missing")
            if fallback:
                fallback.frequency = "daily"
                fallback.from_cache = True
                fallback.last_attempt_at = attempted
                fallback.last_attempt_status = FETCH_FAILED
                fallback.last_attempt_error = err
                fallback.status = fallback.classify()
                out[tenor] = fallback
            elif obs is not None:
                out[tenor] = obs
    return out


def fetch_all() -> dict:
    """Every keyless macro series, each with its own dates and its own failure.

    One dead source degrades one field. Nothing here can zero the macro score,
    and nothing substitutes a previous value for a missing one — a cached value
    is returned as itself, with its own observation date and an explicit
    `from_cache` flag, never re-dated to look fresh.
    """
    ust = _cached_treasury()
    t10 = ust.get("10 Yr")
    t2 = ust.get("2 Yr")

    spread = None
    spread_obs = None
    if t10 and t2 and t10.ok and t2.ok:
        spread = round(t10.value - t2.value, 4)
        spread_obs = t10.observation_date

    out = {
        "treasury_10y": t10.to_dict() if t10 else _fail(
            "treasury_10y", "US Treasury", TREASURY_CSV, "percent", "tenor missing").to_dict(),
        "treasury_2y": t2.to_dict() if t2 else _fail(
            "treasury_2y", "US Treasury", TREASURY_CSV, "percent", "tenor missing").to_dict(),
        "yield_spread_10y2y": {
            "series": "10Y − 2Y par spread", "value": spread, "unit": "percent",
            "observation_date": spread_obs, "release_date": spread_obs,
            "retrieved_at": _now(), "source": "US Treasury (derived)",
            "url": TREASURY_CSV,
            "error": None if spread is not None else "a leg was unavailable",
            # §7 — was this observed or computed? A derived value is only as
            # fresh as its worst input, so it inherits the weaker status rather
            # than claiming the average.
            "derived": True,
            "derived_from": ["treasury_10y", "treasury_2y"],
            "frequency": "daily",
            "from_cache": bool((t10 and t10.from_cache) or (t2 and t2.from_cache)),
            "status": (MISSING if spread is None else
                       STALE if any(o.classify() == STALE for o in (t10, t2) if o)
                       else VALID),
            "age_days": (t10.age_days() if t10 else None),
        },
        # Monthly series go through the cache: the quota is scarce and the
        # number cannot have changed since this morning.
        "cpi_yoy": cached_fetch("cpi_yoy", cpi_yoy, frequency="monthly").to_dict(),
        "unemployment_rate": cached_fetch("unemployment_rate", unemployment_rate,
                                          frequency="monthly").to_dict(),
        # Daily series are cached too, on a much shorter TTL. A restart at
        # 09:00 and another at 09:05 should not be two requests.
        "fed_funds_rate": cached_fetch("fed_funds_rate", effective_fed_funds,
                                       frequency="daily").to_dict(),
    }
    from collections import Counter
    statuses = Counter(v.get("status") for v in out.values() if isinstance(v, dict))
    out["_meta"] = {
        "retrieved_at": _now(),
        "fred_key_configured": fred_available(),
        "ok": [k for k, v in out.items() if isinstance(v, dict) and v.get("value") is not None],
        "failed": [k for k, v in out.items()
                   if isinstance(v, dict) and v.get("value") is None],
        # §2 — the five states, counted. "failed" above says a value is absent;
        # this says whether a present value is current, stale, or a cached
        # observation kept alive through a failed refresh.
        "status_counts": dict(statuses),
        "served_from_cache": [k for k, v in out.items()
                              if isinstance(v, dict) and v.get("from_cache")],
        "refresh_failures": {k: v.get("last_attempt_error")
                             for k, v in out.items()
                             if isinstance(v, dict) and v.get("last_attempt_status") == FETCH_FAILED},
    }
    return out
