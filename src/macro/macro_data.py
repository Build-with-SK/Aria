"""
macro_data.py  —  v2 (hardened)
=================================
Macro data integration via FRED and yfinance.

Fixes vs v1
-----------
[BUG]  treasury_2y: ^IRX already returns a value in percentage (e.g. 4.80).
       v1 divided by 100 giving 0.048 — broke yield-spread calculation.
       Corrected: store ^IRX as-is; the spread calc now operates in consistent
       percentage points throughout.
[BUG]  Nested `if FRED_AVAILABLE` inside a block that already checked it.
       Removed — was dead code that masked the outer guard.
[BUG]  _fetch_fred only fetched 365 days — FRED monthly series (e.g. UNRATE)
       can have a 6-week publication lag; short window sometimes returned empty.
       Extended to 540 days.

New defences
------------
[NEW]  TTL-based disk cache — macro data is cached to JSON for `cache_ttl_hours`
       (default 6 h). Avoids hammering FRED/yfinance on every pipeline run.
[NEW]  _fetch_yf_with_retry — wraps yfinance downloads with up to 3 retries
       and exponential back-off. Prevents transient network failures from
       zeroing out the entire macro score.
[NEW]  macro_score now normalises by weight_used, so a score computed from
       3 of 5 factors is not artificially compressed. (v1 had the comment but
       the division was a no-op: `/ weight_used * 1.0` — fixed to actually
       rescale to the [-100, 100] range correctly.)
[NEW]  Sources log now includes which fields are None so dashboards can show
       partial-data warnings cleanly.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_DIR      = Path("data/cache")
CACHE_TTL_HOURS = 6   # Hours before cached macro data is considered stale

try:
    import pandas_datareader.data as web
    FRED_AVAILABLE = True
except (ImportError, TypeError):
    FRED_AVAILABLE = False
    # Deliberately debug, not warning, and worded accurately. The package is
    # usually INSTALLED and merely incompatible with the pinned pandas — the
    # old message ("install pandas-datareader") sent readers to fix a problem
    # they did not have, while the real fields sat at None for 85 days.
    # src/macro/feeds.py now covers these series without it, so this is a
    # note about a preferred source being absent, not a failure.
    logger.debug(
        "pandas-datareader unavailable or incompatible with the installed "
        "pandas; using the keyless Treasury/BLS/NY Fed feeds instead."
    )


# ===========================================================================
# MacroSnapshot dataclass
# ===========================================================================

@dataclass
class MacroSnapshot:
    """Current macro environment snapshot."""

    # Interest rates — all stored as raw percentage points (e.g. 4.80 not 0.048)
    fed_funds_rate:      Optional[float] = None   # % e.g. 5.33
    treasury_10y:        Optional[float] = None   # % e.g. 4.45
    treasury_2y:         Optional[float] = None   # % e.g. 4.80  ← was divided by 100 in v1
    yield_spread_10y2y:  Optional[float] = None   # 10Y minus 2Y in pct pts (negative = inverted)

    # Inflation
    cpi_yoy:             Optional[float] = None   # % year-over-year

    # Labour market
    unemployment_rate:   Optional[float] = None   # % e.g. 4.1

    # Market stress
    vix:                 Optional[float] = None
    dxy:                 Optional[float] = None
    dxy_trend:           Optional[float] = None   # 20d return of DXY

    # Risk-off indicators
    gold_trend_20d:      Optional[float] = None

    # Derived
    macro_score:         float = 0.0
    regime:              str   = "Unknown"

    # Metadata
    data_date:           str   = ""
    sources_available:   list  = field(default_factory=list)
    warnings:            list  = field(default_factory=list)
    from_cache:          bool  = False

    # Phase 10 — per-field provenance and date semantics. Keyed by field name;
    # each entry carries observation_date (the period the number describes),
    # release_date (when it was published, None where the source does not say),
    # retrieved_at, source and age_days. `data_date` above cannot express that
    # a fetch made today delivered last month's CPI, and treating the two as
    # the same is how look-ahead bias gets into a backtest.
    observations:        dict  = field(default_factory=dict)


# ===========================================================================
# Disk cache helpers
# ===========================================================================

def _cache_path() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / "macro_snapshot.json"


def _load_cache() -> Optional[Dict]:
    """Return cached macro dict if it exists and is within TTL, else None."""
    p = _cache_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01"))
        age_hours = (datetime.utcnow() - cached_at).total_seconds() / 3600
        if age_hours < CACHE_TTL_HOURS:
            logger.info(f"Macro cache hit — {age_hours:.1f} h old (TTL={CACHE_TTL_HOURS} h)")
            return data
        logger.info(f"Macro cache stale ({age_hours:.1f} h) — refreshing")
    except Exception as e:
        logger.debug(f"Cache load error: {e}")
    return None


def _save_cache(payload: Dict) -> None:
    try:
        payload["_cached_at"] = datetime.utcnow().isoformat()
        _cache_path().write_text(json.dumps(payload, default=str, indent=2))
    except Exception as e:
        logger.debug(f"Cache save error: {e}")


# ===========================================================================
# Data fetchers
# ===========================================================================

def _fetch_fred(series_id: str, lookback_days: int = 540) -> Optional[float]:
    """
    Fetch the latest value of a FRED series.
    Extended lookback to 540 days to handle monthly series with publication lag.
    """
    if not FRED_AVAILABLE:
        return None
    try:
        end   = date.today()
        start = end - timedelta(days=lookback_days)
        data  = web.DataReader(series_id, "fred", start, end)
        clean = data.dropna()
        if clean.empty:
            return None
        return float(clean.iloc[-1, 0])
    except Exception as e:
        logger.debug(f"FRED fetch failed for {series_id}: {e}")
        return None


def _fetch_yf_with_retry(
    ticker: str,
    period: str = "1mo",
    max_retries: int = 3,
    backoff: float = 2.0,
) -> Optional[pd.Series]:
    """
    Fetch recent close prices for a yfinance ticker with retry + back-off.
    Returns a clean Series or None.
    """
    for attempt in range(max_retries):
        try:
            df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                close = df["Close"].squeeze()
                return close.dropna()
        except Exception as e:
            if attempt < max_retries - 1:
                wait = backoff ** attempt
                logger.debug(f"yfinance {ticker} attempt {attempt+1} failed ({e}), retrying in {wait:.1f}s")
                time.sleep(wait)
            else:
                logger.debug(f"yfinance {ticker} all retries exhausted: {e}")
    return None


def _pct_change_nd(series: pd.Series, n: int = 20) -> Optional[float]:
    """Return n-day percentage change of a price series."""
    if series is None or len(series) < n + 1:
        return None
    return float((series.iloc[-1] / series.iloc[-n - 1]) - 1)


# ===========================================================================
# Main fetch function
# ===========================================================================

def fetch_macro_snapshot(use_cache: bool = True) -> MacroSnapshot:
    """
    Fetch all macro indicators and return a MacroSnapshot.

    Checks disk cache first (TTL = CACHE_TTL_HOURS).
    Falls back gracefully — missing indicators return None rather than crashing.
    """
    if use_cache:
        cached = _load_cache()
        if cached:
            snap = _snapshot_from_dict(cached)
            snap.from_cache = True
            return snap

    snap    = MacroSnapshot(data_date=str(date.today()))
    sources = []

    # ── government statistics, keyless ───────────────────────────────────────
    # These four fields (fed funds, CPI, unemployment, and the real 2-year
    # yield) were None for 85 days because `import pandas_datareader` raises a
    # TypeError against the installed pandas — so FRED_AVAILABLE is False and
    # every _fetch_fred() below returns None. The package IS installed; the
    # warning telling you to install it is wrong.
    #
    # src/macro/feeds.py gets the same series from the US Treasury, the BLS and
    # the NY Fed, none of which need a key, and each value arrives with its own
    # observation date. FRED stays preferred when FRED_API_KEY is configured.
    obs_dates: dict = {}
    try:
        from src.macro import feeds
        _f = feeds.fetch_all()

        def _take(field: str, key: str, digits: int = 3):
            blk = _f.get(key) or {}
            if blk.get("value") is None:
                return False
            setattr(snap, field, round(float(blk["value"]), digits))
            # §2/§7 — value, observation date, fetch date and source status are
            # four separate facts. A cached observation kept alive through a
            # failed refresh must be distinguishable from a fresh one.
            obs_dates[field] = {"observation_date": blk.get("observation_date"),
                                "release_date": blk.get("release_date"),
                                "retrieved_at": blk.get("retrieved_at"),
                                "source": blk.get("source"),
                                "age_days": blk.get("age_days"),
                                "status": blk.get("status"),
                                "frequency": blk.get("frequency"),
                                "from_cache": blk.get("from_cache"),
                                "derived": blk.get("derived", False),
                                "last_attempt_at": blk.get("last_attempt_at"),
                                "last_attempt_status": blk.get("last_attempt_status"),
                                "last_attempt_error": blk.get("last_attempt_error")}
            return True

        got = []
        for field, key in (("fed_funds_rate", "fed_funds_rate"),
                           ("cpi_yoy", "cpi_yoy"),
                           ("unemployment_rate", "unemployment_rate"),
                           ("treasury_10y", "treasury_10y"),
                           ("treasury_2y", "treasury_2y"),
                           ("yield_spread_10y2y", "yield_spread_10y2y")):
            if _take(field, key):
                got.append(field)
        if got:
            sources.append("US Treasury / BLS / NY Fed")
        for key, blk in _f.items():
            if isinstance(blk, dict) and blk.get("error") and key != "_meta":
                snap.warnings.append(f"{key}: {blk['error']}")
    except Exception as e:
        logger.warning(f"keyless macro feeds failed: {e}")
        snap.warnings.append(f"keyless macro feeds failed: {e}")

    # ── FRED data ────────────────────────────────────────────────────────────
    if FRED_AVAILABLE:
        # `or snap.x` so a FRED miss never overwrites a value the keyless
        # feeds already resolved — FRED is preferred, not authoritative.
        snap.fed_funds_rate    = _fetch_fred("FEDFUNDS") or snap.fed_funds_rate
        snap.unemployment_rate = _fetch_fred("UNRATE") or snap.unemployment_rate
        snap.yield_spread_10y2y = _fetch_fred("T10Y2Y") or snap.yield_spread_10y2y

        # CPI YoY: fetch 14 months and compute manually (more reliable than
        # taking the level directly, which is just the index value)
        try:
            end     = date.today()
            start   = end - timedelta(days=450)
            cpi_raw = web.DataReader("CPIAUCSL", "fred", start, end).dropna()
            if len(cpi_raw) >= 13:
                latest   = float(cpi_raw.iloc[-1, 0])
                year_ago = float(cpi_raw.iloc[-13, 0])
                snap.cpi_yoy = round((latest / year_ago - 1) * 100, 2)
        except Exception as e:
            logger.debug(f"CPI YoY computation failed: {e}")
            snap.warnings.append("CPI YoY unavailable")

        sources.append("FRED")
    elif not obs_dates:
        # Only a warning when the fallbacks ALSO failed. Warning while every
        # field is populated trains the reader to ignore the warnings list,
        # which is where the genuine staleness notices live.
        snap.warnings.append(
            "FRED unavailable and the keyless Treasury/BLS/NY Fed feeds "
            "returned nothing — rates, CPI and unemployment are missing")

    # ── yfinance market data ─────────────────────────────────────────────────

    # VIX
    vix_s = _fetch_yf_with_retry("^VIX", period="5d")
    if vix_s is not None and not vix_s.empty:
        snap.vix = round(float(vix_s.iloc[-1]), 2)
        sources.append("VIX")

    # 10Y Treasury yield — only if the Treasury par curve was unreachable.
    if snap.treasury_10y is None:
        tnx_s = _fetch_yf_with_retry("^TNX", period="5d")
        if tnx_s is not None and not tnx_s.empty:
            snap.treasury_10y = round(float(tnx_s.iloc[-1]), 3)
            sources.append("10Y_yield(^TNX fallback)")

    # 2-year yield. This used to read ^IRX — the THIRTEEN-WEEK T-bill — and
    # store it as `treasury_2y`, labelled "2Y_proxy". A 3-month bill is not a
    # 2-year note, so `yield_spread_10y2y` was really a 10Y–3M spread wearing a
    # 10Y–2Y name, and the regime classifier read it as the latter. On
    # 2026-08-21 that made the difference between a stored 0.865 and the actual
    # 0.50 — a curve measure 73% too wide. The real 2Y now comes from the
    # Treasury par curve above; ^IRX is only a last resort and is labelled as
    # the proxy it is.
    if snap.treasury_2y is None:
        irx_s = _fetch_yf_with_retry("^IRX", period="5d")
        if irx_s is not None and not irx_s.empty:
            snap.treasury_2y = round(float(irx_s.iloc[-1]), 3)   # already in pct pts
            sources.append("13w_bill(^IRX proxy — NOT the 2Y)")
            snap.warnings.append(
                "treasury_2y is the 13-week bill, not the 2-year note — the "
                "Treasury par curve was unreachable, so the 10y2y spread below "
                "is really a 10Y-3M spread")

    # Recompute yield spread if we got both legs from yfinance
    # (overrides FRED T10Y2Y which may lag by a day)
    if snap.treasury_10y is not None and snap.treasury_2y is not None:
        snap.yield_spread_10y2y = round(snap.treasury_10y - snap.treasury_2y, 3)

    # DXY
    dxy_s = _fetch_yf_with_retry("DX-Y.NYB", period="3mo")
    if dxy_s is not None and not dxy_s.empty:
        snap.dxy       = round(float(dxy_s.iloc[-1]), 2)
        snap.dxy_trend = round(_pct_change_nd(dxy_s, 20) or 0.0, 4)
        sources.append("DXY")

    # Gold trend
    gold_s = _fetch_yf_with_retry("GC=F", period="3mo")
    if gold_s is not None and not gold_s.empty:
        snap.gold_trend_20d = round(_pct_change_nd(gold_s, 20) or 0.0, 4)
        sources.append("Gold")

    snap.sources_available = sources

    # Note any None fields so dashboards can show partial-data warnings
    none_fields = [
        f for f in ["fed_funds_rate", "treasury_10y", "treasury_2y",
                    "yield_spread_10y2y", "cpi_yoy", "unemployment_rate",
                    "vix", "dxy"]
        if getattr(snap, f) is None
    ]
    if none_fields:
        snap.warnings.append(f"Missing fields: {none_fields}")

    # ── Derived score + regime ────────────────────────────────────────────────
    snap.macro_score = _compute_macro_score(snap)
    snap.regime      = _classify_regime(snap)

    # Phase 10: what period each number DESCRIBES, not just when it was
    # fetched. `data_date` alone cannot express that today's fetch delivered
    # July's CPI.
    snap.observations = obs_dates
    payload = macro_to_json(snap)
    _save_cache(payload)

    # The pipeline is not the only writer any more — the macro worker calls
    # this directly, so the canonical file is written here rather than left to
    # whatever ran last.
    try:
        out = Path("data/macro_data.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"could not write data/macro_data.json: {e}")
    return snap


def _snapshot_from_dict(d: Dict) -> MacroSnapshot:
    """Reconstitute a MacroSnapshot from a cached JSON dict."""
    snap = MacroSnapshot()
    for f in [
        "fed_funds_rate", "treasury_10y", "treasury_2y", "yield_spread_10y2y",
        "cpi_yoy", "unemployment_rate", "vix", "dxy", "dxy_trend",
        "gold_trend_20d", "macro_score", "regime", "data_date",
        "sources_available", "warnings",
    ]:
        if f in d:
            setattr(snap, f, d[f])
    return snap


# ===========================================================================
# Scoring
# ===========================================================================

def _compute_macro_score(snap: MacroSnapshot) -> float:
    """
    Compute macro score in [-100, +100].

    FIX v1: The final normalisation was `score / weight_used * 1.0` — always
    a no-op since the weights already summed to weight_used. The intent was to
    scale the partial-data score back to the full-weight range. Now done
    correctly: scale by (total_possible_weight / weight_used).
    """
    score        = 0.0
    weight_used  = 0.0
    MAX_WEIGHT   = 1.0   # Weights below sum to 1.0 when all present

    # VIX (25%)
    if snap.vix is not None:
        vix = snap.vix
        if vix < 15:         vix_s = 40
        elif vix < 20:       vix_s = 20
        elif vix < 25:       vix_s = 0
        elif vix < 35:       vix_s = -30
        else:                vix_s = -60
        score += vix_s * 0.25
        weight_used += 0.25

    # Yield spread (25%)
    if snap.yield_spread_10y2y is not None:
        sp = snap.yield_spread_10y2y
        if sp > 1.0:         sp_s = 40
        elif sp > 0.0:       sp_s = 15
        elif sp > -0.5:      sp_s = -20
        else:                sp_s = -50
        score += sp_s * 0.25
        weight_used += 0.25

    # CPI (20%)
    if snap.cpi_yoy is not None:
        cpi = snap.cpi_yoy
        if cpi < 2.5:        cpi_s = 20
        elif cpi < 3.5:      cpi_s = 5
        elif cpi < 5.0:      cpi_s = -20
        else:                cpi_s = -40
        score += cpi_s * 0.20
        weight_used += 0.20

    # DXY trend (15%)
    if snap.dxy_trend is not None:
        dxy_s = float(np.clip(-snap.dxy_trend * 300, -30, 30))
        score += dxy_s * 0.15
        weight_used += 0.15

    # Gold trend (15%)
    if snap.gold_trend_20d is not None:
        gold_s = float(np.clip(-snap.gold_trend_20d * 150, -20, 20))
        score += gold_s * 0.15
        weight_used += 0.15

    # Normalise for missing components — rescale to full [-100, 100] range
    if weight_used > 0 and weight_used < MAX_WEIGHT:
        score = score * (MAX_WEIGHT / weight_used)

    return round(float(np.clip(score, -100, 100)), 2)


def _classify_regime(snap: MacroSnapshot) -> str:
    """
    Classify macro regime: Expansion / Stagflation / Slowdown / Recession.
    Quadrant model: {high/low inflation} × {high/low growth}.
    """
    cpi   = snap.cpi_yoy            or 3.0
    spread = snap.yield_spread_10y2y or 0.0
    vix   = snap.vix                or 20.0
    unemp = snap.unemployment_rate  or 4.5

    high_inflation = cpi > 3.5
    low_growth     = spread < 0 or vix > 25 or unemp > 5.5

    if not high_inflation and not low_growth:
        return "Expansion (Goldilocks)"
    elif high_inflation and not low_growth:
        return "Stagflation Risk"
    elif not high_inflation and low_growth:
        return "Slowdown / Recovery"
    else:
        return "Recession Risk"


# ===========================================================================
# Serialisation
# ===========================================================================

def macro_to_json(snap: MacroSnapshot) -> dict:
    """Serialise MacroSnapshot to JSON-safe dict."""
    return {
        "fed_funds_rate":      snap.fed_funds_rate,
        "treasury_10y":        snap.treasury_10y,
        "treasury_2y":         snap.treasury_2y,
        "yield_spread_10y2y":  snap.yield_spread_10y2y,
        "cpi_yoy":             snap.cpi_yoy,
        "unemployment_rate":   snap.unemployment_rate,
        "vix":                 snap.vix,
        "dxy":                 snap.dxy,
        "dxy_trend":           snap.dxy_trend,
        "gold_trend_20d":      snap.gold_trend_20d,
        "macro_score":         snap.macro_score,
        "regime":              snap.regime,
        "data_date":           snap.data_date,
        "sources_available":   snap.sources_available,
        "warnings":            snap.warnings,
        "from_cache":          snap.from_cache,
        "observations":        getattr(snap, "observations", {}) or {},
    }
