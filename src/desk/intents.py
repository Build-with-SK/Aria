"""
src/desk/intents.py
===================
"BUY 20 AAPL" — SAID OUT LOUD OR TYPED, HELD UNTIL THE MOMENT IS RIGHT.

The owner asked for this exactly: tell her to buy twenty shares, she makes a
note, and when the time comes she buys. Same for selling. It is the gap
between an order (now, at any price) and a wish (someday, if it looks right),
and the wish is what a person actually means when they say "pick me up some
Apple".

HOW AN INTENT IS DIFFERENT FROM AN ORDER
----------------------------------------
An order is an instruction to a broker. An intent is an instruction to HER:
what he wants, how much, and the conditions under which she should act. She
holds it, watches, and fills it when the conditions are met — or lets it
expire and says why. An intent that never fires is not a failure; an intent
that fires badly is.

WHAT MAKES THE MOMENT RIGHT
---------------------------
Three gates, and all of them must pass:

  1. HIS CONDITIONS — a limit price, a window, whatever he said.
  2. HER OPINION — the v5 engine must not currently be pointing the other
     way. If he says buy and every module says sell, she holds the intent and
     tells him, rather than filling it silently. He can override; he cannot
     be un-told.
  3. THE RISK GATE — the same one every desk trade passes. An intent is not
     a bypass around position sizing, heat caps or the drawdown halt.

WHERE THIS STOPS
----------------
PAPER. Every fill goes through the desk's existing executor, which is wrapped
in PaperOnlyBroker, and nothing here touches that wrapper or the approval
queue. An intent is a convenience for expressing what he wants; it is not a
route around the thing that keeps a bad night from becoming an unrecoverable
morning. `execute()` refuses outright if the paper contract is not confirmed.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
INTENTS_FILE = ROOT / "data" / "desk" / "intents.json"

OPEN = "open"
FILLED = "filled"
CANCELLED = "cancelled"
EXPIRED = "expired"
BLOCKED = "blocked"          # conditions met, but a gate refused
SUPERSEDED = "superseded"    # a timed exit whose position had already gone

#: An intent nobody revisits is a landmine. Default life is one trading week.
DEFAULT_EXPIRY_DAYS = 7

_lock = threading.Lock()


# ── understanding what he said ───────────────────────────────────────────────

_NUM = r"(\d+(?:\.\d+)?)"
_SIDE = r"(buy|sell|short|long|get|pick up|grab|dump|offload)"
#: "grab ME 10 TSLA", "get US some AAPL" — people put a pronoun between the
#: verb and the quantity, and the first version of this parser dropped every
#: one of those on the floor.
_FILLER = r"(?:\s+(?:me|us|him|her|them|myself))?"

_PATTERNS = (
    # "buy £50 of AAPL" — notional first, or the share patterns below swallow
    # the 50 as a quantity and turn a fifty-pound order into fifty shares.
    re.compile(rf"\b{_SIDE}{_FILLER}\s+[£$]{_NUM}\s*(?:worth\s+)?(?:of\s+)?"
               rf"([A-Za-z][A-Za-z0-9.\-=^]{{0,14}})\b", re.IGNORECASE),
    # "buy 20 shares of AAPL", "buy me 20 shares AAPL", "sell 5 AAPL"
    re.compile(rf"\b{_SIDE}{_FILLER}\s+{_NUM}\s*"
               rf"(?:shares?\s+(?:of\s+)?|units?\s+(?:of\s+)?|of\s+)?"
               rf"([A-Za-z][A-Za-z0-9.\-=^]{{0,14}})\b", re.IGNORECASE),
    # "buy AAPL 20"
    re.compile(rf"\b{_SIDE}{_FILLER}\s+([A-Za-z][A-Za-z0-9.\-=^]{{0,14}})\s+{_NUM}\b",
               re.IGNORECASE),
)

_SELL_WORDS = {"sell", "short", "dump", "offload"}

#: Words that look like tickers in the pattern above but never are. Whisper
#: turns "buy twenty of Apple" into all sorts of things, and a mis-parsed
#: symbol that became an order would be the worst bug in this repository.
_NOT_TICKERS = {"SHARES", "SHARE", "UNITS", "UNIT", "STOCK", "STOCKS", "SOME",
                "MORE", "THE", "A", "AN", "OF", "AT", "IN", "ME", "US", "IT",
                "WORTH", "ABOUT", "AROUND"}

#: "sell it off in 5 mins" — the exit he names in the same breath as the entry.
_AFTER = re.compile(r"(?:in|after)\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?",
                    re.IGNORECASE)
_UNIT_MINUTES = {"second": 1 / 60, "sec": 1 / 60, "minute": 1, "min": 1,
                 "hour": 60, "hr": 60, "day": 60 * 24}
#: "buy 1 AAPL now" — fill on the next check rather than waiting for a level.
_NOW = re.compile(r"(now|immediately|right away|straight away|at market)",
                  re.IGNORECASE)
#: "and sell it" / "then sell it off" — a round trip in one sentence.
_THEN_EXIT = re.compile(r"(?:and|then)\s+(?:sell|close|exit|dump|offload)",
                        re.IGNORECASE)

_LIMIT = re.compile(r"\b(?:under|below|at or below|less than|cheaper than)\s*"
                    r"[£$]?(\d+(?:\.\d+)?)", re.IGNORECASE)
_ABOVE = re.compile(r"\b(?:above|over|at or above|more than)\s*"
                    r"[£$]?(\d+(?:\.\d+)?)", re.IGNORECASE)


def parse(text: str) -> dict | None:
    """Turn a sentence into an intent, or return None.

    Returning None is the common and correct outcome — most sentences are not
    orders. A parser that finds a trade in "what do you think about Apple"
    would be far worse than one that occasionally misses.
    """
    if not text:
        return None
    for pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groups()
        side_word = groups[0].lower()
        # the two orderings put quantity and symbol in different slots
        if re.fullmatch(_NUM, groups[1] or ""):
            qty, symbol = groups[1], groups[2]
        else:
            symbol, qty = groups[1], groups[2]

        symbol = (symbol or "").upper().strip(".,;:!?")
        if not symbol or symbol in _NOT_TICKERS or symbol.isdigit():
            continue

        side = "sell" if side_word in _SELL_WORDS else "buy"
        intent = {
            "side": side,
            "ticker": symbol,
            "qty": float(qty),
            "notional": "£" in text or "$" in text,
            "limit_price": None,
            "min_price": None,
        }
        limit = _LIMIT.search(text)
        if limit:
            intent["limit_price"] = float(limit.group(1))
        above = _ABOVE.search(text)
        if above:
            intent["min_price"] = float(above.group(1))

        after = _AFTER.search(text)
        minutes = (float(after.group(1)) * _UNIT_MINUTES[after.group(2).lower()]
                   if after else None)
        if _THEN_EXIT.search(text) and minutes is not None:
            # "buy 1 AAPL and sell it in 5 minutes" — one instruction, two
            # legs. The exit is scheduled when the ENTRY fills, not when he
            # said it: five minutes from a fill that never happened is not a
            # deadline, it is a countdown against nothing.
            intent["exit_after_minutes"] = minutes
        elif minutes is not None:
            # "buy 1 AAPL in 5 minutes" — delay the entry itself.
            intent["not_before_minutes"] = minutes

        intent["immediate"] = bool(_NOW.search(text)) or not (
            intent["limit_price"] or intent["min_price"]
            or intent.get("not_before_minutes"))
        return intent
    return None


# ── the book of intents ──────────────────────────────────────────────────────

def _load() -> list[dict]:
    if not INTENTS_FILE.exists():
        return []
    try:
        return json.loads(INTENTS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        # A corrupt book must not read as "he asked for nothing".
        raise RuntimeError(f"the intent book at {INTENTS_FILE} is unreadable "
                           f"({e}). Fix it before trading on it.") from e


def _save(rows: list[dict]) -> None:
    INTENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    INTENTS_FILE.write_text(json.dumps(rows, indent=2, default=str),
                            encoding="utf-8")


def record(intent: dict, *, source: str = "chat", said: str = "",
           expiry_days: int = DEFAULT_EXPIRY_DAYS) -> dict:
    """Write down what he asked for. Recording is not agreeing to fill it."""
    with _lock:
        rows = _load()
        row = {
            "id": f"int-{uuid.uuid4().hex[:8]}",
            "at": datetime.now().isoformat(timespec="seconds"),
            "status": OPEN,
            "source": source,
            "said": said[:300],
            "expires_at": (datetime.now()
                           + timedelta(days=expiry_days)).isoformat(timespec="seconds"),
            "checks": [],
            **intent,
        }
        rows.append(row)
        _save(rows)
    logger.info("intent recorded: %s %s %s (%s)", row["side"], row["qty"],
                row["ticker"], source)
    return row


def open_intents() -> list[dict]:
    return [r for r in _load() if r.get("status") == OPEN]


def cancel(intent_id: str, reason: str = "") -> dict | None:
    with _lock:
        rows = _load()
        for r in rows:
            if r.get("id") == intent_id and r.get("status") == OPEN:
                r["status"] = CANCELLED
                r["closed_at"] = datetime.now().isoformat(timespec="seconds")
                r["reason"] = reason or "cancelled by the owner"
                _save(rows)
                return r
    return None


def _update(intent_id: str, **fields) -> None:
    with _lock:
        rows = _load()
        for r in rows:
            if r.get("id") == intent_id:
                r.update(fields)
                break
        _save(rows)


# ── is the moment right ──────────────────────────────────────────────────────

def evaluate(intent: dict, price: float | None = None,
             opinion: dict | None = None) -> dict:
    """Should this fire now? Returns a decision with its reasons."""
    reasons: list[str] = []
    ready = True

    if intent.get("status") != OPEN:
        return {"ready": False, "reasons": [f"intent is {intent.get('status')}"]}

    try:
        if datetime.fromisoformat(intent["expires_at"]) < datetime.now():
            return {"ready": False, "expired": True,
                    "reasons": ["the intent expired before its conditions were met"]}
    except (KeyError, ValueError):
        pass

    if price is None:
        return {"ready": False,
                "reasons": ["no price — she will not fill blind"]}

    limit = intent.get("limit_price")
    if limit is not None and price > float(limit):
        ready = False
        reasons.append(f"price {price:.2f} is above his limit of {float(limit):.2f}")

    floor = intent.get("min_price")
    if floor is not None and price < float(floor):
        ready = False
        reasons.append(f"price {price:.2f} is below the {float(floor):.2f} he set")

    # Her own read. He can overrule it, but he cannot be un-told.
    if opinion:
        direction = str(opinion.get("direction") or "").lower()
        wants_up = intent.get("side") == "buy"
        against = ((wants_up and direction in ("bear", "bearish", "down"))
                   or (not wants_up and direction in ("bull", "bullish", "up")))
        if against:
            ready = False
            reasons.append(
                f"her read points the other way ({direction}) — held, not "
                f"refused. Confirm and she will fill it.")
        if opinion.get("stale"):
            ready = False
            reasons.append("her market data is stale; she will not time an "
                           "entry on prices she cannot vouch for")

    if ready:
        reasons.append("conditions met")
    return {"ready": ready, "reasons": reasons, "price": price}


def check_once(fill=None) -> dict:
    """Walk the open intents and act on the ones whose moment has come.

    `fill` is injectable so the decision path can be tested without a broker.
    """
    from src.v5 import marketdata as md

    acted, held, expired = [], [], []
    for intent in open_intents():
        ticker = intent.get("ticker")
        price = None
        try:
            df = md.history(ticker, period="5d")
            if df is not None and not df.empty:
                price = float(df["Close"].iloc[-1])
        except Exception as e:
            logger.warning("intent %s: no price for %s: %s",
                           intent.get("id"), ticker, e)

        opinion = None
        try:
            from src.v5 import pipeline
            r = pipeline.analyze(ticker, log=False)
            if not r.get("error"):
                opinion = {"direction": (r.get("ensemble") or {}).get("direction"),
                           "stale": (r.get("data") or {}).get("stale")}
        except Exception as e:
            logger.debug("intent %s: no opinion for %s: %s",
                         intent.get("id"), ticker, e)

        decision = evaluate(intent, price=price, opinion=opinion)
        stamp = {"at": datetime.now().isoformat(timespec="seconds"),
                 "price": price, **decision}

        if decision.get("expired"):
            _update(intent["id"], status=EXPIRED,
                    closed_at=stamp["at"], reason=decision["reasons"][0])
            expired.append(intent["id"])
            continue

        checks = list(intent.get("checks") or [])[-9:] + [stamp]
        if not decision["ready"]:
            _update(intent["id"], checks=checks)
            held.append({"id": intent["id"], "ticker": ticker,
                         "why": decision["reasons"]})
            continue

        # A TIMED EXIT MUST NOT SHORT A FLAT BOOK. If a stop took the shares
        # first, sending the original sell opens a short instead of closing a
        # long - the worst bug available in a round trip.
        if intent.get("closes"):
            from src.desk.roundtrip import check_exit_leg
            leg = check_exit_leg(intent)
            if not leg["act"]:
                _update(intent["id"], status=SUPERSEDED,
                        closed_at=stamp["at"], reason=leg["why"])
                held.append({"id": intent["id"], "ticker": ticker,
                             "why": [leg["why"]]})
                continue
            if leg["qty"] != intent.get("qty"):
                intent = {**intent, "qty": leg["qty"]}
                _update(intent["id"], qty=leg["qty"], trimmed_reason=leg["why"])

        result = execute(intent, price, fill=fill)
        if result.get("ok") and intent.get("exit_after_minutes"):
            try:
                from src.desk.roundtrip import on_fill
                on_fill(intent, result)
            except Exception as e:
                logger.exception("the entry filled but its timed exit could "
                                 "not be scheduled: %s", e)
        _update(intent["id"], checks=checks,
                status=FILLED if result.get("ok") else BLOCKED,
                fill=result, closed_at=stamp["at"])
        (acted if result.get("ok") else held).append(
            {"id": intent["id"], "ticker": ticker, "result": result})

    return {"at": datetime.now().isoformat(timespec="seconds"),
            "acted": acted, "held": held, "expired": expired,
            "open": len(open_intents())}


def execute(intent: dict, price: float, fill=None) -> dict:
    """Fill one intent — in paper, through the desk's own executor.

    The paper contract is checked HERE as well as inside the broker wrapper.
    Two checks for the same thing is not redundancy in this one place: this
    function exists to turn a spoken sentence into a trade, and a spoken
    sentence is the least verified input in the system.
    """
    from src.desk.config import load_config, paper_mode_confirmed

    if not paper_mode_confirmed():
        return {"ok": False,
                "error": ("refusing to fill: the paper contract is not "
                          "confirmed (ALPACA_PAPER must be exactly 'true'). "
                          "An intent is a convenience, not a route around the "
                          "live-money gate.")}
    if fill is not None:
        return fill(intent, price)

    try:
        from src.desk.auto_executor import AutoExecutor
        cfg = load_config()
        executor = AutoExecutor(cfg)
        return executor.execute_intent(intent, price)
    except AttributeError:
        return {"ok": False,
                "error": ("the executor has no intent path yet — the intent "
                          "is recorded and held rather than silently dropped")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def status() -> dict:
    rows = _load()
    return {
        "open": [r for r in rows if r.get("status") == OPEN],
        "recent": rows[-10:],
        "counts": {s: sum(1 for r in rows if r.get("status") == s)
                   for s in (OPEN, FILLED, CANCELLED, EXPIRED, BLOCKED,
                             SUPERSEDED)},
        "note": ("Intents are held instructions, not orders. Every fill goes "
                 "through the paper broker; nothing here can reach live "
                 "money."),
    }
