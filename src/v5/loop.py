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
import threading
from datetime import date, datetime, timedelta
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

PREDICT_HOUR = 22        # the evening slot, local time (kept for the cron)
RESOLVE_INTERVAL_HOURS = 6
# A session's daily bar is treated as final this long after the 16:00 New York
# close. The old 22:10-London slot was 17:10 New York, so this keeps the same
# information set while no longer depending on which timezone the box is in.
SETTLE_MINUTES = 60
# How long after a session settles the loop may take to make its call before
# health() reports the miss. The catch-up job checks hourly.
PREDICT_GRACE_HOURS = 3

_predict_lock = threading.Lock()


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

    session = _latest_session(datetime.now())
    summary = {"tickers": len(names), "logged": len(logged),
               "skipped": len(skipped), "errors": len(errors),
               "session": session.isoformat() if session else None,
               "detail": logged, "skipped_detail": skipped,
               "error_detail": errors}
    _beat("predict", {k: summary[k] for k in
                      ("tickers", "logged", "skipped", "errors", "session")})
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


# ── sessions: what the predict leg is keyed on ──────────────────────────────

def _latest_session(now: datetime) -> date | None:
    from src.trading.market import latest_completed_session
    return latest_completed_session(now, settle_minutes=SETTLE_MINUTES)


def _read_heartbeat() -> dict:
    if not HEARTBEAT.exists():
        return {}
    try:
        state = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def predict_last_run_date() -> str:
    """The date the predict leg last ran, from the heartbeat file."""
    return str((_read_heartbeat().get("predict") or {}).get("at", ""))[:10]


def predict_last_session() -> date | None:
    """The NYSE session the predict leg last made its calls on.

    Heartbeats written before sessions were recorded carry only a local
    timestamp; the session is then the one that was latest-completed at that
    moment, which is exactly what those calls were built on.
    """
    leg = _read_heartbeat().get("predict") or {}
    if leg.get("session"):
        try:
            return date.fromisoformat(str(leg["session"]))
        except ValueError:
            pass
    at = leg.get("at")
    if not at:
        return None
    try:
        return _latest_session(datetime.fromisoformat(str(at)))
    except (ValueError, TypeError):
        return None


def predict_due(now: datetime | None = None) -> dict:
    """Should the predict leg run now? Keyed on the NYSE session, not a clock.

    THE BUG THIS FIXES (2026-10-06)
    -------------------------------
    The previous rule was "not before 22:00 local". The predict cron needs the
    process alive at 22:10 and the catch-up ran once, at startup. Every backend
    start between 23 Sep and 6 Oct happened before 22:00 London and the box was
    never up at 22:10, so the catch-up declined every time and no prediction
    was made for thirteen days while the resolve leg kept running.

    "22:00 London" was standing in for "after the US close". The rule now says
    that directly:

      * never inside the session window (open to close + SETTLE_MINUTES) — a
        call built on a partial daily bar is a different measurement;
      * one call per completed session — a Saturday call is Friday's close
        again, and logging it as new evidence double-counts the sample;
      * a missed session is made up the next time the box is awake outside the
        window (e.g. 07:40 London before the open), on that session's close.

    This is still not a backfill: the call is made now, timestamped now, on the
    latest close available now — exactly what the evening cron would have used.
    """
    now = now or datetime.now()
    from src.trading.market import in_session_window
    window = in_session_window(now, settle_minutes=SETTLE_MINUTES)
    session = _latest_session(now)
    if window is None or session is None:
        return {"due": False, "session": None,
                "why": "no NYSE holiday calendar covers today — refusing to "
                       "guess which close a call would be built on; extend "
                       "src/trading/market.NYSE_HOLIDAYS"}
    if window:
        return {"due": False, "session": session.isoformat(),
                "why": "inside the US session window (open to close + "
                       f"{SETTLE_MINUTES} min) — a call on a partial daily bar "
                       "is not the same measurement"}
    last = predict_last_session()
    if last is not None and last >= session:
        return {"due": False, "session": session.isoformat(),
                "why": f"predict has already run for the {session} session"}
    return {"due": True, "session": session.isoformat(),
            "last_session": last.isoformat() if last else None,
            "why": f"no calls yet for the {session} session"}


def predict_catchup(now: datetime | None = None) -> dict:
    """Make the latest session's calls if they have not been made.

    Safe to call as often as you like — on startup, hourly, from the evening
    cron. predict_due() decides; a lock stops two jobs from running the leg at
    once, and the heartbeat written by predict_once() stops a restart loop
    from manufacturing rows.
    """
    due = predict_due(now)
    if not due["due"]:
        return {"ran": False, **due}
    if not _predict_lock.acquire(blocking=False):
        return {"ran": False, "why": "the predict leg is already running"}
    try:
        # Re-check under the lock: another job may have finished meanwhile.
        due = predict_due(now)
        if not due["due"]:
            return {"ran": False, **due}
        logger.warning("research loop: no calls yet for the %s session (last "
                       "session %s) — making them now", due["session"],
                       due.get("last_session") or "never")
        summary = predict_once()
        summary["catchup"] = True
        return {"ran": True, **summary, "session": due["session"]}
    finally:
        _predict_lock.release()


def run_once() -> dict:
    """Both legs, for a manual trigger or a smoke test."""
    return {"predict": predict_once(), "resolve": resolve_once()}


# ── health ───────────────────────────────────────────────────────────────────

def _missed_sessions(last: date | None, expected: date) -> int | None:
    """Trading sessions after the last one predicted, up to the expected one."""
    from src.trading.market import is_trading_day
    if last is None:
        return None
    n, d = 0, last + timedelta(days=1)
    while d <= expected and n < 400:
        if is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def predict_health(now: datetime | None = None) -> dict:
    """Has the latest completed session been predicted? With a grace period
    after the session settles, because the catch-up checks hourly."""
    now = now or datetime.now()
    from src.trading.market import session_close
    expected = _latest_session(now)
    last = predict_last_session()
    if expected is None:
        return {"ok": None, "expected_session": None,
                "last_session": last.isoformat() if last else None,
                "missed_sessions": None,
                "why": "no NYSE calendar for this date — cannot say what is due"}
    missed = _missed_sessions(last, expected)
    close = session_close(expected)
    if close is None:
        return {"ok": None, "expected_session": expected.isoformat(),
                "last_session": last.isoformat() if last else None,
                "missed_sessions": missed,
                "why": f"the NYSE calendar gives no close for {expected} — cannot say "
                       f"whether the grace period has passed"}
    settled_at = close + timedelta(minutes=SETTLE_MINUTES)
    in_grace = (now.astimezone(settled_at.tzinfo) - settled_at
                < timedelta(hours=PREDICT_GRACE_HOURS))
    ok = missed == 0 or (missed == 1 and in_grace)
    return {"ok": bool(ok), "expected_session": expected.isoformat(),
            "last_session": last.isoformat() if last else None,
            "missed_sessions": missed,
            "why": ("up to date" if missed == 0 else
                    "within the grace period after the close" if ok else
                    "never run" if missed is None else
                    f"{missed} completed session(s) have no calls")}


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
    # Judged by sessions, not hours: a 36-hour rule called every Monday a
    # stall (Friday's call is 72 h old by then) and could not tell a holiday
    # from a fault. Without a calendar, fall back to a generous age bound.
    ph = predict_health()
    predict_ok = bool(ph["ok"]) if ph["ok"] is not None else (
        predict_age is not None and predict_age < 96)
    resolve_ok = resolve_age is not None and resolve_age < RESOLVE_INTERVAL_HOURS * 3

    from src.v5 import learning
    preds = learning.load_predictions()
    resolved = [p for p in preds if p.get("resolved")]

    return {
        "healthy": bool(predict_ok and resolve_ok),
        "predict": {"last_run": (state.get("predict") or {}).get("at"),
                    "hours_ago": predict_age, "ok": predict_ok,
                    "expected_session": ph["expected_session"],
                    "last_session": ph["last_session"],
                    "missed_sessions": ph["missed_sessions"],
                    "status": ph["why"],
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
                 "The loop has stalled — predictions are not accumulating"
                 + (f" ({ph['why']})." if not predict_ok else ".")),
    }
