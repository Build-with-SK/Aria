"""
src/intelligence/research/events.py
===================================
Event intelligence: what is scheduled, what just happened, and what that does
to the risk of holding a view — using only events knowable at T.

Visibility (src/intelligence/research/pit_data.Event.visible_at):

    an event that has HAPPENED by T is public (its occurrence is the
    publication); a SCHEDULED event is visible at T only if its publication
    time — or, when the vendor gives none, the time ARIA first observed it —
    is at or before T; a withdrawn schedule disappears from its withdrawal.
    Outcome fields (reported EPS, surprise, dividend amount) are shown only
    for events that have occurred by T.

Event risk over the horizon:

    HIGH       an instrument-specific HIGH-importance event (earnings) inside it
    ELEVATED   a MEDIUM event (split, market-wide economic release) inside it
    LOW        only LOW-importance events, or none, known
    UNKNOWN    the event provider failed or is not available — absence of known
               events is then NOT evidence of absence

Corporate actions inside a returns window are flagged: prices here are
adjusted, so returns are comparable across them, but any raw recorded price
straddling one is not.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from src.intelligence.research.pit_data import Event, MacroObservation

CALENDAR_PER_TRADING_DAY = 7 / 5
RECENT_DAYS = 10
SURPRISE_LOOKBACK_DAYS = 120
_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def macro_release_events(obs: list[MacroObservation]) -> list[Event]:
    """Each macro observation as an ECONOMIC_RELEASE that occurred when it
    became knowable (release time, else retrieval time)."""
    out = []
    for o in obs:
        k = o.known_at()
        if k is None:
            continue
        out.append(Event(f"RELEASE:{o.series}:{o.observation_date.isoformat()}",
                         "ECONOMIC_RELEASE", "MARKET:US", k, o.source, "MEDIUM",
                         "a market-wide statistic", publication_time=o.release_time,
                         observed_at=o.retrieved_at,
                         outcome={"value": o.value, "unit": o.unit,
                                  "observation_date": o.observation_date.isoformat(),
                                  "known_at_basis": ("release time" if o.release_time
                                                     else "retrieval time (no release time "
                                                          "published)")}))
    return out


def context(visible: list[Event], at: datetime, *, horizon_days: int,
            provider_ok: bool, window_start: Optional[datetime] = None) -> dict:
    """Upcoming, recent, corporate actions, last earnings surprise, event risk."""
    end = at + timedelta(days=horizon_days * CALENDAR_PER_TRADING_DAY)
    upcoming = [e for e in visible if at < e.event_time <= end]
    recent = [e for e in visible if at - timedelta(days=RECENT_DAYS) <= e.event_time <= at]
    actions = [e for e in visible if e.kind in ("SPLIT", "DIVIDEND")
               and window_start is not None and window_start <= e.event_time <= at]
    surprise = None
    for e in sorted(visible, key=lambda e: e.event_time, reverse=True):
        if (e.kind == "EARNINGS" and e.event_time <= at
                and e.event_time >= at - timedelta(days=SURPRISE_LOOKBACK_DAYS)
                and e.outcome.get("surprise_pct") is not None):
            surprise = e.view_at(at)
            break
    if not provider_ok:
        risk, basis = "UNKNOWN", ("the event provider failed or is unavailable: no known "
                                  "event is not the same as no event")
    else:
        top = max((_RANK.get(e.importance, 0) for e in upcoming), default=0)
        risk = {3: "HIGH", 2: "ELEVATED"}.get(top, "LOW")
        basis = (", ".join(f"{e.kind} {e.event_time.date()}" for e in upcoming
                           if _RANK.get(e.importance, 0) == top) if top >= 2
                 else "no HIGH or MEDIUM event known inside the horizon")
    return {
        "event_risk": {"state": risk, "basis": basis, "horizon_days": horizon_days,
                       "horizon_end": end.isoformat()},
        "upcoming": [e.view_at(at) for e in upcoming],
        "recent": [e.view_at(at) for e in recent],
        "corporate_actions_in_window": [e.view_at(at) for e in actions],
        "last_earnings_surprise": surprise,
        "provider_ok": provider_ok,
        "visible_events": len(visible),
    }
