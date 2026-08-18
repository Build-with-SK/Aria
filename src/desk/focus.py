"""
src/desk/focus.py
=================
WHAT THE DESK TRADES, CHOSEN BY THE THING THAT DOES THE THINKING.

The desk used to pick its names by ranking `signals.json` on
`abs(composite_score)` — one technical composite. Meanwhile the 41-module v5
engine ran every day, produced a direction and a confidence for every name on
the watchlist, wrote them to the prediction log, and was never consulted about
what to trade.

Two programs sharing a database. The owner's objection was exact: a system
that computes recommendations and then trades a different universe is not an
intelligence trading system.

So the ranking comes from the research engine now, and the composite is the
fallback for names it has not scored — not the other way round.

WHAT "BEST" MEANS HERE
----------------------
Conviction, not direction. A name the engine calls bearish with 0.72
confidence is as interesting as a bullish one at 0.72: the desk can go either
way, and a ranking that preferred longs would be a thumb on the scale nobody
asked for. So candidates are ranked by DISTANCE FROM THE COIN FLIP —
|p_bull - 0.5| — carrying the side with them.

WHAT THIS DOES NOT DO
---------------------
It does not decide to trade. It nominates names for a debate, and the debate,
the risk officer and the paper gate all still stand between a nomination and a
fill. Nor does it pretend the engine has an edge: 0 of 76 predictions have
resolved, and ranking by a confidence nobody has validated is a way of
choosing what to look at, not a claim about what will happen.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

#: A prediction older than this is not a current view. The engine predicts
#: daily; anything past a few days is a stale opinion about a market that has
#: moved on.
MAX_PREDICTION_AGE_DAYS = 4

#: Below this distance from a coin flip there is no view to act on. 0.52 is
#: the engine saying "I do not know" in a voice that sounds like a number.
MIN_EDGE = 0.03


def _age_days(at: str) -> float | None:
    try:
        return (datetime.now() - datetime.fromisoformat(at)).total_seconds() / 86400
    except (TypeError, ValueError):
        return None


def recommended(limit: int = 20) -> list[dict]:
    """The engine's current views, strongest conviction first.

    One row per ticker — the most recent prediction wins, because two
    predictions for the same name are the same name reconsidered, not two
    opportunities.
    """
    try:
        from src.v5 import learning
        rows = learning.load_predictions()
    except Exception as e:
        logger.warning("v5 predictions unavailable: %s", e)
        return []

    latest: dict[str, dict] = {}
    for r in rows:
        ticker = (r.get("ticker") or "").upper()
        if not ticker:
            continue
        age = _age_days(r.get("at") or "")
        if age is None or age > MAX_PREDICTION_AGE_DAYS:
            continue
        seen = latest.get(ticker)
        if seen is None or (r.get("at") or "") > (seen.get("at") or ""):
            latest[ticker] = r

    out = []
    for ticker, r in latest.items():
        p_bull = r.get("p_bull")
        if not isinstance(p_bull, (int, float)):
            continue
        edge = abs(float(p_bull) - 0.5)
        if edge < MIN_EDGE:
            continue
        out.append({
            "ticker": ticker,
            "side": "long" if float(p_bull) >= 0.5 else "short",
            "p_bull": round(float(p_bull), 4),
            "edge": round(edge, 4),
            "confidence": r.get("confidence"),
            "direction": r.get("direction"),
            "at": r.get("at"),
            "age_days": round(_age_days(r.get("at") or "") or 0, 2),
            "source": "v5 research engine",
        })
    out.sort(key=lambda x: -x["edge"])
    return out[:limit]


def focus_tickers(n: int, account: dict, executable, limit: int = 60) -> dict:
    """Names for this cycle, and an account of where they came from.

    `executable` is the caller's filter — market hours, asset class, futures
    and indices — passed in rather than duplicated here, so there is one
    definition of what the desk can trade.
    """
    held = {p.get("ticker") for p in (account.get("positions") or [])}
    try:
        from src.execution.approval_queue import ApprovalQueue
        pending = {t.ticker for t in ApprovalQueue().get_all(limit=500)
                   if t.status == "pending"}
    except Exception:
        pending = set()

    skip = held | pending
    chosen, why = [], []

    for row in recommended(limit=limit):
        if len(chosen) >= n:
            break
        t = row["ticker"]
        if t in skip or not executable(t):
            continue
        chosen.append(t)
        why.append({**row, "picked_by": "recommendation"})

    fell_back = []
    if len(chosen) < n:
        # The engine has not scored enough tradeable names. The composite is a
        # weaker signal, and saying so beats silently filling the slate as
        # though the ranking were uniform.
        from src.desk.opinion import load_data_json
        signals = load_data_json("signals.json")
        ranked = sorted(
            ((t, d) for t, d in signals.items()
             if isinstance(d, dict) and t not in skip and t not in chosen
             and executable(t) and (d.get("current_price") or 0) > 0),
            key=lambda kv: -abs(kv[1].get("composite_score") or 0))
        for t, d in ranked:
            if len(chosen) >= n:
                break
            chosen.append(t)
            fell_back.append(t)
            why.append({"ticker": t, "picked_by": "composite fallback",
                        "composite_score": d.get("composite_score"),
                        "source": "signals.json"})

    return {
        "tickers": chosen,
        "why": why,
        "from_recommendations": len(chosen) - len(fell_back),
        "from_fallback": len(fell_back),
        "note": ("Ranked by the research engine's conviction, strongest first."
                 if not fell_back else
                 f"{len(chosen) - len(fell_back)} from the engine, "
                 f"{len(fell_back)} from the technical composite because the "
                 f"engine had no fresh view on enough tradeable names."),
    }
