"""A positions read that fails is not "the broker holds nothing".

account_snapshot() marked the account connected BEFORE it read positions. If
that read failed — Alpaca's adapter returned [] on an error and, since Codex's
09f2158, raises — the snapshot said connected with no positions, and the
management tick then treated every tracked position as closed at the broker:
it recorded an external close (P&L from whatever last fill it could find) and
cancelled the position's protective orders, leaving a position that still
exists with no stop. The snapshot now reports the account unavailable, and
the tick skips, exactly as it does when the broker is down.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.execution.broker_base import AccountInfo


class _PositionsFail:
    """A connected, paper broker whose position read fails."""
    paper = True
    name = "Alpaca"

    def __init__(self):
        self.cancelled = []

    def is_connected(self):
        return True

    def get_account(self):
        return AccountInfo(broker="Alpaca", cash=5_000.0, portfolio_value=10_000.0,
                           buying_power=5_000.0)

    def status(self):
        return {"confirmed_paper": True, "endpoint_is_paper": True}

    def get_positions(self):
        raise ConnectionError("Alpaca positions unreadable")

    def get_open_orders(self):
        return [{"id": "oco-1", "ticker": "AAPL", "side": "sell", "qty": 10,
                 "order_type": "limit"}]

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        return True


@pytest.fixture
def desk(tmp_path, monkeypatch):
    from src.desk import auto_executor as ae
    from src.desk import position_manager as pm
    broker = _PositionsFail()
    mgr = SimpleNamespace(_alpaca=broker,
                          open_orders_by_ticker=lambda: {"AAPL": broker.get_open_orders()})
    monkeypatch.setattr(ae, "get_order_manager", lambda *a, **k: mgr)
    monkeypatch.setattr(pm, "POSITIONS_FILE", tmp_path / "positions.json")
    monkeypatch.setattr(pm, "CLOSED_FILE", tmp_path / "closed.jsonl")
    return ae, pm, broker


def test_the_snapshot_does_not_say_connected_with_no_positions(desk):
    ae, _, _ = desk
    snap = ae.account_snapshot()
    assert snap["connected"] is False
    assert "positions" in snap.get("error", "")


def test_the_tick_does_not_close_or_unprotect_a_position_it_could_not_read(desk):
    _, pm, broker = desk
    mgr = pm.PositionManager({"ntfy_topic": ""})
    mgr.save_positions({"AAPL": {"ticker": "AAPL", "qty": 10, "side": "long",
                                 "entry_price": 10.0, "asset_class": "equity"}})
    summary = mgr.tick()
    assert summary["exits"] == []
    assert broker.cancelled == [], "the protective OCO was not cancelled"
    assert "AAPL" in mgr.load_positions(), "still tracked"
    assert any("skipped" in e for e in summary["errors"])
