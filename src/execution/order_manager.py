"""
src/execution/order_manager.py
================================
Orchestrates approved trades to the correct broker.
Called by the FastAPI endpoint after user approval.

Flow:
  1. User clicks APPROVE in UI
  2. API calls order_manager.execute(trade_id)
  3. OrderManager looks up the trade in ApprovalQueue
  4. Routes to Alpaca (equities/crypto) or IBKR (futures/forex/options)
  5. Submits order, updates queue with fill details
  6. Optionally places bracket stop-loss + take-profit orders
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .approval_queue import ApprovalQueue, PendingTrade
from .broker_base import (
    AssetClass, OrderRefused, OrderRequest, OrderResult, OrderSide,
    OrderStatus, OrderType, BrokerBase
)

logger = logging.getLogger(__name__)

#: Append-only record of every reconciliation lookup and what it found.
RECONCILE_LOG = Path(__file__).parent.parent.parent / "data" / "execution" / "order_reconciliation.jsonl"


def _audit(row: dict) -> None:
    """One reconciliation lookup, appended. A failure to write is logged,
    never raised: the audit trail must not decide the order state."""
    try:
        RECONCILE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RECONCILE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                **row}, default=str) + "\n")
    except Exception as e:
        logger.error(f"order reconciliation audit not written: {e} — {row}")


def queue_client_order_id(trade: PendingTrade) -> str:
    """The broker idempotency key for one approval-queue trade.

    Derived from the trade's own id and creation time, so the same queued
    trade always sends the same key: the broker refuses a second order with
    it, and after a timeout the order can be looked up by it. Without one, a
    submit that timed out after Alpaca accepted the order was recorded
    REJECTED — and a reflex playbook that read "failed" stayed armed for a
    second order (S1.3)."""
    h = hashlib.sha256(f"approval_queue|{trade.id}|{trade.created_at}".encode()).hexdigest()[:24]
    return f"aria-q-{h}"


class OrderManager:
    # After a submit whose outcome is unknown, how many times to ask the broker
    # for the order by its client id, and how long to wait before each ask. The
    # wait gives a request still in flight time to land: "not found" one
    # millisecond after a timeout says little.
    SETTLE_LOOKUPS = 3
    SETTLE_WAIT_S = 2.0

    def __init__(self, alpaca: Optional[BrokerBase] = None, ibkr: Optional[BrokerBase] = None,
                 *, market_data=None, portfolio_limits=None):
        self._alpaca = alpaca
        self._ibkr   = ibkr
        self._queue  = ApprovalQueue()
        # Portfolio risk at the moment of sending (src/risk/pretrade). Without
        # `market_data` it measures exposure and concentration from the
        # broker's own account and holds unknown-risk positions to a fraction
        # of the name limit; it never fetches history on the send path unless
        # given a source.
        self.market_data = market_data
        self.portfolio_limits = portfolio_limits

    def _broker_for(self, trade: PendingTrade) -> Optional[BrokerBase]:
        if trade.broker == "ibkr":
            return self._ibkr
        if trade.broker == "alpaca":
            return self._alpaca
        # Auto: IBKR for futures/forex, Alpaca for everything else
        ac = trade.asset_class
        if ac in ("futures", "forex", "option"):
            return self._ibkr or self._alpaca
        return self._alpaca or self._ibkr

    def _build_order_request(self, trade: PendingTrade) -> OrderRequest:
        return OrderRequest(
            ticker=trade.ticker,
            side=OrderSide(trade.side),
            qty=trade.qty,
            order_type=OrderType(trade.order_type),
            limit_price=trade.limit_price,
            stop_price=trade.stop_price,
            time_in_force=trade.time_in_force,
            asset_class=AssetClass(trade.asset_class),
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            signal_score=trade.signal_score,
            situation=trade.situation,
            thesis_summary=trade.thesis_summary,
            client_order_id=queue_client_order_id(trade),
        )

    @staticmethod
    def session_refusal(ticker: str, asset_class: str,
                        now: Optional[datetime] = None,
                        halted: Optional[bool] = None) -> Optional[str]:
        """None when the instrument is in continuous trading at its own venue
        now (src/markets/sessions); otherwise why not. UNKNOWN, a provider
        failure and any error here are refusals — never a pass. `halted` is a
        data feed's halt flag when the caller has one; a schedule cannot know."""
        try:
            from src.markets.sessions import session
            info = session(ticker, now, asset_class=asset_class, halted=halted)
        except Exception as e:
            return f"session check failed ({type(e).__name__}: {e}) — unknown is never open"
        if info.tradable:
            return None
        why = "; ".join(r for r in info.reasons if r) or "not in continuous trading"
        nxt = f"; next open {info.next_open}" if info.next_open else ""
        return f"{ticker} is {info.state.value} at {info.venue or 'an unknown venue'} ({why}){nxt}"

    def execute(self, trade_id: str, now: Optional[datetime] = None,
                halted: Optional[bool] = None) -> dict:
        """Execute an approved trade. Returns status dict.

        The instrument's session is checked HERE, at the moment of sending,
        for every caller — the owner's approve button, the desk's auto-executor
        and the reflex lane all arrive through this method, and a desk cycle
        that checked the session when it started can finish after the close.
        A closed or UNKNOWN market leaves the trade pending, unsent: a market
        order sent now would be filled blind at the next open.

        A queued EXIT (`reduce_only`) is re-checked against the broker's
        position at the moment of sending and clipped to it, or refused when
        the position has gone — an exit approved late must not open a new
        position. Protective brackets go through place_protective_orders,
        which enforces the same invariant.

        Every order carries a client_order_id (queue_client_order_id), written
        to the queue row with status "submitting" BEFORE the POST, so a cancel
        racing the send, or a crash during it, cannot leave a row that claims
        nothing was sent. After a submit whose outcome is unknown — a timeout,
        a dropped connection, a 5xx — the order is looked up by that id: found,
        it continues as whatever the broker says it is; not found, or the
        lookup fails, it stays UNKNOWN. Absence is not proof: an order accepted
        behind a timeout can become visible later (Codex review of e4f01ad), so
        only the broker's own answer (reconcile_unresolved) or the owner's
        release resolves an UNKNOWN row; execute() never resends it.

        While an order for an instrument is unresolved (submitting / unknown),
        no other order that could add to it is sent, from any lane — owner,
        desk or reflex — across restarts, because the state is the queue file.
        Exits (reduce-only) still go: they are clipped to the broker's position
        and cannot add exposure. The guard and the settlement only read; every
        gate above still decides what is sent.
        """
        trade = self._queue.get_by_id(trade_id)
        if not trade:
            return {"ok": False, "error": f"Trade {trade_id} not found"}
        if trade.status not in ("pending", "approved"):
            return {"ok": False, "error": f"Trade {trade_id} status is '{trade.status}', not approvable"}

        refusal = self.session_refusal(trade.ticker, trade.asset_class, now, halted)
        if refusal:
            logger.warning(f"Trade {trade_id} not sent — {refusal}")
            return {"ok": False, "session_closed": True,
                    "error": f"market not in continuous trading: {refusal}. Nothing was "
                             f"sent; the trade stays {trade.status}."}

        # Mark approved
        self._queue.approve(trade_id)

        broker = self._broker_for(trade)
        if not broker:
            self._queue.reject(trade_id, "No broker connected")
            return {"ok": False, "error": "No broker connected for this asset class"}

        if not broker.is_connected():
            self._queue.reject(trade_id, f"{broker.name} not connected")
            return {"ok": False, "error": f"{broker.name} is not connected"}

        req    = self._build_order_request(trade)
        if not getattr(trade, "reduce_only", False):
            blockers = self.unresolved_for(trade)
            if blockers:
                held = ", ".join(f"{b.id} ({b.status}, client id {b.client_order_id or 'n/a'})"
                                 for b in blockers)
                why = (f"an earlier order for {trade.ticker} is unresolved — {held}. It may "
                       f"be at the broker; settle it (reconcile, or the owner's release) "
                       f"before sending another. Nothing was sent.")
                self._queue.reject(trade_id, why)
                logger.warning(f"Trade {trade_id} blocked: {why}")
                return {"ok": False, "blocked": True, "unresolved": [b.id for b in blockers],
                        "error": why}
        ro = None
        risk = None
        if getattr(trade, "reduce_only", False):
            from src.execution import reduce_only
            ro = reduce_only.at_broker(broker, trade.ticker, trade.side, trade.qty)
            if not ro.ok:
                self._queue.reject(trade_id, f"reduce-only exit refused: {ro.reason}")
                return {"ok": False, "reduce_only": ro.to_dict(),
                        "error": f"reduce-only exit not sent: {ro.reason}"}
            req.qty = ro.qty
            req.reduce_only = True
        else:
            # PORTFOLIO RISK, fresh, at the moment of sending: the book as the
            # broker holds it NOW, not as it was when the trade was queued.
            from src.risk.pretrade import Verdict
            risk = self.portfolio_risk(trade, broker, now)
            if not risk.ok:
                why = "; ".join(risk.reasons) or risk.verdict.value
                if risk.verdict == Verdict.REJECT:
                    self._queue.reject(trade_id, f"portfolio risk: {why}")
                return {"ok": False, "risk": risk.to_dict(),
                        "risk_deferred": risk.verdict == Verdict.DEFER,
                        "error": f"portfolio risk {risk.verdict.value}: {why}. Nothing was sent."}
            if risk.approved_qty < req.qty:
                logger.warning(f"Trade {trade_id} reduced by portfolio risk "
                               f"{req.qty:g} -> {risk.approved_qty:g} ({risk.binding})")
                req.qty = risk.approved_qty

        # The send. Claim the row first: from here it is "submitting", with its
        # client id on disk, and cannot be cancelled as never sent. The claim
        # re-checks, under the queue lock, that no other order for this
        # instrument is in flight — the guard above cannot see one claimed a
        # moment ago by another lane.
        claimed, why = self._queue.claim_for_submit(trade_id, req.client_order_id,
                                                    exclusive=not req.reduce_only)
        if claimed is None:
            blocked = "unresolved" in why
            if blocked:
                self._queue.reject(trade_id, f"{why}. Nothing was sent.")
            return {"ok": False, "blocked": blocked,
                    "error": f"Trade {trade_id} not sent: {why}. Nothing was sent."}
        try:
            result = broker.submit_order(req)
        except Exception as exc:
            # The adapter may raise after writing the request to its socket.
            # The durable claim must survive; only a broker lookup can settle it.
            result = OrderResult("", OrderStatus.UNKNOWN,
                                 error_message=f"{type(exc).__name__}: {exc}")
        settled = None
        if result.status == OrderStatus.UNKNOWN:
            result, settled = self._settle_unknown(broker, req, result)

        if result.status in (OrderStatus.SUBMITTED, OrderStatus.FILLED, OrderStatus.PARTIAL):
            self._queue.mark_executed(
                trade_id,
                broker_order_id=result.broker_order_id,
                fill_price=result.avg_fill_price,
                client_order_id=req.client_order_id,
                order_status=result.status.value,
                filled_qty=result.filled_qty,
            )
            logger.info(
                f"Order executed [{trade_id}]: {trade.side.upper()} {trade.qty} {trade.ticker} "
                f"via {broker.name} → order_id={result.broker_order_id}"
            )

            # Alpaca market orders come back "accepted", not "filled" — poll
            # until the fill confirms, THEN place the protective brackets.
            # A position must never sit naked because the ack was async.
            if result.status != OrderStatus.FILLED and (trade.stop_loss or trade.take_profit):
                result = self._await_fill(broker, result)

            if result.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED,
                                 OrderStatus.EXPIRED):
                # Acknowledged, then ended by the broker with nothing filled
                # (a part-fill reads PARTIAL). Not a success, and not "executed".
                why = (f"the broker acknowledged order {result.broker_order_id}, then "
                       f"{result.status.value} it with nothing filled"
                       + (f": {result.error_message}" if result.error_message else ""))
                self._queue.mark_ended_unfilled(trade_id, result.status.value, why)
                logger.warning(f"Trade {trade_id}: {why}")
                return {"ok": False, "ended_after_ack": True, "broker": broker.name,
                        "status": result.status.value,
                        "broker_order_id": result.broker_order_id,
                        "client_order_id": req.client_order_id, "settled": settled,
                        "filled_qty": 0.0, "fill_price": None, "error": why}

            sl_result = None
            tp_result = None
            protection = None
            if result.status == OrderStatus.FILLED and (trade.stop_loss or trade.take_profit):
                # One path for every protective order: reduce-only against the
                # position as it is now, and never a stop and a target as two
                # independent full-size orders (both could fill and reverse it).
                protection = self.place_protective_orders(
                    trade.ticker, result.filled_qty or req.qty, trade.side,
                    trade.stop_loss, trade.take_profit,
                    asset_class=trade.asset_class, broker=broker)
                sl_result = protection.get("stop_loss_order")
                tp_result = protection.get("take_profit_order")
                if (trade.stop_loss and not sl_result) or (trade.take_profit and not tp_result):
                    logger.error(f"BRACKETS INCOMPLETE for {trade.ticker} — "
                                 f"reconciliation must heal on next desk tick")
            elif trade.stop_loss or trade.take_profit:
                logger.warning(f"{trade.ticker} entry not confirmed filled within poll "
                               f"window — brackets deferred to reconciliation")

            # Refresh the queue with what the broker last said: status, real
            # fill price and filled quantity.
            self._queue.mark_executed(trade_id,
                                      broker_order_id=result.broker_order_id,
                                      fill_price=result.avg_fill_price,
                                      client_order_id=req.client_order_id,
                                      order_status=result.status.value,
                                      filled_qty=result.filled_qty)

            return {
                "ok": True,
                "broker": broker.name,
                "broker_order_id": result.broker_order_id,
                "client_order_id": req.client_order_id,
                "settled": settled,
                "status": result.status.value,
                "fill_price": result.avg_fill_price,
                "filled_qty": result.filled_qty,
                "stop_loss_order": sl_result,
                "take_profit_order": tp_result,
                "brackets_pending": bool(
                    (trade.stop_loss and not sl_result) or
                    (trade.take_profit and not tp_result)),
                "protection": protection,
                "reduce_only": ro.to_dict() if ro else None,
                "risk": risk.to_dict() if risk else None,
                "sent_qty": req.qty,
            }
        elif result.status == OrderStatus.UNKNOWN:
            # The broker may hold this order. Not rejected (that invites a
            # retry, and a retry is how one trade becomes two) and not
            # executed (nothing confirmed it): the queue says "unknown",
            # execute() refuses it, and it blocks this instrument until the
            # broker's answer is found or the owner releases it.
            why = (f"order state UNKNOWN after {result.error_message or 'an unanswered submit'}; "
                   f"{settled}. It may have been placed — {trade.ticker} is blocked until "
                   f"client order id {req.client_order_id} is found at the broker or the "
                   f"owner releases it. Not resent.")
            self._queue.mark_unknown(trade_id, why, client_order_id=req.client_order_id)
            logger.critical(f"Trade {trade_id}: {why}")
            return {
                "ok": False,
                "unknown": True,
                "broker": broker.name,
                "status": OrderStatus.UNKNOWN.value,
                "client_order_id": req.client_order_id,
                "settled": settled,
                "sent_qty": req.qty,
                "error": why,
            }
        else:
            why = result.error_message or (
                f"Order rejected with status {result.status.value}"
                + (f" ({settled})" if settled else ""))
            self._queue.reject(trade_id, why, client_order_id=req.client_order_id,
                               after_send=True)
            return {
                "ok": False,
                "broker": broker.name,
                "status": result.status.value,
                "client_order_id": req.client_order_id,
                "settled": settled,
                "error": why,
            }

    def _settle_unknown(self, broker: BrokerBase, req: OrderRequest,
                        first: OrderResult) -> tuple[OrderResult, str]:
        """Try to settle a submit whose outcome is unknown by asking the broker
        for the order by its client id. Only reads; never sends anything.

          found        → the broker's own answer (filled, working, rejected…)
          not found    → stays UNKNOWN
          can't ask    → stays UNKNOWN

        "Not found" is NOT "never placed": a request accepted behind a timeout
        can become visible after the last lookup. Codex's review of e4f01ad
        reproduced exactly that — three 404s, the order visible on the fourth
        look, and a second order under a new id. More lookups or a longer wait
        cannot prove absence, so only a found answer settles here; the rest is
        left to reconcile_unresolved() and the owner. Returns the result and a
        short account of what was tried."""
        cid = req.client_order_id
        lookup = getattr(broker, "get_order_by_client_id", None)
        if not cid or lookup is None:
            return first, (f"{getattr(broker, 'name', 'the broker')} cannot look orders "
                           f"up by client id, so it could not be settled")
        misses, errors = 0, []
        for _ in range(max(1, int(self.SETTLE_LOOKUPS))):
            if self.SETTLE_WAIT_S > 0:
                time.sleep(self.SETTLE_WAIT_S)
            try:
                found = lookup(cid)
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")
                continue
            if found is not None:
                logger.warning(f"order {cid} found after an unknown submit: "
                               f"{found.status.value} (broker id {found.broker_order_id})")
                return found, f"found by client id as {found.status.value}"
            misses += 1
        tried = []
        if misses:
            tried.append(f"not found in {misses} lookup(s) — absence is not proof it was "
                         f"never placed")
        if errors:
            tried.append("lookup failed (" + "; ".join(errors[-2:]) + ")")
        return first, "; ".join(tried)

    def unresolved_for(self, trade) -> list:
        """Other queue rows for the same broker and instrument whose order may
        be at the broker without an answer (approval_queue.UNRESOLVED)."""
        from .reduce_only import same_symbol
        return [t for t in self._queue.in_flight()
                if t.id != trade.id and same_symbol(t.ticker, trade.ticker)
                and (t.status in ("submitting", "unknown") or t.situation != "protective_exit")
                and t.broker == trade.broker]

    def reconcile_unresolved(self) -> dict:
        """Look every unresolved queue row (submitting / unknown) up by its
        client id and record the broker's answer. READ-ONLY toward the broker:
        lookups only — no submit, cancel or replace — and the id never changes.

          found working / partial / filled → executed, with the broker's
                                             status, order id, price and qty
          found cancelled / rejected / expired with nothing filled
                                           → rejected, the broker's word
          not found, lookup failed, or no lookup at this broker → unchanged

        A 404 resolves nothing (see _settle_unknown); the owner's release does
        that. Every lookup is appended to RECONCILE_LOG with its result."""
        report: dict = {"checked": 0, "settled": {}, "still_unresolved": [], "errors": {}}
        for t in self._queue.in_flight():
            report["checked"] += 1
            broker = self._broker_for(t)
            lookup = getattr(broker, "get_order_by_client_id", None) if broker else None
            row = {"trade_id": t.id, "ticker": t.ticker, "client_order_id": t.client_order_id,
                   "was": t.status, "broker": getattr(broker, "name", None)}
            found = None
            if not t.client_order_id or lookup is None:
                row["result"] = "cannot look up — unchanged"
            else:
                try:
                    found = lookup(t.client_order_id)
                    if found is None:
                        row["result"] = "not found — not proof; unchanged"
                except Exception as e:
                    row["result"] = "lookup failed — unchanged"
                    row["error"] = f"{type(e).__name__}: {e}"
                    report["errors"][t.id] = row["error"]
            if found is not None:
                row.update(result=found.status.value, broker_order_id=found.broker_order_id,
                           filled_qty=found.filled_qty, fill_price=found.avg_fill_price)
                if found.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIAL,
                                    OrderStatus.FILLED):
                    self._queue.mark_executed(
                        t.id, broker_order_id=found.broker_order_id,
                        fill_price=found.avg_fill_price, client_order_id=t.client_order_id,
                        order_status=found.status.value, filled_qty=found.filled_qty)
                    report["settled"][t.id] = found.status.value
                elif found.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED,
                                      OrderStatus.EXPIRED):
                    self._queue.mark_ended_unfilled(
                        t.id, found.status.value,
                        f"reconciled by client id: the broker {found.status.value} order "
                        f"{found.broker_order_id}; nothing filled")
                    report["settled"][t.id] = found.status.value
            if t.id not in report["settled"]:
                report["still_unresolved"].append(t.id)
            _audit(row)
        return report

    def portfolio_risk(self, trade, broker, now: Optional[datetime] = None):
        """src/risk/pretrade.assess for a queued entry, against the broker's
        account and a fresh quote, with the desk's risk-profile limits.
        Recorded in data/trading/risk_decisions.jsonl either way."""
        from datetime import timezone as _tz
        from src.risk import decisions, pretrade
        from src.trading import account as _account
        at = (now or datetime.now(_tz.utc))
        if at.tzinfo is None:
            at = at.replace(tzinfo=_tz.utc)
        acct = _account.from_legacy_broker(broker, now=at)
        price, price_at, spread = None, None, None
        try:
            q = broker.get_quote(trade.ticker) or {}
            bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
            price = (ask if trade.side == "buy" else bid) or float(q.get("mid") or 0) or None
            ts = q.get("timestamp")
            price_at = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
            if price_at is not None and price_at.tzinfo is None:
                price_at = None                         # an unzoned time is not a time
            if bid > 0 and ask > 0:
                spread = (ask - bid) / ((ask + bid) / 2) * 1e4
        except Exception as e:
            logger.warning(f"portfolio risk: no quote for {trade.ticker}: {e}")
        limits = self.portfolio_limits or pretrade.PortfolioRiskLimits.from_profile(_desk_profile())
        order = pretrade.ProposedOrder(
            symbol=trade.ticker, side=trade.side, qty=float(trade.qty), price=price,
            price_at=price_at, asset_class=trade.asset_class, spread_bps=spread,
            lot=1e-6 if trade.asset_class == "crypto" else 1.0,
            conviction=self._conviction(trade, at))
        verdict = pretrade.assess(order, acct, at=at, limits=limits, data=self.market_data)
        decisions.record(verdict, path="approval_queue",
                         context={"trade_id": trade.id, "broker": getattr(broker, "name", "")})
        return verdict

    @staticmethod
    def _conviction(trade, at) -> Optional[dict]:
        """The prediction behind a desk trade (its debate), as context for the
        risk gate — src/intelligence/conviction. None for a trade that names no
        debate, or when it cannot be built: the gate then decides exactly as it
        did before predictions were attached. It can only ever veto or defer;
        it never enlarges an order."""
        try:
            from src.intelligence.conviction import for_queued_trade
            return for_queued_trade(trade, as_of=at)
        except Exception as e:
            logger.warning(f"conviction unavailable for {getattr(trade, 'id', '?')}: {e}")
            return None

    def _await_fill(self, broker: BrokerBase, result: OrderResult,
                    timeout_s: float = 30.0, poll_s: float = 2.0) -> OrderResult:
        """Poll the broker until the order fills (or the window closes).
        Returns the freshest OrderResult either way."""
        deadline = time.monotonic() + timeout_s
        latest = result
        while time.monotonic() < deadline:
            time.sleep(poll_s)
            try:
                got = broker.get_order_status(result.broker_order_id)
            except Exception:
                continue  # a failed read cannot turn an acknowledged order into a rejection
            if got.status == OrderStatus.UNKNOWN:
                continue        # a failed read says nothing about the order
            latest = got
            if latest.status == OrderStatus.FILLED:
                return latest
            if latest.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED,
                                 OrderStatus.EXPIRED):
                return latest
        return latest

    def place_protective_orders(self, ticker: str, qty: float, entry_side: str,
                                stop: Optional[float], target: Optional[float],
                                asset_class: str = "equity",
                                broker: Optional[BrokerBase] = None) -> dict:
        """Place SL/TP for an already-held position (used by fill flow and by
        the desk's reconciliation pass when brackets are found missing).

        These orders rest at the venue outside any session gate — a stop must
        stand through the night — so they carry the reduce-only invariant
        instead (src/execution/reduce_only): checked against the broker's
        position and resting orders NOW, clipped to what is held, refused
        when that cannot be read. A stop and a target together go out as ONE
        OCO; a broker without OCO gets the stop only, because two independent
        full-size exits can both fill and turn a long into a short."""
        from src.execution import reduce_only
        broker = broker or self._alpaca
        out: dict = {"stop_loss_order": None, "take_profit_order": None}
        if not broker or not broker.is_connected():
            return out
        exit_side = OrderSide.SELL if entry_side in ("buy", "long") else OrderSide.BUY
        ro = reduce_only.at_broker(broker, ticker, exit_side.value, qty)
        out["reduce_only"] = ro.to_dict()
        if not ro.ok:
            logger.warning(f"protective orders for {ticker} not placed — {ro.reason}")
            return out
        qty = ro.qty
        # Equities demand whole-cent prices — sub-penny is rejected (42210000)
        # — and fractional qtys cannot be GTC, so floor to whole shares
        # (the sub-share dust stays managed by the 5-min software tick).
        if asset_class != "crypto":
            stop = round(stop, 2) if stop else stop
            target = round(target, 2) if target else target
            qty = float(int(qty))
            if qty <= 0:
                return out
        if not stop and not target:
            return out
        from . import kill_switch
        if kill_switch.is_engaged():
            out["error"] = "kill switch engaged; no protection submitted"
            return out
        route = "alpaca" if broker is self._alpaca else "ibkr"
        active = [t for t in self._queue.in_flight() if t.situation == "protective_exit"
                  and t.broker == route and reduce_only.same_symbol(t.ticker, ticker)]
        if active:
            out.update(protection_pending=True, client_order_id=active[0].client_order_id)
            return out
        use_oco = bool(stop and target and hasattr(broker, "submit_oco_exit"))
        req = OrderRequest(ticker=ticker, side=exit_side, qty=qty,
                           order_type=OrderType.STOP if stop else OrderType.LIMIT,
                           stop_price=stop, limit_price=target, time_in_force="gtc",
                           asset_class=AssetClass(asset_class), reduce_only=True,
                           situation="protective_exit")
        queued = self._queue.push(req, broker=route)
        req.client_order_id = queue_client_order_id(queued)
        claimed, why = self._queue.claim_for_submit(queued.id, req.client_order_id,
                                                    exclusive=False, protection=True)
        out["client_order_id"] = req.client_order_id
        if claimed is None:
            self._queue.reject(queued.id, why)
            out["protection_pending"] = True
            return out
        # Both protections on one position need ONE OCO order — separate
        # stop + limit sells fight over the same reserved shares (40310000),
        # and at a broker that does not reserve them, both could fill.
        try:
            if use_oco:
                oid = broker.submit_oco_exit(ticker, qty, entry_side, stop, target,
                                             client_order_id=req.client_order_id)
                r = OrderResult(oid or "", OrderStatus.SUBMITTED if oid else OrderStatus.UNKNOWN)
            else:
                if stop and target:
                    out["target_skipped"] = (f"{broker.name} has no OCO: a separate "
                                              "target and stop could both fill")
                r = broker.submit_order(req)
        except OrderRefused as e:
            # Definitely refused (kill switch, mode gate, a 4xx to the one
            # POST): nothing exists, so nothing must block the symbol.
            r = OrderResult("", OrderStatus.REJECTED, error_message=str(e))
        except Exception as e:
            r = OrderResult("", OrderStatus.UNKNOWN, error_message=str(e))
        if r.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIAL, OrderStatus.FILLED):
            self._queue.mark_executed(queued.id, r.broker_order_id, r.avg_fill_price,
                                      client_order_id=req.client_order_id,
                                      order_status=r.status.value, filled_qty=r.filled_qty)
            if stop:
                out["stop_loss_order"] = {"broker_order_id": r.broker_order_id, "stop_price": stop}
            if use_oco or not stop:
                out["take_profit_order"] = {"broker_order_id": r.broker_order_id, "limit_price": target}
        elif r.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED):
            self._queue.mark_ended_unfilled(queued.id, r.status.value, r.error_message)
        else:
            self._queue.mark_unknown(queued.id, r.error_message or "protection acknowledgement lost",
                                     client_order_id=req.client_order_id)
            out.update(unknown=True, protection_pending=True)
            logger.critical("Protection for %s is UNKNOWN; no fallback or blind retry", ticker)
        return out

    def _connected_brokers(self) -> list[BrokerBase]:
        return [b for b in (self._alpaca, self._ibkr) if b and b.is_connected()]

    def open_orders_by_ticker(self, broker: Optional[BrokerBase] = None) -> dict:
        """{ticker: [open order dicts]} — for bracket healing and stale cleanup.

        Single-broker by default, and that is deliberate rather than an
        oversight. Merging every broker's orders into one dict keyed only by
        ticker briefly looked like the fix for "IBKR's open orders are never
        read", and a review pointed out what it actually did: consumers pass
        each order's id straight back to ONE broker's cancel_order, and
        bracket healing concluded an Alpaca position was protected because an
        IBKR stop existed on the same symbol — a naked position produced by a
        safety check.

        So callers that mean "every broker" say so by passing one explicitly in
        a loop (see cancel_stale_orders). IBKR's orders are read there.
        """
        brokers = [broker] if broker is not None else (
            [self._alpaca] if self._alpaca else [])
        grouped: dict = {}
        for b in brokers:
            if not b or not b.is_connected():
                continue
            try:
                for o in b.get_open_orders():
                    grouped.setdefault(o["ticker"], []).append(
                        {**o, "broker": getattr(b, "name", "")})
            except Exception as e:
                logger.error(f"open_orders_by_ticker({getattr(b, 'name', '?')}): {e}")
        return grouped

    def cancel_stale_orders(self, max_age_days: float = 1.0) -> list[dict]:
        """Cancel unfilled orders older than max_age_days for tickers with NO
        open position (i.e. stale entries). Protective GTC stops/targets belong
        to held tickers and are deliberately left alone."""
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        cancelled = []
        for broker in self._connected_brokers():
            held = {p.ticker for p in broker.get_positions()}
            for ticker, orders in self.open_orders_by_ticker(broker).items():
                if ticker in held:
                    continue
                for o in orders:
                    raw = (o.get("submitted_at") or "").replace("Z", "+00:00")
                    try:
                        sub = datetime.fromisoformat(raw).replace(tzinfo=None)
                    except Exception:
                        # An unparseable timestamp means "cannot judge its
                        # age", which is not the same as "not stale". Say so
                        # rather than skipping in silence — a broker adapter
                        # returning the wrong shape here disables the whole
                        # cleanup and looks exactly like having nothing stale.
                        logger.warning(
                            "stale-order check: %s order %s has an unreadable "
                            "submitted_at (%r) — leaving it alone",
                            getattr(broker, "name", "?"), o.get("id"), raw)
                        continue
                    if sub < cutoff and broker.cancel_order(o["id"]):
                        cancelled.append(o)
                        logger.info(f"Cancelled stale order {o['id']} "
                                    f"({o['side']} {o['qty']:g} {ticker})")
        return cancelled

    def get_broker_status(self) -> dict:
        alpaca_connected = self._alpaca.is_connected() if self._alpaca else False
        ibkr_connected   = self._ibkr.is_connected()   if self._ibkr   else False

        result = {
            "alpaca": {
                "connected": alpaca_connected,
                "paper": self._alpaca.paper if self._alpaca else None,
            },
            "ibkr": {
                "connected": ibkr_connected,
                "paper": self._ibkr.paper if self._ibkr else None,
            },
        }

        if alpaca_connected and self._alpaca is not None:
            try:
                acct = self._alpaca.get_account()
                result["alpaca"]["account"] = {
                    "cash": acct.cash,
                    "portfolio_value": acct.portfolio_value,
                    "buying_power": acct.buying_power,
                }
            except Exception:
                pass

        if ibkr_connected and self._ibkr is not None:
            try:
                acct = self._ibkr.get_account()
                result["ibkr"]["account"] = {
                    "cash": acct.cash,
                    "portfolio_value": acct.portfolio_value,
                    "buying_power": acct.buying_power,
                }
            except Exception:
                pass

        return result

    def get_all_positions(self) -> list[dict]:
        positions = []
        if self._alpaca and self._alpaca.is_connected():
            for p in self._alpaca.get_positions():
                positions.append({**p.__dict__, "broker": "Alpaca"})
        if self._ibkr and self._ibkr.is_connected():
            for p in self._ibkr.get_positions():
                positions.append({**p.__dict__, "broker": "IBKR"})
        return positions


def _desk_profile():
    """The desk's configured risk profile; MODERATE if it cannot be read."""
    from src.risk.profiles import get_profile
    try:
        from src.desk.config import load_config
        return get_profile(load_config().get("risk_profile"))
    except Exception:
        return get_profile(None)
