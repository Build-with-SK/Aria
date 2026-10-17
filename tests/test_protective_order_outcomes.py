"""A protective OCO's outcome is recorded as what it is (review of Codex 09f2158).

09f2158 gave protective orders a durable queue row and a client id, so a lost
reply can no longer be followed by a second bracket. But `submit_oco_exit`
returned None for every failure and None was recorded UNKNOWN — so an OCO the
broker definitely REFUSED (Alpaca 40310000: the shares are already reserved),
or one the mode gate refused before sending, became an unresolved row that
blocked the symbol's entries and its bracket healing until the owner released
it by hand. A refusal now raises OrderRefused and is recorded rejected; a
timeout is still UNKNOWN.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_order_settlement import FakeAlpaca, FakeClient, ReadTimeout, Unprocessable  # noqa: F401

from src.execution.broker_base import OrderRefused, Position


class Forbidden(Exception):
    status_code = 403


class HeldAlpaca(FakeAlpaca):
    """The real adapter's OCO code over FakeClient, holding 10 AAPL."""

    def get_positions(self):
        return [Position(ticker="AAPL", qty=10.0, avg_cost=10.0, market_value=100.0,
                         unrealized_pl=0.0, side="long")]


@pytest.fixture
def world(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    from src.execution.order_manager import OrderManager
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")

    def make(client):
        broker = HeldAlpaca(client)
        return OrderManager(alpaca=broker), broker, aq.ApprovalQueue()
    return make


def _protect(mgr, broker):
    return mgr.place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0,
                                       broker=broker)


@pytest.mark.parametrize("error", [Forbidden("insufficient qty available (40310000)"),
                                   Unprocessable("stop price must be below")],
                         ids=["403-reserved", "422-invalid"])
def test_a_refused_oco_is_rejected_and_blocks_nothing(world, error):
    mgr, broker, queue = world(FakeClient(submit_error=error))
    out = _protect(mgr, broker)
    assert not out.get("unknown") and not out["stop_loss_order"]
    row = [t for t in queue.get_all() if t.situation == "protective_exit"][-1]
    assert row.status == "rejected" and row.client_order_id.startswith("aria-q-")
    assert queue.unresolved() == [] and queue.in_flight() == []
    # Nothing stands in the way of trying again on the next tick.
    broker._client._accept = SimpleNamespace(id="oco-2")
    broker._client._submit_error = None
    again = _protect(mgr, broker)
    assert again["stop_loss_order"]["broker_order_id"] == "oco-2"


def test_a_timed_out_oco_stays_unknown_and_is_not_retried_blind(world):
    mgr, broker, queue = world(FakeClient(submit_error=ReadTimeout("read timed out")))
    out = _protect(mgr, broker)
    assert out["unknown"] is True and out["protection_pending"] is True
    assert [t.status for t in queue.unresolved()] == ["unknown"]
    sent = len(broker._client.sent)
    again = _protect(mgr, broker)
    # Refused at the reduce-only check: the unknown claim reserves the shares.
    assert again["reduce_only"]["ok"] is False and "not visible" in again["reduce_only"]["reason"]
    assert len(broker._client.sent) == sent, "no second OCO while the first is unknown"


def test_the_mode_gate_refusing_an_oco_is_a_refusal(world, monkeypatch):
    from src.trading import access, modes
    mgr, broker, queue = world(FakeClient(accept=SimpleNamespace(id="never")))
    gated = access.ModeGatedBroker(broker, ceiling=modes.ExecutionMode.PAPER,
                                   queue_blocked=False)
    monkeypatch.setattr(modes, "effective_mode", lambda: modes.ExecutionMode.RESEARCH)
    with pytest.raises(OrderRefused):
        gated.submit_oco_exit("AAPL", 10, "buy", 9.0, 12.0, client_order_id="aria-q-x")
    assert broker._client.sent == []


def test_the_adapter_refuses_under_the_kill_switch(monkeypatch):
    from src.execution import kill_switch
    broker = HeldAlpaca(FakeClient(accept=SimpleNamespace(id="never")))
    monkeypatch.setattr(kill_switch, "is_engaged", lambda: True)
    monkeypatch.setattr(kill_switch, "status", lambda: {"source": "test", "reason": "t"})
    with pytest.raises(OrderRefused):
        broker.submit_oco_exit("AAPL", 10, "buy", 9.0, 12.0, client_order_id="aria-q-k")
    assert broker._client.sent == []
