"""
src/data/lse_data.py
====================
London Strategic Edge market-data client (https://londonstrategicedge.com/data).

Key-gated and strictly optional: without LSE_API_KEY in the environment every
call returns empty and ARIA behaves exactly as before. With a key, the desk's
analysts pick up extra evidence (bond yields, macro series, economic calendar,
insider trades) and the options stack can pull IV surfaces.

API shape (documented at /api-documentation):
  base   https://api.londonstrategicedge.com/vault
  auth   x-api-key: lse_live_...
  reads  /catalog /meta /candles /series /ref/{dataset} /usage
  limits 5,000 rows/page, 100 calls/min, monthly byte allowance
Discovery calls (/catalog /meta /reference /usage) are free and unmetered.

urllib only, per project convention. Responses cached on disk with a TTL so
repeated desk cycles don't burn the allowance.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
CACHE_DIR = ROOT / "data" / "lse_cache"
BASE_URL = "https://api.londonstrategicedge.com/vault"

DEFAULT_TTL_S = 1800        # 30 min — matches the enrichment layer's cadence
SLOW_TTL_S = 6 * 3600       # calendars, insider filings, yields move slowly


def api_key() -> str:
    return os.environ.get("LSE_API_KEY", "").strip()


def available() -> bool:
    return bool(api_key())


def _cache_path(endpoint: str, params: dict) -> Path:
    import hashlib
    raw = endpoint + "?" + urllib.parse.urlencode(sorted(params.items()))
    return CACHE_DIR / (hashlib.sha1(raw.encode()).hexdigest()[:16] + ".json")


def _get(endpoint: str, params: dict | None = None,
         ttl_s: float = DEFAULT_TTL_S, timeout: float = 20.0):
    """GET one endpoint. Returns parsed JSON ([] / {} on any failure)."""
    if not available():
        return []
    params = {k: v for k, v in (params or {}).items() if v is not None}
    cache = _cache_path(endpoint, params)
    if cache.exists() and time.time() - cache.stat().st_mtime < ttl_s:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = f"{BASE_URL}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"x-api-key": api_key()})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        logger.warning(f"LSE {endpoint} HTTP {e.code}"
                       + (" — key invalid/expired" if e.code in (401, 403) else
                          " — rate/allowance limit" if e.code == 429 else ""))
        return []
    except Exception as e:
        logger.warning(f"LSE {endpoint} failed: {e}")
        return []
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    return data


# ── public surface ───────────────────────────────────────────────────────────

def usage() -> dict:
    """Live allowance balance (free discovery call)."""
    out = _get("/usage", ttl_s=60)
    return out if isinstance(out, dict) else {}

def candles(symbol: str, timeframe: str = "1d", start: str | None = None,
            end: str | None = None, limit: int = 500,
            order: str = "desc") -> list[dict]:
    return _get("/candles", {"symbol": symbol, "timeframe": timeframe,
                             "start": start, "end": end, "limit": limit,
                             "order": order}) or []

def series(symbol: str, start: str | None = None, limit: int = 500,
           order: str = "desc") -> list[dict]:
    """Macro series or bond yields (cpi_yoy, fdtr, US10Y, DE10Y, ...)."""
    return _get("/series", {"symbol": symbol, "start": start,
                            "limit": limit, "order": order},
                ttl_s=SLOW_TTL_S) or []

def ref(dataset: str, **filters) -> list[dict]:
    """Reference sets: economic_calendar, insider_trades, dividends,
    stock_splits, cot, financial_reports, company_profiles,
    stock_fundamentals, bond_yields."""
    return _get(f"/ref/{dataset}", filters, ttl_s=SLOW_TTL_S) or []


# ── analyst-ready digests (evidence-shaped, cheap) ───────────────────────────

def yield_snapshot() -> dict:
    """US 10Y latest level and ~1-month change, for the macro conditioner."""
    rows = series("US10Y", limit=25)
    if not rows:
        return {}
    try:
        latest = float(rows[0]["value"])
        prior = float(rows[-1]["value"])
        return {"us10y": latest, "us10y_chg_1m": round(latest - prior, 3),
                "as_of": rows[0].get("date", "")}
    except Exception:
        return {}


def upcoming_us_events(limit: int = 8) -> list[dict]:
    """Next high-signal US calendar events (CPI, FOMC, NFP...)."""
    rows = ref("economic_calendar", region="US", released=None,
               order="desc", limit=50)
    out = []
    for r in rows:
        name = str(r.get("event", ""))
        if any(k in name.lower() for k in
               ("cpi", "fomc", "rate", "nonfarm", "payroll", "gdp", "pce")):
            out.append({"datetime": r.get("datetime"), "event": name})
        if len(out) >= limit:
            break
    return out


def insider_buys(symbol: str, limit: int = 20) -> list[dict]:
    """Recent open-market insider purchases — fundamental-agent evidence."""
    return ref("insider_trades", symbol=symbol, type="P-Purchase",
               order="desc", limit=limit)
