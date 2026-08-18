"""
src/desk/roundtrip.py
=====================
"BUY 1 AAPL AND SELL IT OFF IN 5 MINS" — BOTH LEGS.

An entry he wants now and an exit he wants on a clock. One sentence, two
instructions, and the second one only makes sense once the first has happened.

THE EXIT IS SCHEDULED FROM THE FILL, NOT FROM THE SENTENCE
----------------------------------------------------------
Five minutes from a fill that never happened is a countdown against nothing.
If the entry is refused by the risk officer, or the market is shut, or the
symbol does not resolve, there is no position and there must be no exit
waiting to sell one. So the exit leg is created by `on_fill()` and not a
moment earlier — and it sells exactly what the entry bought, not what he
asked for, because a partial fill of 1 share should not produce a sell of 20.

WHAT IT WILL NOT DO
-------------------
Sell something he does not own. The exit checks the broker for the position
before it acts, and if the shares are gone — a stop took them, he closed it
himself, the desk's exit engine got there first — the leg is closed as
`superseded` rather than sending a sell that would open a SHORT. That is the
single most dangerous bug available in this file: a timed exit firing against
a flat book turns a round trip into an unintended short position.

Everything runs through the paper broker. Nothing here touches live_guard or
the approval queue.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SUPERSEDED = "superseded"


def schedule_exit(entry: dict, filled_qty: float,
                  minutes: float, record=None) -> dict | None:
    """Create the exit leg for a filled entry.

    `record` is injectable so the wiring can be tested without writing to the
    intent book.
    """
    from src.desk import intents

    if filled_qty is None or filled_qty <= 0:
        logger.info("no exit scheduled for %s: nothing was filled",
                    entry.get("ticker"))
        return None

    side = "sell" if entry.get("side") == "buy" else "buy"
    not_before = datetime.now() + timedelta(minutes=float(minutes))
    leg = {
        "side": side,
        "ticker": entry.get("ticker"),
        # What was ACTUALLY filled. A partial fill of 1 must not close 20.
        "qty": float(filled_qty),
        "notional": False,
        "limit_price": None,
        "min_price": None,
        "immediate": True,
        "not_before": not_before.isoformat(timespec="seconds"),
        "closes": entry.get("id"),
        "reason": (f"timed exit — he asked for this {minutes:g} minutes after "
                   f"the entry filled"),
    }
    writer = record or intents.record
    row = writer(leg, source="round trip",
                 said=f"{side} {filled_qty} {entry.get('ticker')} "
                      f"{minutes:g} minutes after the entry")
    logger.info("exit leg %s scheduled for %s at %s", row.get("id"),
                entry.get("ticker"), not_before.isoformat(timespec="seconds"))
    return row


def on_fill(entry: dict, result: dict) -> dict | None:
    """Called when an entry fills. Creates the exit leg if one was asked for."""
    minutes = entry.get("exit_after_minutes")
    if not minutes:
        return None
    if not (result or {}).get("ok"):
        return None
    filled = (result.get("filled_qty") or result.get("qty")
              or entry.get("qty"))
    return schedule_exit(entry, filled, float(minutes))


def held_qty(ticker: str, account: dict | None = None) -> float:
    """How much of `ticker` the BROKER says is held. The authority."""
    if account is None:
        from src.desk.auto_executor import account_snapshot
        account = account_snapshot() or {}
    for p in (account.get("positions") or []):
        if str(p.get("ticker", "")).upper() == str(ticker).upper():
            return float(p.get("qty") or 0)
    return 0.0


def check_exit_leg(intent: dict, account: dict | None = None) -> dict:
    """Is this timed exit still valid, and for how much?

    Returns {"act": bool, "qty": float, "why": str}. The qty comes back
    possibly REDUCED — if a stop took half the position, selling the original
    size would short the remainder.
    """
    ticker = intent.get("ticker")
    wanted = float(intent.get("qty") or 0)
    closing_a_long = intent.get("side") == "sell"

    held = held_qty(ticker, account)
    if closing_a_long:
        available = max(held, 0.0)
    else:                                   # closing a short
        available = max(-held, 0.0)

    if available <= 0:
        return {"act": False, "qty": 0.0,
                "why": (f"nothing left to close — the {ticker} position is "
                        f"gone. A stop, his own hand or the exit engine got "
                        f"there first, and selling now would open a short "
                        f"rather than close a long.")}

    if available < wanted:
        return {"act": True, "qty": available,
                "why": (f"closing {available:g} rather than {wanted:g} — that "
                        f"is what is left of the position")}

    return {"act": True, "qty": wanted, "why": "closing the full position"}


def due(intent: dict, now: datetime | None = None) -> bool:
    """Has the clock run out on a scheduled leg?"""
    stamp = intent.get("not_before")
    if not stamp:
        return True
    try:
        return (now or datetime.now()) >= datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return True


def describe(intent: dict) -> str:
    """One line he can read in chat."""
    if intent.get("closes"):
        return (f"exit leg: {intent['side']} {intent['qty']:g} "
                f"{intent['ticker']} at {intent.get('not_before', 'the next check')}")
    if intent.get("exit_after_minutes"):
        return (f"{intent['side']} {intent['qty']:g} {intent['ticker']} now, "
                f"then close it {intent['exit_after_minutes']:g} minutes after "
                f"it fills")
    return f"{intent['side']} {intent['qty']:g} {intent['ticker']}"
