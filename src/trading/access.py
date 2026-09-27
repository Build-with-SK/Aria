"""
src/trading/access.py
=====================
WHO GETS WHICH BROKER, DECIDED BY THE EXECUTION MODE — AT CALL TIME.

    mode              submit                             cancel
    RESEARCH          refused (no orders at all)         paper account only
    PAPER             paper account only                 paper account only
    LIVE_AUTONOMOUS   live allowed, under hard limits    allowed

`ModeGatedBroker` extends the existing `PaperOnlyBroker` rather than replacing
it, so everything that gate already guarantees still holds: reads pass, the raw
client is never handed out, unknown methods are treated as writes. On top, every
write asks `modes.effective_mode()` AT THE MOMENT OF THE CALL — a desk holding a
cached order manager cannot keep trading after the mode drops to RESEARCH.

CEILINGS PER CALLER
-------------------
  the legacy desk (src/desk)         ceiling PAPER. It predates the hard limits,
                                     the validator and idempotent orders, so it
                                     never reaches real money in any mode.
  the owner's approval queue         ceiling LIVE_AUTONOMOUS. A human-approved
                                     trade may go live ONLY in LIVE_AUTONOMOUS,
                                     and even then passes the hard-limit check.
  the new pipeline (src/trading)     `live_adapter()` / `paper_adapter()` below.

Before this module, the approval path used the raw broker: an approved trade on
a live account executed in any configuration. It no longer can, unless the mode
is LIVE_AUTONOMOUS — and this deployment does not enable that.
"""
from __future__ import annotations

import logging
from typing import Optional

from src.execution.broker_base import OrderRequest, OrderResult, OrderStatus
from src.execution.live_guard import READ_METHODS, PaperOnlyBroker, _WRAPPED

from . import modes
from .modes import ExecutionMode

logger = logging.getLogger(__name__)

CANCEL_METHODS = {"cancel_order", "cancel_all_orders"}


class LiveAccessDenied(RuntimeError):
    pass


class ModeGatedBroker(PaperOnlyBroker):
    def __init__(self, broker, *, ceiling: ExecutionMode = ExecutionMode.PAPER,
                 queue_blocked: bool = True):
        super().__init__(broker, queue_blocked=queue_blocked)
        self.ceiling = ExecutionMode(ceiling).value

    def allowed_mode(self) -> ExecutionMode:
        return modes.lower(modes.effective_mode(), ExecutionMode(self.ceiling))

    def __getattr__(self, item):
        target = super().__getattr__(item)
        if item in READ_METHODS or not callable(target):
            return target

        def mode_gated(*args, **kwargs):
            mode = self.allowed_mode()
            if mode == ExecutionMode.RESEARCH:
                if item in CANCEL_METHODS:
                    return target(*args, **kwargs)        # paper-gated cancel
                return self._refuse_mode(item, "RESEARCH mode — no orders are placed", args)
            if mode == ExecutionMode.PAPER:
                return target(*args, **kwargs)            # PaperOnlyBroker's gate
            # LIVE_AUTONOMOUS, and this caller's ceiling allows it
            if not modes.can_access_live_broker():
                return self._refuse_mode(item, "live access is not currently permitted", args)
            if item == "submit_order" and args and isinstance(args[0], OrderRequest):
                problems = legacy_order_violations(args[0], _WRAPPED[self])
                if problems:
                    return self._refuse_mode(item, "hard limits: " + "; ".join(problems), args)
            return getattr(_WRAPPED[self], item)(*args, **kwargs)
        return mode_gated

    def _refuse_mode(self, item: str, why: str, args):
        logger.error("mode gate refused %s: %s", item, why)
        if item == "submit_order":
            return OrderResult(broker_order_id="", status=OrderStatus.REJECTED,
                               error_message=f"{why}")
        if item in CANCEL_METHODS:
            return False
        return None


def legacy_order_violations(req: OrderRequest, broker) -> list[str]:
    """Hard-limit check for an order arriving from the legacy approval path in
    LIVE_AUTONOMOUS. The new pipeline runs the full risk engine and validator;
    this is the minimum a human-approved order must still clear."""
    from . import limits as _limits
    lim = _limits.load()
    problems = []
    try:
        q = broker.get_quote(req.ticker) or {}
        price = req.limit_price or q.get("ask") or q.get("mid")
        acct = broker.get_account()
        equity = acct.portfolio_value or acct.cash
    except Exception as exc:
        return [f"could not read account or quote ({exc}) — refusing"]
    if not price or price <= 0:
        return ["no price available — refusing"]
    notional = req.qty * price
    if notional > lim.max_order_value:
        problems.append(f"order value {notional:.2f} > max_order_value {lim.max_order_value}")
    if equity and notional > equity * lim.max_position_pct / 100:
        problems.append(f"order is {notional / equity * 100:.1f}% of equity "
                        f"> max_position_pct {lim.max_position_pct}")
    return problems


# ── for the new pipeline ─────────────────────────────────────────────────────

def paper_adapter(raw_broker=None):
    """A paper-only adapter over the configured broker (Alpaca by default)."""
    from .broker import LegacyBrokerAdapter
    if modes.effective_mode() == ExecutionMode.RESEARCH:
        raise LiveAccessDenied("RESEARCH mode: no broker adapter for orders")
    if raw_broker is None:
        from src.execution.alpaca_broker import AlpacaBroker
        raw_broker = AlpacaBroker()
    return LegacyBrokerAdapter(ModeGatedBroker(raw_broker, ceiling=ExecutionMode.PAPER,
                                               queue_blocked=False), is_live=False)


def live_adapter(raw_broker=None):
    """THE ONLY FUNCTION THAT RETURNS A LIVE-CAPABLE ADAPTER.

    Refuses unless the effective mode is LIVE_AUTONOMOUS — which itself needs
    the out-of-process switch — and the kill switch is clear. Even the adapter
    it returns re-checks the mode on every call, so obtaining it once is not a
    standing permission.
    """
    from .broker import LegacyBrokerAdapter
    if not modes.can_access_live_broker():
        raise LiveAccessDenied(
            f"live broker access requires effective mode LIVE_AUTONOMOUS and a clear "
            f"kill switch (effective mode is {modes.effective_mode().value})")
    if raw_broker is None:
        from src.execution.alpaca_broker import AlpacaBroker
        raw_broker = AlpacaBroker()
    return LegacyBrokerAdapter(
        ModeGatedBroker(raw_broker, ceiling=ExecutionMode.LIVE_AUTONOMOUS,
                        queue_blocked=False),
        is_live=not bool(getattr(raw_broker, "paper", True)))


def gate_for_desk(raw_broker) -> ModeGatedBroker:
    return ModeGatedBroker(raw_broker, ceiling=ExecutionMode.PAPER, queue_blocked=True)


def gate_for_approvals(raw_broker) -> Optional[ModeGatedBroker]:
    if raw_broker is None:
        return None
    return ModeGatedBroker(raw_broker, ceiling=ExecutionMode.LIVE_AUTONOMOUS,
                           queue_blocked=False)
