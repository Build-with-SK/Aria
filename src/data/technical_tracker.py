"""
src/data/technical_tracker.py
=============================
Performance tracker for the technical recommendations — so you know whether
they actually work, honestly, on YOUR universe.

Each day it SNAPSHOTS the recommendations (symbol, label, entry price, date)
to data/technical/recommendations_log.jsonl. Later it EVALUATES them: fetch
the current price, compute the forward return, and score a "hit" when the call
pointed the right way (Buy → up, Sell → down). The report gives hit-rate and
average return per label — real numbers that accumulate over time, not a
back-fitted claim.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
LOG_FILE = ROOT / "data" / "technical" / "recommendations_log.jsonl"

BULLISH = ("Strong Buy", "Buy")
BEARISH = ("Strong Sell", "Sell")


# ── snapshot (record today's calls) ──────────────────────────────────────────

def _load_log() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    out = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


SETTLE_MINUTES = 60


def _entry_session(e: dict) -> str | None:
    """The NYSE session an entry's batch was taken for. Entries written before
    2026-10-06 carry only a timestamp; the session is then the one that was
    latest-settled at that moment."""
    if e.get("snapshot_session"):
        return e["snapshot_session"]
    try:
        from src.trading.market import latest_completed_session
        d = latest_completed_session(datetime.fromisoformat(str(e.get("at"))),
                                     settle_minutes=SETTLE_MINUTES)
        return d.isoformat() if d else None
    except (TypeError, ValueError):
        return None


def _row_session(symbol: str, batch_session: str | None) -> str | None:
    from src.data.technical_summary import _resolve_yahoo
    from src.markets import sessions
    from src.markets.venues import VENUES
    venue = sessions.resolve(_resolve_yahoo(symbol)).venue
    if not venue or VENUES[venue].kind != "exchange":
        return None
    if venue == "XNYS":
        return batch_session
    d = sessions.latest_completed_session(venue, datetime.now().astimezone(),
                                          settle_minutes=SETTLE_MINUTES)
    return d.isoformat() if d else None


def snapshot_due(now: datetime | None = None) -> dict:
    """One batch per settled NYSE session, never inside the session window.

    The old trigger was a 21:30 cron: it needed the process alive at 21:30
    London (none was from 28 Sep), and 21:30 London is 16:30 New York, when
    the daily bar is not yet final, so entry prices were provisional closes.
    """
    from src.trading.market import in_session_window, latest_completed_session
    now = now or datetime.now()
    window = in_session_window(now, settle_minutes=SETTLE_MINUTES)
    session = latest_completed_session(now, settle_minutes=SETTLE_MINUTES)
    if window is None or session is None:
        return {"due": False, "session": None, "why": "no NYSE calendar for today"}
    if window:
        return {"due": False, "session": session.isoformat(),
                "why": "inside the US session window"}
    done = {_entry_session(e) for e in _load_log()}
    if session.isoformat() in done:
        return {"due": False, "session": session.isoformat(),
                "why": f"already snapshotted for the {session} session"}
    return {"due": True, "session": session.isoformat(), "why": "not yet taken"}


def snapshot_catchup(now: datetime | None = None, timeframe: str = "1d",
                     limit: int = 30) -> dict:
    """Take the latest session's snapshot if it has not been taken. Safe to
    call hourly; it declines everywhere except the first eligible moment."""
    due = snapshot_due(now)
    if not due["due"]:
        return {"ran": False, **due}
    out = snapshot(timeframe=timeframe, limit=limit, session=due["session"])
    return {"ran": True, **out}


def snapshot(timeframe: str = "1d", limit: int = 25, session: str | None = None) -> dict:
    """Record the recommendations with entry prices. Idempotent per
    (session, symbol, timeframe) so repeated runs don't double-log."""
    from src.data.technical_summary import scan_universe_recommendations
    if session is None:
        session = _entry_session({"at": datetime.now().isoformat()})
    scan = scan_universe_recommendations(timeframe, limit)
    recs = scan.get("strong_buy", []) + scan.get("buy", []) + scan.get("sell", [])
    today = date.today().isoformat()
    seen = {(_entry_session(e), e["symbol"], e["timeframe"]) for e in _load_log()}
    added = []
    import math
    try:
        from src.core.versioning import model_version
        version = model_version("technical-composite", "src/data/technical_summary.py",
                                timeframe=timeframe)
    except Exception:
        version = None
    try:
        from src.macro.regime import stamp
        regime = stamp()
    except Exception:
        regime = {"regime_at": None, "regime_basis": None}
    for r in recs:
        px = r.get("price")
        if not px or (isinstance(px, float) and math.isnan(px)):
            continue
        key = (session, r["symbol"], timeframe)
        if key in seen:
            continue
        info_session = _row_session(r["symbol"], session)
        entry = {"at": datetime.now().isoformat(), "date": today,
                 "snapshot_session": session,
                 # The row's OWN venue's latest settled close (src/markets);
                 # None for a 24/7 quote (crypto) or an unresolvable venue.
                 "information_session": info_session,
                 "symbol": r["symbol"], "timeframe": timeframe,
                 "summary": r["summary"], "entry_price": r["price"],
                 "model_version": version, **regime}
        _append(entry)
        added.append(r["symbol"])
    logger.info(f"technical tracker: snapshot logged {len(added)} recs")
    return {"date": today, "session": session, "timeframe": timeframe,
            "logged": len(added), "symbols": added}


# ── evaluate (did they work?) ────────────────────────────────────────────────

def _current_prices(symbols: list) -> dict:
    """Batch current prices via one yfinance download. {symbol: price}."""
    if not symbols:
        return {}
    from src.data.technical_summary import _resolve_yahoo
    import yfinance as yf
    y2s = {}
    for s in symbols:
        y2s[_resolve_yahoo(s)] = s
    prices = {}
    try:
        data = yf.download(list(y2s), period="1d", interval="1d",
                           progress=False, group_by="ticker", threads=True)
        for yh, sym in y2s.items():
            try:
                col = data[yh]["Close"] if len(y2s) > 1 else data["Close"]
                px = float(col.dropna().iloc[-1])
                if px == px:
                    prices[sym] = px
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"tracker price fetch failed: {e}")
    return prices


def evaluate(min_age_days: int = 1) -> dict:
    """Score every logged rec old enough to judge. A 'hit' = the call pointed
    the right way (bullish→price up, bearish→down). Returns per-label and
    overall hit-rate + average return."""
    log = _load_log()
    if not log:
        return {"note": "no recommendations logged yet — snapshots accumulate daily"}
    now = date.today()
    ripe = []
    for e in log:
        try:
            age = (now - date.fromisoformat(e["date"])).days
        except Exception:
            continue
        if age >= min_age_days and e.get("entry_price"):
            ripe.append(e)
    if not ripe:
        return {"note": f"recs logged but none aged ≥{min_age_days}d yet"}

    prices = _current_prices(sorted({e["symbol"] for e in ripe}))
    from collections import defaultdict
    buckets = defaultdict(lambda: {"n": 0, "hits": 0, "ret_sum": 0.0})
    graded = 0
    for e in ripe:
        cur = prices.get(e["symbol"])
        if not cur:
            continue
        entry = float(e["entry_price"])
        if not entry:
            continue
        ret = (cur - entry) / entry * 100.0
        bullish = e["summary"] in BULLISH
        hit = (ret > 0) if bullish else (ret < 0)
        directional = "bullish" if bullish else "bearish"
        for key in (e["summary"], directional, "ALL"):
            b = buckets[key]
            b["n"] += 1
            b["hits"] += 1 if hit else 0
            # signed return in the call's direction (bullish keeps sign,
            # bearish flips so "good" is always positive)
            b["ret_sum"] += ret if bullish else -ret
        graded += 1

    report = {}
    for key, b in buckets.items():
        if b["n"]:
            report[key] = {
                "count": b["n"],
                "hit_rate": round(b["hits"] / b["n"], 3),
                "avg_return_in_call_direction_pct": round(b["ret_sum"] / b["n"], 2),
            }
    return {"graded": graded, "logged_total": len(log),
            "min_age_days": min_age_days, "by_label": report}
