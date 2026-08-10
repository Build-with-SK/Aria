"""
src/execution/live_guard.py
===========================
One place where "this account is real money" stops an autonomous order.

WHY THIS EXISTS AS A WRAPPER AND NOT AS A CHECK
-----------------------------------------------
The safety contract said live accounts always route to human approval, and the
entry path did check. But the desk reaches the broker from more than one place:
it heals missing brackets, it replaces a stop when the trailing rule moves it,
and it cancels stale orders — each of those places and cancels real orders, and
none of them asked whether the account was paper. Every one of those is a
"modify or cancel an order" that a live account was supposed to never see
without a human.

Adding the same three-line check to each call site would have closed today's
holes and left tomorrow's open, because the check is a thing you must remember.
So the desk does not get a broker. It gets this wrapper, and the refusal lives
at the boundary every order must cross, whichever method the caller reaches for
— including methods nobody has written yet.

WHAT COUNTS AS CONFIRMED PAPER
------------------------------
Three checks, all required:

  1. the paper env var for THIS broker (ALPACA_PAPER / IBKR_PAPER) is exactly
     "true" — the operator's stated intent,
  2. the broker object reports paper=True — the adapter's own view,
  3. where orders will actually be sent is a paper destination: Alpaca's
     resolved base URL, or IBKR's TCP port.

Be precise about how independent these are, because an earlier version of this
comment was not. In today's `AlpacaBroker`, (1), (2) and (3) all descend from
`ALPACA_PAPER`: the flag sets `paper=` on the client, which selects the base
URL. So for Alpaca these are one fact checked three ways, and they will not
catch live API keys used with ALPACA_PAPER=true — that fails at Alpaca's own
authentication, which is luck rather than this gate.

What (3) does buy is real but narrower than "independent confirmation": it
catches a broker object whose flag and destination disagree — a future adapter
that takes an explicit base URL, a mislabelled test double, a client
reconfigured after construction — and, for IBKR, it reads the port, which is
genuinely a separate fact from IBKR_PAPER and is the only thing that
distinguishes the live gateway from the paper one.

When (3) cannot be determined it abstains rather than confirming, and
`endpoint_checked: False` is reported so that "nobody could tell" is
distinguishable from "the endpoint says paper".

WHAT REFUSAL LOOKS LIKE
-----------------------
Never silence. A blocked entry or protective order is pushed into the approval
queue so the human sees the thing the desk wanted to do; a blocked cancel is
logged as an error, because a cancel has nothing to queue. Refusing to place a
protective stop on a live position is not obviously the safe direction — an
unprotected position is a real risk — so the human is told, loudly, rather than
the desk quietly doing it for them on real money.
"""
from __future__ import annotations

import logging
import os

from .broker_base import OrderRequest, OrderResult, OrderStatus

logger = logging.getLogger(__name__)

# THE ALLOWLIST IS THE READS, NOT THE WRITES.
#
# This started as a list of write methods, with everything else passing through
# untouched. An independent review named the consequence: a future adapter
# method called `modify_order` or `flatten` would sail straight past a
# hand-maintained list of writes, and the name-prefix test meant to catch that
# was guessing at names too. Two of the seven entries did not even exist on
# either adapter, which is what a list maintained by hand looks like.
#
# Inverted, the default is refusal. Reads are a small, knowable, slow-changing
# set; anything not on it — including a method nobody has written yet — is
# treated as a write and has to pass the gate. Adding a read method to a broker
# means adding it here, and forgetting costs a loud refusal rather than a
# silent live order.
READ_METHODS = frozenset({
    "name", "paper", "is_connected", "supports_asset", "disconnect",
    "get_account", "get_positions", "get_position", "get_quote",
    "get_order_status", "get_open_orders",
    # Reads the fill history to recover a realised exit price. Missing it was
    # not a harmless refusal: position_manager calls this inside a broad
    # try/except, so the refusal was swallowed and the code fell through to an
    # ESTIMATED exit price, writing invented P&L into the closed-trades ledger
    # that the track record and the teacher's lessons are computed from.
    "last_closed_fill",
    # Declarations the gate itself reads.
    "PAPER_ENV_VAR", "endpoint_is_paper",
})

# Writes that actually exist on an adapter. Not load-bearing for the gate,
# which refuses anything outside READ_METHODS — its job is to keep the
# classification test honest.
#
# It used to carry four entries (cancel_all_orders, close_position,
# close_all_positions, replace_order) that existed on neither adapter. That is
# not harmless decoration: the classification test passes if a member is in
# EITHER set, so an aspirational write list makes it easy to file a new READ
# here by mistake and blind the desk — which is exactly what happened to
# last_closed_fill. Every entry must be a real method.
WRITE_METHODS = frozenset({
    "submit_order",
    "submit_oco_exit",
    "cancel_order",
})


class LiveAccountBlocked(RuntimeError):
    """Raised when autonomous code reaches for a live account."""


# The wrapped brokers live HERE, not on the wrapper instances.
#
# `self._broker` was a public unwrap; name-mangling it to `_WRAPPED[self]` made
# it `wrapper._PaperOnlyBroker__broker`, which is the same door with a longer
# handle — and it still showed up in `vars(wrapper)`. Holding the broker in a
# side table keyed by the wrapper means it is not an attribute of the wrapper
# at all: there is nothing in `__dict__` to find, and the garbage collector
# still cleans up because the keys are weak.
#
# This is containment, not a security boundary against code running in this
# process — anything in-process can import this module and read the table. The
# point is that the desk cannot reach a live broker by ACCIDENT, and that no
# short idiom exists to do it on purpose.
import weakref                                              # noqa: E402

_WRAPPED: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


# IBKR's documented ports. Everything outside BOTH sets is unknown, and unknown
# abstains — a Docker port map or an SSH tunnel in front of a live gateway must
# not be read as "not on the live list, therefore paper".
IBKR_LIVE_PORTS = {7496, 4001}
IBKR_PAPER_PORTS = {7497, 4002}


def env_paper_confirmed(broker=None) -> bool:
    """The operator's stated intent, from the env var THIS broker declares.

    Each adapter names its own variable via `PAPER_ENV_VAR`. Dispatching on a
    substring of the broker's name ("ibkr" in name…) worked for the two
    adapters that exist and put the next one straight back into the bug it was
    written to fix: a third broker fell through to ALPACA_PAPER, i.e. it was
    graded on a setting belonging to a different company's account.

    An adapter that declares nothing fails CLOSED. Exact string match too:
    "True", "1" and "yes" are refused, because a gate that accepts a typo
    accepts a typo as authorisation.
    """
    var = getattr(broker, "PAPER_ENV_VAR", None)
    if not var:
        logger.error(
            "live gate: %s declares no PAPER_ENV_VAR, so its paper status "
            "cannot be confirmed from the environment — refusing.",
            getattr(broker, "name", type(broker).__name__))
        return False
    return os.environ.get(var, "") == "true"


def endpoint_is_paper(broker) -> bool | None:
    """True/False from where the broker will ACTUALLY send orders, None if that
    cannot be determined.

    The only check that looks past the flags. Each adapter answers for itself
    through `endpoint_is_paper()`; this function only knows how to fall back
    for an alpaca-py style client, and abstains for anything it does not
    recognise rather than guessing.
    """
    if broker is None:
        return None

    own = getattr(broker, "endpoint_is_paper", None)
    if callable(own):
        try:
            return own()
        except Exception as e:                              # pragma: no cover
            logger.warning("endpoint_is_paper() raised on %s: %s",
                           getattr(broker, "name", "?"), e)
            return None

    client = getattr(broker, "_client", None)
    if client is None:
        return None
    raw = getattr(client, "_base_url", None)
    if raw is None:
        return None
    text = str(getattr(raw, "value", raw)).lower()
    if "paper" in text:
        return True
    if "live" in text or "api.alpaca.markets" in text:
        return False
    return None


def paper_status(broker) -> dict:                       # noqa: C901 - documented
    """See below. Defined before PaperOnlyBroker, so the unwrap is by duck type."""
    # A wrapped broker answers for itself: reaching through the wrapper for
    # `_client` is exactly what the wrapper now refuses, so asking it directly
    # is both correct and the only thing that works.
    if hasattr(broker, "_paper_status_of_wrapped"):
        return broker._paper_status_of_wrapped()
    return _paper_status(broker)


def _paper_status(broker) -> dict:
    """Every input to the decision, reportable — a gate whose reasoning cannot
    be read is a gate nobody can audit.

    `endpoint_checked` is reported separately from `endpoint_is_paper` because
    "the endpoint says paper" and "nobody could tell" are different facts, and
    collapsing them is how an unrecognised adapter came to look like a
    confirmed paper account.
    """
    env = env_paper_confirmed(broker)
    flag = bool(getattr(broker, "paper", False)) if broker is not None else False
    endpoint = endpoint_is_paper(broker) if broker is not None else None
    return {
        "env_paper": env,
        "broker_paper_flag": flag,
        "endpoint_is_paper": endpoint,
        "endpoint_checked": endpoint is not None,
        "confirmed_paper": bool(env and flag and endpoint is not False),
    }


def paper_confirmed(broker) -> bool:
    return paper_status(broker)["confirmed_paper"]


class PaperOnlyBroker:
    """A broker that reads like the real one and refuses to write on live money.

    Reads (positions, account, quotes, order status) pass straight through —
    the desk still needs to see a live account to manage what is already there.
    Writes go through the gate.
    """

    def __init__(self, broker, *, queue_blocked: bool = True):
        _WRAPPED[self] = broker
        self._queue_blocked = queue_blocked

    # -- identity ---------------------------------------------------------
    @property
    def name(self) -> str:
        return getattr(_WRAPPED[self], "name", "unknown")

    @property
    def paper(self) -> bool:
        return bool(getattr(_WRAPPED[self], "paper", False))

    def status(self) -> dict:
        """The gate's reasoning, for reporting. Callers that want to know about
        the underlying account ask this rather than reaching for the broker —
        the public `wrapped` accessor that used to exist was an unwrapping
        escape hatch sitting in the middle of the thing it was guarding."""
        return _paper_status(_WRAPPED[self])

    def _paper_status_of_wrapped(self) -> dict:
        """Hook for module-level paper_status(), so `paper_confirmed(wrapper)`
        asks the account rather than trying to read `_client` through a gate
        that refuses to hand out client objects."""
        return _paper_status(_WRAPPED[self])

    # -- the gate ---------------------------------------------------------
    def _refuse(self, method: str, *args, **kwargs):
        st = paper_status(_WRAPPED[self])
        detail = (f"{method} refused: autonomous execution is paper-only "
                  f"(env_paper={st['env_paper']}, "
                  f"broker_paper={st['broker_paper_flag']}, "
                  f"endpoint_is_paper={st['endpoint_is_paper']}). "
                  f"Real money goes through the approval queue.")
        logger.error(detail)

        if method == "submit_order" and args and isinstance(args[0], OrderRequest):
            if self._queue_blocked:
                self._queue_for_human(args[0], detail)
            return OrderResult(broker_order_id="", status=OrderStatus.REJECTED,
                               error_message=detail)
        if method in ("cancel_order", "cancel_all_orders", "close_position",
                      "close_all_positions", "replace_order"):
            return False
        if method == "submit_oco_exit":
            return None
        raise LiveAccountBlocked(detail)

    def _queue_for_human(self, req: OrderRequest, why: str):
        """The desk wanted to do this; the human decides instead."""
        try:
            from .approval_queue import ApprovalQueue
            ApprovalQueue().push(
                req,
                confidence="",
                bull_case="",
                bear_case="",
                invalidation=f"Blocked by the live-account gate. {why}",
                current_price=0.0,
                broker="alpaca",
            )
        except Exception as e:                                # pragma: no cover
            logger.error("could not queue the blocked order for approval: %s", e)

    def __getattr__(self, item):
        # Reached only for attributes this class does not define itself.
        try:
            broker = _WRAPPED[self]
        except KeyError:            # half-constructed (unpickling, __new__)
            raise AttributeError(item) from None
        target = getattr(broker, item)

        if item in READ_METHODS:
            return target

        # "Non-callables cannot place an order" was the assumption here, and it
        # is wrong in the one way that matters: `_client` is an alpaca-py
        # TradingClient and `_ib` is an ib_insync.IB — objects, not callables,
        # with live order methods hanging off them. Passing them through handed
        # any caller `mgr._alpaca._client.submit_order(...)`, a complete bypass
        # of the gate they were standing behind.
        #
        # So only INERT values pass: the primitives and plain containers that a
        # status report is made of. Anything else — any object with methods of
        # its own — is refused, because it is a door.
        if not callable(target) and isinstance(
                target, (str, bytes, int, float, bool, type(None),
                         list, tuple, dict, set, frozenset)):
            return target

        if not callable(target):
            st = paper_status(_WRAPPED[self])
            raise LiveAccountBlocked(
                f"access to {item!r} is refused: it is a broker client object, "
                f"and handing it out would bypass this gate entirely "
                f"(confirmed_paper={st['confirmed_paper']}). Use the wrapper's "
                f"own methods.")

        # Deliberately does NOT close over `target`. A closure over the bound
        # method puts the live broker's own submit_order one
        # `.__closure__[i].cell_contents` away — a review demonstrated exactly
        # that call. Re-looking it up per invocation costs a dictionary read
        # and leaves nothing to harvest.
        def guarded(*args, **kwargs):
            live = _WRAPPED[self]
            if paper_confirmed(live):
                return getattr(live, item)(*args, **kwargs)
            return self._refuse(item, *args, **kwargs)

        return guarded
