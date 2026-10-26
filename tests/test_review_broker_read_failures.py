"""Independent review, 2026-10-26 (Claude, reviewer seat) — broker read failures.

ARIA-D8: `AlpacaBroker.get_positions()` and `get_open_orders()` catch every
exception and return `[]`; IBKR's `get_positions()` does the same. The
callers were written to fail CLOSED on a read failure —
`reduce_only.at_broker` ("any read failure is a refusal") and
`account.from_legacy_broker` ("a KNOWN-UNKNOWN state — never a zero-filled
guess") — but they only see an exception if the adapter raises, and it never
does. So a transient API error reads as "no positions" / "no resting orders".

Strict xfails: they fail now and XPASS once a read failure reaches the caller.
The broker is the real AlpacaBroker class with an in-memory client; no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


class _Client:
    """Stands in for alpaca-py's TradingClient. One call can be made to fail."""

    def __init__(self, *, positions_fail=False, orders_fail=False, orders=None):
        self.positions_fail = positions_fail
        self.orders_fail = orders_fail
        self.orders = orders or []
        self.cancelled = []

    def get_account(self):
        return SimpleNamespace(cash="100000", portfolio_value="110000",
                               buying_power="100000", daytrade_count=0)

    def get_all_positions(self):
        if self.positions_fail:
            raise ConnectionError("HTTP 503 from /v2/positions")
        return [SimpleNamespace(symbol="AAPL", qty="100", avg_entry_price="100",
                                market_value="10000", unrealized_pl="0")]

    def get_orders(self, *a, **k):
        if self.orders_fail:
            raise ConnectionError("HTTP 503 from /v2/orders")
        return self.orders

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)


def _resting_stop(age_days=3):
    side = SimpleNamespace(value="sell")
    return SimpleNamespace(
        id="stop-1", symbol="AAPL", side=side, type=SimpleNamespace(value="stop"),
        qty="100", stop_price="90", limit_price=None,
        submitted_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        status=SimpleNamespace(value="new"), legs=None)


@pytest.fixture(autouse=True)
def _alpaca_sdk_stub(monkeypatch):
    """get_open_orders imports alpaca-py inside the call; CI does not install
    the broker SDKs, and without them the call ALSO returns [] — which would
    make these tests pass or fail for the wrong reason. Stub the two modules."""
    import sys
    import types
    req = types.ModuleType("alpaca.trading.requests")
    req.GetOrdersRequest = lambda **kw: SimpleNamespace(**kw)
    enums = types.ModuleType("alpaca.trading.enums")
    enums.QueryOrderStatus = SimpleNamespace(OPEN="open")
    for name, mod in {"alpaca": types.ModuleType("alpaca"),
                      "alpaca.trading": types.ModuleType("alpaca.trading"),
                      "alpaca.trading.requests": req,
                      "alpaca.trading.enums": enums}.items():
        monkeypatch.setitem(sys.modules, name, mod)


def _alpaca(client):
    from src.execution import alpaca_broker as ab
    b = ab.AlpacaBroker.__new__(ab.AlpacaBroker)
    b._api_key, b._secret_key, b._is_paper = "k", "s", True
    b._client, b._stock_data, b._crypto_data = client, None, None
    return b


@pytest.mark.xfail(strict=True, reason="ARIA-D8: an unreadable order book reads as 'no resting exits'")
def test_reduce_only_refuses_when_open_orders_cannot_be_read():
    """100 AAPL held, a 100-share protective stop resting. The orders endpoint
    fails. A new 100-share exit must be refused: if both the stop and the new
    exit fill, a long becomes a 100-share short."""
    from src.execution import reduce_only
    broker = _alpaca(_Client(orders_fail=True, orders=[_resting_stop()]))
    ro = reduce_only.at_broker(broker, "AAPL", "sell", 100)
    assert not ro.ok, f"exit approved for {ro.qty} with the resting orders unreadable"


@pytest.mark.xfail(strict=True, reason="ARIA-D8: an unreadable position book reads as an empty book")
def test_portfolio_state_is_unknown_when_positions_cannot_be_read():
    """The pre-trade risk gate sizes against this state. An empty book where
    a 100-share position exists lets concentration limits pass."""
    from src.trading import account
    broker = _alpaca(_Client(positions_fail=True))
    state = account.from_legacy_broker(broker)
    assert state.known is False, "positions read failed, yet the account is 'known' with no holdings"


@pytest.mark.xfail(strict=True, reason="ARIA-D8: a positions outage makes protective stops look stale")
def test_stale_order_cleanup_never_cancels_a_stop_when_positions_are_unreadable(monkeypatch):
    """cancel_stale_orders cancels day-old orders on tickers with NO position.
    With /v2/positions failing and /v2/orders fine, the 3-day-old protective
    GTC stop on a held name is cancelled — the position is left naked."""
    from src.execution.order_manager import OrderManager
    client = _Client(positions_fail=True, orders=[_resting_stop(age_days=3)])
    broker = _alpaca(client)
    monkeypatch.setattr(type(broker), "is_connected", lambda self: True)
    cancelled = OrderManager(alpaca=broker).cancel_stale_orders(max_age_days=1.0)
    assert cancelled == [] and client.cancelled == [], "protective stop cancelled during a positions outage"


def test_control_reduce_only_refuses_a_second_exit_when_orders_are_readable():
    """Control: with every read working, the same scenario is refused. This
    proves the fakes above are realistic and the xfails measure the failure
    path, not a broken stub."""
    from src.execution import reduce_only
    broker = _alpaca(_Client(orders=[_resting_stop()]))
    ro = reduce_only.at_broker(broker, "AAPL", "sell", 100)
    assert not ro.ok and ro.resting == 100


def test_control_stale_cleanup_keeps_a_stop_on_a_held_name(monkeypatch):
    from src.execution.order_manager import OrderManager
    client = _Client(orders=[_resting_stop(age_days=3)])
    broker = _alpaca(client)
    monkeypatch.setattr(type(broker), "is_connected", lambda self: True)
    assert OrderManager(alpaca=broker).cancel_stale_orders(max_age_days=1.0) == []
    assert client.cancelled == []
