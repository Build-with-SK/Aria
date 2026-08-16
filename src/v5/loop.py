"""
src/v5/loop.py
==============
The loop that turns opinions into a track record, unattended.

WHY THIS FILE HAD TO EXIST
--------------------------
The pieces of the flywheel were all present and none of them turned. V5 logs a
prediction whenever somebody opens an analysis; `learning.resolve_pending()`
scores predictions whose horizon has elapsed. But nothing called
`resolve_pending` except a manual POST endpoint, and nothing generated
predictions except a human opening a page.

So the honest description of the track record before this was not "too early to
tell". It was: predictions accumulate only when someone browses, and they are
never scored unless someone remembers to press a button. Waiting longer would
not have produced a track record, because nothing was running.

This is the missing loop:

  * PREDICT — analyse a fixed watchlist on a schedule, which logs a prediction
    per name (deduplicated per ticker per day by learning.log_prediction).
  * RESOLVE — score every prediction whose horizon has elapsed, which is what
    moves the counter towards the 20 resolved calls the track record needs
    before it will report anything.
  * HEARTBEAT — write when each leg last ran, so "is it actually running" is a
    question with an answer rather than a belief.

The watchlist is fixed and boring on purpose. A track record built from names
chosen after the fact is not a track record; picking the universe once, in
advance, in a file, is what makes the resulting hit rate mean something.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
STATE_DIR = ROOT / "data" / "v5"
HEARTBEAT = STATE_DIR / "loop_heartbeat.json"
WATCHLIST_FILE = STATE_DIR / "watchlist.json"

# Committed in advance, and deliberately unglamorous: a spread of sectors and
# two index proxies. Changing it is allowed; changing it *because of results*
# is how a track record becomes marketing, so every edit is timestamped in the
# heartbeat and the file lives in git.
# Thirty names, not ten, and the reason is arithmetic rather than ambition.
#
# Only DIRECTIONAL calls count toward calibration — `track_record.calibration`
# filters on direction in (bull, bear), because a neutral call states no
# probability to grade. The first live run of this loop produced ten
# predictions of which four were directional; at that rate the twentieth
# directional call arrives on day five and resolves twenty-nine days after
# that. Three times the names is three times the rate, and it costs nothing
# but CPU on a machine that is idle overnight.
#
# Fixed in advance and written to data/v5/watchlist.json with a date. That
# matters more than the contents: a universe edited after seeing results is
# not a track record, it is a highlight reel. Spread across sectors so the
# eventual number is not a bet on one of them.
DEFAULT_WATCHLIST = [
    # mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    # financials
    "JPM", "BAC", "GS",
    # energy / materials
    "XOM", "CVX",
    # healthcare
    "JNJ", "PFE", "MRK",
    # staples / retail
    "WMT", "COST", "PG", "KO",
    # industrials
    "CAT", "BA", "GE", "HON",
    # communications / consumer discretionary
    "DIS", "NKE", "SBUX",
    # older tech, lower beta
    "INTC", "CSCO", "IBM",
    # index proxies
    "SPY", "QQQ",
]

PREDICT_HOUR = 22        # after the US close, local time
RESOLVE_INTERVAL_HOURS = 6


def watchlist() -> list[str]:
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            names = [str(t).upper() for t in (data.get("tickers") or []) if t]
            if names:
                return names
        except Exception as e:
            logger.warning("watchlist unreadable, using the default: %s", e)
    return list(DEFAULT_WATCHLIST)


def _beat(leg: str, payload: dict) -> None:
    """Record that a leg ran. Failures here must never stop the leg itself."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state = {}
        if HEARTBEAT.exists():
            try:
                state = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state[leg] = {"at": datetime.now().isoformat(timespec="seconds"), **payload}
        HEARTBEAT.write_text(json.dumps(state, indent=2, default=str),
                             encoding="utf-8")
    except Exception as e:
        logger.warning("could not write loop heartbeat: %s", e)


# ── the two legs ─────────────────────────────────────────────────────────────

def predict_once(tickers: list[str] | None = None) -> dict:
    """Analyse the watchlist. Each analysis logs one prediction.

    One name failing must not stop the rest — a delisted ticker or a vendor
    hiccup should cost one row, not the day's whole sample.
    """
    from src.v5 import pipeline

    names = tickers or watchlist()
    logged, skipped, errors = [], [], []
    for t in names:
        try:
            res = pipeline.analyze(t, log=True)
            if res.get("error"):
                skipped.append({"ticker": t, "why": res["error"][:120]})
                continue
            logged.append({"ticker": t,
                           "prediction_id": res.get("prediction_id"),
                           "direction": res["ensemble"]["direction"],
                           "confidence": res["recommendation"]["confidence"],
                           "data_stale": res.get("data", {}).get("stale")})
        except Exception as e:
            logger.exception("research loop failed on %s", t)
            errors.append({"ticker": t, "error": str(e)[:200]})

    summary = {"tickers": len(names), "logged": len(logged),
               "skipped": len(skipped), "errors": len(errors),
               "detail": logged, "skipped_detail": skipped,
               "error_detail": errors}
    _beat("predict", {k: summary[k] for k in
                      ("tickers", "logged", "skipped", "errors")})
    logger.info("research loop: %d/%d predictions logged (%d skipped, %d errors)",
                len(logged), len(names), len(skipped), len(errors))
    return summary


def resolve_once() -> dict:
    """Score everything whose horizon has elapsed. This is the leg that moves
    the resolved-call counter, and it is the one that was never running."""
    from src.v5 import learning
    try:
        out = learning.resolve_pending()
    except Exception as e:
        logger.exception("resolve leg failed")
        _beat("resolve", {"error": str(e)[:200]})
        return {"error": str(e)}
    _beat("resolve", {"checked": out.get("checked"), "resolved": out.get("resolved")})
    if out.get("resolved"):
        logger.info("research loop: %d prediction(s) resolved", out["resolved"])
    return out


def predict_last_run_date() -> str:
    """The date the predict leg last ran, from the heartbeat file."""
    if not HEARTBEAT.exists():
        return ""
    try:
        state = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str((state.get("predict") or {}).get("at", ""))[:10]


def predict_catchup(now: datetime | None = None) -> dict:
    """Make today's prediction if the scheduled slot was missed.

    THE BUG THIS FIXES
    ------------------
    `resolve` is registered with `next_run_time=now`, so it runs the moment the
    backend wakes. `predict` is a plain daily cron at PREDICT_HOUR:10. A
    machine that is not awake at 22:10 therefore skips that day's predictions
    entirely and nothing anywhere says so — the resolve leg keeps running
    happily over an input that stopped arriving. That asymmetry is why 16
    predictions exist in total and none have resolved: the loop has been
    turning with nothing going into it.

    THIS IS NOT A BACKFILL (invariant 5)
    ------------------------------------
    A backfill invents calls that were never made. This makes TODAY's call,
    late, with today's data and today's timestamp — the same thing the cron
    would have done a few hours earlier.

    And it will not run before PREDICT_HOUR. The slot is after the US close on
    purpose: a call made at 11am against an unsettled session is a different
    measurement from one made at 22:10, and quietly mixing the two would put a
    methodological seam through the middle of the sample that nobody would
    find later. Before the hour, this does nothing and lets the cron fire.
    """
    now = now or datetime.now()
    today = now.date().isoformat()

    if predict_last_run_date() == today:
        return {"ran": False, "why": "predict has already run today"}
    if now.hour < PREDICT_HOUR:
        return {"ran": False,
                "why": f"before the {PREDICT_HOUR}:10 slot — the cron will fire "
                       f"today; a pre-close call is not the same measurement"}

    logger.warning("research loop: the %d:10 predict slot was missed (last run "
                   "%s) — making today's call now", PREDICT_HOUR,
                   predict_last_run_date() or "never")
    summary = predict_once()
    summary["catchup"] = True
    return {"ran": True, **summary}


def run_once() -> dict:
    """Both legs, for a manual trigger or a smoke test."""
    return {"predict": predict_once(), "resolve": resolve_once()}


# ── health ───────────────────────────────────────────────────────────────────

def health() -> dict:
    """Is the flywheel actually turning? Answered from the heartbeat file, not
    from whether an object exists in memory."""
    state = {}
    if HEARTBEAT.exists():
        try:
            state = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    def age_h(leg):
        at = (state.get(leg) or {}).get("at")
        if not at:
            return None
        try:
            return round((datetime.now() - datetime.fromisoformat(at))
                         .total_seconds() / 3600, 2)
        except Exception:
            return None

    predict_age, resolve_age = age_h("predict"), age_h("resolve")
    # A day and a half of grace on the daily leg: one missed run is a restart,
    # two is a fault.
    predict_ok = predict_age is not None and predict_age < 36
    resolve_ok = resolve_age is not None and resolve_age < RESOLVE_INTERVAL_HOURS * 3

    from src.v5 import learning
    preds = learning.load_predictions()
    resolved = [p for p in preds if p.get("resolved")]

    return {
        "healthy": bool(predict_ok and resolve_ok),
        "predict": {"last_run": (state.get("predict") or {}).get("at"),
                    "hours_ago": predict_age, "ok": predict_ok,
                    **{k: v for k, v in (state.get("predict") or {}).items()
                       if k != "at"}},
        "resolve": {"last_run": (state.get("resolve") or {}).get("at"),
                    "hours_ago": resolve_age, "ok": resolve_ok,
                    **{k: v for k, v in (state.get("resolve") or {}).items()
                       if k != "at"}},
        "watchlist": watchlist(),
        "predictions_logged": len(preds),
        "predictions_resolved": len(resolved),
        "note": ("The loop has never run." if predict_age is None else
                 "Running." if predict_ok and resolve_ok else
                 "The loop has stalled — predictions are not accumulating."),
    }
