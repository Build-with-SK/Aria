"""
backend/main.py
===============
Phase 5 — FastAPI Backend Server.

Exposes all trading intelligence data via a clean REST API.
The React frontend and any external tools consume this API.

Endpoints:
  GET  /api/signals              — All current signals
  GET  /api/signals/{ticker}     — Single asset signal
  GET  /api/macro                — Macro snapshot
  GET  /api/futures              — Futures signals
  GET  /api/options              — Options data
  GET  /api/ml                   — ML predictions
  GET  /api/backtest             — Backtest results
  GET  /api/portfolio            — Portfolio analysis + optimisation
  GET  /api/alerts               — Current alerts
  GET  /api/report               — Daily report
  GET  /api/history/{ticker}     — Signal history from DB
  GET  /api/stats                — Database stats
  POST /api/run                  — Trigger a new main.py run
  GET  /health                   — Health check

Run with:
  uvicorn backend.main:app --reload --port 8000

Then open:
  http://localhost:8000/docs    ← Interactive API docs (Swagger UI)
  http://localhost:8000/redoc  ← ReDoc docs
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load .env so API keys are available to all modules. override=True so a
# rotated key in .env wins over stale values inherited from the parent
# process — otherwise a reload keeps dead credentials forever.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Anthropic client (lazy)
_anthropic_client = None

def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        try:
            import anthropic, os
            _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        except Exception as e:
            logger.warning(f"Anthropic client init failed: {e}")
    return _anthropic_client

# ===========================================================================
# App initialisation
# ===========================================================================

app = FastAPI(
    title="Trading Intelligence System API",
    description="Phase 5 — REST API for the Trading Intelligence System. All signals, macro data, ML predictions, and portfolio analytics.",
    version="5.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

@app.on_event("startup")
async def _startup():
    """Pre-warm the order manager on startup (runs in the asyncio thread — safe for ib_insync)."""
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _get_order_manager)
    import threading
    threading.Thread(target=_start_brain_if_ollama, daemon=True).start()
    threading.Thread(target=_fx_monitor_loop, daemon=True).start()
    threading.Thread(target=_start_desk, daemon=True).start()
    threading.Thread(target=_discover_models, daemon=True).start()


def _discover_models():
    """Ollama model auto-discovery → SQLite registry for the inference router."""
    try:
        from src.inference.ollama_discovery import discover
        discover()
    except Exception as e:
        logger.warning(f"model discovery failed: {e}")


def _start_desk():
    """Start the autonomous desk daemon (analysts→debate→risk→slate→exec cycle).
    Cycles always run; whether anything auto-executes is decided solely by the
    desk safety contract (paper-only, armed, budgeted)."""
    import time
    time.sleep(12)  # let the server and brokers settle first
    try:
        from src.desk.desk_daemon import get_desk
        get_desk().start()
        logger.info("ARIA desk daemon started.")
    except Exception as e:
        logger.warning(f"Desk daemon startup failed: {e}")


def _fx_monitor_loop():
    """Check GBP/INR every 30 min and raise remittance alerts on meaningful moves."""
    import time
    time.sleep(8)
    while True:
        try:
            from src.data.fx_monitor import check
            check()
        except Exception as e:
            logger.debug(f"FX monitor tick failed: {e}")
        time.sleep(1800)
    threading.Thread(target=_start_quant_lab, daemon=True).start()


def _start_quant_lab():
    """Start the automated quant researcher (no Ollama required — it has a
    deterministic fallback mapper). Research only; never executes trades."""
    import time
    time.sleep(10)  # let the server settle first
    try:
        from src.brain.quant_lab import get_lab
        get_lab().start()
        logger.info("Quant lab daemon started.")
    except Exception as e:
        logger.warning(f"Quant lab startup failed: {e}")


def _start_brain_if_ollama():
    """Start the cognitive brain daemon only if Ollama is available.
    Set ARIA_RUN_BRAIN=false to skip it entirely — the brain is research-only
    and needs heavy embedding deps (torch/sentence-transformers); the trading
    desk runs fine without it (memory recall degrades gracefully). Ideal for
    the low-power Mac mini deployment."""
    import time
    time.sleep(5)  # let the server finish coming up first
    if os.environ.get("ARIA_RUN_BRAIN", "true").lower() == "false":
        logger.info("ARIA_RUN_BRAIN=false — brain daemon disabled (desk only).")
        return
    try:
        if _ollama_available():
            from src.brain.brain_daemon import get_brain
            get_brain().start()
            logger.info("ARIA cognitive brain daemon started.")
        else:
            logger.info("Ollama offline — brain daemon not started.")
    except Exception as e:
        logger.warning(f"Brain daemon startup failed: {e}")

# CORS — allows the React frontend to call this API. Exact origins ONLY:
# a "*" here lets any webpage in the same browser fire requests at the
# trading API (audit finding C1).
_ALLOWED_ORIGINS = [
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:5173", "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _mutation_guard(request, call_next):
    """Audit C1: the human-approval story needs technical backing.
    For every mutating request (POST/PATCH/PUT/DELETE):
      1. If the browser sent an Origin header it must be an allowed origin —
         blocks CSRF-style 'simple' cross-origin POSTs that skip preflight.
      2. If ARIA_API_KEY is set in the environment, the x-api-key header
         must match — protects deployments exposed beyond localhost.
    GETs stay open (read-only), so dashboards keep working."""
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in _ALLOWED_ORIGINS:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403,
                                content={"detail": "origin not allowed"})
        required = os.environ.get("ARIA_API_KEY", "")
        if required and request.headers.get("x-api-key") != required:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401,
                                content={"detail": "x-api-key required"})
    return await call_next(request)

# ===========================================================================
# Helper
# ===========================================================================

def _load(filename: str) -> Any:
    """Load a JSON data file. Raises 404 if missing."""
    path = ROOT / "data" / filename
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{filename} not found. Run: python main.py"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not parse {filename}: {e}")


def _load_optional(filename: str) -> Any:
    """Load a JSON data file. Returns empty dict if missing (no error)."""
    path = ROOT / "data" / filename
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sanitize(obj: Any) -> Any:
    """Replace NaN/Inf floats with None so FastAPI can JSON-serialize them."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


# ===========================================================================
# Health
# ===========================================================================

@app.get("/health", tags=["System"])
def health_check():
    """Quick health check — confirms API is running."""
    signals_exist = (ROOT / "data" / "signals.json").exists()
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "data_available": signals_exist,
        "version": "5.0.0",
        "phase": "Phase 5",
    }


# ===========================================================================
# Signals
# ===========================================================================

@app.get("/api/signals", tags=["Signals"])
def get_all_signals():
    """
    Get all current signals for every asset in the universe.
    Returns composite scores, actions, risk parameters, and explanations.
    """
    data = _load("signals.json")
    # Add summary metadata
    scores = [v.get("composite_score", 0) for v in data.values()]
    return {
        "count": len(data),
        "bullish": sum(1 for s in scores if s > 10),
        "bearish": sum(1 for s in scores if s < -10),
        "neutral": sum(1 for s in scores if -10 <= s <= 10),
        "last_updated": _load_optional("daily_report.json").get("date", "unknown"),
        "signals": data,
    }


@app.get("/api/signals/{ticker}", tags=["Signals"])
def get_signal(ticker: str):
    """
    Get the full signal for one asset.
    Ticker examples: AAPL, NVDA, BTC-USD, GC=F, ^GSPC
    """
    data = _load("signals.json")
    # Try exact match first, then case-insensitive
    if ticker in data:
        return data[ticker]
    upper = ticker.upper()
    for key, val in data.items():
        if key.upper() == upper:
            return val
    raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found in signals")


# ===========================================================================
# Macro
# ===========================================================================

@app.get("/api/macro", tags=["Macro"])
def get_macro():
    """
    Get current macro environment snapshot.
    Includes VIX, DXY, 10Y yield, CPI, regime classification, macro score.
    """
    return _load("macro_data.json")


# ===========================================================================
# Derivatives
# ===========================================================================

@app.get("/api/futures", tags=["Derivatives"])
def get_futures():
    """Get all futures contract signals and confirmation scores."""
    return _load("futures_signals.json")


@app.get("/api/options", tags=["Derivatives"])
def get_options():
    """
    Get options chain analysis.
    Includes P/C ratios, IV skew, ATM greeks, sentiment scores.
    """
    return _load("options_data.json")


@app.get("/api/derivatives/context", tags=["Derivatives"])
def get_derivatives_context():
    """Get combined futures + options context per spot ticker."""
    return _load_optional("derivatives_context.json")


# ===========================================================================
# Quant Analytics (GS-Quant-inspired bridge — standalone, no GS credentials)
# ===========================================================================

def _ticker_spot_iv(ticker: str):
    """Pull spot price and an IV proxy (annualised realised vol) from signals."""
    signals = _load_optional("signals.json")
    sig = signals.get(ticker) or {}
    if not sig:
        upper = ticker.upper()
        for k, v in signals.items():
            if k.upper() == upper:
                sig = v; break
    spot = sig.get("current_price") or 0.0
    iv = sig.get("realised_vol") or 0.25
    score = sig.get("composite_score", 50)
    return spot, iv, score, sig


@app.get("/api/quant/scenarios", tags=["Quant"])
def quant_scenarios():
    """Run all macro stress scenarios against the current signal book."""
    try:
        from src.gs_quant_bridge import ScenarioEngine
        eng = ScenarioEngine()
        results = eng.run_all_scenarios()
        return {"count": len(results),
                "scenarios": [{
                    "name": r.name, "description": r.description,
                    "pnl_estimate": round(float(r.pnl_estimate), 2),
                    "pnl_pct": round(float(r.pnl_pct), 2),
                    "confidence": r.confidence,
                    "top_losers": [[t, round(float(p), 2)] for t, p in r.top_losers[:5]],
                    "top_winners": [[t, round(float(p), 2)] for t, p in r.top_winners[:5]],
                    "key_risks": r.key_risks,
                } for r in results]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quant/scenario/{scenario_type}", tags=["Quant"])
def quant_scenario(scenario_type: str, bps: float = 100, pct: float = -20, vix_move: float = 20):
    """Run one custom scenario. Types: rate_shock, equity_crash, vol_spike, credit_widening, soft_landing, stagflation."""
    try:
        from src.gs_quant_bridge import ScenarioEngine
        r = ScenarioEngine().run_custom_scenario(scenario_type, bps=bps, pct=pct, vix_move=vix_move)
        if r is None:
            raise HTTPException(status_code=404, detail=f"Unknown scenario '{scenario_type}'")
        return {"name": r.name, "description": r.description,
                "pnl_estimate": round(float(r.pnl_estimate), 2), "pnl_pct": round(float(r.pnl_pct), 2),
                "confidence": r.confidence,
                "top_losers": [[t, round(float(p), 2)] for t, p in r.top_losers[:5]],
                "top_winners": [[t, round(float(p), 2)] for t, p in r.top_winners[:5]],
                "key_risks": r.key_risks}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quant/options/{ticker}", tags=["Quant"])
def quant_options(ticker: str, days: int = 30):
    """Black-Scholes options summary for a ticker (spot + IV from signals)."""
    try:
        from src.gs_quant_bridge import quick_options_summary
        spot, iv, score, sig = _ticker_spot_iv(ticker)
        if not spot:
            raise HTTPException(status_code=404, detail=f"No spot price for '{ticker}' in signals")
        text = quick_options_summary(ticker, spot, signal_score=score, iv=iv, days_to_expiry=days)
        return {"ticker": ticker, "spot": spot, "iv": iv, "days": days, "report": text}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class GreeksRequest(BaseModel):
    spot: float
    strike: float
    days_to_expiry: int = 30
    iv: float = 0.25
    risk_free_rate: float = 0.05
    option_type: str = "call"


@app.post("/api/quant/greeks", tags=["Quant"])
def quant_greeks(body: GreeksRequest):
    """Price and Greeks for an arbitrary option."""
    try:
        from src.gs_quant_bridge import BlackScholes
        g = BlackScholes(body.spot, body.strike, body.days_to_expiry / 365,
                         body.risk_free_rate, body.iv).price_and_greeks(body.option_type)
        return g.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quant/strategy/{strategy}", tags=["Quant"])
def quant_strategy(strategy: str, ticker: str = "", spot: float = 0, days: int = 30, iv: float = 0.25):
    """
    Analyse a multi-leg option strategy. Provide ?ticker= to pull live spot/IV,
    or pass ?spot= directly. Strategies: bull_call_spread, bear_put_spread,
    long_straddle, iron_condor.
    """
    try:
        from src.gs_quant_bridge import StrategyEngine, AVAILABLE_STRATEGIES
        if ticker:
            s, v, _score, _sig = _ticker_spot_iv(ticker)
            if s:
                spot, iv = s, v
        if not spot:
            raise HTTPException(status_code=400, detail="Provide ?ticker= or ?spot=")
        eng = StrategyEngine(spot=spot, time_to_expiry=days / 365, iv=iv)
        r = eng.run(strategy)
        if r is None:
            raise HTTPException(status_code=404,
                                detail=f"Unknown strategy '{strategy}'. Available: {AVAILABLE_STRATEGIES}")
        out = r.to_dict()
        out["spot"] = spot; out["iv"] = iv; out["days"] = days
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quant/option-strategies", tags=["Quant"])
def quant_option_strategies_list():
    """List available multi-leg option strategies (renamed to avoid clashing
    with the Quant Lab's /api/quant/strategies research endpoint)."""
    from src.gs_quant_bridge import AVAILABLE_STRATEGIES
    return {"strategies": AVAILABLE_STRATEGIES}


# ===========================================================================
# ML Predictions
# ===========================================================================

@app.get("/api/ml", tags=["ML Predictions"])
def get_ml_predictions():
    """
    Get ML predictions for all assets.
    Includes 1d, 5d, 20d directional probabilities and ensemble signal.
    """
    return _load("ml_predictions.json")


@app.get("/api/ml/{ticker}", tags=["ML Predictions"])
def get_ml_prediction(ticker: str):
    """Get ML predictions for a single asset."""
    data = _load("ml_predictions.json")
    if ticker not in data:
        raise HTTPException(status_code=404, detail=f"No ML predictions for '{ticker}'")
    return data[ticker]


# ===========================================================================
# Backtesting
# ===========================================================================

@app.get("/api/backtest", tags=["Backtesting"])
def get_backtest_results():
    """
    Get backtest results for all assets.
    Includes equity curves, trade logs, and performance metrics.
    """
    data = _sanitize(_load("backtest_results.json"))
    sharpes = [v.get("metrics", {}).get("sharpe_ratio") or 0 for v in data.values()]
    return {
        "count": len(data),
        "avg_sharpe": round(sum(sharpes) / len(sharpes), 3) if sharpes else 0,
        "results": data,
    }


@app.get("/api/backtest/{ticker}", tags=["Backtesting"])
def get_backtest(ticker: str):
    """Get backtest result for a single asset."""
    data = _load("backtest_results.json")
    if ticker not in data:
        raise HTTPException(status_code=404, detail=f"No backtest for '{ticker}'")
    return _sanitize(data[ticker])


# ===========================================================================
# Portfolio
# ===========================================================================

@app.get("/api/portfolio", tags=["Portfolio"])
def get_portfolio():
    """
    Get portfolio analysis and all 3 optimised allocations.
    Includes exposure, correlation, diversification score, and risk contribution.
    """
    analysis  = _load_optional("portfolio_analysis.json")
    optimised = _load_optional("optimised_portfolios.json")
    return {
        "analysis":   analysis,
        "optimised":  optimised,
    }


@app.get("/api/portfolio/optimised/{strategy}", tags=["Portfolio"])
def get_optimised_portfolio(strategy: str):
    """
    Get a specific optimisation strategy result.
    strategy: signal_weighted | risk_parity | mean_variance
    """
    data = _load_optional("optimised_portfolios.json")
    if strategy not in data:
        available = list(data.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Strategy '{strategy}' not found. Available: {available}"
        )
    return data[strategy]


# ===========================================================================
# Alerts
# ===========================================================================

@app.get("/api/alerts", tags=["Alerts"])
def get_alerts(severity: Optional[str] = None):
    """
    Get all current alerts.
    Optional filter: ?severity=CRITICAL or ?severity=WARNING
    """
    data = _load("alerts.json")
    if not isinstance(data, list):
        data = []
    if severity:
        data = [a for a in data if a.get("severity", "").upper() == severity.upper()]
    return {
        "count": len(data),
        "critical": sum(1 for a in data if a.get("severity") == "CRITICAL"),
        "warning":  sum(1 for a in data if a.get("severity") == "WARNING"),
        "info":     sum(1 for a in data if a.get("severity") == "INFO"),
        "alerts":   data,
    }


# ===========================================================================
# Sentiment
# ===========================================================================

@app.get("/api/sentiment", tags=["Sentiment"])
def get_sentiment():
    """Get news sentiment scores for all assets."""
    return _load_optional("sentiment_data.json")


# ===========================================================================
# Daily Report
# ===========================================================================

@app.get("/api/report", tags=["Reports"])
def get_daily_report():
    """Get the full daily market intelligence report."""
    return _load("daily_report.json")


# ===========================================================================
# Database history
# ===========================================================================

@app.get("/api/history/{ticker}", tags=["Database"])
def get_signal_history(ticker: str, days: int = 30):
    """
    Get historical signals for one ticker from the database.
    Returns signal score, action, confidence, and price for last N days.
    """
    try:
        sys.path.insert(0, str(ROOT))
        from src.database.database import get_signal_history as _get_hist
        df = _get_hist(ticker, days=days)
        if df.empty:
            return {"ticker": ticker, "days": days, "records": []}
        return {
            "ticker":  ticker,
            "days":    days,
            "records": df.to_dict(orient="records"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats", tags=["Database"])
def get_database_stats():
    """Get statistics about what's stored in the SQLite database."""
    try:
        from src.database.database import get_database_stats as _stats
        return _stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/performance/{ticker}", tags=["Database"])
def get_signal_performance(ticker: str):
    """
    Get historical signal performance for one ticker.
    Shows whether past Buy/Sell signals were correct.
    """
    try:
        from src.database.database import get_connection
        import pandas as pd
        with get_connection() as conn:
            df = pd.read_sql_query("""
                SELECT signal_date, action, composite_score, entry_price,
                       return_1d, return_5d, return_20d,
                       signal_correct_1d, signal_correct_5d, signal_correct_20d
                FROM signal_performance
                WHERE ticker = ?
                ORDER BY signal_date DESC
                LIMIT 50
            """, conn, params=(ticker,))
        if df.empty:
            return {"ticker": ticker, "message": "No performance data yet. Run main.py daily for a week.", "records": []}
        accuracy_1d  = df["signal_correct_1d"].mean()  if "signal_correct_1d"  in df else None
        accuracy_5d  = df["signal_correct_5d"].mean()  if "signal_correct_5d"  in df else None
        accuracy_20d = df["signal_correct_20d"].mean() if "signal_correct_20d" in df else None
        return {
            "ticker": ticker,
            "accuracy_1d":  round(accuracy_1d,  3) if accuracy_1d  is not None else None,
            "accuracy_5d":  round(accuracy_5d,  3) if accuracy_5d  is not None else None,
            "accuracy_20d": round(accuracy_20d, 3) if accuracy_20d is not None else None,
            "records": df.to_dict(orient="records"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Trigger pipeline run
# ===========================================================================

# ===========================================================================
# Execution Engine (lazy-loaded)
# ===========================================================================

_order_manager = None

def _get_order_manager():
    global _order_manager
    if _order_manager is not None:
        return _order_manager
    try:
        from src.execution.alpaca_broker import AlpacaBroker
        from src.execution.order_manager  import OrderManager
        alpaca = AlpacaBroker()
        ibkr   = None
        try:
            import asyncio
            # Ensure an event loop exists in this thread (required by ib_insync)
            try:
                asyncio.get_event_loop()
            except RuntimeError:
                asyncio.set_event_loop(asyncio.new_event_loop())
            from src.execution.ibkr_broker import IBKRBroker
            ibkr = IBKRBroker()
        except Exception as e:
            logger.info(f"IBKR init skipped (TWS not running): {e}")
        _order_manager = OrderManager(alpaca=alpaca, ibkr=ibkr)
        logger.info("Execution engine ready.")
    except Exception as e:
        logger.warning(f"Execution engine init failed: {e}")
    return _order_manager


class ProposeTrade(BaseModel):
    ticker:        str
    side:          str
    qty:           float
    order_type:    str = "market"
    limit_price:   Optional[float] = None
    stop_price:    Optional[float] = None
    time_in_force: str = "day"
    asset_class:   str = "equity"
    stop_loss:     Optional[float] = None
    take_profit:   Optional[float] = None
    signal_score:  float = 0.0
    confidence:    str = ""
    situation:     str = ""
    thesis_summary: str = ""
    bull_case:     str = ""
    bear_case:     str = ""
    invalidation:  str = ""
    current_price: float = 0.0
    broker:        str = "auto"


@app.post("/api/execute/propose", tags=["Execution"])
def propose_trade(body: ProposeTrade):
    """Add a trade proposal to the approval queue. Sits there until user approves."""
    try:
        from src.execution.approval_queue import ApprovalQueue
        from src.execution.broker_base    import (
            AssetClass, OrderRequest, OrderSide, OrderType
        )
        queue = ApprovalQueue()
        req   = OrderRequest(
            ticker=body.ticker,
            side=OrderSide(body.side.lower()),
            qty=body.qty,
            order_type=OrderType(body.order_type.lower()),
            limit_price=body.limit_price,
            stop_price=body.stop_price,
            time_in_force=body.time_in_force,
            asset_class=AssetClass(body.asset_class.lower()),
            stop_loss=body.stop_loss,
            take_profit=body.take_profit,
            signal_score=body.signal_score,
            situation=body.situation,
            thesis_summary=body.thesis_summary,
        )
        trade = queue.push(
            req,
            confidence=body.confidence,
            bull_case=body.bull_case,
            bear_case=body.bear_case,
            invalidation=body.invalidation,
            current_price=body.current_price,
            broker=body.broker,
        )
        return {"ok": True, "trade_id": trade.id, "trade": trade.__dict__}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/execute/queue", tags=["Execution"])
def get_approval_queue(status: Optional[str] = None):
    """Get all trades in the approval queue."""
    try:
        from src.execution.approval_queue import ApprovalQueue
        queue  = ApprovalQueue()
        trades = queue.get_all(limit=200)
        if status:
            trades = [t for t in trades if t.status == status]
        return {
            "count":  len(trades),
            "stats":  queue.stats(),
            "trades": [t.__dict__ for t in reversed(trades)],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/execute/approve/{trade_id}", tags=["Execution"])
def approve_and_execute(trade_id: str):
    """Approve and immediately execute a pending trade via the broker."""
    mgr = _get_order_manager()
    if not mgr:
        raise HTTPException(status_code=503, detail="Execution engine not available.")
    result = mgr.execute(trade_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Execution failed"))
    return result


@app.post("/api/execute/reject/{trade_id}", tags=["Execution"])
def reject_trade(trade_id: str, reason: str = ""):
    """Reject a pending trade — it will not be executed."""
    try:
        from src.execution.approval_queue import ApprovalQueue
        queue = ApprovalQueue()
        trade = queue.reject(trade_id, reason)
        if not trade:
            raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found or not pending")
        return {"ok": True, "trade": trade.__dict__}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/execute/cancel/{trade_id}", tags=["Execution"])
def cancel_trade(trade_id: str):
    """Cancel a pending trade before it reaches the broker."""
    try:
        from src.execution.approval_queue import ApprovalQueue
        ok = ApprovalQueue().cancel(trade_id)
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/execute/brokers", tags=["Execution"])
def get_broker_status():
    """Connection status and account info for all brokers."""
    mgr = _get_order_manager()
    if not mgr:
        return {"alpaca": {"connected": False}, "ibkr": {"connected": False}}
    return mgr.get_broker_status()


@app.get("/api/execute/positions", tags=["Execution"])
def get_live_positions():
    """Live positions across Alpaca and IBKR."""
    mgr = _get_order_manager()
    if not mgr:
        return {"positions": []}
    return {"count": len(p := mgr.get_all_positions()), "positions": p}


# ===========================================================================
# Obsidian Vault (direct filesystem — no MCP server)
# ===========================================================================

def _vault_context(messages) -> str:
    """Relevant vault knowledge for the latest user message. '' on any failure."""
    try:
        user_msgs = [m.content for m in messages if m.role == "user"]
        if not user_msgs:
            return ""
        from src.brain.vault import get_vault
        return get_vault().context_for(user_msgs[-1], n=3, max_chars=1500)
    except Exception as e:
        logger.debug(f"Vault context unavailable: {e}")
        return ""


@app.get("/api/vault/status", tags=["Vault"])
def vault_status():
    """Obsidian vault index status."""
    try:
        from src.brain.vault import VaultIndex, get_vault_path, peek_vault
        v = peek_vault()
        if v is not None:
            return v.stats()
        # don't build the index just to report status
        path = get_vault_path()
        return {"vault_path": str(path), "vault_exists": path.exists(),
                "notes_indexed": None, "chunks": None,
                "note": "Index not loaded yet — call /api/vault/reindex or use chat."}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/vault/reindex", tags=["Vault"])
def vault_reindex(force: bool = False):
    """(Re)index the Obsidian vault into the semantic store."""
    try:
        from src.brain.vault import get_vault
        return get_vault().reindex(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/vault/search", tags=["Vault"])
def vault_search(q: str, n: int = 5):
    """Semantic search over the user's Obsidian vault."""
    try:
        from src.brain.vault import get_vault
        return {"query": q, "results": get_vault().search(q, n=n)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/vault/note", tags=["Vault"])
def vault_note(path: str):
    """Full text of one vault note by relative path."""
    try:
        from src.brain.vault import get_vault
        content = get_vault().read_note(path)
        if content is None:
            raise HTTPException(status_code=404, detail=f"Note '{path}' not found in vault")
        return {"path": path, "content": content}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# ARIA Chat
# ===========================================================================

class ChatMsg(BaseModel):
    role: str     # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMsg]

_TICKER_STOPWORDS = {
    "I", "A", "THE", "AND", "OR", "IF", "IS", "IT", "TO", "IN", "ON", "OF", "MY",
    "ME", "DO", "GO", "AT", "BE", "AS", "SO", "US", "UK", "AI", "ML", "P&L", "OK",
    "USD", "GBP", "INR", "EUR", "VIX", "DXY", "PE", "EPS", "ROE", "ETF", "IPO",
    "CEO", "GDP", "FED", "RBI", "SELL", "BUY", "HOLD", "NSE", "BSE", "LSE",
    "NYSE", "NASDAQ", "NIFTY", "SENSEX", "FTSE", "SIP",
    # common question/filler words so a name search doesn't false-match
    "WHAT", "WHY", "HOW", "WHEN", "WHO", "WHICH", "SHOULD", "ABOUT", "TELL",
    "THINK", "YOU", "DOES", "CAN", "WILL", "NOW", "GOOD", "BAD", "STOCK",
    "SHARE", "SHARES", "PRICE", "MARKET", "TODAY", "PLEASE", "GIVE", "SHOW",
    "DATA", "INFO", "ANY", "SOME", "MORE", "LESS", "THAN", "FROM", "WITH",
}


def _detect_query_ticker(message: str):
    """Best-effort (ticker, market_hint) from a chat message. Returns (None, None)
    when nothing looks like a specific stock, so normal chat is untouched."""
    import re
    text = message or ""
    low = text.lower()
    market = ("NSE" if any(w in low for w in ("nse", "nifty", " india", "indian", "sensex", "bse")) else
              "LSE" if any(w in low for w in ("lse", "ftse", "london")) else None)
    if "bse" in low:
        market = "BSE"
    # explicit yahoo-style tickers first (SAIL.NS, BRK.B, BTC-USD)
    m = re.search(r"\b([A-Z][A-Z0-9&]{0,9}(?:\.(?:NS|BO|L))?(?:-USD)?)\b", text)
    for tok in re.findall(r"\b([A-Z][A-Z0-9&]{0,9}(?:\.(?:NS|BO|L))?(?:-USD)?)\b", text):
        base = tok.split(".")[0].replace("-USD", "")
        if tok in _TICKER_STOPWORDS or base in _TICKER_STOPWORDS or len(base) < 2:
            continue
        return tok, market
    # company-name fallback: match individual proper-noun words against the
    # known index by name (so "Tesla" → TSLA). Capitalised words first.
    try:
        words = re.findall(r"[A-Za-z][A-Za-z&]{2,}", text)
        cands = [w for w in words if w[:1].isupper() and w.upper() not in _TICKER_STOPWORDS]
        cands += [w for w in words if w.upper() not in _TICKER_STOPWORDS and w not in cands]
        for w in cands[:5]:
            hits = _get_universe().search(w, n=1)
            if hits and (w.upper() in hits[0]["symbol"].upper()
                         or w.lower() in (hits[0].get("name") or "").lower()):
                return hits[0]["symbol"], market
    except Exception:
        pass
    return None, market


def _ondemand_ticker_context(messages: List["ChatMsg"]) -> str:
    """If the latest user message names a stock, fetch it live (any global
    market) and return a compact context block for the system prompt. Empty
    string when nothing stock-shaped is mentioned."""
    try:
        last_user = next((m.content for m in reversed(messages)
                          if m.role == "user"), "")
        ticker, market = _detect_query_ticker(last_user)
        if not ticker:
            return ""
        res = _get_universe().explore(ticker, prefer=market, n_peers=4)
        if not res or "error" in res:
            return ""
        r, d = res["resolved"], res.get("dossier") or {}
        f = d.get("fundamentals") or {}
        rr = d.get("returns") or {}
        rel = res.get("related") or {}
        comp = ", ".join(c["symbol"] for c in rel.get("competitors", [])) or "—"
        sup = ", ".join(s["symbol"] for s in rel.get("suppliers", [])) or "—"
        cur = f.get("currency", "")
        line = lambda k: f"{rr[k]:+.1f}%" if rr.get(k) is not None else "n/a"
        # Investing.com-style technical consensus (best-effort, non-fatal)
        tech = ""
        try:
            from src.data.technical_summary import multi_timeframe
            mt = multi_timeframe(r["symbol"], ["1h", "1d", "1wk"])
            per = " | ".join(f"{tf} {v.get('summary')}" for tf, v
                             in mt["timeframes"].items() if v.get("summary"))
            if per:
                tech = f"\nTechnical consensus: {mt['consensus']} ({per})"
        except Exception:
            pass
        return (
            f"\n── ON-DEMAND DATA (fetched live just now) ──────────────\n"
            f"{r['symbol']} — {r.get('name')} [{r.get('exchange')}, {cur}]\n"
            f"Price {d.get('price')} {cur} | 1M {line('1M')} | 6M {line('6M')} | "
            f"1Y {line('1Y')} | 5Y {line('5Y')}\n"
            f"Sector {f.get('sector','?')} / {f.get('industry','?')} | "
            f"P/E {f.get('trailingPE','?')} | ROE {f.get('returnOnEquity','?')} | "
            f"MktCap {f.get('marketCap','?')}\n"
            f"Competitors: {comp}\nSuppliers: {sup}{tech}\n"
            f"──────────────────────────────────────────────────────")
    except Exception as e:
        logger.warning(f"on-demand ticker context failed: {e}")
        return ""


@app.post("/api/chat", tags=["ARIA"])
def aria_chat(body: ChatRequest):
    """Send a message to ARIA. Returns the assistant reply with market context baked in."""
    client = _get_anthropic()
    if not client:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    # Build live market context from latest data files
    signals  = _load_optional("signals.json")
    macro    = _load_optional("macro_data.json")
    ml       = _load_optional("ml_predictions.json")
    report   = _load_optional("daily_report.json")

    scores     = list(signals.values())
    top_bull   = sorted(scores, key=lambda x: x.get("composite_score", 0), reverse=True)[:6]
    top_bear   = sorted(scores, key=lambda x: x.get("composite_score", 0))[:6]
    ml_bull    = [t for t, p in ml.items() if p.get("overall_signal") == "Bullish"][:5]
    ml_bear    = [t for t, p in ml.items() if p.get("overall_signal") == "Bearish"][:5]

    def _fmt(s):
        t = s.get("ticker", "?")
        sc = s.get("composite_score", 0)
        a  = s.get("action", "HOLD")
        return f"  {t}: score {sc:+.1f}  action={a}"

    ctx = f"""── LIVE MARKET STATE ──────────────────────────────────
Regime     : {macro.get('regime', 'Unknown')}
Macro Score: {macro.get('macro_score', 0):.1f}
VIX        : {macro.get('vix', 'N/A')}
DXY        : {macro.get('dxy', 'N/A')}
10Y Yield  : {macro.get('treasury_10y', 'N/A')}
Assets     : {len(signals)} analyzed  |  Bullish {sum(1 for s in scores if s.get('composite_score',0)>10)}  Bearish {sum(1 for s in scores if s.get('composite_score',0)<-10)}  Neutral {sum(1 for s in scores if -10<=s.get('composite_score',0)<=10)}

Top BULLISH signals:
{chr(10).join(_fmt(s) for s in top_bull)}

Top BEARISH signals:
{chr(10).join(_fmt(s) for s in top_bear)}

ML Bullish tickers: {', '.join(ml_bull) or 'none'}
ML Bearish tickers: {', '.join(ml_bear) or 'none'}

Daily report summary: {report.get('summary', 'Not available')}
──────────────────────────────────────────────────────"""

    vault_ctx = _vault_context(body.messages)
    ondemand_ctx = _ondemand_ticker_context(body.messages)

    system = f"""You are ARIA — Adaptive Risk Intelligence Agent — the AI brain of a sophisticated trading intelligence system. You analyse markets using macro regime detection, ML signals, and a multi-broker execution engine (Alpaca + IBKR). You can fetch ANY globally-listed stock on demand — US, India (NSE/BSE), and London — with live price, fundamentals, and its competitors/suppliers; if the ON-DEMAND DATA block below is present, that data was just fetched for the ticker the user asked about, so never say you lack data for it.

{ctx}
{ondemand_ctx}

{vault_ctx}

Your personality: precise, confident, data-driven. You reason from signals, not opinion. You always cite the actual numbers from the data above when discussing a ticker. You flag risk clearly.

You CAN: analyse tickers, interpret signals, explain methodology, identify themes, discuss macro, suggest what to watch.
You CANNOT: guarantee profits or give regulated/personalised financial advice (no buy/sell/hold/convert directives to the user). Always include a brief risk note and defer personal decisions to a licensed adviser."""

    msgs = [{"role": m.role, "content": m.content} for m in body.messages]

    # Long sessions: progressive context compression (no-op under 70% util)
    try:
        from src.compression.engine import CompressionEngine
        msgs = CompressionEngine().process(msgs)
    except Exception as e:
        logger.warning(f"chat compression skipped: {e}")

    try:
        # DEEP tier: Anthropic first, biggest local model as the fallback —
        # a rate-limited API no longer 500s the chat when Ollama is up.
        from src.inference.router import Tier, get_router
        result = get_router().complete(
            Tier.DEEP, msgs, system=system, max_tokens=1024, timeout=90)
        return {
            "content": result.text,
            "model": result.model,
            "provider": result.provider,
            "tokens": {},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Local Brain (Ollama)
# ===========================================================================

from src.inference.base import ollama_base
OLLAMA_BASE = ollama_base()   # env OLLAMA_BASE overrides (mini -> laptop)

def _ollama_available() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2)
        return True
    except Exception:
        return False

def _ollama_models() -> list:
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as r:
            return _json.loads(r.read()).get("models", [])
    except Exception:
        return []

def _build_market_context() -> str:
    signals  = _load_optional("signals.json")
    macro    = _load_optional("macro_data.json")
    ml       = _load_optional("ml_predictions.json")
    report   = _load_optional("daily_report.json")
    scores   = list(signals.values())
    top_bull = sorted(scores, key=lambda x: x.get("composite_score", 0), reverse=True)[:6]
    top_bear = sorted(scores, key=lambda x: x.get("composite_score", 0))[:6]
    ml_bull  = [t for t, p in ml.items() if p.get("overall_signal") == "Bullish"][:5]
    ml_bear  = [t for t, p in ml.items() if p.get("overall_signal") == "Bearish"][:5]
    def _fmt(s):
        return f"  {s.get('ticker','?')}: score {s.get('composite_score',0):+.1f}  action={s.get('action','?')}  conf={s.get('confidence','?')}"
    return f"""── LIVE MARKET STATE ──────────────────────────────────
Regime     : {macro.get('regime', 'Unknown')}
Macro Score: {macro.get('macro_score', 0):.1f}
VIX        : {macro.get('vix', 'N/A')}
DXY        : {macro.get('dxy', 'N/A')}
10Y Yield  : {macro.get('treasury_10y', 'N/A')}
Assets     : {len(signals)} analyzed  |  Bullish {sum(1 for s in scores if s.get('composite_score',0)>10)}  Bearish {sum(1 for s in scores if s.get('composite_score',0)<-10)}
Top BULLISH:
{chr(10).join(_fmt(s) for s in top_bull)}
Top BEARISH:
{chr(10).join(_fmt(s) for s in top_bear)}
ML Bullish : {', '.join(ml_bull) or 'none'}
ML Bearish : {', '.join(ml_bear) or 'none'}
Summary    : {report.get('summary', 'N/A')}
──────────────────────────────────────────────────────"""

_ARIA_SYSTEM = """You are ARIA — Adaptive Risk Intelligence Agent — a specialized trading intelligence system.
You analyse markets using a 766-ticker universe, LightGBM ensemble ML, macro regime detection, 10 Market Situation Archetypes, and a multi-broker execution engine.
Be precise, data-driven, and reference actual signal numbers. Flag risk clearly. Keep responses concise."""


class LocalChatRequest(BaseModel):
    messages: List[ChatMsg]
    model:    str = "qwen2.5-coder:7b"

@app.post("/api/chat/local", tags=["Local Brain"])
def aria_chat_local(body: LocalChatRequest):
    """Chat with the local Ollama model — no cloud API needed."""
    if not _ollama_available():
        raise HTTPException(status_code=503, detail="Ollama is not running. Start it with: ollama serve")
    ctx = _build_market_context()
    vault_ctx = _vault_context(body.messages)
    ondemand_ctx = _ondemand_ticker_context(body.messages)
    system = f"{_ARIA_SYSTEM}\n\n{ctx}{ondemand_ctx}"
    if vault_ctx:
        system += f"\n\n{vault_ctx}"
    try:
        from src.inference.router import get_router
        result = get_router().complete_with(
            "ollama", body.model,
            [{"role": m.role, "content": m.content} for m in body.messages],
            system=system, max_tokens=1024, timeout=120)
        return {"content": result.text, "model": body.model, "tokens": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/brain/status", tags=["Local Brain"])
def brain_status():
    """Full brain status — Ollama, models, daemon state, memory stats, adapters."""
    models = _ollama_models()
    adapter_dir = ROOT / "src" / "brain" / "adapters"
    adapters = [p.name for p in adapter_dir.glob("*/adapter_config.json")] if adapter_dir.exists() else []
    training_data = ROOT / "data" / "brain_training_data.jsonl"

    # Daemon state — peek only, never instantiate the heavy brain here
    daemon = {"running": False, "thinking": False, "model": None,
              "interval_minutes": 15, "cycle_count": 0,
              "last_cycle_at": None, "memory_count": None}
    try:
        from src.brain.brain_daemon import peek_brain
        b = peek_brain()
        if b is not None:
            daemon = b.status()
    except Exception as e:
        logger.debug(f"Brain daemon peek failed: {e}")

    return {
        "ollama_running": _ollama_available(),
        "models": [{"name": m.get("name"), "size_gb": round(m.get("size", 0) / 1e9, 1)} for m in models],
        "fine_tuned_adapters": adapters,
        "training_samples": sum(1 for _ in training_data.open()) if training_data.exists() else 0,
        "recommended_model": "llama3.1:8b",
        "daemon": daemon,
    }


# ── Cognitive brain daemon endpoints ────────────────────────────────────────

@app.get("/api/brain/last-cycle", tags=["Local Brain"])
def brain_last_cycle():
    """Full thinking log from the most recent reasoning cycle."""
    return _load_optional("brain_state.json")


@app.get("/api/brain/consult", tags=["Local Brain"])
def brain_consult_status():
    """Frontier-consult status: whether hard reasoning steps use a frontier model."""
    from src.brain.cognitive.reasoner import consult_status
    return consult_status()


@app.post("/api/brain/consult", tags=["Local Brain"])
def brain_consult_set(enabled: bool = True, frontier_model: str = ""):
    """Toggle frontier-consult. When on, ARIA's ORIENT/ANALYSE/DECIDE/REFLECT
    steps route to a frontier model (needs ANTHROPIC_API_KEY). Budget-capped."""
    if enabled and not _get_anthropic():
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured — cannot enable consult")
    from src.brain.cognitive.reasoner import set_consult_config
    updates = {"enabled": enabled}
    if frontier_model:
        updates["frontier_model"] = frontier_model
    return set_consult_config(updates)


@app.post("/api/brain/run-now", tags=["Local Brain"])
def brain_run_now():
    """Trigger an immediate reasoning cycle (doesn't wait for the scheduler)."""
    if not _ollama_available():
        raise HTTPException(status_code=503, detail="Ollama is not running. Start it with: ollama serve")
    from src.brain.brain_daemon import get_brain
    brain = get_brain()
    if brain.thinking:
        return {"status": "already_thinking", "message": "A reasoning cycle is already in progress."}
    brain.run_now()
    return {"status": "started", "message": "Reasoning cycle started. Poll /api/brain/last-cycle for the thought stream."}


@app.post("/api/brain/start", tags=["Local Brain"])
def brain_start(model: str = "qwen2.5-coder:7b"):
    """Start (or resume) the brain daemon."""
    if not _ollama_available():
        raise HTTPException(status_code=503, detail="Ollama is not running. Start it with: ollama serve")
    from src.brain.brain_daemon import get_brain
    brain = get_brain(model=model)
    brain.model = model
    brain.start()
    return {"status": "started", **brain.status()}


@app.post("/api/brain/stop", tags=["Local Brain"])
def brain_stop():
    """Pause the brain daemon."""
    from src.brain.brain_daemon import peek_brain
    brain = peek_brain()
    if brain is None or not brain.running:
        return {"status": "not_running"}
    brain.stop()
    return {"status": "stopped", **brain.status()}


@app.patch("/api/brain/interval", tags=["Local Brain"])
def brain_set_interval(minutes: int = 15):
    """Change the reasoning cycle interval."""
    if minutes < 1 or minutes > 1440:
        raise HTTPException(status_code=400, detail="Interval must be between 1 and 1440 minutes")
    from src.brain.brain_daemon import get_brain
    brain = get_brain()
    brain.set_interval(minutes)
    return {"status": "ok", "interval_minutes": brain.interval_minutes}


@app.get("/api/brain/memories", tags=["Local Brain"])
def brain_memories(n: int = 20):
    """Most recent memories from the long-term store."""
    try:
        from src.brain.brain_daemon import peek_brain
        b = peek_brain()
        if b is not None and b.memory is not None:
            ltm = b.memory
        else:
            from src.brain.cognitive.long_term_memory import LongTermMemory
            ltm = LongTermMemory()
        memories = ltm.recent(n=n)
        return {"count": len(memories), "stats": ltm.stats(),
                "memories": [m.to_dict() for m in memories]}
    except Exception as e:
        return {"count": 0, "memories": [], "error": str(e)}


@app.get("/api/brain/recall", tags=["Local Brain"])
def brain_recall(q: str, n: int = 10):
    """Semantic search over the brain's long-term memories."""
    try:
        from src.brain.brain_daemon import peek_brain
        b = peek_brain()
        if b is not None and b.memory is not None:
            ltm = b.memory
        else:
            from src.brain.cognitive.long_term_memory import LongTermMemory
            ltm = LongTermMemory()
        memories = ltm.recall(q, n=n)
        return {"query": q, "count": len(memories),
                "memories": [m.to_dict() for m in memories]}
    except Exception as e:
        return {"query": q, "count": 0, "memories": [], "error": str(e)}


@app.post("/api/brain/generate-training-data", tags=["Local Brain"])
def generate_training_data(background_tasks: BackgroundTasks):
    """Generate fine-tuning training data from current signals and historical data."""
    def _run():
        try:
            sys.path.insert(0, str(ROOT))
            from src.brain.data_generator import generate_all
            generate_all()
        except Exception as e:
            logger.error(f"Training data generation failed: {e}")
    background_tasks.add_task(_run)
    return {"status": "started", "message": "Generating training data in background. Check /api/brain/status for progress."}


@app.post("/api/brain/pull-model", tags=["Local Brain"])
def pull_model(model: str = "llama3.1:8b"):
    """Pull a model from Ollama registry (runs in background)."""
    import subprocess as sp
    sp.Popen(["ollama", "pull", model], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    return {"status": "pulling", "model": model, "message": f"Pulling {model} in background. Check /api/brain/status for available models."}


# ===========================================================================
# Pipeline runner
# ===========================================================================

_run_in_progress = False

def _run_pipeline(args: str = "--no-sentiment"):
    global _run_in_progress
    _run_in_progress = True
    try:
        # Audit M5: no shell — args as a list, injection-shaped pattern gone
        python = sys.executable
        argv = [python, str(ROOT / "main.py")] + [a for a in args.split() if a]
        subprocess.run(argv, shell=False, cwd=str(ROOT))
    finally:
        _run_in_progress = False


@app.post("/api/run", tags=["System"])
def trigger_run(background_tasks: BackgroundTasks, no_sentiment: bool = True):
    """
    Trigger a fresh main.py run in the background.
    The API continues responding while the pipeline runs.
    Poll /health to check when data is refreshed.
    """
    global _run_in_progress
    if _run_in_progress:
        return {"status": "already_running", "message": "A pipeline run is already in progress"}
    args = "--no-sentiment" if no_sentiment else ""
    background_tasks.add_task(_run_pipeline, args)
    return {
        "status": "started",
        "message": f"Pipeline started in background (args: {args}). Poll /health for status.",
    }


# ===========================================================================
# Summary endpoint — everything in one call
# ===========================================================================

@app.get("/api/summary", tags=["Signals"])
def get_summary():
    """
    Get a lightweight summary of the entire system state.
    Useful for dashboards that need a quick market overview.
    """
    signals   = _load_optional("signals.json")
    macro     = _load_optional("macro_data.json")
    alerts_d  = _load_optional("alerts.json")
    ml        = _load_optional("ml_predictions.json")

    if not isinstance(alerts_d, list):
        alerts_d = []

    scores = [v.get("composite_score", 0) for v in signals.values()]
    top5_bull = sorted(signals.values(), key=lambda x: x.get("composite_score", 0), reverse=True)[:5]
    top5_bear = sorted(signals.values(), key=lambda x: x.get("composite_score", 0))[:5]

    return {
        "timestamp":      datetime.now().isoformat(),
        "market_regime":  macro.get("regime", "Unknown"),
        "macro_score":    macro.get("macro_score", 0),
        "vix":            macro.get("vix"),
        "dxy":            macro.get("dxy"),
        "assets_total":   len(signals),
        "bullish":        sum(1 for s in scores if s > 10),
        "bearish":        sum(1 for s in scores if s < -10),
        "neutral":        sum(1 for s in scores if -10 <= s <= 10),
        "alerts_critical": sum(1 for a in alerts_d if a.get("severity") == "CRITICAL"),
        "alerts_warning":  sum(1 for a in alerts_d if a.get("severity") == "WARNING"),
        "top_bullish": [
            {"ticker": s.get("ticker"), "score": s.get("composite_score"), "action": s.get("action")}
            for s in top5_bull
        ],
        "top_bearish": [
            {"ticker": s.get("ticker"), "score": s.get("composite_score"), "action": s.get("action")}
            for s in top5_bear
        ],
        "ml_bullish": sum(1 for p in ml.values() if p.get("overall_signal") == "Bullish"),
        "ml_bearish": sum(1 for p in ml.values() if p.get("overall_signal") == "Bearish"),
    }


# ===========================================================================
# Universe — GTA-style streaming market data layer (tiered LOD)
# ===========================================================================

def _get_universe():
    """Lazy import so the heavy data stack stays off the startup path."""
    from src.data.universe import get_universe
    return get_universe()


@app.get("/api/universe/status", tags=["Universe"])
def universe_status():
    """Index size, cache counts, last refresh — never touches the network."""
    try:
        return _get_universe().status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/universe/refresh", tags=["Universe"])
def universe_refresh(background_tasks: BackgroundTasks, force: bool = False):
    """Rebuild the tier-0 symbol index (all NSE equities + global universe.yaml)."""
    background_tasks.add_task(lambda: _get_universe().refresh_index(force=force))
    return {"status": "started", "message": "Index refresh running in background. Poll /api/universe/status."}


@app.get("/api/universe/search", tags=["Universe"])
def universe_search(q: str, n: int = 20):
    """Instant tier-0 symbol/name search across the whole known market."""
    if not q or len(q) < 1:
        return []
    return _get_universe().search(q, n=min(n, 50))


@app.get("/api/technical/summary", tags=["Technical"])
def technical_summary_endpoint(symbol: str, timeframes: Optional[str] = None):
    """Investing.com-style technical summary (Strong Buy…Strong Sell) across
    timeframes for ANY global symbol. `timeframes` = comma list (e.g.
    5m,15m,1h,1d,1wk); omit for the default set."""
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    from src.data.technical_summary import multi_timeframe
    tfs = [t.strip() for t in timeframes.split(",")] if timeframes else None
    return _sanitize(multi_timeframe(symbol, tfs))


@app.get("/api/technical/recommendations", tags=["Technical"])
def technical_recommendations(timeframe: str = "1d", limit: int = 25,
                              symbols: Optional[str] = None):
    """Technical recommendations page — strongest setups. Scans `symbols`
    (comma list) or the top-|composite| universe names on `timeframe`."""
    from src.data.technical_summary import (recommendations,
                                            scan_universe_recommendations)
    if symbols:
        recs = recommendations([s.strip() for s in symbols.split(",")], timeframe)
        return _sanitize({"timeframe": timeframe, "recommendations": recs})
    return _sanitize(scan_universe_recommendations(timeframe, min(limit, 60)))


@app.get("/api/universe/explore", tags=["Universe"])
def universe_explore(q: str, market: Optional[str] = None, peers: int = 5):
    """Explore ANY global ticker on demand — including names not in the index
    (e.g. SAIL on NSE). Fetches it live from Yahoo, adds it to the index,
    returns its dossier, and warms related names in the background.
    `market` (NSE/BSE/US/LSE) disambiguates a bare symbol."""
    if not q or len(q) < 1:
        raise HTTPException(status_code=400, detail="q is required")
    return _sanitize(_get_universe().explore(q, prefer=market,
                                             n_peers=min(max(peers, 0), 10)))


@app.get("/api/universe/quote/{symbol}", tags=["Universe"])
def universe_quote(symbol: str):
    """Tier-1 quote card: price + day change, cached 15 min."""
    result = _get_universe().quote(symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return _sanitize(result)


@app.get("/api/fx/gbpinr", tags=["FX"])
def fx_gbpinr(check: bool = True):
    """GBP/INR remittance monitor: live rate, trend vs baseline, and send-direction advice.
    check=true fetches a fresh rate and raises alerts if it moved meaningfully."""
    try:
        from src.data.fx_monitor import check as fx_check, status as fx_status
        return fx_check() if check else fx_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/fx/gbpinr/targets", tags=["FX"])
def fx_gbpinr_targets(high: float = None, low: float = None):
    """Set alert levels: high = alert when £ buys ≥ high INR (send UK→India),
    low = alert when £ buys ≤ low INR (send India→UK)."""
    try:
        from src.data.fx_monitor import set_targets
        return set_targets(high=high, low=low)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/universe/news/{symbol}", tags=["Universe"])
def universe_news(symbol: str, force: bool = False):
    """Fresh news + sentiment + political exposure for a searched symbol (on-demand)."""
    try:
        u = _get_universe()
        info = u._lookup(symbol) if u else None
        name = (info or {}).get("name", "") if info else ""
        from src.data.enrichment import enrich
        return enrich(symbol, name=name, force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/universe/dossier/{symbol}", tags=["Universe"])
def universe_dossier(symbol: str):
    """Tier-2 full dossier: 5y history + fundamentals, cached 24h.
    Quietly prefetches sector peers in the background."""
    result = _get_universe().dossier(symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return _sanitize(result)


@app.get("/api/universe/peers/{symbol}", tags=["Universe"])
def universe_peers(symbol: str, n: int = 5):
    """Same-sector peers by market cap (fills in as the world is explored)."""
    return _sanitize(_get_universe().peers(symbol, n=min(n, 15)))


@app.get("/api/universe/options/{symbol}", tags=["Universe"])
def universe_options(symbol: str):
    """Options chain (nearest expiry, near-the-money strikes) where Yahoo serves
    one; for NSE F&O names, lot-size coverage from the official NSE lot file."""
    result = _get_universe().options_chain(symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return _sanitize(result)


# ===========================================================================
# NEXUS — deterministic, evidence-linked deep research
# ===========================================================================

@app.get("/api/nexus/research/{symbol}", tags=["NEXUS"])
def nexus_research(symbol: str):
    """Full research dossier: red-flag rules (PASS/FAIL, no LLM), evidence-linked
    SWOT, peer comparison, and a /10 score with visible breakdown."""
    from src.data.nexus import research  # lazy: heavy data stack off startup path
    result = research(symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return _sanitize(result)


# ===========================================================================
# QUANT LAB — automated self-learning researcher (research only, no trading)
# ===========================================================================

def _get_lab():
    from src.brain.quant_lab import get_lab
    return get_lab()


@app.get("/api/quant/status", tags=["Quant Lab"])
def quant_status():
    return _get_lab().status()


@app.post("/api/quant/run-now", tags=["Quant Lab"])
def quant_run_now():
    _get_lab().run_now()
    return {"status": "started", "message": "Cycle running in background. Poll /api/quant/status."}


@app.get("/api/quant/papers", tags=["Quant Lab"])
def quant_papers(n: int = 50):
    return _get_lab().papers(n)


@app.get("/api/quant/strategies", tags=["Quant Lab"])
def quant_strategies(n: int = 50):
    return _sanitize(_get_lab().strategies(n))


# ===========================================================================
# THE DESK — autonomous multi-agent trading desk (v3)
# analysts → debate → risk officer → slate → auto-exec (paper-only) → reflect
# ===========================================================================

@app.get("/api/lse/status", tags=["Data"])
def lse_status():
    """London Strategic Edge databank: key present, live usage/allowance.
    Configure LSE_API_KEY in .env to enable the enrichment."""
    from src.data import lse_data
    if not lse_data.available():
        return {"configured": False,
                "hint": "Set LSE_API_KEY in .env (free key from "
                        "londonstrategicedge.com/data) to enrich the macro "
                        "and fundamental agents."}
    return _sanitize({"configured": True, "usage": lse_data.usage()})


@app.post("/api/inference/discover", tags=["Inference"])
def inference_discover():
    """Re-run Ollama model discovery and refresh the router's registry."""
    from src.inference.ollama_discovery import discover
    return _sanitize(discover())


@app.get("/api/inference/status", tags=["Inference"])
def inference_status():
    """Router tiers, circuit-breaker states, and the model registry."""
    from src.inference.ollama_discovery import list_models
    from src.inference.router import get_router
    return _sanitize({**get_router().status(), "registry": list_models(False)})


@app.get("/api/desk/status", tags=["Desk"])
def desk_status():
    """Desk state: daemon, safety gates, auto_execute flag, day budget,
    account=paper|live|disconnected."""
    from src.desk.desk_daemon import get_desk
    return _sanitize(get_desk().status())


@app.post("/api/desk/auto-execute", tags=["Desk"])
def desk_auto_execute(enabled: bool):
    """Arm/disarm autonomous paper execution.
    REFUSES (409) to arm when the connected account is live — auto-exec can
    only ever touch the paper account. Disarming is always allowed."""
    from src.desk.auto_executor import account_snapshot
    from src.desk.config import load_config, paper_mode_confirmed, save_config
    if enabled:
        if not paper_mode_confirmed():
            raise HTTPException(
                status_code=409,
                detail="ALPACA_PAPER is not exactly 'true' — auto-execute cannot be "
                       "armed. Set ALPACA_PAPER=true in .env (paper account) first.")
        account = account_snapshot()
        if account.get("paper") is False:
            raise HTTPException(
                status_code=409,
                detail="LIVE account detected — auto-execute is paper-only and will "
                       "not arm. Real money always goes through human approval.")
    cfg = save_config({"auto_execute": bool(enabled)})
    return {"ok": True, "auto_execute": cfg["auto_execute"]}


@app.post("/api/desk/run-now", tags=["Desk"])
def desk_run_now():
    """Run one analysts→debate→slate→(exec) cycle immediately."""
    from src.desk.desk_daemon import get_desk
    desk = get_desk()
    if desk.working:
        return {"status": "already_working",
                "message": "A desk cycle is already in progress."}
    desk.run_now()
    return {"status": "started",
            "message": "Desk cycle started. Poll /api/desk/status."}


@app.patch("/api/desk/config", tags=["Desk"])
def desk_config(updates: Dict[str, Any]):
    """Update desk config (interval, budgets, caps, notification topics).
    auto_execute is NOT settable here — use /api/desk/auto-execute."""
    from src.desk.config import save_config
    updates.pop("auto_execute", None)
    cfg = save_config(updates)
    if "interval_minutes" in updates:
        try:
            from src.desk.desk_daemon import peek_desk
            d = peek_desk()
            if d is not None:
                d.set_interval(int(updates["interval_minutes"]))
        except Exception as e:
            logger.warning(f"desk reschedule failed: {e}")
    return cfg


@app.get("/api/desk/slate", tags=["Desk"])
def desk_slate():
    """Latest ranked trade slate + debate summaries."""
    return _sanitize(_load_optional("desk/slate.json") or
                     {"slate": [], "rejected": [], "debates": []})


@app.get("/api/desk/debate/{debate_id}", tags=["Desk"])
def desk_debate(debate_id: str):
    """Full debate transcript — every argument, evidence citation, and the verdict."""
    from src.desk.debate import load_debate
    t = load_debate(debate_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Debate '{debate_id}' not found")
    return _sanitize(t)


@app.get("/api/desk/debates", tags=["Desk"])
def desk_debates(n: int = 12):
    """Most recent debate transcripts, newest first."""
    from src.desk.debate import DEBATES_DIR
    if not DEBATES_DIR.exists():
        return {"count": 0, "debates": []}
    files = sorted(DEBATES_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    out = []
    for p in files:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return _sanitize({"count": len(out), "debates": out})


@app.get("/api/desk/executions", tags=["Desk"])
def desk_executions(n: int = 100):
    """Auto-execution log — what ARIA bought/queued, when, and why."""
    from src.desk.auto_executor import read_executions
    records = read_executions(n)
    return _sanitize({
        "count": len(records),
        "auto_fills": sum(1 for r in records if r.get("mode") == "auto"),
        "queued": sum(1 for r in records if r.get("mode") == "queued"),
        "executions": records,
    })


@app.get("/api/desk/health", tags=["Desk"])
def desk_health():
    """Liveness for watchdogs: fresh = a management tick ran in the last
    15 minutes. Cheap — reads one small file, no broker calls."""
    from datetime import datetime
    hb_file = ROOT / "data" / "desk" / "heartbeat.json"
    hb, age_s = {}, None
    if hb_file.exists():
        try:
            hb = json.loads(hb_file.read_text(encoding="utf-8"))
            age_s = (datetime.now()
                     - datetime.fromisoformat(hb.get("at", ""))).total_seconds()
        except Exception:
            pass
    fresh = age_s is not None and age_s < 15 * 60
    return {"ok": fresh, "heartbeat_age_s": round(age_s, 1) if age_s else None,
            "tick_count": hb.get("tick_count"), "last_errors": hb.get("errors")}


@app.post("/api/desk/tick-now", tags=["Desk"])
def desk_tick_now():
    """Run one PositionManager management tick immediately (exit rules,
    trailing stops, bracket healing, legacy triage)."""
    from src.desk.desk_daemon import get_desk
    get_desk().run_tick_now()
    return {"status": "started",
            "message": "Management tick running. Poll /api/desk/status."}


@app.get("/api/desk/lessons", tags=["Desk"])
def desk_lessons(n: int = 50):
    """Fable's teaching lessons from reviewing closed trades."""
    from src.desk.teacher import read_lessons
    return _sanitize({"lessons": list(reversed(read_lessons(n)))})


@app.post("/api/desk/teach-now", tags=["Desk"])
def desk_teach_now():
    """Have Fable review any un-taught closed trades right now (opt-in;
    needs teacher_enabled + ANTHROPIC_API_KEY, budget-capped)."""
    from src.desk.teacher import teach_from_closed_trades
    return _sanitize(teach_from_closed_trades())


@app.get("/api/desk/playbooks", tags=["Desk"])
def desk_playbooks():
    """Armed reflex playbooks (fast-lane triggers) + recent reflex config."""
    from src.desk.config import load_config
    from src.desk.reflex import load_playbooks
    cfg = load_config()
    pbs = load_playbooks()
    return _sanitize({
        "enabled": cfg.get("reflex_enabled", True),
        "poll_seconds": cfg.get("reflex_poll_seconds", 3),
        "min_prob": cfg.get("reflex_min_prob", 0.70),
        "armed": [p for p in pbs if p.get("status") == "armed"],
        "recent": list(reversed(pbs))[:30],
    })


@app.post("/api/desk/reflex-scan", tags=["Desk"])
def desk_reflex_scan():
    """Run one reflex scan pass immediately (for testing the fast lane)."""
    from src.desk.reflex import get_reflex
    return _sanitize({"results": get_reflex().scan_once()})


@app.get("/api/desk/positions", tags=["Desk"])
def desk_positions():
    """Tracked positions (exit-engine state) + latest closed trades."""
    from src.desk.position_manager import PositionManager, read_closed_trades
    return _sanitize({
        "positions": PositionManager().load_positions(),
        "closed": list(reversed(read_closed_trades(50))),
    })


@app.get("/api/desk/performance", tags=["Desk"])
def desk_performance():
    """REALIZED performance from closed trades — the numbers a human uses to
    judge whether this desk deserves real-money copy-trading elsewhere.
    Win rate, avg R, profit factor, expectancy, per-agent hit rates."""
    from datetime import datetime, timedelta
    from src.desk.position_manager import read_closed_trades
    from src.desk.scorecard import agent_hit_rates, current_weights
    raw = read_closed_trades(2000)

    # Audit M2: a 50% scale-out leg + its runner are ONE logical trade —
    # merge legs on (ticker, entry_at) so win rate isn't structurally
    # inflated by scale-outs (which only ever fire at a profitable target).
    merged: Dict[tuple, dict] = {}
    for t in raw:
        key = (t.get("ticker"), t.get("entry_at") or t.get("at"))
        if key in merged:
            m = merged[key]
            m["pnl"] = round(float(m.get("pnl") or 0) + float(t.get("pnl") or 0), 2)
            if float(t.get("fraction") or 1.0) >= 1.0:
                m.update({"at": t.get("at"), "reason": t.get("reason"),
                          "r_multiple": t.get("r_multiple"),
                          "exit_price": t.get("exit_price")})
        else:
            merged[key] = dict(t)
    trades = list(merged.values())

    def stats(subset):
        if not subset:
            return {"trades": 0, "pnl": 0.0, "win_rate": None, "avg_r": None,
                    "profit_factor": None, "expectancy": None}
        pnls = [float(t.get("pnl") or 0.0) for t in subset]
        wins = [p for p in pnls if p > 0]
        losses = [-p for p in pnls if p < 0]
        rs = [float(t.get("r_multiple") or 0.0) for t in subset
              if t.get("r_multiple") is not None]
        return {
            "trades": len(subset),
            "pnl": round(sum(pnls), 2),
            "win_rate": round(len(wins) / len(pnls), 3),
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "profit_factor": (round(sum(wins) / sum(losses), 2)
                              if losses and sum(losses) > 0 else None),
            "expectancy": round(sum(pnls) / len(pnls), 2),
        }

    now = datetime.now()

    def since(days):
        cutoff = now - timedelta(days=days)
        out = []
        for t in trades:
            try:
                if datetime.fromisoformat(t["at"]) >= cutoff:
                    out.append(t)
            except Exception:
                continue
        return out

    by_reason: Dict[str, int] = {}
    for t in trades:
        r = (t.get("reason") or "?").split("(")[0].strip()
        by_reason[r] = by_reason.get(r, 0) + 1

    return _sanitize({
        "all": stats(trades),
        "day": stats(since(1)),
        "week": stats(since(7)),
        "exit_reasons": by_reason,
        "agents": agent_hit_rates(),
        "judge_weights": current_weights(),
        "recent": list(reversed(trades[-20:])),
    })


@app.get("/api/desk/pnl", tags=["Desk"])
def desk_pnl():
    """Paper equity curve + open positions + day/total P&L."""
    from src.desk.auto_executor import account_snapshot
    from src.desk.day_state import load_day_state
    account = account_snapshot()
    day = load_day_state(current_equity=account.get("equity"))

    curve = []
    curve_path = ROOT / "data" / "desk" / "equity_curve.jsonl"
    if curve_path.exists():
        for line in curve_path.read_text(encoding="utf-8").splitlines()[-500:]:
            try:
                curve.append(json.loads(line))
            except Exception:
                continue

    equity = account.get("equity") or 0.0
    start_eq = day.get("start_equity") or 0.0
    first_eq = curve[0]["equity"] if curve else start_eq
    positions = account.get("positions") or []
    winners = sum(1 for p in positions if (p.get("unrealized_pl") or 0) > 0)

    return _sanitize({
        "connected": account.get("connected"),
        "account": "paper" if account.get("paper") else
                   ("live" if account.get("paper") is False else "disconnected"),
        "equity": equity,
        "cash": account.get("cash"),
        "day_pnl": round(equity - start_eq, 2) if start_eq else 0.0,
        "day_pnl_pct": round((equity - start_eq) / start_eq * 100, 3) if start_eq else 0.0,
        "total_pnl": round(equity - first_eq, 2) if first_eq else 0.0,
        "total_pnl_pct": round((equity - first_eq) / first_eq * 100, 3) if first_eq else 0.0,
        "open_positions": positions,
        "open_win_rate": round(winners / len(positions), 3) if positions else None,
        "equity_curve": curve,
        "day": day,
    })


# ===========================================================================
# Static frontend (Mac mini deployment: `npm run build` → served here at /app,
# no Node process needed at runtime). Mounted only when a build exists.
# ===========================================================================
try:
    from fastapi.staticfiles import StaticFiles
    _dist = ROOT / "frontend" / "dist"
    if _dist.exists():
        app.mount("/app", StaticFiles(directory=str(_dist), html=True), name="app")
        logger.info(f"Serving built frontend at /app from {_dist}")
except Exception as _e:
    logger.warning(f"static frontend mount skipped: {_e}")
