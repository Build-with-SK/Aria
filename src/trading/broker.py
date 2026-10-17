"""
src/trading/broker.py
=====================
The broker boundary. Strategy, risk and validation never know which broker.

    BrokerAdapter
      get_account()  get_positions()  get_balances()  get_quote(symbol)
      submit_order(order)  cancel_order(client_order_id)
      get_order(client_order_id)  get_open_orders()

Orders are addressed by the CLIENT order id — the idempotency key ARIA chose —
not the broker's own id, because after a timeout the broker's id is exactly what
we do not have.

Three failure types, and callers must treat them differently:

  BrokerRejected     the broker said no. The order does not exist.
  BrokerTimeout      we do not know whether the order exists. Never retry blind:
                     look it up by client id first (orders.OrderEngine does).
  BrokerUnavailable  could not even ask. Nothing was sent.

Implementations here:
  SimulatedBroker      paper, in memory, deterministic, with fault injection for
                       tests — timeouts, lost acknowledgements, lookup failures.
  LegacyBrokerAdapter  the existing Alpaca/IBKR classes behind src/trading/access,
                       so the mode gate and hard limits sit under every order.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import costs as _costs
from .market import Quote

# Order states as this layer names them (the broker's own vocabulary is mapped).
NEW = "NEW"                  # accepted, resting
FILLED = "FILLED"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"
TERMINAL = {FILLED, CANCELLED, REJECTED}


class BrokerRejected(RuntimeError):
    pass


class BrokerTimeout(RuntimeError):
    pass


class BrokerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderSpec:
    client_order_id: str
    symbol: str
    side: str                    # buy | sell
    qty: float
    order_type: str = "market"   # market | limit
    limit_price: Optional[float] = None
    time_in_force: str = "day"
    asset_class: str = "equity"
    strategy_id: str = ""
    signal_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrderAck:
    client_order_id: str
    status: str
    broker_order_id: str = ""
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fees: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    detail: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["at"] = self.at.isoformat()
        return d


class BrokerAdapter(ABC):
    name: str = "broker"
    is_live: bool = False
    # Only an in-process synchronous simulator can prove that no request is
    # still in flight. A remote 404 never authorizes a retry after a timeout.
    authoritative_absence: bool = False

    @abstractmethod
    def get_account(self) -> dict: ...
    @abstractmethod
    def get_positions(self) -> list[dict]: ...
    @abstractmethod
    def get_balances(self) -> dict: ...
    @abstractmethod
    def get_quote(self, symbol: str) -> Optional[Quote]: ...
    @abstractmethod
    def submit_order(self, order: OrderSpec) -> OrderAck: ...
    @abstractmethod
    def cancel_order(self, client_order_id: str) -> bool: ...
    @abstractmethod
    def get_order(self, client_order_id: str) -> Optional[OrderAck]: ...
    @abstractmethod
    def get_open_orders(self) -> list[OrderAck]: ...


# ── the paper broker ─────────────────────────────────────────────────────────

class SimulatedBroker(BrokerAdapter):
    """A paper venue that charges fees and slippage and can be made to fail.

    Deliberately not generous: market orders fill at the far side of the touch
    plus `slippage_bps`, fees follow a real schedule, and cash is enforced. A
    simulator that fills at mid for free makes every strategy look viable.
    """
    authoritative_absence = True
    name = "simulated"
    is_live = False

    def __init__(self, *, cash: float = 100.0, currency: str = "USD",
                 fee_schedule: _costs.FeeSchedule | None = None,
                 slippage_bps: float = 5.0):
        self.cash = float(cash)
        self.currency = currency
        self.fees = fee_schedule or _costs.SCHEDULES["simulated"]
        self.slippage_bps = float(slippage_bps)
        self.positions: dict[str, dict] = {}
        self.quotes: dict[str, Quote] = {}
        self.orders: dict[str, OrderAck] = {}
        self.specs: dict[str, OrderSpec] = {}
        self.fees_paid = 0.0
        self.submit_calls = 0
        self._lock = threading.Lock()
        # fault injection
        self.fail_next_submit: Optional[Exception] = None
        self.lose_next_ack = False
        self.fail_lookups: Optional[Exception] = None
        self.unavailable = False

    # reads
    def set_quote(self, q: Quote) -> None:
        self.quotes[q.symbol] = q

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if self.unavailable:
            raise BrokerUnavailable("simulated outage")
        return self.quotes.get(symbol)

    def get_positions(self) -> list[dict]:
        if self.unavailable:
            raise BrokerUnavailable("simulated outage")
        return [dict(symbol=s, **p) for s, p in self.positions.items() if p["qty"] != 0]

    def get_balances(self) -> dict:
        return {"cash": self.cash, "currency": self.currency}

    def get_account(self) -> dict:
        if self.unavailable:
            raise BrokerUnavailable("simulated outage")
        mv = 0.0
        for s, p in self.positions.items():
            q = self.quotes.get(s)
            mv += p["qty"] * (q.mid if q else p["avg_price"])
        return {"equity": self.cash + mv, "cash": self.cash, "currency": self.currency,
                "fees_paid": self.fees_paid, "paper": True}

    def get_order(self, client_order_id: str) -> Optional[OrderAck]:
        if self.fail_lookups:
            raise self.fail_lookups
        return self.orders.get(client_order_id)

    def get_open_orders(self) -> list[OrderAck]:
        return [a for a in self.orders.values() if a.status in (NEW, PARTIALLY_FILLED)]

    def open_order_details(self) -> list[dict]:
        """Symbol, side and quantity of each resting order — what the kill
        switch needs to tell a protective order from an entry."""
        return [{"client_order_id": a.client_order_id, "symbol": self.specs[a.client_order_id].symbol,
                 "side": self.specs[a.client_order_id].side,
                 "qty": self.specs[a.client_order_id].qty - (a.filled_qty or 0.0),
                 "parent_id": None}
                for a in self.get_open_orders() if a.client_order_id in self.specs]

    # writes
    def submit_order(self, order: OrderSpec) -> OrderAck:
        with self._lock:
            self.submit_calls += 1
            if self.unavailable:
                raise BrokerUnavailable("simulated outage")
            if self.fail_next_submit is not None:
                exc, self.fail_next_submit = self.fail_next_submit, None
                raise exc
            if order.client_order_id in self.orders:
                # Broker-side idempotency: the same client id is the same order.
                return self.orders[order.client_order_id]
            ack = self._execute(order)
            self.orders[order.client_order_id] = ack
            self.specs[order.client_order_id] = order
            if self.lose_next_ack:
                # The broker took it; the reply never arrived.
                self.lose_next_ack = False
                raise BrokerTimeout("simulated: order accepted, acknowledgement lost")
            return ack

    def _execute(self, o: OrderSpec) -> OrderAck:
        q = self.quotes.get(o.symbol)
        if q is None:
            return OrderAck(o.client_order_id, REJECTED, detail="no quote for symbol")
        if o.qty <= 0:
            return OrderAck(o.client_order_id, REJECTED, detail="non-positive qty")
        touch = q.ask if o.side == "buy" else q.bid
        if o.order_type == "limit":
            marketable = (o.limit_price or 0) >= q.ask if o.side == "buy" \
                else (o.limit_price or 0) <= q.bid
            if not marketable:
                return OrderAck(o.client_order_id, NEW,
                                broker_order_id=f"sim-{o.client_order_id}")
        slip = self.slippage_bps / 1e4
        price = touch * (1 + slip) if o.side == "buy" else touch * (1 - slip)
        fee = _costs.order_fee(self.fees, side=o.side, qty=o.qty, price=price)
        pos = self.positions.setdefault(o.symbol, {"qty": 0.0, "avg_price": 0.0})
        if o.side == "buy":
            cost = o.qty * price + fee
            if cost > self.cash + 1e-9:
                return OrderAck(o.client_order_id, REJECTED, detail="insufficient cash")
            new_qty = pos["qty"] + o.qty
            pos["avg_price"] = (pos["qty"] * pos["avg_price"] + o.qty * price) / new_qty
            pos["qty"] = new_qty
            self.cash -= cost
        else:
            if o.qty > pos["qty"] + 1e-9:
                return OrderAck(o.client_order_id, REJECTED,
                                detail="insufficient position (no shorting)")
            pos["qty"] -= o.qty
            self.cash += o.qty * price - fee
        self.fees_paid += fee
        return OrderAck(o.client_order_id, FILLED, broker_order_id=f"sim-{o.client_order_id}",
                        filled_qty=o.qty, avg_price=round(price, 8), fees=round(fee, 8))

    def cancel_order(self, client_order_id: str) -> bool:
        a = self.orders.get(client_order_id)
        if a is None or a.status in TERMINAL:
            return False
        a.status = CANCELLED
        return True


# ── the existing brokers, behind the new interface ──────────────────────────

_STATUS_MAP = {"submitted": NEW, "pending": NEW, "filled": FILLED,
               "partial": PARTIALLY_FILLED, "cancelled": CANCELLED,
               "rejected": REJECTED, "expired": CANCELLED}


class LegacyBrokerAdapter(BrokerAdapter):
    """Wraps an existing broker object — in practice a ModeGatedBroker from
    src/trading/access.py, never a raw one — in the new interface."""

    def __init__(self, gated_broker, *, is_live: bool):
        self._b = gated_broker
        self.name = getattr(gated_broker, "name", "legacy")
        self.is_live = bool(is_live)

    def get_account(self) -> dict:
        a = self._b.get_account()
        return {"equity": a.portfolio_value or a.cash, "cash": a.cash,
                "currency": a.currency, "buying_power": a.buying_power,
                "paper": not self.is_live}

    def get_positions(self) -> list[dict]:
        return [{"symbol": p.ticker, "qty": p.qty, "avg_price": p.avg_cost,
                 "market_value": p.market_value, "side": getattr(p, "side", "")}
                for p in self._b.get_positions()]

    def get_balances(self) -> dict:
        a = self._b.get_account()
        return {"cash": a.cash, "currency": a.currency}

    def get_quote(self, symbol: str) -> Optional[Quote]:
        raw = self._b.get_quote(symbol) or {}
        if not raw or not raw.get("ask") or not raw.get("bid"):
            return None
        ts = raw.get("timestamp")
        ts = datetime.fromisoformat(ts) if isinstance(ts, str) else None
        return Quote(symbol=symbol, bid=float(raw["bid"]), ask=float(raw["ask"]),
                     timestamp=ts, bid_size=float(raw.get("bid_size") or 0),
                     ask_size=float(raw.get("ask_size") or 0), venue=self.name)

    def submit_order(self, order: OrderSpec) -> OrderAck:
        from src.execution.broker_base import (AssetClass, OrderRequest, OrderSide,
                                               OrderStatus, OrderType)
        req = OrderRequest(
            ticker=order.symbol, side=OrderSide(order.side), qty=order.qty,
            order_type=OrderType(order.order_type), limit_price=order.limit_price,
            time_in_force=order.time_in_force, asset_class=AssetClass(order.asset_class),
            client_order_id=order.client_order_id,
            situation=f"strategy {order.strategy_id}", thesis_summary=order.signal_id)
        r = self._b.submit_order(req)
        if r.status == OrderStatus.UNKNOWN:
            raise BrokerTimeout(r.error_message or "order state unknown")
        if r.status == OrderStatus.REJECTED:
            raise BrokerRejected(r.error_message or "rejected")
        return OrderAck(order.client_order_id, _STATUS_MAP.get(r.status.value, NEW),
                        broker_order_id=r.broker_order_id, filled_qty=r.filled_qty,
                        avg_price=r.avg_fill_price)

    def cancel_order(self, client_order_id: str) -> bool:
        a = self.get_order(client_order_id)
        if a is None or not a.broker_order_id:
            return False
        return bool(self._b.cancel_order(a.broker_order_id))

    def get_order(self, client_order_id: str) -> Optional[OrderAck]:
        lookup = getattr(self._b, "get_order_by_client_id", None)
        if lookup is None:
            raise BrokerUnavailable(f"{self.name} cannot look orders up by client id; "
                                    f"an unknown order state cannot be reconciled")
        r = lookup(client_order_id)
        if r is None:
            return None
        return OrderAck(client_order_id, _STATUS_MAP.get(r.status.value, NEW),
                        broker_order_id=r.broker_order_id, filled_qty=r.filled_qty,
                        avg_price=r.avg_fill_price)

    def get_open_orders(self) -> list[OrderAck]:
        return [OrderAck(str(o.get("client_order_id") or o.get("id")), NEW,
                         broker_order_id=str(o.get("id")))
                for o in (self._b.get_open_orders() or [])]

    def open_order_details(self) -> list[dict]:
        """Symbol, side and quantity of each resting order, keyed like
        get_open_orders(). OCO legs carry their parent's id."""
        return [{"client_order_id": str(o.get("client_order_id") or o.get("id")),
                 "symbol": str(o.get("ticker") or o.get("symbol") or ""),
                 "side": str(o.get("side") or "").lower(),
                 "qty": float(o.get("qty") or 0.0), "parent_id": o.get("parent_id")}
                for o in (self._b.get_open_orders() or [])]
