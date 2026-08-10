"""
src/v5/vendors.py
=================
Where price history actually comes from, and what to do when it doesn't.

THE PROBLEM THIS SOLVES
-----------------------
Every live signal in this system rested on one unpaid, unofficial API. When
yfinance is rate-limited or Yahoo changes a response shape, the old code
retried twice and then reached for whatever was left on disk — which could be a
week old — and handed it back looking exactly like a fresh fetch. One vendor is
not a data pipeline; it is a single point of failure with a cache in front of
it.

So: a chain of independent vendors, tried in order, each one a real fetch from
a different company's servers.

    1. yfinance        — the incumbent. Free, unofficial, generous, flaky.
    2. Alpha Vantage   — free tier, needs ALPHAVANTAGE_API_KEY. 25 req/day on
                         the free plan, which is why it is second and not first.
    3. Stooq           — free, keyless, CSV over https. Thin coverage outside
                         US/EU equities, but it needs no account, so the
                         fallback works on a fresh checkout with nothing
                         configured. A fallback that requires setup is a
                         fallback that is not there on the night it is needed.

Anything after the first is a DEGRADED source: the data is real, but the
primary path failed, and the caller is told so rather than left to assume.

CONVENTIONS
-----------
urllib only (project rule — no requests/httpx anywhere in this codebase), short
timeouts, and every failure returns a reason string instead of raising. A
vendor that throws would take down the analysis it was supposed to rescue.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 12
UA = "Mozilla/5.0 (compatible; ARIA/5.0; +https://github.com/)"


@dataclass
class VendorResult:
    """What one vendor came back with. `df` is None when it could not help."""
    vendor: str
    df: Optional[pd.DataFrame] = None
    error: str = ""
    fetched_at: datetime = field(default_factory=datetime.now)

    @property
    def ok(self) -> bool:
        return self.df is not None and not self.df.empty


def _get(url: str, timeout: int = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ── vendor 1: yfinance ───────────────────────────────────────────────────────

def fetch_yfinance(symbol: str, interval: str = "1d",
                   period: str = "10y") -> VendorResult:
    """The incumbent. Symbol is expected to already be a Yahoo ticker."""
    try:
        import yfinance as yf
    except ImportError as e:
        return VendorResult("yfinance", error=f"yfinance not installed: {e}")
    try:
        from src.v5.marketdata import _quiet_vendor
        with _quiet_vendor():
            df = yf.Ticker(symbol).history(period=period, interval=interval,
                                           auto_adjust=True)
        if df is None or df.empty:
            return VendorResult("yfinance", error="empty response")
        return VendorResult("yfinance", df=df)
    except Exception as e:
        return VendorResult("yfinance", error=str(e))


# ── vendor 2: Alpha Vantage ──────────────────────────────────────────────────

def alphavantage_key() -> str:
    return (os.environ.get("ALPHAVANTAGE_API_KEY") or "").strip()


def _av_symbol(symbol: str) -> str:
    """Alpha Vantage speaks plain tickers, and LSE/NSE suffixes with them.
    Futures and indices (`^VIX`, `CL=F`) it does not cover at all."""
    return symbol


def fetch_alphavantage(symbol: str, interval: str = "1d",
                       period: str = "10y") -> VendorResult:
    """Daily OHLCV from Alpha Vantage's free tier.

    Uses TIME_SERIES_DAILY, not ..._ADJUSTED: the adjusted endpoint moved
    behind the paid plan, and a fallback that quietly needs a subscription is
    not a fallback.
    """
    name = "alphavantage"
    if interval != "1d":
        return VendorResult(name, error=f"interval {interval} unsupported")
    key = alphavantage_key()
    if not key:
        return VendorResult(name, error="ALPHAVANTAGE_API_KEY not set")
    if any(c in symbol for c in ("^", "=")):
        return VendorResult(name, error=f"{symbol} is not an Alpha Vantage instrument")

    url = ("https://www.alphavantage.co/query?" + urllib.parse.urlencode({
        "function": "TIME_SERIES_DAILY",
        "symbol": _av_symbol(symbol),
        "outputsize": "full",
        "apikey": key,
    }))
    try:
        payload = json.loads(_get(url))
    except Exception as e:
        return VendorResult(name, error=str(e))

    # Alpha Vantage answers 200 OK with an explanation in the body for rate
    # limits and bad symbols alike, so the status code proves nothing.
    if "Note" in payload or "Information" in payload:
        return VendorResult(name, error=str(payload.get("Note")
                                            or payload.get("Information"))[:160])
    if "Error Message" in payload:
        return VendorResult(name, error=str(payload["Error Message"])[:160])

    series = payload.get("Time Series (Daily)")
    if not isinstance(series, dict) or not series:
        return VendorResult(name, error="no daily series in response")

    rows = []
    for day, bar in series.items():
        try:
            rows.append({
                "Date": pd.to_datetime(day),
                "Open": float(bar["1. open"]),
                "High": float(bar["2. high"]),
                "Low": float(bar["3. low"]),
                "Close": float(bar["4. close"]),
                "Volume": float(bar.get("5. volume", 0) or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return VendorResult(name, error="series present but unparseable")
    df = pd.DataFrame(rows).set_index("Date").sort_index()
    return VendorResult(name, df=df)


# ── vendor 3: Stooq ──────────────────────────────────────────────────────────

def _stooq_symbol(symbol: str) -> Optional[str]:
    """Stooq's own naming. Returns None for instruments it does not carry,
    rather than fetching a plausible-looking wrong series."""
    s = symbol.strip().lower()
    if not s or any(c in s for c in ("^", "=")):
        return None
    if s.endswith(".ns") or s.endswith(".bo"):      # Indian listings
        return None
    if s.endswith("-usd"):                          # crypto
        return None
    if s.endswith(".l"):                            # London
        return s.replace(".l", ".uk")
    if "." in s:
        return s
    return f"{s}.us"


def fetch_stooq(symbol: str, interval: str = "1d",
                period: str = "10y") -> VendorResult:
    """Keyless daily CSV. The reason failover works out of the box."""
    name = "stooq"
    if interval != "1d":
        return VendorResult(name, error=f"interval {interval} unsupported")
    mapped = _stooq_symbol(symbol)
    if not mapped:
        return VendorResult(name, error=f"{symbol} not covered by Stooq")
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(mapped)}&i=d"
    try:
        text = _get(url)
    except Exception as e:
        return VendorResult(name, error=str(e))
    if not text or text.strip().lower().startswith("<"):
        return VendorResult(name, error="non-CSV response")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rows.append({
                "Date": pd.to_datetime(row["Date"]),
                "Open": float(row["Open"]),
                "High": float(row["High"]),
                "Low": float(row["Low"]),
                "Close": float(row["Close"]),
                "Volume": float(row.get("Volume") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return VendorResult(name, error="empty or unparseable CSV")
    df = pd.DataFrame(rows).set_index("Date").sort_index()
    return VendorResult(name, df=df)


# ── the chain ────────────────────────────────────────────────────────────────

# Order is deliberate and is the whole design: index 0 is the primary, and
# anything served from index ≥ 1 is degraded and must be labelled as such.
CHAIN: list[tuple[str, Callable[..., VendorResult]]] = [
    ("yfinance", fetch_yfinance),
    ("alphavantage", fetch_alphavantage),
    ("stooq", fetch_stooq),
]

PRIMARY = CHAIN[0][0]


def fetch(symbol: str, interval: str = "1d", period: str = "10y",
          *, skip: tuple[str, ...] = ()) -> tuple[Optional[pd.DataFrame], dict]:
    """Try each vendor in turn. Returns (df, report).

    `report` records what happened at every vendor — not just the winner —
    because "the fallback worked" and "the primary is down" are two different
    operational facts and both need to be visible.
    """
    attempts = []
    for name, fn in CHAIN:
        if name in skip:
            attempts.append({"vendor": name, "ok": False, "error": "skipped"})
            continue
        res = fn(symbol, interval, period)
        attempts.append({"vendor": name, "ok": res.ok,
                         "error": res.error[:200] if res.error else ""})
        if res.ok:
            if name != PRIMARY:
                logger.warning("market data for %s served by FALLBACK vendor %s "
                               "— %s failed", symbol, name, PRIMARY)
            return res.df, {
                "vendor": name,
                "is_primary": name == PRIMARY,
                "attempts": attempts,
                "fetched_at": res.fetched_at.isoformat(timespec="seconds"),
            }
    logger.error("every market-data vendor failed for %s: %s", symbol, attempts)
    return None, {"vendor": None, "is_primary": False, "attempts": attempts,
                  "fetched_at": datetime.now().isoformat(timespec="seconds")}


def health() -> dict:
    """Which vendors are configured and usable — for /api/v5/data-health."""
    try:
        import yfinance                                  # noqa: F401
        yf_ok, yf_why = True, ""
    except ImportError as e:
        yf_ok, yf_why = False, str(e)
    return {
        "chain": [name for name, _ in CHAIN],
        "primary": PRIMARY,
        "vendors": {
            "yfinance": {"available": yf_ok, "needs_key": False, "note": yf_why},
            "alphavantage": {"available": bool(alphavantage_key()),
                             "needs_key": True,
                             "note": "" if alphavantage_key()
                                     else "set ALPHAVANTAGE_API_KEY to enable"},
            "stooq": {"available": True, "needs_key": False,
                      "note": "US/EU equities and ETFs only"},
        },
    }
