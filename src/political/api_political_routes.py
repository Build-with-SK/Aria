"""
api_political_routes.py
========================
FastAPI route definitions for the Political Intelligence Layer.

Add to your existing FastAPI app:

    from src.political.api_political_routes import router as political_router
    app.include_router(political_router, prefix="/api")

Endpoints:
    GET /api/political/watchlist
    GET /api/political/signals
    GET /api/political/sources
    GET /api/trade-candidates
    GET /api/trade-candidates/{ticker}
    GET /api/political/confirmation-matrix
    POST /api/political/refresh

Trading Intelligence System — Phase 4
Research and educational purposes only.
"""

try:
    from fastapi import APIRouter, HTTPException, Query
    from fastapi.responses import JSONResponse
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    # Create dummy APIRouter so the file can be imported without FastAPI installed
    class APIRouter:
        def get(self, *a, **kw):
            def decorator(f): return f
            return decorator
        def post(self, *a, **kw):
            def decorator(f): return f
            return decorator

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WATCHLIST_JSON = BASE_DIR / "data" / "political" / "watchlist.json"
SIGNALS_JSON = BASE_DIR / "data" / "political" / "political_signals.json"
TRADE_CANDIDATES_JSON = BASE_DIR / "data" / "trade_candidates.json"

router = APIRouter(tags=["Political Intelligence"])


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load {path}: {e}")
        return {}


# ─────────────────────────────────────────────
#  GET /api/political/watchlist
# ─────────────────────────────────────────────

@router.get("/political/watchlist")
def get_political_watchlist(
    min_score: float = Query(default=0.0, description="Minimum absolute political_activity_score"),
    activity_type: Optional[str] = Query(default=None, description="Filter by: accumulation, distribution, mixed, neutral"),
    limit: int = Query(default=50, le=200),
):
    """
    Return the political disclosure watchlist.

    Sorted by absolute political_activity_score descending.
    Political activity is a WATCHLIST TRIGGER, not a trade signal.
    """
    data = _load_json(WATCHLIST_JSON)
    watchlist = data.get("watchlist", [])

    if min_score > 0:
        watchlist = [e for e in watchlist if abs(e.get("political_activity_score", 0)) >= min_score]

    if activity_type:
        watchlist = [e for e in watchlist if e.get("activity_type") == activity_type]

    return {
        "generated_at": data.get("generated_at", ""),
        "count": len(watchlist[:limit]),
        "data_policy": data.get("data_policy", ""),
        "disclaimer": (
            "Political watchlist is derived from publicly available disclosures only. "
            "Political activity is a research signal, not a trade recommendation. "
            "Confirm with technical, options, futures, macro, and risk signals."
        ),
        "watchlist": watchlist[:limit],
    }


# ─────────────────────────────────────────────
#  GET /api/political/signals
# ─────────────────────────────────────────────

@router.get("/political/signals")
def get_political_signals(
    ticker: Optional[str] = Query(default=None, description="Filter by ticker symbol"),
    limit: int = Query(default=50, le=200),
):
    """
    Return political intelligence signals.
    """
    data = _load_json(SIGNALS_JSON)
    signals = data.get("signals", [])

    if ticker:
        signals = [s for s in signals if s.get("ticker", "").upper() == ticker.upper()]

    return {
        "generated_at": data.get("generated_at", ""),
        "count": len(signals[:limit]),
        "signals": signals[:limit],
    }


# ─────────────────────────────────────────────
#  GET /api/trade-candidates
# ─────────────────────────────────────────────

@router.get("/trade-candidates")
def get_trade_candidates(
    status: Optional[str] = Query(
        default=None,
        description="Filter by: Trade Candidate, Research Candidate, Watchlist Only, Avoid"
    ),
    action: Optional[str] = Query(default=None, description="Filter by: bullish, bearish, neutral"),
    min_score: float = Query(default=0.0, description="Minimum final_trade_candidate_score"),
    limit: int = Query(default=50, le=200),
):
    """
    Return trade candidates.

    IMPORTANT: These are RESEARCH SIGNALS ONLY.
    No automatic execution. Political signals are 10% of the final score.
    Trade Candidates require technical + macro + risk confirmation.
    """
    data = _load_json(TRADE_CANDIDATES_JSON)
    candidates = data.get("trade_candidates", [])

    if status:
        candidates = [c for c in candidates if c.get("trade_status") == status]
    if action:
        candidates = [c for c in candidates if c.get("action") == action]
    if min_score > 0:
        candidates = [c for c in candidates if c.get("final_trade_candidate_score", 0) >= min_score]

    return {
        "generated_at": data.get("generated_at", ""),
        "count": len(candidates[:limit]),
        "score_formula": data.get("score_formula", {}),
        "important_disclaimer": data.get("important_disclaimer", ""),
        "trade_candidates": candidates[:limit],
    }


# ─────────────────────────────────────────────
#  GET /api/trade-candidates/{ticker}
# ─────────────────────────────────────────────

@router.get("/trade-candidates/{ticker}")
def get_trade_candidate_by_ticker(ticker: str):
    """
    Return trade candidate detail for a specific ticker.
    """
    data = _load_json(TRADE_CANDIDATES_JSON)
    candidates = data.get("trade_candidates", [])

    ticker = ticker.upper()
    match = next((c for c in candidates if c.get("ticker", "").upper() == ticker), None)

    if not match:
        if _FASTAPI_AVAILABLE:
            raise HTTPException(
                status_code=404,
                detail=f"Ticker {ticker} not found in trade candidates. "
                       f"It may not have active political disclosure activity."
            )
        return {"error": f"Ticker {ticker} not found"}

    return {
        "important_disclaimer": data.get("important_disclaimer", ""),
        "candidate": match,
    }


# ─────────────────────────────────────────────
#  GET /api/political/confirmation-matrix
# ─────────────────────────────────────────────

@router.get("/political/confirmation-matrix")
def get_confirmation_matrix(
    limit: int = Query(default=30, le=100),
    status_filter: Optional[str] = Query(default=None),
):
    """
    Return the confirmation matrix.

    Format: Ticker | Political | Technical | Options | Futures | Macro | Risk | Final Status

    This is the key decision-support table.
    Trade Candidates require all major confirmations to align.
    """
    data = _load_json(TRADE_CANDIDATES_JSON)
    candidates = data.get("trade_candidates", [])

    if status_filter:
        candidates = [c for c in candidates if c.get("trade_status") == status_filter]

    matrix = []
    for c in candidates[:limit]:
        pol_raw = c.get("political_activity_score", 0)
        pol_norm = round((pol_raw + 100.0) / 2.0, 0)
        matrix.append({
            "ticker": c.get("ticker"),
            "political": pol_norm,
            "technical": c.get("technical_score", 50),
            "options": c.get("options_score", 50),
            "futures": c.get("futures_score", 50),
            "macro": c.get("macro_score", 50),
            "risk": c.get("risk_score", 50),
            "final_score": c.get("final_trade_candidate_score"),
            "trade_status": c.get("trade_status"),
            "action": c.get("action"),
            "confidence": c.get("confidence"),
        })

    return {
        "generated_at": data.get("generated_at", ""),
        "count": len(matrix),
        "note": "Political score weight: 10%. Trade status requires market confirmation.",
        "matrix": matrix,
    }


# ─────────────────────────────────────────────
#  GET /api/political/sources
# ─────────────────────────────────────────────

@router.get("/political/sources")
def get_political_sources():
    """
    Return the status and configuration of all political data sources.
    """
    try:
        from src.political.political_sources import list_sources_status
        return {
            "sources": list_sources_status(),
            "note": "Only publicly available, legal disclosures are used.",
        }
    except Exception as e:
        return {"error": str(e), "sources": []}


# ─────────────────────────────────────────────
#  POST /api/political/refresh
# ─────────────────────────────────────────────

@router.post("/political/refresh")
def refresh_political_layer():
    """
    Trigger a fresh run of the political intelligence pipeline.
    Re-reads the manual CSV, re-scores all records, and re-writes all output files.
    """
    try:
        from src.political.political_main_integration import run_political_layer
        candidates = run_political_layer(verbose=False)
        return {
            "status": "success",
            "refreshed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "candidates_generated": len(candidates),
            "output_files": [
                "data/political/watchlist.json",
                "data/political/political_signals.json",
                "data/trade_candidates.json",
            ],
        }
    except Exception as e:
        logger.error(f"Political refresh failed: {e}")
        if _FASTAPI_AVAILABLE:
            raise HTTPException(status_code=500, detail=str(e))
        return {"status": "error", "message": str(e)}
