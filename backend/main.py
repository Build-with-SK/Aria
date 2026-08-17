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

from fastapi import (BackgroundTasks, Depends, FastAPI, File, HTTPException,
                     Request, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Not lazy on purpose: this is the guard on every order-placing route, and a
# guard that is imported inside the handler is a guard that can fail to import
# at the worst possible moment.
from src.auth.guard import require_owner

def _configure_logging():
    """Give the application's own loggers somewhere to go.

    Without this nothing configures the root logger — uvicorn sets up only its
    own — so every logger.info() in this codebase was silently discarded. That
    meant no record of who signed in, which requests were refused, or who was
    being rate limited: the audit trail existed in the source and nowhere else.
    On a machine with a broker attached that is the difference between noticing
    someone probing and never knowing.

    Rotating file plus stdout. data/*.log is gitignored, and the file is what
    survives a restart on the mini where nobody is watching the console.
    """
    root = logging.getLogger()
    if any(getattr(h, "_aria", False) for h in root.handlers):
        return                                   # --reload runs this twice
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console._aria = True
    root.addHandler(console)

    try:
        from logging.handlers import RotatingFileHandler
        (ROOT / "data").mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(ROOT / "data" / "aria.log",
                                 maxBytes=5_000_000, backupCount=3,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        fh._aria = True
        root.addHandler(fh)
    except Exception as e:                       # read-only disk, permissions
        console.handle(logging.LogRecord(
            "aria", logging.WARNING, __file__, 0,
            f"file logging unavailable: {e}", None, None))

    # Third-party libraries are chatty at INFO and would bury the audit lines.
    for noisy in ("httpx", "httpcore", "urllib3", "yfinance", "peewee",
                  "watchfiles", "asyncio", "chromadb", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
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
    threading.Thread(target=_start_desk, daemon=True).start()
    threading.Thread(target=_discover_models, daemon=True).start()
    threading.Thread(target=_fx_monitor_loop, daemon=True).start()
    # The quant lab used to be launched from the last line of the FX monitor
    # loop — after a `while True:`, so it was unreachable and the researcher
    # never actually started. It belongs here with the other daemons.
    threading.Thread(target=_start_quant_lab, daemon=True).start()
    threading.Thread(target=_outcome_resolver_loop, daemon=True).start()


def _outcome_resolver_loop():
    """Grade V5 predictions whose horizon has elapsed.

    This is the flywheel's drive belt, and until now it was not attached to
    anything. `learning.resolve_pending()` existed and worked, but the ONLY
    caller was the manual `POST /api/v5/learning/resolve` endpoint — so unless
    somebody remembered to hit it by hand, no prediction was ever graded.

    Everything downstream is built on those labels: the calibration numbers, the
    Brier score, the baseline comparison, the per-module tiers, and the learned
    reliability multipliers that reweight the ensemble. With no resolver running
    they all correctly reported "not measurable", forever, no matter how long
    the system ran. The track record was not empty because ARIA was young; it
    was empty because nothing was closing the loop.

    Hourly is ample — horizons are measured in weeks, and `resolve_pending` is
    idempotent and cheap when there is nothing due. It runs on the same clock
    whether or not anyone is looking at the deck, which is the point: a measured
    track record has to accumulate while you are not watching it.
    """
    import time
    time.sleep(20)          # let the vendor caches and universe DB settle
    while True:
        try:
            from src.v5 import learning
            out = learning.resolve_pending()
            if out.get("resolved"):
                logger.info(f"V5 outcomes resolved: {out['resolved']} "
                            f"of {out.get('checked', 0)} pending")
        except Exception as e:
            # A vendor outage must not kill the resolver thread — the calls it
            # could not grade today are still pending tomorrow.
            logger.warning(f"outcome resolver tick failed: {e}")
        time.sleep(3600)


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

# The model every non-owner gets. Small on purpose: the 24/7 host is a 2014
# Intel Mac mini with no usable GPU, where a 7B runs at 1-3 tok/s and two
# concurrent requests do not fit in 8GB. 4B is the largest thing that answers
# in roughly a minute there instead of three.
FREE_MODEL = os.environ.get("ARIA_FREE_MODEL", "gemma3:4b")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _origin_allowed(request, origin: str) -> bool:
    """Is this mutating request coming from somewhere we accept?

    Two ways to qualify, and the second one was missing:

      1. An explicitly allowed origin — the Vite dev servers on 3000/5173.
      2. THE APP'S OWN ORIGIN. The built frontend is served by this process at
         `/app`, so its Origin is whatever host this API is reached on —
         `http://localhost:8000` normally, a tunnel hostname behind cloudflared.
         None of those are in the static list, so every POST from the built UI
         was refused: chat, run-now, approvals, all of it. The dev server
         worked and the shipped app did not.

    Allowing (2) is not a loosening. CSRF is by definition a request from
    ANOTHER origin, and the browser sets `Origin` itself — a page on evil.com
    cannot make it say `localhost:8000`. Comparing it to the Host this request
    actually arrived on is the standard same-origin check, and a foreign origin
    still fails it.
    """
    if origin in _ALLOWED_ORIGINS:
        return True
    try:
        from urllib.parse import urlparse
        return bool(origin) and urlparse(origin).netloc == request.headers.get("host", "")
    except ValueError:
        return False


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
        if origin and not _origin_allowed(request, origin):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403,
                                content={"detail": "origin not allowed"})
        required = os.environ.get("ARIA_API_KEY", "")
        if required and request.headers.get("x-api-key") != required:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401,
                                content={"detail": "x-api-key required"})
    return await call_next(request)


# Registered last, so it is the OUTERMOST middleware and decides who you are
# before anything else runs. _mutation_guard above only covers writes; the
# vault, the portfolio and the desk leak through GETs, which is precisely how
# 112 endpoints ended up readable by anyone who could reach the port.
@app.middleware("http")
async def _role_guard(request, call_next):
    """Attach a role to every request and refuse anything outside it.

    Deny is the default: free users reach only the research allowlist in
    src/auth/policy.py, and everything else — vault, portfolio, execution,
    desk, brain, cloud chat — is owner-only regardless of tier. A route added
    tomorrow is invisible to free users until it is deliberately named, so
    forgetting costs a 403 rather than a disclosure.
    """
    from src.auth import policy

    # CORS preflight carries no credentials by design; 403-ing it would break
    # the browser before the real, authenticated request is ever sent.
    if request.method == "OPTIONS":
        return await call_next(request)

    from src.auth import hardening, session as sess
    from fastapi.responses import JSONResponse

    ip = hardening.client_ip(request)

    # Rate limit before authenticating: the cost of a request must not depend on
    # whether the caller has an account, or an unauthenticated flood is free.
    allowed, retry = hardening.rate_limit(ip, request.url.path)
    if not allowed:
        logger.info("rate_limit: %s throttled on %s", ip, request.url.path)
        return JSONResponse(status_code=429,
                            content={"detail": "Too many requests. Please slow down.",
                                     "retry_after": retry},
                            headers={"Retry-After": str(retry)})

    # Before deciding anything: does this request prove a proxy is in front of
    # us? If so the loopback fallback is retired from here on, whatever the
    # configuration says. A tunnel makes every caller look like localhost.
    policy.note_proxy_evidence(request.headers)

    who = sess.read(request.cookies.get(sess.COOKIE))
    # `ip` may have come from X-Forwarded-For, which the caller writes. The
    # rate limiter wants it (count the human, not the proxy); the ownership
    # decision must not have it, or "X-Forwarded-For: 127.0.0.1" is a login.
    role, basis = policy.resolve_role_with_basis(
        request.headers, ip, who, peer_host=hardening.peer_ip(request))

    # NOTE on what is deliberately NOT done here. A review argued that inferred
    # ownership should also be refused the vault and the portfolio, not only a
    # broker. The tunnel case it was worried about is closed above — a proxied
    # request carries a forwarding header, which retires the fallback before
    # this line runs. What remains is a caller who reached this process on a
    # real loopback socket with no proxy anywhere, i.e. someone already on the
    # machine, which is the documented single-user design. Downgrading that
    # would break the owner's own laptop to defend against an attacker who is
    # already inside it.
    request.state.role_basis = basis
    request.state.role = role
    request.state.user = who

    def _harden(resp):
        for k, v in hardening.security_headers(hardening.is_https(request)).items():
            resp.headers.setdefault(k, v)
        return resp

    if not policy.is_allowed(request.url.path, role):
        logger.info("role_guard: %s denied %s %s",
                    role, request.method, request.url.path)
        # 401 means "sign in and try again"; 403 means "signed in, still no".
        # Collapsing them would leave the UI unable to tell a login prompt from
        # a dead end.
        if role == policy.ANON:
            return _harden(JSONResponse(status_code=401, content={
                "detail": "Sign in to use ARIA.",
                "login": "/api/auth/providers",
                "role": role,
            }))
        return _harden(JSONResponse(status_code=403, content={
            "detail": "This is owner-only.",
            "reason": policy.denial_reason(request.url.path),
            "role": role,
        }))
    return _harden(await call_next(request))

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


@app.get("/api/desk/intents", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_intents():
    """What he has asked her to trade, and what she is still waiting for.

    An intent is a held instruction, not an order. It fills when his
    conditions are met, her own read does not contradict it, and the risk gate
    passes — in paper, through the same executor as every desk trade."""
    from src.desk import intents
    return _sanitize(intents.status())


@app.post("/api/desk/intents", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_intent_create(body: dict):
    """Record an intent from a sentence, or from explicit fields.

    Body: `{"text": "buy 20 AAPL under 210"}` or
    `{"side": "buy", "ticker": "AAPL", "qty": 20, "limit_price": 210}`."""
    from src.desk import intents

    text = str(body.get("text") or "")
    parsed = intents.parse(text) if text else None
    if parsed is None:
        if not (body.get("ticker") and body.get("qty")):
            raise HTTPException(
                status_code=400,
                detail=("no trade found in that. Say it like 'buy 20 AAPL' or "
                        "'sell 5 NVDA above 130', or pass ticker and qty "
                        "explicitly. Refusing to guess is deliberate — a "
                        "mis-parsed sentence that became an order would be "
                        "the worst bug in this system."))
        parsed = {"side": str(body.get("side") or "buy").lower(),
                  "ticker": str(body["ticker"]).upper(),
                  "qty": float(body["qty"]),
                  "notional": bool(body.get("notional")),
                  "limit_price": body.get("limit_price"),
                  "min_price": body.get("min_price")}
    return _sanitize(intents.record(parsed,
                                    source=str(body.get("source") or "api"),
                                    said=text))


@app.post("/api/desk/intents/{intent_id}/cancel", tags=["Desk"],
          dependencies=[Depends(require_owner)])
def desk_intent_cancel(intent_id: str, body: dict | None = None):
    """Withdraw a held intent."""
    from src.desk import intents
    row = intents.cancel(intent_id, (body or {}).get("reason", ""))
    if not row:
        raise HTTPException(status_code=404,
                            detail="no open intent with that id")
    return _sanitize(row)


@app.post("/api/desk/intents/check", tags=["Desk"],
          dependencies=[Depends(require_owner)])
def desk_intents_check():
    """Walk the open intents now rather than waiting for the next tick."""
    from src.desk import intents
    return _sanitize(intents.check_once())


@app.get("/api/portfolio/holdings", tags=["Portfolio"],
         dependencies=[Depends(require_owner)])
def portfolio_holdings():
    """What he ACTUALLY owns, from the broker outward.

    `/api/portfolio` above serves `data/portfolio_analysis.json`, computed
    over a fixed analysis universe — AAPL, ^GSPC, EURUSD=X, GC=F and twenty
    others — and last written on 31 May. None of it was ever bought. This is
    the account: positions the broker reports, priced, weighted, and carrying
    the debate that opened each one."""
    from src.portfolio.holdings import report
    return _sanitize(report())


@app.get("/api/portfolio/related", tags=["Portfolio"],
         dependencies=[Depends(require_owner)])
def portfolio_related(per_holding: int = 4, score: bool = False):
    """Instruments related to what he holds, scored by the same v5 engine.

    `score=true` runs the full pipeline per name and takes real seconds — the
    default returns the neighbourhood and lets the caller ask for scores.

    Anything that adds to an already-heavy exposure is flagged: recommending
    three more oil funds to a man long oil is a correlation trap, and the fact
    that each looks good individually is exactly how it happens."""
    from src.portfolio.related import report
    return _sanitize(report(limit_per_holding=max(1, min(per_holding, 10)),
                            score=bool(score)))


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


@app.post("/api/execute/propose", tags=["Execution"], dependencies=[Depends(require_owner)])
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


@app.get("/api/execute/queue", tags=["Execution"], dependencies=[Depends(require_owner)])
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


@app.post("/api/execute/approve/{trade_id}", tags=["Execution"], dependencies=[Depends(require_owner)])
def approve_and_execute(trade_id: str):
    """Approve and immediately execute a pending trade via the broker."""
    mgr = _get_order_manager()
    if not mgr:
        raise HTTPException(status_code=503, detail="Execution engine not available.")
    result = mgr.execute(trade_id)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Execution failed"))
    return result


@app.post("/api/execute/reject/{trade_id}", tags=["Execution"], dependencies=[Depends(require_owner)])
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


@app.post("/api/execute/cancel/{trade_id}", tags=["Execution"], dependencies=[Depends(require_owner)])
def cancel_trade(trade_id: str):
    """Cancel a pending trade before it reaches the broker."""
    try:
        from src.execution.approval_queue import ApprovalQueue
        ok = ApprovalQueue().cancel(trade_id)
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/execute/brokers", tags=["Execution"], dependencies=[Depends(require_owner)])
def get_broker_status():
    """Connection status and account info for all brokers."""
    mgr = _get_order_manager()
    if not mgr:
        return {"alpaca": {"connected": False}, "ibkr": {"connected": False}}
    return mgr.get_broker_status()


@app.get("/api/execute/positions", tags=["Execution"], dependencies=[Depends(require_owner)])
def get_live_positions():
    """Live positions across Alpaca and IBKR."""
    mgr = _get_order_manager()
    if not mgr:
        return {"positions": []}
    return {"count": len(p := mgr.get_all_positions()), "positions": p}


# ===========================================================================
# Authentication — sign in with Google / GitHub / Apple
# ===========================================================================

@app.get("/api/auth/providers", tags=["Auth"])
def auth_providers():
    """Which sign-in buttons the login page should show, and why any are absent."""
    from src.auth import oauth
    return {"providers": oauth.providers(),
            "configured": oauth.any_configured(),
            "base_url": oauth.base_url()}


@app.get("/api/auth/me", tags=["Auth"])
def auth_me(request: Request):
    """The current session. Always 200 so the frontend can ask before login."""
    who = getattr(request.state, "user", None)
    role = _role_of(request)
    if not who:
        return {"authenticated": False, "role": role}
    return {"authenticated": True, "role": role,
            "email": who.get("email"), "name": who.get("name"),
            "avatar": who.get("avatar"), "provider": who.get("provider"),
            "owner": bool(who.get("owner"))}


@app.get("/api/auth/login/{provider}", tags=["Auth"])
def auth_login(provider: str, next: str = "/"):
    """Send the browser to the provider's consent screen."""
    from fastapi.responses import RedirectResponse
    from src.auth import oauth
    if provider not in oauth.PROVIDERS:
        raise HTTPException(status_code=404, detail="unknown provider")
    avail = {p["id"]: p for p in oauth.providers()}
    if not avail[provider]["available"]:
        raise HTTPException(status_code=503,
                            detail=f"{provider} is not configured: {avail[provider]['reason']}")
    return RedirectResponse(oauth.authorize_url(provider, oauth.safe_next(next)),
                            status_code=302)


@app.get("/api/auth/callback/{provider}", tags=["Auth"])
def auth_callback(provider: str, request: Request,
                  code: str = "", state: str = "", error: str = ""):
    """Provider redirect lands here. Verifies state, exchanges the code, sets
    the session cookie, and bounces back into the app."""
    from fastapi.responses import RedirectResponse
    from src.auth import oauth
    from src.auth import session as sess

    import urllib.parse
    front = os.environ.get("ARIA_FRONTEND_URL", "http://localhost:3000").rstrip("/")

    if error or not code:
        return RedirectResponse(f"{front}/login?error={urllib.parse.quote(error or 'no_code')}",
                                status_code=302)

    st = oauth.read_state(state)
    if not st or st.get("p") != provider:
        # Rejected before any code is exchanged — a forged redirect cannot
        # start a session.
        return RedirectResponse(f"{front}/login?error=bad_state", status_code=302)

    identity = oauth.exchange(provider, code)
    if not identity:
        return RedirectResponse(f"{front}/login?error=exchange_failed", status_code=302)

    resp = RedirectResponse(f"{front}{oauth.safe_next(st.get('n'))}", status_code=302)
    resp.set_cookie(sess.COOKIE, sess.issue(identity), **sess.cookie_kwargs())
    is_owner = sess.is_owner_identity(identity)
    # With OAuth there is no separate registration step: the first sign-in IS
    # the sign-up, and this is where it happens. Recorded here rather than in a
    # middleware so it captures the verified identity the provider returned,
    # not a role derived from a cookie later.
    from src.auth import users
    users.record_sign_in(identity, owner=is_owner)
    logger.info("auth: %s signed in via %s (owner=%s)",
                identity.get("email") or identity.get("sub"), provider, is_owner)
    return resp


@app.get("/api/auth/users", tags=["Auth"],
         dependencies=[Depends(require_owner)])
def auth_users():
    """Everyone who has ever signed in. Owner-only.

    The moment real people use this, the file behind it holds personal data and
    the owner is a controller under UK GDPR — which needs a lawful basis, a
    privacy notice saying what is kept, and a way to erase someone. The record
    is deliberately minimal so those obligations are cheap to honour; see
    src/auth/users.py and the DELETE below."""
    from src.auth import users
    return _sanitize({"stats": users.stats(), "users": users.list_users()})


@app.delete("/api/auth/users/{key:path}", tags=["Auth"],
            dependencies=[Depends(require_owner)])
def auth_delete_user(key: str):
    """Erase one person's record — the deletion request path. Owner-only."""
    from src.auth import users
    return {"ok": users.delete(key), "key": key}


@app.post("/api/auth/logout", tags=["Auth"])
def auth_logout():
    from fastapi.responses import JSONResponse
    from src.auth import session as sess
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(sess.COOKIE, path="/")
    return resp


# ===========================================================================
# Obsidian Vault (direct filesystem — no MCP server)
# ===========================================================================

def _vault_context(messages, role: str = "free") -> str:
    """Relevant vault knowledge for the latest user message. '' on any failure.

    The vault is the owner's Obsidian notes — 486 of them, including personal
    folders — and this function decides whether a reply may be grounded in them.
    It is owner-only structurally: the role has to be passed in and checked
    here, rather than read from a config flag, so there is no setting anyone can
    flip to share someone's private notes with a stranger. The default is the
    safe one, so a caller that forgets to pass a role gets no vault.
    """
    try:
        from src.auth import policy
        if not policy.can_read_vault(role):
            return ""
        user_msgs = [m.content for m in messages if m.role == "user"]
        if not user_msgs:
            return ""
        from src.brain.vault import get_vault
        return get_vault().context_for(user_msgs[-1], n=3, max_chars=1500)
    except Exception as e:
        logger.debug(f"Vault context unavailable: {e}")
        return ""


def _role_of(request) -> str:
    """Role attached by _role_guard; 'free' if anything is missing."""
    try:
        return getattr(request.state, "role", "free") or "free"
    except Exception:
        return "free"


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


@app.post("/api/vault/reindex", tags=["Vault"], dependencies=[Depends(require_owner)])
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


@app.post("/api/chat", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_chat(body: ChatRequest, request: Request):
    """Send a message to ARIA. Returns the assistant reply with market context baked in.

    Owner-only. Her reasoning runs on weights this machine holds — the router
    refuses vendor providers in this path (`src/inference/policy.py`,
    decision 1). The Anthropic key gate that used to stand here is gone with
    it: a missing vendor key is no longer a reason she cannot think, and its
    presence is no longer a reason she reaches for one."""

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

    vault_ctx = _vault_context(body.messages, _role_of(request))
    ondemand_ctx = _ondemand_ticker_context(body.messages)

    # V5 identity: the Two Absolute Laws and the persona live in one place
    # (src/v5/identity.py) so chat, brain and desk prose cannot drift apart.
    try:
        from src.v5.identity import system_prompt as _v5_system_prompt
        _identity = _v5_system_prompt() + "\n\n---\n\n"
    except Exception as _e:
        logger.warning(f"V5 identity unavailable, using the legacy preamble: {_e}")
        _identity = ""

    # "Buy 20 AAPL." Recorded as a held intent, not filled here: the chat
    # endpoint's job is to notice he asked, write it down, and tell him it is
    # written down. src/desk/intents.py decides when the moment is right, and
    # every fill goes through the paper broker.
    _intent_ctx = ""
    try:
        from src.desk import intents as _intents
        _said = next((m.content for m in reversed(body.messages)
                      if m.role == "user"), "")
        _parsed = _intents.parse(_said)
        if _parsed:
            _row = _intents.record(_parsed, source="chat", said=_said)
            _cond = []
            if _row.get("limit_price"):
                _cond.append(f"only at or below {_row['limit_price']}")
            if _row.get("min_price"):
                _cond.append(f"only at or above {_row['min_price']}")
            _intent_ctx = (
                f"\n## HE JUST ASKED YOU TO TRADE\n"
                f"You have recorded this as a held intent ({_row['id']}): "
                f"{_row['side']} {_row['qty']} {_row['ticker']}"
                + (f", {' and '.join(_cond)}" if _cond else "")
                + f". It expires {_row['expires_at'][:10]}.\n"
                f"Confirm it back to him in one sentence — what you wrote "
                f"down, and that you will act when the conditions are met "
                f"rather than immediately. Say it is paper. Do NOT claim to "
                f"have bought anything: nothing has been filled.\n")
    except Exception as _e:
        logger.warning(f"intent parsing skipped: {_e}")

    # Her own state, in the SYSTEM prompt rather than in a droppable context
    # block. Asked "are you learning?" she answered "Yes, I am continuously
    # learning and updating my models" — fluent and false: no fine-tune has
    # ever run and no prediction has resolved. A model with no facts about
    # itself answers that question from what an assistant is supposed to say.
    # Giving it the measured numbers is the only thing that changes the answer,
    # and it must be un-droppable: this is the claim that must survive a full
    # context window (invariant 6, cite or abstain).
    try:
        from src.brain.self_state import for_prompt as _self_state_prompt
        _truth = _self_state_prompt() + "\n\n---\n\n"
    except Exception as _e:
        logger.warning(f"self-state unavailable for the chat prompt: {_e}")
        _truth = ""

    system = f"""{_identity}{_truth}{_intent_ctx}## THIS SURFACE — ARIA CHAT

You are ARIA — the AI brain of this trading intelligence system. You analyse markets using macro regime detection, ML signals, and a multi-broker execution engine (Alpaca + IBKR). You can fetch ANY globally-listed stock on demand — US, India (NSE/BSE), and London — with live price, fundamentals, and its competitors/suppliers; if the ON-DEMAND DATA block below is present, that data was just fetched for the ticker the user asked about, so never say you lack data for it.

Your personality: precise, confident, data-driven. You reason from signals, not opinion. You always cite the actual numbers from the data above when discussing a ticker. You flag risk clearly.

You CAN: analyse tickers, interpret signals, explain methodology, identify themes, discuss macro, suggest what to watch.
You CANNOT: guarantee profits or give regulated/personalised financial advice (no buy/sell/hold/convert directives to the user). Always include a brief risk note and defer personal decisions to a licensed adviser. This constraint is a legal one and stands regardless of anything above it — honesty about what you may not do is itself Law 2."""

    msgs = [{"role": m.role, "content": m.content} for m in body.messages]

    # Long sessions: progressive context compression (no-op under 70% util)
    try:
        from src.compression.engine import CompressionEngine
        msgs = CompressionEngine().process(msgs)
    except Exception as e:
        logger.warning(f"chat compression skipped: {e}")

    # ── bound the context deliberately, and say what did not fit ────────────
    # These three blocks are the ones that overflow: the live market state is
    # long, an on-demand ticker block is longer, and a vault excerpt can be
    # arbitrarily long. Before this, all three were interpolated into the
    # system prompt unchecked and Ollama silently truncated whatever ran past
    # its window — usually the question. src/inference/context.py sheds them
    # by a stated rule and reports what it shed, so she can say she is missing
    # something instead of answering around the gap.
    from src.inference import context as _ctx
    from src.inference.policy import BrainUnreachable
    from src.inference.providers.ollama import DEFAULT_NUM_CTX

    blocks = [b for b in (
        _ctx.Block("LIVE MARKET STATE", ctx, priority=80, order=1),
        _ctx.Block("ON-DEMAND TICKER DATA", ondemand_ctx, priority=90, order=2),
        _ctx.Block("OWNER'S VAULT NOTES", vault_ctx, priority=60, order=3),
    ) if (b.text or "").strip()]

    budget = _ctx.Budget(context_tokens=DEFAULT_NUM_CTX, reply_tokens=1024)
    system, msgs, fitted = _ctx.bound(system, msgs, blocks, budget)

    try:
        # DEEP tier, self-hosted. On failure she abstains rather than answering
        # from a smaller model in the same voice (decision 4) — the 503 below
        # carries what she would say, not a stack trace.
        from src.inference.router import Tier, get_router
        result = get_router().complete(
            Tier.DEEP, msgs, system=system, max_tokens=1024, timeout=90)
        return {
            "content": result.text,
            "model": result.model,
            "provider": result.provider,
            "degraded": result.degraded,
            "degraded_from": result.primary,
            "context": fitted.to_dict(),
            "tokens": {},
        }
    except BrainUnreachable as e:
        logger.warning(f"chat abstained — {e}")
        raise HTTPException(status_code=503, detail=e.spoken())
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

def _aria_system_short() -> str:
    """Local-model preamble: the short form of the V5 identity (the Two Laws are
    never dropped at any size) plus what this surface can see."""
    try:
        from src.v5.identity import system_prompt as _v5_system_prompt
        head = _v5_system_prompt(full=False) + "\n\n---\n\n"
    except Exception:
        head = ""
    return head + """## THIS SURFACE — LOCAL BRAIN

You analyse markets using a 766-ticker universe, LightGBM ensemble ML, macro regime
detection, 10 Market Situation Archetypes, and a multi-broker execution engine.
Be precise, data-driven, and reference actual signal numbers. Flag risk clearly.
Keep responses concise. Do not give regulated or personalised financial advice."""


_ARIA_SYSTEM = _aria_system_short()


class LocalChatRequest(BaseModel):
    messages: List[ChatMsg]
    model:    str = "qwen2.5-coder:7b"

@app.post("/api/chat/local", tags=["Local Brain"])
def aria_chat_local(body: LocalChatRequest, request: Request):
    """Chat with the local Ollama model — no cloud API needed.

    This is the surface free users get. Three things are enforced here rather
    than left to configuration:

    * the model is pinned to FREE_MODEL, because the deployment target is a
      2014 Intel Mac mini with no usable GPU — a 7B there runs at 1-3 tok/s and
      cannot hold a second concurrent request in 8GB;
    * `complete_with("ollama", ...)` names the provider directly, so there is no
      tier that can fall back to Anthropic. A sleeping laptop must return "try
      again", never a bill on the owner's key;
    * no vault, ever — _vault_context checks the role itself.
    """
    role = _role_of(request)
    if not _ollama_available():
        raise HTTPException(
            status_code=503,
            detail="ARIA's local model is not reachable right now. Please try again shortly.")

    # Free users may not choose the model; a 7B request would queue the box.
    model = body.model if role == "owner" else FREE_MODEL

    ctx = _build_market_context()
    vault_ctx = _vault_context(body.messages, role)
    ondemand_ctx = _ondemand_ticker_context(body.messages)
    system = f"{_ARIA_SYSTEM}\n\n{ctx}{ondemand_ctx}"
    if vault_ctx:
        system += f"\n\n{vault_ctx}"
    try:
        from src.inference.router import get_router
        result = get_router().complete_with(
            "ollama", model,
            [{"role": m.role, "content": m.content} for m in body.messages],
            system=system, max_tokens=1024, timeout=180)
        return {"content": result.text, "model": model, "tokens": {}}
    except Exception as e:
        logger.warning("local chat failed for role=%s model=%s: %s", role, model, e)
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/brain/pulse", tags=["Local Brain"])
def brain_pulse():
    """The brain's vital signs — the ONE brain endpoint everybody may read.

    ARIA thinking is the most compelling thing this system does, and it should
    not be behind a login. But the brain reasons WITH the owner's Obsidian vault
    in context (brain_daemon REASON step), so its cycle transcripts and its 783
    stored memories can quote personal notes verbatim. /api/brain/memories also
    returns the absolute ChromaDB path, which puts the owner's username back in
    a response after we removed it from every file.

    So this returns telemetry and no prose: is it alive, which step is it on,
    how many cycles has it run, how many memories does it hold. Enough to drive
    a living visualisation that is genuinely reflecting a real brain, without a
    single line of what it is actually thinking about.

    Nothing here is a control. Every POST — start, stop, run-now, consult,
    pull-model, generate-training-data — stays owner-only, because those spend
    the owner's CPU, disk and API budget.
    """
    daemon = {"running": False, "thinking": False, "cycle_count": 0,
              "last_cycle_at": None, "memory_count": None, "model": None,
              "step": None}
    try:
        from src.brain.brain_daemon import peek_brain
        b = peek_brain()
        if b is not None:
            s = b.status() or {}
            # Allowlist the fields. A blocklist would leak whatever gets added
            # to status() next — and something will be.
            daemon = {
                "running": bool(s.get("running")),
                "thinking": bool(s.get("thinking")),
                "cycle_count": int(s.get("cycle_count") or 0),
                "last_cycle_at": s.get("last_cycle_at"),
                "memory_count": s.get("memory_count"),
                "model": s.get("model"),
                "step": s.get("step") or s.get("current_step"),
            }
    except Exception as e:
        logger.debug(f"brain pulse peek failed: {e}")

    return {"alive": bool(daemon["running"]), "daemon": daemon,
            "ollama_running": _ollama_available()}


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


@app.post("/api/brain/consult", tags=["Local Brain"], dependencies=[Depends(require_owner)])
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


@app.post("/api/brain/run-now", tags=["Local Brain"], dependencies=[Depends(require_owner)])
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


@app.post("/api/brain/start", tags=["Local Brain"], dependencies=[Depends(require_owner)])
def brain_start(model: str = "qwen2.5-coder:7b"):
    """Start (or resume) the brain daemon."""
    if not _ollama_available():
        raise HTTPException(status_code=503, detail="Ollama is not running. Start it with: ollama serve")
    from src.brain.brain_daemon import get_brain
    brain = get_brain(model=model)
    brain.model = model
    brain.start()
    return {"status": "started", **brain.status()}


@app.post("/api/brain/stop", tags=["Local Brain"], dependencies=[Depends(require_owner)])
def brain_stop():
    """Pause the brain daemon."""
    from src.brain.brain_daemon import peek_brain
    brain = peek_brain()
    if brain is None or not brain.running:
        return {"status": "not_running"}
    brain.stop()
    return {"status": "stopped", **brain.status()}


@app.patch("/api/brain/interval", tags=["Local Brain"], dependencies=[Depends(require_owner)])
def brain_set_interval(minutes: int = 15):
    """Change the reasoning cycle interval."""
    if minutes < 1 or minutes > 1440:
        raise HTTPException(status_code=400, detail="Interval must be between 1 and 1440 minutes")
    from src.brain.brain_daemon import get_brain
    brain = get_brain()
    brain.set_interval(minutes)
    return {"status": "ok", "interval_minutes": brain.interval_minutes}


@app.patch("/api/brain/eye-interval", tags=["Local Brain"],
           dependencies=[Depends(require_owner)])
def brain_set_eye_interval(minutes: int = 20):
    """Change how often the eye is offered a look.

    This is not how often she looks: blink() only visits watches whose own
    cadence is due, so a 20-minute offer against a 60-minute watch costs
    three cheap no-ops an hour and one real sweep. Lower it to make her more
    responsive to breaking news; raise it to spend fewer requests."""
    if minutes < 1 or minutes > 1440:
        raise HTTPException(status_code=400, detail="Interval must be between 1 and 1440 minutes")
    from src.brain.brain_daemon import get_brain
    brain = get_brain()
    brain.set_eye_interval(minutes)
    return {"status": "ok", "eye_interval_minutes": brain.eye_interval_minutes}


@app.post("/api/brain/blink-now", tags=["Local Brain"],
          dependencies=[Depends(require_owner)])
def brain_blink_now():
    """Look at the internet now, ignoring every cadence.

    Unlike run-now this does not need Ollama: looking is not thinking."""
    from src.brain.brain_daemon import get_brain
    brain = get_brain()
    brain.blink_now()
    return {"status": "looking",
            "message": "Blink started. Poll /api/brain/status or /api/research/eye."}


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


@app.post("/api/brain/generate-training-data", tags=["Local Brain"], dependencies=[Depends(require_owner)])
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


@app.post("/api/brain/pull-model", tags=["Local Brain"], dependencies=[Depends(require_owner)])
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


@app.post("/api/run", tags=["System"], dependencies=[Depends(require_owner)])
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


@app.post("/api/universe/refresh", tags=["Universe"], dependencies=[Depends(require_owner)])
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


@app.get("/api/universe/index", tags=["Universe"])
def universe_index(exchange: Optional[str] = None, asset_class: Optional[str] = None,
                   limit: int = 40000):
    """Bulk lightweight symbol dump for the 3D universe map — every listed
    symbol ARIA knows (symbol, name, exchange, asset_class). Never touches the
    network. Optional filters: exchange, asset_class."""
    return _get_universe().index(exchange=exchange, asset_class=asset_class,
                                 limit=min(max(limit, 1), 60000))


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


@app.post("/api/technical/snapshot", tags=["Technical"], dependencies=[Depends(require_owner)])
def technical_snapshot(timeframe: str = "1d", limit: int = 30):
    """Record today's technical recommendations for forward performance
    tracking (also runs daily on its own at 21:30)."""
    from src.data.technical_tracker import snapshot
    return _sanitize(snapshot(timeframe, min(limit, 60)))


@app.get("/api/technical/performance", tags=["Technical"])
def technical_performance(min_age_days: int = 1):
    """Honest hit-rate + average return of past technical recommendations,
    by label (Strong Buy/Buy/Sell/…). Accumulates as daily snapshots age."""
    from src.data.technical_tracker import evaluate
    return _sanitize(evaluate(min_age_days=min_age_days))


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
    """Tier-1 quote card: price + day change, cached 15 min.

    `currency` is resolved from src.data.currency rather than read straight off
    the universe row, because that column is NULL for most NSE listings and —
    worse — reports plain "GBP" for London shares that are actually quoted in
    PENCE. A £15.76 share was being labelled £1,576. London now correctly
    returns "GBp", and any consumer must divide by 100 before treating it as
    pounds (the frontend currency helper does)."""
    result = _get_universe().quote(symbol)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    try:
        from src.data.currency import native_currency
        resolved = native_currency(result.get("yahoo") or symbol) \
            or native_currency(result.get("symbol") or symbol)
        if resolved:
            result["currency"] = resolved
            result["quoted_in_minor_units"] = (resolved == "GBp")
    except Exception as e:
        logger.debug(f"quote currency resolution failed for {symbol}: {e}")
    return _sanitize(result)


@app.get("/api/quote/live/{symbol}", tags=["Universe"])
def quote_live(symbol: str, intraday: bool = True):
    """LIVE intraday quote — the price that actually moves during a session.

    Distinct from /api/universe/quote, which serves a 15-minute-cached DAILY
    CLOSE and therefore cannot change intraday. This returns the last traded
    price, the change against the previous close, today's range, and
    `market_state` so a still price can be told apart from a stale feed."""
    from src.data.live_quote import live_quote
    q = live_quote(symbol, with_intraday=intraday)
    if "error" in q:
        raise HTTPException(status_code=404, detail=q["error"])
    return _sanitize(q)


@app.get("/api/quote/live", tags=["Universe"])
def quote_live_batch(symbols: str):
    """Live quotes for several symbols — for the tape and tables."""
    from src.data.live_quote import live_quotes
    syms = [s.strip() for s in (symbols or "").split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="no symbols supplied")
    return _sanitize({"count": len(syms), "quotes": live_quotes(syms)})


@app.get("/api/fx/gbpinr", tags=["FX"])
def fx_gbpinr(check: bool = True):
    """GBP/INR remittance monitor: live rate, trend vs baseline, and send-direction advice.
    check=true fetches a fresh rate and raises alerts if it moved meaningfully."""
    try:
        from src.data.fx_monitor import check as fx_check, status as fx_status
        return fx_check() if check else fx_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/fx/gbpinr/targets", tags=["FX"], dependencies=[Depends(require_owner)])
def fx_gbpinr_targets(high: float = None, low: float = None):
    """Set alert levels: high = alert when £ buys ≥ high INR (send UK→India),
    low = alert when £ buys ≤ low INR (send India→UK)."""
    try:
        from src.data.fx_monitor import set_targets
        return set_targets(high=high, low=low)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fx/rates", tags=["FX"])
def fx_rates(base: str = "USD"):
    """Display-conversion FX rates: units of each supported currency per 1 base.

    For showing prices in the viewer's local currency. Daily closes, cached for
    an hour — not dealable prices, and they carry no spread."""
    from src.data.currency import SUPPORTED, rates
    payload = dict(rates(base))
    payload["supported"] = SUPPORTED
    return _sanitize(payload)


@app.get("/api/universe/currencies", tags=["Universe"])
def universe_currencies(symbols: str):
    """Native quote currency for each symbol — what a price is actually
    denominated in before any conversion.

    Returns "GBp" for London equities, which quote in PENCE, not pounds.
    Callers must divide by 100 before treating it as GBP; the frontend's
    currency helper does this for you. A null means the currency could not be
    established, and such a value must be displayed unconverted."""
    from src.data.currency import currencies_for
    syms = [s.strip() for s in (symbols or "").split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="no symbols supplied")
    return _sanitize({"count": len(syms), "currencies": currencies_for(syms[:400])})


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


@app.post("/api/quant/run-now", tags=["Quant Lab"], dependencies=[Depends(require_owner)])
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


@app.post("/api/inference/discover", tags=["Inference"], dependencies=[Depends(require_owner)])
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


@app.get("/api/desk/status", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_status():
    """Desk state: daemon, safety gates, auto_execute flag, day budget,
    account=paper|live|disconnected."""
    from src.desk.desk_daemon import get_desk
    return _sanitize(get_desk().status())


@app.post("/api/desk/auto-execute", tags=["Desk"], dependencies=[Depends(require_owner)])
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
        # Arm only on a POSITIVELY confirmed paper account. `paper is False`
        # alone let it arm while the broker was disconnected or unreadable —
        # "not known to be live" is not the same as "known to be paper".
        if account.get("paper") is False or account.get("paper_confirmed") is not True:
            raise HTTPException(
                status_code=409,
                detail="LIVE or unconfirmed account — auto-execute is paper-only and "
                       "will not arm. Real money always goes through human approval. "
                       f"Gate: {account.get('paper_status')}")
    cfg = save_config({"auto_execute": bool(enabled)})
    return {"ok": True, "auto_execute": cfg["auto_execute"]}


@app.post("/api/desk/run-now", tags=["Desk"], dependencies=[Depends(require_owner)])
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


@app.patch("/api/desk/config", tags=["Desk"], dependencies=[Depends(require_owner)])
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


@app.get("/api/desk/slate", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_slate():
    """Latest ranked trade slate + debate summaries."""
    return _sanitize(_load_optional("desk/slate.json") or
                     {"slate": [], "rejected": [], "debates": []})


@app.get("/api/desk/debate/{debate_id}", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_debate(debate_id: str):
    """Full debate transcript — every argument, evidence citation, and the verdict."""
    from src.desk.debate import load_debate
    t = load_debate(debate_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Debate '{debate_id}' not found")
    return _sanitize(t)


@app.get("/api/desk/debates", tags=["Desk"], dependencies=[Depends(require_owner)])
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


@app.get("/api/desk/executions", tags=["Desk"], dependencies=[Depends(require_owner)])
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


@app.get("/api/desk/health", tags=["Desk"], dependencies=[Depends(require_owner)])
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


@app.post("/api/desk/tick-now", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_tick_now():
    """Run one PositionManager management tick immediately (exit rules,
    trailing stops, bracket healing, legacy triage)."""
    from src.desk.desk_daemon import get_desk
    get_desk().run_tick_now()
    return {"status": "started",
            "message": "Management tick running. Poll /api/desk/status."}


@app.get("/api/desk/lessons", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_lessons(n: int = 50):
    """Fable's teaching lessons from reviewing closed trades."""
    from src.desk.teacher import read_lessons
    return _sanitize({"lessons": list(reversed(read_lessons(n)))})


@app.post("/api/desk/teach-now", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_teach_now():
    """Have Fable review any un-taught closed trades right now (opt-in;
    needs teacher_enabled + ANTHROPIC_API_KEY, budget-capped)."""
    from src.desk.teacher import teach_from_closed_trades
    return _sanitize(teach_from_closed_trades())


@app.get("/api/desk/playbooks", tags=["Desk"], dependencies=[Depends(require_owner)])
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


@app.post("/api/desk/reflex-scan", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_reflex_scan():
    """Run one reflex scan pass immediately (for testing the fast lane)."""
    from src.desk.reflex import get_reflex
    return _sanitize({"results": get_reflex().scan_once()})


@app.get("/api/desk/positions", tags=["Desk"], dependencies=[Depends(require_owner)])
def desk_positions():
    """Tracked positions (exit-engine state) + latest closed trades."""
    from src.desk.position_manager import PositionManager, read_closed_trades
    return _sanitize({
        "positions": PositionManager().load_positions(),
        "closed": list(reversed(read_closed_trades(50))),
    })


@app.get("/api/desk/performance", tags=["Desk"], dependencies=[Depends(require_owner)])
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


@app.get("/api/desk/pnl", tags=["Desk"], dependencies=[Depends(require_owner)])
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
# ARIA V5 — multi-strategy research engine, ensemble, risk gate, meta-reasoning
# Heavy imports stay inside the handlers (src.v5 pulls sklearn/scipy).
# Nothing here can place an order; the desk remains the only execution path.
# ===========================================================================

@app.get("/api/v5/modules", tags=["V5"])
def v5_modules(family: Optional[str] = None):
    """Every registered research module, individually callable."""
    from src.v5 import registry
    from src.v5.ensemble import FAMILY_WEIGHTS
    mods = registry.list_modules(family)
    return _sanitize({
        "count": len(mods),
        "families": registry.FAMILIES,
        "family_weights": FAMILY_WEIGHTS,
        "modules": mods,
    })


@app.get("/api/v5/module/{name}/{ticker}", tags=["V5"])
def v5_module(name: str, ticker: str):
    """Run ONE research module. Abstentions are a valid, successful response."""
    from src.v5 import registry
    return _sanitize(registry.run(name, ticker.upper()).to_dict())


@app.get("/api/v5/analyze/{ticker}", tags=["V5"])
def v5_analyze(ticker: str, log: bool = True, families: Optional[str] = None):
    """Full V5 chain: modules → ensemble → meta → risk gate → self-audit."""
    from src.v5 import pipeline
    fam = [f.strip() for f in families.split(",")] if families else None
    result = pipeline.analyze(ticker.upper(), families=fam, log=log)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return _sanitize(result)


@app.get("/api/v5/report/{ticker}", tags=["V5"])
def v5_report(ticker: str, log: bool = False, format: str = "json"):
    """The investment-committee memo. `format=md` returns the raw markdown as
    plain text so the link is readable in a browser tab."""
    from fastapi.responses import PlainTextResponse
    from src.v5 import pipeline, report
    result = pipeline.analyze(ticker.upper(), log=log)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    markdown = report.render(result)
    if format == "md":
        return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")
    return {"ticker": ticker.upper(), "markdown": markdown, "as_of": result["as_of"]}


@app.get("/api/v5/screen", tags=["V5"])
def v5_screen(tickers: str, limit: int = 10):
    """Rank several names by risk-adjusted conviction. Screens are not logged."""
    from src.v5 import pipeline
    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="no tickers supplied")
    return _sanitize(pipeline.screen(syms, limit=limit))


@app.get("/api/v5/learning", tags=["V5"])
def v5_learning():
    """Prediction log, calibration, attribution counts and module weights."""
    from src.v5 import learning
    return _sanitize(learning.performance_summary())


@app.post("/api/v5/learning/resolve", tags=["V5"], dependencies=[Depends(require_owner)])
def v5_learning_resolve():
    """Score every prediction whose horizon has elapsed, then reweight if — and
    only if — the evidence is statistically significant."""
    from src.v5 import learning
    return _sanitize(learning.resolve_pending())


@app.post("/api/v5/learning/revert/{version}", tags=["V5"], dependencies=[Depends(require_owner)])
def v5_learning_revert(version: int):
    """Restore module weights as they were at `version`. Learning must be
    reversible or it cannot be audited."""
    from src.v5 import learning
    result = learning.revert_to(version)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return _sanitize(result)


@app.get("/api/v5/track-record", tags=["V5"])
def v5_track_record():
    """THE FLYWHEEL — every labelled outcome ARIA has produced, in one payload:
    the four source loops, the calibration of its stated confidence, per-module
    skill, outcome attribution, and the weight-version history."""
    from src.v5 import track_record
    return _sanitize(track_record.build())


@app.get("/api/v5/track-record/export", tags=["V5"])
def v5_track_record_export(format: str = "json"):
    """Resolved predictions as supervised training rows — the module vector in,
    the realised outcome out. Only resolved predictions are exported; an
    unresolved one has no label."""
    from fastapi.responses import PlainTextResponse
    from src.v5 import track_record
    if format == "jsonl":
        return PlainTextResponse(track_record.training_export_jsonl(),
                                 media_type="application/x-ndjson")
    rows = track_record.training_export()
    return _sanitize({"count": len(rows), "rows": rows})


@app.get("/api/v5/walk-forward", tags=["V5"])
def v5_walk_forward(module: Optional[str] = None):
    """Per-module walk-forward validation: hit rate against the base rate, the
    information coefficient, and — for every module that has never been through
    it — that fact, stated.

    Regenerated by `python scripts/run_walkforward.py`, which is a batch job:
    this endpoint reads the stored record and never runs the replay."""
    from src.v5 import walkforward
    if module:
        return _sanitize({"module": module, **walkforward.status(module)})
    return _sanitize(walkforward.summary())


@app.get("/api/v5/loop-health", tags=["V5"])
def v5_loop_health():
    """Is the research flywheel actually turning? Reports when each leg last
    ran, from the heartbeat on disk — not from whether an object exists in
    memory, which is true even when the loop has silently stopped."""
    from src.v5 import loop
    return _sanitize(loop.health())


@app.post("/api/v5/loop/run", tags=["V5"],
          dependencies=[Depends(require_owner)])
def v5_loop_run(leg: str = "both"):
    """Run the research loop now: predict, resolve, or both.

    Owner-only despite living under the free /api/v5 prefix — it analyses a
    whole watchlist, which is minutes of CPU, and it writes to the prediction
    log that the track record is computed from."""
    from src.v5 import loop
    if leg == "predict":
        return _sanitize({"predict": loop.predict_once()})
    if leg == "resolve":
        return _sanitize({"resolve": loop.resolve_once()})
    return _sanitize(loop.run_once())


@app.get("/api/v5/weight-fit", tags=["V5"])
def v5_weight_fit():
    """Out-of-sample proposal for the ensemble's family weights, or an honest
    statement of how far short of the required sample we still are.

    Applies nothing — adoption is a human decision, made through the versioned
    weights mechanism in src/v5/learning.py."""
    from src.v5 import weightfit
    return _sanitize(weightfit.propose())


@app.get("/api/v5/data-health", tags=["V5"])
def v5_data_health(ticker: Optional[str] = None):
    """Which market-data vendors are configured and reachable, and — if a
    ticker is given — exactly where that instrument's prices came from.

    Exists so "the numbers look odd today" has an answer that is not "read the
    server logs": a degraded feed is visible from the API itself."""
    from src.v5 import marketdata as md, vendors
    out = {"as_of": datetime.now().isoformat(timespec="seconds"),
           **vendors.health()}
    if ticker:
        t = ticker.strip().upper()
        md.closes(t, period="1y")          # load so provenance exists to report
        out["ticker"] = t
        out["provenance"] = md.provenance(t)
        out["citation"] = md.source_label(t)
        out["stale"] = md.is_stale(t)
    return _sanitize(out)


@app.get("/api/v5/baselines", tags=["V5"])
def v5_baselines():
    """THE HARDER QUESTION — would a rule you could write on a napkin have done
    the same thing? Re-runs buy-and-hold, an SMA cross and 60-day momentum over
    exactly the calls ARIA made, and compares on the paired sample with
    McNemar's test. `measurable: false` until enough calls have resolved."""
    from src.v5 import baselines
    return _sanitize(baselines.report())


@app.get("/api/v5/tiers", tags=["V5"])
def v5_tiers():
    """Which of the 41 research engines have EARNED a vote. Tiers are computed
    from each module's own resolved calls — nothing here is hand-set — and are
    reported both raw and after correcting for having run 41 simultaneous
    tests, because roughly two modules clear p<0.05 by chance alone."""
    from src.v5 import tiers
    return _sanitize(tiers.report())


@app.get("/api/v5/identity", tags=["V5"])
def v5_identity():
    """The system prompt ARIA runs on, verbatim — including the Two Laws."""
    from src.v5.identity import CREATOR, system_prompt
    return {"creator": CREATOR, "full": system_prompt(), "short": system_prompt(full=False)}


@app.get("/api/aria/voice", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_voice_status():
    """Her voice: Kokoro-82M, local, and which voices are available."""
    from src.brain.cognitive import voice
    return _sanitize(voice.status())


@app.post("/api/aria/speak", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_speak(body: dict):
    """Say something. Body: `{"text": "...", "voice": "af_heart", "speed": 1.0}`

    Returns the path to a wav and the text she ACTUALLY spoke, which is not
    the text you sent: markup, tables and URLs are stripped and numbers are
    normalised, because a voice that reads "asterisk asterisk NVDA asterisk
    asterisk" is a screen reader, not a voice."""
    from src.brain.cognitive import voice

    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="nothing to say")
    result = voice.say(text, voice=str(body.get("voice") or voice.DEFAULT_VOICE),
                       speed=float(body.get("speed") or voice.DEFAULT_SPEED))
    if not result.ok:
        raise HTTPException(status_code=503, detail=result.error)
    return _sanitize(result.to_dict())


@app.get("/api/aria/audio/{name}", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_audio(name: str):
    """Serve one clip she generated, so the browser can play it.

    `/api/aria/speak` returns a filename, not audio: an `<audio>` element then
    streams it from here, which lets the browser buffer and seek instead of
    holding a megabyte of base64 in a JSON payload.

    The name is resolved INSIDE data/audio and verified to still be there
    afterwards. A path parameter that reaches the filesystem is the classic
    traversal hole, and `../../../.env` is a short walk from here to the
    owner's API keys."""
    from src.brain.cognitive.voice import AUDIO_DIR

    candidate = (AUDIO_DIR / name).resolve()
    root = AUDIO_DIR.resolve()
    if not (candidate == root or root in candidate.parents):
        raise HTTPException(status_code=400, detail="that is not an audio clip")
    if candidate.suffix.lower() != ".wav" or not candidate.is_file():
        raise HTTPException(status_code=404, detail="no such clip")
    return FileResponse(str(candidate), media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/aria/hearing", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_hearing_status():
    """Her ears, and how much of his own speech she has accumulated."""
    from src.brain.cognitive import hearing
    return _sanitize(hearing.status())


@app.post("/api/aria/listen", tags=["ARIA"], dependencies=[Depends(require_owner)])
async def aria_listen(file: UploadFile = File(...), remember: bool = True,
                      context: str = ""):
    """Transcribe one clip of speech — press-to-talk, not ambient.

    `remember=true` keeps the text in the spoken corpus so she learns how he
    talks: slang, shorthand, phrasing the vault does not contain. Pass
    `remember=false` to transcribe and forget.

    The transcript is research text. Ticker symbols heard at this model size
    are unreliable — "NVDA" comes back as "in video" often enough that
    resolving symbols from a transcript would be a hazard. Nothing here
    reaches an order."""
    import tempfile
    from src.brain.cognitive import hearing

    suffix = Path(file.filename or "clip.wav").suffix or ".wav"
    tmp = Path(tempfile.gettempdir()) / f"aria-listen-{os.getpid()}{suffix}"
    try:
        tmp.write_bytes(await file.read())
        result = hearing.listen(tmp)
        if not result.ok:
            raise HTTPException(status_code=422, detail=result.error)
        kept = hearing.remember_speech(result, context=context, consent=remember)
        return _sanitize({**result.to_dict(), "remembered": kept})
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/api/aria/novelty", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_novelty(record: bool = False):
    """Is today like the days she has seen before?

    Not a forecast and not a signal — a strange day can resolve into nothing.
    It exists to make her look, and to say out loud that she is looking. Below
    thirty remembered days it reports `not_enough_history` rather than calling
    everything unprecedented, which is the same as saying nothing.

    `record=false` by default: reading this endpoint should not add an
    observation to her memory of what is usual. The brain cycle is what
    remembers."""
    from src.brain.cognitive import novelty
    result = novelty.observe(record=record)
    return _sanitize({**result.to_dict(), "history": novelty.status()})


@app.get("/api/aria/sight", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_sight_status():
    """Can she look at images, and with which model."""
    from src.brain.cognitive import sight
    return _sanitize(sight.status())


@app.post("/api/aria/look", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_look(body: dict):
    """Look at an image — a chart, a screenshot, a scanned filing page.

    Body: `{"path": "..."} ` or `{"image_base64": "..."}`, plus an optional
    `question`.

    What comes back is a SIGHTING, and it is labelled as one. Measured on this
    machine, the vision model read a chart's moving average as its price line
    and its y-axis floor as 50 when it was 82 — in the same confident register
    as everything it got right. Use this for what a picture shows that the
    feed does not; never to read a number she can fetch."""
    from src.brain.cognitive import sight

    question = str(body.get("question") or "")
    if body.get("image_base64"):
        import base64 as _b64
        try:
            raw = _b64.b64decode(str(body["image_base64"]), validate=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"bad base64: {e}")
        result = sight.look(raw, question)
    elif body.get("path"):
        result = sight.look(str(body["path"]), question)
    else:
        raise HTTPException(status_code=400,
                            detail="give her something to look at: 'path' or "
                                   "'image_base64'")
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.error)
    return _sanitize(result.to_dict())


@app.get("/api/aria/state", tags=["ARIA"], dependencies=[Depends(require_owner)])
def aria_self_state():
    """Whether her own parts are working: reasoning model reachable, market
    state fresh, research loop turning, how many calls have resolved.

    The failure this exists to catch is the quiet one — everything answers
    normally while nothing accumulates. Every check degrades to "unknown"
    rather than to "fine", because a health report that says OK when it could
    not run turns an outage into a false assurance.

    `speak` is empty when nothing is wrong; when something is, it is one
    sentence she can volunteer before answering."""
    from src.brain import self_state
    return _sanitize(self_state.report())


# ===========================================================================
# RESEARCH REACH — read the open internet, and keep the receipts.
#
# Everything except /status carries require_owner. Not because the data is
# secret, but because these routes make the server fetch a URL the caller
# chose: unguarded, that is an SSRF gadget and a way to spend ARIA's rate
# limits on someone else's crawl. src/research/url.py rejects private and
# loopback targets; the guard is the second lock, not the first.
# ===========================================================================

@app.get("/api/research/status", tags=["Research"])
def research_status():
    """Which sources can actually serve a request right now, and why not.

    A source that needs a binary (yt-dlp) or a credential (X, Meta) reports
    `available: false` with the specific fix, rather than failing at fetch
    time. Nine of the eleven need neither."""
    from src.research import status
    return _sanitize({"as_of": datetime.now().isoformat(timespec="seconds"),
                      "sources": status()})


@app.get("/api/research/read", tags=["Research"], dependencies=[Depends(require_owner)])
def research_read(url: str, store: bool = True):
    """Read one URL through whichever source claims it, and archive it.

    The response carries `content_id` — the SHA-256 of exactly the bytes
    returned. A later report citing this retrieval can be checked against
    the archived copy even after the page changes underneath it."""
    from src.research import read
    from src.research.base import SourceError
    try:
        doc = read(url, store=store)
    except ValueError as e:          # rejected by the URL guard
        raise HTTPException(status_code=400, detail=str(e))
    except SourceError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _sanitize({
        "url": doc.url, "title": doc.title, "source": doc.source,
        "backend": doc.backend, "fetched_at": doc.fetched_at,
        "chars": len(doc.text), "text": doc.text, "meta": doc.meta,
    })


@app.get("/api/research/feed", tags=["Research"], dependencies=[Depends(require_owner)])
def research_feed(url: str, limit: int = 50, store: bool = True):
    """Expand an RSS/Atom feed into its entries without fetching each page.

    Entries pointing somewhere non-public are dropped rather than fetched —
    a feed is a list of links written by someone else."""
    from src.research import read_feed
    from src.research.base import SourceError
    try:
        docs = read_feed(url, limit=limit, store=store)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SourceError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _sanitize({"feed": url, "count": len(docs), "entries": [
        {"url": d.url, "title": d.title, "excerpt": d.text[:400], "meta": d.meta}
        for d in docs
    ]})


@app.post("/api/research/hunt", tags=["Research"], dependencies=[Depends(require_owner)])
def research_hunt(query: str, ticker: Optional[str] = None,
                  limit_per_source: int = 15, store: bool = True):
    """Sweep every available source for one query, in parallel.

    POST because it is expensive and it writes: a sweep spends rate limit on
    nine services and archives everything it finds.

    The response separates `ran`, `failed` and `skipped` per source on
    purpose. A sweep where Reddit was throttled and returned nothing looks
    identical to a sweep where Reddit had nothing to say, unless the API
    says which happened — and those two facts mean opposite things about a
    quiet ticker."""
    from src.research import hunt
    sweep = hunt(query, ticker=ticker, limit_per_source=limit_per_source, store=store)
    return _sanitize(sweep.as_dict())


@app.get("/api/research/records", tags=["Research"], dependencies=[Depends(require_owner)])
def research_records(day: Optional[str] = None, limit: int = 200):
    """The provenance log for one UTC day (YYYY-MM-DD, default today).

    One record per retrieval, with a ready-made citation line. Repeated
    retrievals of unchanged content appear repeatedly and share a
    content_id — that is how a source going stale becomes visible."""
    from src.research.store import citation, records
    rows = records(day)[-limit:]
    return _sanitize({"day": day or "today", "count": len(rows),
                      "records": [{**r, "citation": citation(r)} for r in rows]})


@app.get("/api/research/doc/{content_id}", tags=["Research"],
         dependencies=[Depends(require_owner)])
def research_doc(content_id: str):
    """The archived bytes behind a citation, by content id."""
    from src.research.store import load
    if not content_id.isalnum() or len(content_id) != 64:
        raise HTTPException(status_code=400, detail="content_id must be a sha256 hex digest")
    text = load(content_id)
    if text is None:
        raise HTTPException(status_code=404, detail=f"no archived document {content_id}")
    return {"content_id": content_id, "chars": len(text), "text": text}


# ===========================================================================
# THE BREEDING LAB — thousands of strategies, and a bar that knows it.
#
# Evolutionary search over quant_lab's templates, where survivors must beat
# the Sharpe the luckiest of N worthless strategies would have posted. Most
# campaigns return nothing, and that is reported as success: see
# src/evolution/statistics.py for why a single out-of-sample test cannot
# clear a strategy that a search chose.
# ===========================================================================

@app.get("/api/evolution/space", tags=["Evolution"])
def evolution_space():
    """What the search is allowed to explore, and the bar survivors must clear."""
    from src.evolution import SPACE, SURVIVAL_THRESHOLD
    return _sanitize({
        "templates": {name: {p: {"low": lo, "high": hi, "kind": kind}
                             for p, (lo, hi, kind) in params.items()}
                      for name, params in SPACE.items()},
        "survival_threshold": SURVIVAL_THRESHOLD,
        "rule": "a survivor must beat the Sharpe the luckiest of N worthless "
                "strategies would post, where N is every strategy this "
                "campaign evaluated",
    })


@app.post("/api/evolution/run", tags=["Evolution"], dependencies=[Depends(require_owner)])
def evolution_run(population: int = 120, generations: int = 12,
                  seed: Optional[int] = None, universe: Optional[str] = None):
    """Run one breeding campaign. Minutes, not seconds.

    Returns every finalist with its in-sample fitness, its holdout Sharpe and
    the deflated probability — including the ones that failed, because the
    gap between a 2.70 in-sample and a 0.67 holdout is the most useful thing
    the lab produces."""
    if population < 10 or population > 2000:
        raise HTTPException(status_code=400, detail="population must be 10..2000")
    if generations < 1 or generations > 100:
        raise HTTPException(status_code=400, detail="generations must be 1..100")
    from src.evolution import run_campaign
    return _sanitize(run_campaign(population=population, generations=generations,
                                  seed=seed, universe=universe))


@app.get("/api/evolution/campaigns", tags=["Evolution"],
         dependencies=[Depends(require_owner)])
def evolution_campaigns(limit: int = 20):
    """Past campaigns, newest first."""
    from src.evolution import history
    return _sanitize({"campaigns": history(limit=limit)})


# ===========================================================================
# THE EYE — standing attention on the internet.
#
# /read and /hunt are a hand: they fetch what they are told to. These routes
# drive the eye, which looks on its own cadence and reports only what changed.
# It observes and hands what it saw to the brain; it does not propose, size or
# execute anything.
# ===========================================================================

@app.get("/api/research/eye", tags=["Research"], dependencies=[Depends(require_owner)])
def research_eye(hours: int = 24, limit: int = 50, min_salience: float = 0.0):
    """What the eye has noticed lately, most salient first.

    An empty list is a real answer: nothing new crossed the threshold. A
    perception layer that always has something to say is filling airtime."""
    from dataclasses import asdict

    from src.research import eye
    return _sanitize({
        "hours": hours,
        "watches": [asdict(w) for w in eye.watches()],
        "observations": eye.recent(hours=hours, limit=limit,
                                   min_salience=min_salience),
        "briefing": eye.briefing(hours=min(hours, 12)),
    })


@app.post("/api/research/eye/blink", tags=["Research"],
          dependencies=[Depends(require_owner)])
def research_eye_blink(force: bool = False, max_observations: int = 12):
    """Look now at every watch that is due. `force=true` ignores cadence.

    A watch's first look reports nothing and records a baseline instead —
    everything is new the first time you open your eyes, and reporting that
    is how a perception system loses its reader on day one."""
    from src.research import eye
    return _sanitize(eye.blink(force=force, max_observations=max_observations))


@app.post("/api/research/eye/watch", tags=["Research"],
          dependencies=[Depends(require_owner)])
def research_eye_watch(query: str, kind: str = "topic", cadence_minutes: int = 60):
    """Add or update a standing watch (kind: ticker | topic | feed).

    Re-adding an existing watch keeps its baseline, so it will not re-report
    what it has already shown you."""
    from dataclasses import asdict

    from src.research import eye
    if kind not in ("ticker", "topic", "feed"):
        raise HTTPException(status_code=400, detail="kind must be ticker, topic or feed")
    return _sanitize(asdict(eye.watch(query, kind=kind,
                                           cadence_minutes=cadence_minutes)))


@app.delete("/api/research/eye/watch/{watch_id:path}", tags=["Research"],
            dependencies=[Depends(require_owner)])
def research_eye_unwatch(watch_id: str):
    """Stop watching, and forget the baseline with it."""
    from src.research import eye
    if not eye.unwatch(watch_id):
        raise HTTPException(status_code=404, detail=f"no watch {watch_id}")
    return {"removed": watch_id}


@app.post("/api/research/eye/attend-portfolio", tags=["Research"],
          dependencies=[Depends(require_owner)])
def research_eye_attend_portfolio():
    """Point the eye at every open position.

    Attention should follow exposure without anyone remembering to update a
    list — the position you forgot to watch is the one that gaps."""
    from dataclasses import asdict

    from src.research import eye
    added = eye.attend_to_portfolio()
    return _sanitize({"watching": [asdict(w) for w in added]})


# ===========================================================================
# The guard on the guard.
#
# Every route that can reach a broker carries Depends(require_owner). A
# dependency you have to remember to add is a convention, and conventions decay
# — so the app refuses to start if one is missing. Adding an execution endpoint
# without a guard is now a crash on import, not a quiet hole in production.
# ===========================================================================

def _is_guarded(route) -> bool:
    names = {getattr(getattr(d, "call", None), "__name__", "")
             for d in getattr(getattr(route, "dependant", None), "dependencies", [])}
    return "require_owner" in names


def _assert_execution_routes_guarded() -> list[str]:
    """Raise if anything that can change state is reachable without the guard.

    The first version of this check only inspected routes under
    /api/execute and /api/desk — which is the same path-prefix reasoning the
    guard exists to escape. An independent review pointed out the obvious
    consequence: a write endpoint added under /api/v5 or /api/universe was
    invisible to it, and four such endpoints were reachable by any signed-in
    free user, including one that moves the ensemble weights the desk sizes
    trades from.

    So the rule is now about METHOD, not path. Every POST/PATCH/PUT/DELETE in
    the application must either carry Depends(require_owner) or be named in
    policy.PUBLIC_WRITE_ROUTES as a deliberate exception. Adding a write
    endpoint without deciding which it is stops the app from starting.
    """
    import inspect
    from src.auth import policy

    # Names that mean "this handler can reach a broker". A GET is not covered
    # by the method rule, and a read-only broker endpoint added under a free
    # prefix (/api/v5/my-positions) would be caught by neither the method rule
    # nor the path rule — so the handler's own source is the third signal.
    BROKER_TOUCHING = ("_get_order_manager", "OrderManager", "AlpacaBroker",
                       "IBKRBroker", "account_snapshot", "get_order_manager")

    def _touches_a_broker(route) -> bool:
        fn = getattr(route, "endpoint", None)
        if fn is None:
            return False
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            return False
        return any(token in src for token in BROKER_TOUCHING)

    guarded, unguarded = [], []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", set()) or set())
        mutating = bool(methods & {"POST", "PATCH", "PUT", "DELETE"})
        if not (mutating or policy.is_execution_path(path)
                or _touches_a_broker(route)):
            continue
        if _is_guarded(route):
            guarded.append(path)
        elif path in policy.PUBLIC_WRITE_ROUTES:
            continue                        # declared public, on purpose
        else:
            unguarded.append(f"{'/'.join(sorted(methods & {'POST','PATCH','PUT','DELETE'})) or 'GET'} {path}")
    if unguarded:
        raise RuntimeError(
            "State-changing routes without Depends(require_owner): "
            + ", ".join(sorted(set(unguarded)))
            + " — every route that can change state must either declare the "
              "guard or be listed in policy.PUBLIC_WRITE_ROUTES as a "
              "deliberate exception. See src/auth/guard.py.")
    return sorted(set(guarded))


_GUARDED_EXECUTION_ROUTES = _assert_execution_routes_guarded()
logger.info("guarded state-changing routes: %d", len(_GUARDED_EXECUTION_ROUTES))


@app.on_event("startup")
async def _warn_about_auth_configuration():
    from src.auth import policy
    policy.warn_if_unprotected()


@app.on_event("startup")
async def _recheck_route_guards():
    """Run the same check again once every route is registered.

    The import-time call above executes at whatever line it sits on, so a route
    decorated below it is never examined — and the bottom of the file is
    exactly where new routes get appended. Re-checking at startup closes that.
    """
    _assert_execution_routes_guarded()


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
