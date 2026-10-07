"""
src/execution/reduce_only.py
============================
THE REDUCE-ONLY INVARIANT.

An order that exists to make a position smaller — a protective stop or
target, an OCO exit, the exit engine's close, a queued manual exit, a
kill-switch flatten — is allowed things an entry is not: a stop rests through
the night, a halt may close while every other order is refused. The price of
that latitude is this, checked at the moment the order is sent:

    the order is on the REDUCING side of a position that exists NOW, and
    its quantity plus everything already resting on that side is at most
    the position — so even if every one of them fills, the position
    shrinks towards zero and never flips sign.

    |position after all of them fill|  <=  |position now|

Anything that cannot be established — the position unreadable, the resting
orders unreadable, a symbol the broker no longer holds — sends NOTHING. A
reduce-only order that cannot be proven reduce-only is a new position waiting
to happen, and the failure must be the order not sent, never the order sent.

Pure arithmetic (`check`) is separate from the broker read (`at_broker`) so
the arithmetic is testable on its own and the read is one place.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

EPS = 1e-9


@dataclass(frozen=True)
class ReduceOnly:
    side: str
    requested: float
    qty: float                       # what may be sent; 0 means send nothing
    position: Optional[float]        # signed broker position at check time
    resting: Optional[float]         # same-side reducing qty already resting
    reason: str

    @property
    def ok(self) -> bool:
        return self.qty > EPS

    def to_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}


def reducing_side(position: float) -> Optional[str]:
    """The side that shrinks `position`: sell a long, buy back a short."""
    if position > EPS:
        return "sell"
    if position < -EPS:
        return "buy"
    return None


def check(side: str, requested: float, position: Optional[float],
          resting: Optional[float] = 0.0) -> ReduceOnly:
    """How much of `requested` on `side` may be sent without any possible
    combination of fills increasing |position|. Pure."""
    side = (side or "").lower()
    def out(q, why):
        return ReduceOnly(side, requested, q, position, resting, why)

    if not (isinstance(requested, (int, float)) and math.isfinite(requested)) or requested <= EPS:
        return out(0.0, "nothing to send")
    if position is None or not math.isfinite(position):
        return out(0.0, "position unreadable — a reduce-only order is not sent blind")
    if resting is None or not math.isfinite(resting) or resting < -EPS:
        return out(0.0, "resting orders unreadable — cannot rule out a double fill")
    want = reducing_side(position)
    if want is None:
        return out(0.0, "no position: a reduce-only order would open one")
    if side != want:
        return out(0.0, f"wrong side: a {side} adds to a {'long' if position > 0 else 'short'} position")
    room = abs(position) - resting
    if room <= EPS:
        return out(0.0, f"already covered: {resting:g} resting on the {side} side "
                        f"against a position of {abs(position):g}")
    qty = min(requested, room)
    if qty < requested - EPS:
        return out(qty, f"clipped from {requested:g} to {qty:g}: position {abs(position):g}, "
                        f"{resting:g} already resting")
    return out(qty, "reduces the position")


# ── reading the broker ───────────────────────────────────────────────────────

def _norm(symbol: str) -> str:
    return (symbol or "").strip().upper().replace("-", "").replace("/", "")


def same_symbol(a: str, b: str) -> bool:
    """BTC-USD, BTC/USD and Alpaca's BTCUSD are one instrument."""
    return bool(a) and _norm(a) == _norm(b)


def _field(p, *names):
    for n in names:
        v = p.get(n) if isinstance(p, dict) else getattr(p, n, None)
        if v is not None:
            return v
    return None


def signed_position(positions: Iterable, ticker: str) -> float:
    """Net signed quantity held in `ticker` (+ long, - short). Accepts the
    legacy broker's Position objects and the trading layer's dicts."""
    net = 0.0
    for p in positions or []:
        sym = _field(p, "ticker", "symbol")
        if not same_symbol(str(sym or ""), ticker):
            continue
        qty = float(_field(p, "qty") or 0.0)
        side = str(_field(p, "side") or "").lower()
        net += -abs(qty) if (side == "short" or qty < 0) else abs(qty)
    return net


def resting_reduce_qty(open_orders: Iterable, ticker: str, side: str,
                       exclude_ids: Iterable[str] = ()) -> float:
    """Quantity already resting on `side` for `ticker` that could fill.

    Legs of one OCO / bracket group (linked by `parent_id`) can fill only
    once between them, so a group counts its largest leg; unlinked orders
    count in full — the conservative reading when the link is not known.
    """
    skip = {str(i) for i in exclude_ids}
    groups: dict[str, float] = {}
    for o in open_orders or []:
        oid = str(_field(o, "id", "client_order_id") or "")
        if oid in skip:
            continue
        if not same_symbol(str(_field(o, "ticker", "symbol") or ""), ticker):
            continue
        if str(_field(o, "side") or "").lower() != side:
            continue
        key = str(_field(o, "parent_id") or oid or id(o))
        groups[key] = max(groups.get(key, 0.0), abs(float(_field(o, "qty") or 0.0)))
    return sum(groups.values())


def at_broker(broker, ticker: str, side: str, requested: float, *,
              exclude_ids: Iterable[str] = ()) -> ReduceOnly:
    """`check` against the broker's state as it is NOW (positions and open
    orders read fresh, never a caller's snapshot). Any read failure is a
    refusal."""
    try:
        position = signed_position(broker.get_positions(), ticker)
    except Exception as exc:
        return ReduceOnly((side or "").lower(), requested, 0.0, None, None,
                          f"position unreadable ({type(exc).__name__}: {exc}) — nothing sent")
    try:
        resting = resting_reduce_qty(broker.get_open_orders(), ticker,
                                     (side or "").lower(), exclude_ids)
    except Exception as exc:
        return ReduceOnly((side or "").lower(), requested, 0.0, position, None,
                          f"open orders unreadable ({type(exc).__name__}: {exc}) — nothing sent")
    return check(side, requested, position, resting)
