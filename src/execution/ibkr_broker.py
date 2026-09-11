"""
src/execution/ibkr_broker.py
=============================
Interactive Brokers adapter via ib_insync.
Handles: Stocks, Options, Futures, Forex, Crypto.

Requires:
  pip install ib_insync
  TWS or IB Gateway running locally with API enabled on port 7497 (paper) or 7496 (live)

.env keys:
  IBKR_HOST=127.0.0.1
  IBKR_PORT=7497          # 7497 = paper TWS, 7496 = live TWS, 4002 = paper gateway, 4001 = live gateway
  IBKR_CLIENT_ID=1
  IBKR_PAPER=true
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from . import kill_switch
from .broker_base import (
    AssetClass, BrokerBase, AccountInfo, OrderRequest, OrderResult,
    OrderSide, OrderStatus, OrderType, Position
)

logger = logging.getLogger(__name__)

try:
    import ib_insync as ibi
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    logger.warning("ib_insync not installed. Run: pip install ib_insync")


# TWS/Gateway ports. 7496 and 4001 are real money; 7497 and 4002 are paper.
LIVE_PORTS = {7496, 4001}
PAPER_PORTS = {7497, 4002}


def _make_contract(ticker: str, asset_class: AssetClass) -> "ibi.Contract":
    """Build an IBKR Contract object from ticker + asset class."""
    if asset_class == AssetClass.FUTURES:
        # e.g. "ES=F" → ES
        sym = ticker.split("=")[0]
        return ibi.Future(sym, exchange="CME")
    elif asset_class == AssetClass.FOREX:
        # e.g. "EURUSD=X" → EUR.USD
        base = ticker.replace("=X", "").replace("/", "")
        pair = f"{base[:3]}.{base[3:]}" if len(base) == 6 else base
        return ibi.Forex(pair)
    elif asset_class == AssetClass.CRYPTO:
        sym = ticker.replace("-USD", "")
        return ibi.Crypto(sym, "PAXOS", "USD")
    else:
        return ibi.Stock(ticker, "SMART", "USD")


# IBKR's own vocabulary, complete. Anything outside it is treated as "still
# working" — never as filled, never as dead — because both of those conclusions
# make the caller act: one places brackets on a position that may not exist, the
# other abandons an order that is still live at the exchange.
_IBKR_STATUS = {
    "ApiPending":    OrderStatus.SUBMITTED,
    "PendingSubmit": OrderStatus.SUBMITTED,
    "PreSubmitted":  OrderStatus.SUBMITTED,
    "Submitted":     OrderStatus.SUBMITTED,
    "PendingCancel": OrderStatus.SUBMITTED,   # still cancellable ⇒ still live
    "Filled":        OrderStatus.FILLED,
    "Cancelled":     OrderStatus.CANCELLED,
    "ApiCancelled":  OrderStatus.CANCELLED,
    "Inactive":      OrderStatus.REJECTED,
}

def _parse_ibkr_status(status: str, filled: float = 0.0,
                       remaining: float | None = None) -> OrderStatus:
    """Map IBKR's status to ours, using the fill counters as well as the label.

    The label alone is not the state. IBKR reports a partially filled order as
    "Submitted" with filled > 0 — identical to an order that has not traded a
    single share. Collapsing those two into SUBMITTED is what let a half-filled
    position be treated as no position at all, so the fill counters decide
    wherever they disagree with the word.
    """
    mapped = _IBKR_STATUS.get(status)
    if mapped is None:
        if status:
            logger.warning("IBKR status %r is not in the known set — treating "
                           "as still working", status)
        mapped = OrderStatus.SUBMITTED

    if mapped is OrderStatus.FILLED and remaining is not None and remaining > 0:
        # "Filled" while shares remain is a contradiction; believe the counter.
        return OrderStatus.PARTIAL
    if filled > 0 and mapped in (OrderStatus.SUBMITTED, OrderStatus.CANCELLED,
                                 OrderStatus.REJECTED):
        # Cancelled/rejected after a part-fill still leaves an open position.
        # Reporting CANCELLED there tells the caller it owns nothing.
        return OrderStatus.PARTIAL
    return mapped


def _rejection_reason(trade) -> str:
    """IBKR does not put the reason in orderStatus — it arrives as an error
    event and lands in trade.log. Without reading it, every rejection reads as
    a bare 'Inactive'."""
    try:
        for entry in reversed(list(getattr(trade, "log", []) or [])):
            msg = (getattr(entry, "message", "") or "").strip()
            if msg:
                return msg
    except Exception:
        pass
    return ""


def _fills_total(trade) -> tuple[float, float]:
    """(shares, average price) summed from the actual execution events.

    orderStatus is a running summary the API maintains; trade.fills are the
    executions the exchange actually reported. When they disagree the fills are
    the record of what happened, so they are what gets reconciled against.
    """
    shares = 0.0
    notional = 0.0
    try:
        for f in getattr(trade, "fills", []) or []:
            ex = getattr(f, "execution", None)
            if ex is None:
                continue
            q = float(getattr(ex, "shares", 0) or 0)
            p = float(getattr(ex, "price", 0) or 0)
            shares += q
            notional += q * p
    except Exception as e:                                  # pragma: no cover
        logger.warning("could not read fills: %s", e)
        return 0.0, 0.0
    return shares, (notional / shares if shares else 0.0)


def _submitted_at(trade) -> str:
    """ISO timestamp of the order's first log entry, or "".

    Consumers parse this with datetime.fromisoformat inside a bare except, so a
    wrong shape here does not raise — it silently disqualifies every order from
    stale-order cleanup.
    """
    try:
        for entry in getattr(trade, "log", []) or []:
            t = getattr(entry, "time", None)
            if t is not None:
                return t.isoformat() if hasattr(t, "isoformat") else str(t)
    except Exception:
        pass
    return ""


def _reconcile(trade, broker_order_id: str = "") -> OrderResult:
    """The single place an IBKR trade object becomes an OrderResult.

    Both submit_order and get_order_status go through here, so a fix to one
    cannot leave the other reporting something different about the same order.
    """
    st = getattr(trade, "orderStatus", None)
    label = (getattr(st, "status", "") or "") if st else ""
    status_filled = float(getattr(st, "filled", 0) or 0) if st else 0.0
    remaining = float(getattr(st, "remaining", 0) or 0) if st else None
    avg_price = float(getattr(st, "avgFillPrice", 0) or 0) if st else 0.0

    fill_shares, fill_avg = _fills_total(trade)

    # Neither source is authoritative on its own, and this took two attempts to
    # get right.
    #
    #   min() was wrong: execDetails routinely arrives ahead of the final
    #   orderStatus, so filled=0 alongside a 100-share execution is normal, and
    #   the minimum reported "FILLED, 0 shares".
    #
    #   Then "executions always win" was wrong in the other direction:
    #   trade.fills only holds executions seen on THIS connection and clientId,
    #   so a reconnect mid-fill, a second client, or a table rebuilt from
    #   reqAllOpenOrders leaves it truncated — and a completed 100-share order
    #   reported as 40.
    #
    # The summary is server-side and complete; the executions are timely but
    # possibly partial. Neither ever invents shares that did not trade, so the
    # larger number is the one supported by evidence, and the disagreement is
    # logged either way.
    filled = max(status_filled, fill_shares)
    discrepancy = abs(status_filled - fill_shares) > 1e-9
    if discrepancy:
        logger.warning("IBKR fill mismatch on order %s: orderStatus=%s "
                       "executions=%s — taking %s pending reconciliation",
                       broker_order_id, status_filled, fill_shares, filled)

    # RECONCILE against the order itself, rather than picking a winner between
    # two counters and hoping.
    #
    # Three passes produced three tiebreak rules (min, then executions-only,
    # then max), each justified by naming the previous one's failure. They all
    # shared a flaw: the invariant was sitting unused three lines away.
    # `filled + remaining == totalQuantity` is what makes a fill count
    # believable, and a count that breaks it is not evidence to be preferred —
    # it is a number to refuse.
    total_qty = None
    try:
        raw_total = getattr(getattr(trade, "order", None), "totalQuantity", None)
        if raw_total is not None:
            total_qty = float(raw_total)
    except (TypeError, ValueError):
        total_qty = None

    reconciled = True
    if total_qty and total_qty > 0:
        if filled > total_qty + 1e-9:
            # Cannot have filled more than was ordered. A stale-high summary
            # (a bust or correction re-reported as a fresh execution) lands
            # here, and clamping is the conservative reading.
            logger.error("IBKR order %s reports %s filled against a total "
                         "order size of %s — clamping and flagging",
                         broker_order_id, filled, total_qty)
            filled = total_qty
            reconciled = False
        if remaining is not None and abs(filled + remaining - total_qty) > 1e-6:
            reconciled = False
            logger.warning("IBKR order %s does not reconcile: filled=%s + "
                           "remaining=%s != total=%s",
                           broker_order_id, filled, remaining, total_qty)

    status = _parse_ibkr_status(label, filled=filled, remaining=remaining)

    err = ""
    # A rejection reason must survive the status mapping. "Inactive" with a
    # part-fill maps to PARTIAL, and the branch below used to key on the final
    # status — so IBKR's own explanation was dropped precisely in the case that
    # most needed it: a partly-filled order that was then refused.
    if label in ("Inactive",) or status is OrderStatus.REJECTED:
        err = _rejection_reason(trade) or f"IBKR reported {label or 'no status'}"

    # A terminal FILLED with nothing filled is not a fill. Report it as still
    # working so pollers keep asking, rather than as a completed order with a
    # zero position — which reads downstream as "entry done, place brackets".
    if status is OrderStatus.FILLED and filled <= 0:
        logger.error("IBKR order %s reports Filled with zero quantity — "
                     "treating as unconfirmed", broker_order_id)
        status = OrderStatus.SUBMITTED
        err = err or "IBKR reported Filled with no executed quantity"

    # Price follows whichever count was used. The `or fill_avg` fallback that
    # used to close this expression defeated its own comment: when
    # avgFillPrice is 0 — the normal state while the summary leads the
    # executions — a 100-share fill was priced at the VWAP of the 40 executions
    # that happened to be visible. An unknown price is reported as unknown;
    # callers already treat 0.0 as "not known yet" and keep polling.
    if fill_shares and filled == fill_shares:
        price = fill_avg
    else:
        price = avg_price

    # Notes accumulate rather than compete. `err = err or ...` meant whichever
    # condition was written first silenced the rest, and the one that got
    # silenced was the reconciliation failure — the more serious of the two.
    notes = [err] if err else []
    if not reconciled:
        notes.append(f"fill counts do not reconcile against the order size "
                     f"(filled={filled}, remaining={remaining}, "
                     f"total={total_qty})")
    if not price and filled:
        notes.append("fill quantity known but average price not yet "
                     "reported by IBKR")
    err = "; ".join(notes)

    return OrderResult(
        broker_order_id=broker_order_id or str(
            getattr(getattr(trade, "order", None), "orderId", "")),
        status=status,
        filled_qty=filled,
        avg_fill_price=price,
        error_message=err,
        raw={"ibkr_status": label,
             "order_status_filled": status_filled,
             "executions_filled": fill_shares,
             "remaining": remaining,
             "total_quantity": total_qty,
             "fill_discrepancy": discrepancy,
             "reconciled": reconciled},
    )


class IBKRBroker(BrokerBase):
    """Interactive Brokers adapter — full asset class support."""

    # Read by src/execution/live_guard.py. Declared here rather than inferred
    # from the class name, so the gate never grades this account on another
    # broker's environment variable.
    PAPER_ENV_VAR = "IBKR_PAPER"

    def endpoint_is_paper(self):
        """True/False from the PORT — the only thing that actually decides
        whether an order reaches the paper gateway or the live one. None when
        the port is neither of IBKR's documented pairs (a Docker map, an SSH
        tunnel), because "not on the live list" is not evidence of paper."""
        try:
            port = int(self._port)
        except (TypeError, ValueError, AttributeError):
            return None
        if port in LIVE_PORTS:
            return False
        if port in PAPER_PORTS:
            return True
        logger.warning("IBKR port %s is neither a documented live nor paper "
                       "port — the live gate cannot confirm this account", port)
        return None

    def __init__(self):
        self._host      = os.getenv("IBKR_HOST", "127.0.0.1")
        self._port      = int(os.getenv("IBKR_PORT", "7497"))
        self._client_id = int(os.getenv("IBKR_CLIENT_ID", "1"))
        self._is_paper  = os.getenv("IBKR_PAPER", "true").lower() != "false"
        self._ib: Optional["ibi.IB"] = None

        if IBKR_AVAILABLE:
            try:
                self._ib = ibi.IB()
                self._ib.connect(self._host, self._port, clientId=self._client_id, timeout=3)
                logger.info(f"IBKR connected ({'PAPER' if self._is_paper else 'LIVE'}) on port {self._port}")
            except Exception as e:
                logger.warning(f"IBKR connection failed (TWS/Gateway may not be running): {e}")
                self._ib = None

    @property
    def name(self) -> str:
        return "IBKR"

    @property
    def paper(self) -> bool:
        return self._is_paper

    def is_connected(self) -> bool:
        return IBKR_AVAILABLE and self._ib is not None and self._ib.isConnected()

    def get_account(self) -> AccountInfo:
        if not self.is_connected():
            return AccountInfo(broker="IBKR", cash=0, portfolio_value=0, buying_power=0)
        try:
            vals = self._ib.accountValues()
            def _get(tag: str) -> float:
                for v in vals:
                    if v.tag == tag and v.currency == "USD":
                        return float(v.value)
                return 0.0
            return AccountInfo(
                broker="IBKR",
                cash=_get("CashBalance"),
                portfolio_value=_get("NetLiquidation"),
                buying_power=_get("BuyingPower"),
                currency="USD",
            )
        except Exception as e:
            logger.error(f"IBKR get_account: {e}")
            return AccountInfo(broker="IBKR", cash=0, portfolio_value=0, buying_power=0)

    def get_positions(self) -> list[Position]:
        if not self.is_connected():
            return []
        try:
            result = []
            for p in self._ib.positions():
                sym = p.contract.symbol
                ac  = AssetClass.FUTURES if isinstance(p.contract, ibi.Future) else \
                      AssetClass.FOREX   if isinstance(p.contract, ibi.Forex)  else \
                      AssetClass.CRYPTO  if isinstance(p.contract, ibi.Crypto) else \
                      AssetClass.EQUITY
                result.append(Position(
                    ticker=sym,
                    qty=float(p.position),
                    avg_cost=float(p.avgCost),
                    market_value=float(p.position) * float(p.avgCost),
                    unrealized_pl=0.0,  # requires market data subscription
                    side="long" if float(p.position) > 0 else "short",
                    asset_class=ac,
                ))
            return result
        except Exception as e:
            logger.error(f"IBKR get_positions: {e}")
            return []

    def get_position(self, ticker: str) -> Optional[Position]:
        for p in self.get_positions():
            if p.ticker == ticker:
                return p
        return None

    def submit_order(self, req: OrderRequest) -> OrderResult:
        # Below every other gate: nothing reaches IBKR while the switch is on.
        if kill_switch.is_engaged():
            st = kill_switch.status()
            logger.critical("IBKR order refused — kill switch engaged (%s)", st.get("source"))
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message=f"KILL SWITCH ENGAGED ({st.get('source')}): {st.get('reason')}",
            )
        if not self.is_connected():
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message="IBKR not connected. Start TWS or IB Gateway.",
            )
        try:
            contract = _make_contract(req.ticker, req.asset_class)
            self._ib.qualifyContracts(contract)

            side = "BUY" if req.side == OrderSide.BUY else "SELL"

            if req.order_type == OrderType.MARKET:
                order = ibi.MarketOrder(side, req.qty)
            elif req.order_type == OrderType.LIMIT:
                order = ibi.LimitOrder(side, req.qty, req.limit_price)
            elif req.order_type == OrderType.STOP:
                order = ibi.StopOrder(side, req.qty, req.stop_price)
            elif req.order_type == OrderType.STOP_LIMIT:
                order = ibi.StopLimitOrder(side, req.qty, req.limit_price, req.stop_price)
            else:
                order = ibi.MarketOrder(side, req.qty)

            order.tif = req.time_in_force.upper()
            trade = self._ib.placeOrder(contract, order)
        except Exception as e:
            # Nothing reached IBKR: REJECTED is the truth here.
            logger.error(f"IBKR submit_order: {e}")
            return OrderResult(
                broker_order_id="",
                status=OrderStatus.REJECTED,
                error_message=str(e),
            )

        # Past this line the order EXISTS at IBKR. Anything that goes wrong now
        # is a failure to read it, not a failure to place it — and reporting
        # REJECTED for a live order is how a position ends up unmanaged, with
        # no bracket and nobody polling it.
        order_id = str(getattr(trade.order, "orderId", "") or "")
        try:
            self._ib.sleep(1)  # let the acknowledgement arrive
            return _reconcile(trade, order_id)
        except Exception as e:
            logger.error(f"IBKR submit_order: placed {order_id} but could not "
                         f"read its state: {e}")
            return OrderResult(
                broker_order_id=order_id,
                status=OrderStatus.SUBMITTED,
                error_message=f"order placed but status unreadable: {e}",
                raw={"orderId": order_id, "placed": True},
            )

    def cancel_order(self, broker_order_id: str) -> bool:
        """True means "this order will not fill from here on".

        Scanning openTrades() alone conflated three different worlds — already
        cancelled, already filled, and never heard of — into one False. The
        second of those matters: a caller that cancels a protective order
        before replacing it must not be told "no" for an order that has in fact
        already executed.
        """
        if not self.is_connected():
            return False
        try:
            for trade in self._ib.trades():
                if str(trade.order.orderId) != broker_order_id:
                    continue
                state = _reconcile(trade, broker_order_id)
                if state.status is OrderStatus.FILLED:
                    logger.error("cancel_order(%s): already FILLED — nothing to "
                                 "cancel, and the position is real",
                                 broker_order_id)
                    return False
                if state.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
                    return True            # already dead; the caller's goal holds
                self._ib.cancelOrder(trade.order)
                return True
            logger.warning("cancel_order(%s): unknown to IBKR", broker_order_id)
            return False
        except Exception as e:
            logger.error(f"IBKR cancel_order: {e}")
            return False

    def get_order_status(self, broker_order_id: str) -> OrderResult:
        if not self.is_connected():
            # Disconnected is not rejected. The order is still live at IBKR;
            # saying otherwise retires a working order from the caller's books.
            return OrderResult(
                broker_order_id=broker_order_id,
                status=OrderStatus.SUBMITTED,
                error_message="IBKR not connected — order state unknown")
        try:
            # Audit C2: an order absent from openTrades() may be filled,
            # cancelled, OR rejected — absence must never imply FILLED.
            # ib.trades() includes terminal orders; read the real status.
            for trade in self._ib.trades():
                if str(trade.order.orderId) == broker_order_id:
                    return _reconcile(trade, broker_order_id)
            # Unknown order id: report SUBMITTED (not filled) so pollers keep
            # waiting and never place brackets on an unconfirmed position.
            return OrderResult(
                broker_order_id=broker_order_id,
                status=OrderStatus.SUBMITTED,
                error_message="order not found in ib.trades() — cannot confirm fill",
            )
        except Exception as e:
            # A failure to READ the order is not the order being rejected.
            logger.error("IBKR get_order_status(%s): %s", broker_order_id, e)
            return OrderResult(
                broker_order_id=broker_order_id,
                status=OrderStatus.SUBMITTED,
                error_message=f"could not read order state: {e}",
            )

    def get_open_orders(self) -> list[dict]:
        """Working orders as plain dicts — the desk's reconciliation view.

        BrokerBase returns [] by default, and IBKR never overrode it, so
        `open_orders_by_ticker()` reported "no open orders" for every IBKR
        ticker. Bracket healing, stale-order cleanup and the "is an exit already
        in flight" check all read that list, which meant all three silently did
        nothing for IBKR positions — including the check that stops exit orders
        from stacking.
        """
        if not self.is_connected():
            return []
        out = []
        try:
            for trade in self._ib.openTrades():
                o, st = trade.order, trade.orderStatus
                contract = getattr(trade, "contract", None)
                out.append({
                    "id": str(getattr(o, "orderId", "")),
                    "ticker": getattr(contract, "symbol", "") or "",
                    "side": "buy" if str(getattr(o, "action", "")).upper() == "BUY" else "sell",
                    "order_type": {"MKT": "market", "LMT": "limit", "STP": "stop",
                                   "STP LMT": "stop_limit"}.get(
                                       str(getattr(o, "orderType", "")).upper(),
                                       str(getattr(o, "orderType", "")).lower()),
                    "qty": float(getattr(o, "totalQuantity", 0) or 0),
                    "stop_price": float(getattr(o, "auxPrice", 0) or 0) or None,
                    "limit_price": float(getattr(o, "lmtPrice", 0) or 0) or None,
                    # ib_insync.Trade has no `logTime` — the timestamp lives on
                    # the first TradeLogEntry. Getting this wrong is invisible
                    # until cancel_stale_orders does fromisoformat("") and its
                    # bare except skips every order in silence.
                    "submitted_at": _submitted_at(trade),
                    "status": str(getattr(st, "status", "") or ""),
                })
        except Exception as e:
            logger.error(f"IBKR get_open_orders: {e}")
            return []
        return out

    def get_quote(self, ticker: str) -> dict:
        if not self.is_connected():
            return {}
        try:
            contract = ibi.Stock(ticker, "SMART", "USD")
            self._ib.qualifyContracts(contract)
            [ticker_data] = self._ib.reqTickers(contract)
            return {
                "ask":  float(ticker_data.ask  or 0),
                "bid":  float(ticker_data.bid  or 0),
                "last": float(ticker_data.last or 0),
                "mid":  round((float(ticker_data.ask or 0) + float(ticker_data.bid or 0)) / 2, 4),
            }
        except Exception as e:
            logger.warning(f"IBKR get_quote({ticker}): {e}")
            return {}

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
