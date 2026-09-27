"""
tests/test_trading_access.py
============================
THE FOUR CLAIMS ABOUT REAL MONEY, EACH ONE ATTACKED:

  1. RESEARCH cannot place an order anywhere.
  2. PAPER cannot place an order on a live account.
  3. only LIVE_AUTONOMOUS can obtain a live-capable adapter.
  4. the hard limits still apply in LIVE_AUTONOMOUS.

Plus the one that is easy to forget: holding an adapter is not a standing
permission. The mode is re-read on every call, so a desk that grabbed a broker
while the system was live stops working the moment the mode drops.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from trading_helpers import FakeLegacyBroker                        # noqa: E402
from src.execution.broker_base import (AssetClass, OrderRequest,    # noqa: E402
                                       OrderSide, OrderStatus, OrderType)
from src.trading import access, modes                               # noqa: E402
from src.trading.access import LiveAccessDenied, ModeGatedBroker    # noqa: E402
from src.trading.modes import ExecutionMode                         # noqa: E402

pytestmark = pytest.mark.usefixtures("clean_trading_state")


def order(qty: float = 0.5) -> OrderRequest:
    return OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=qty,
                        order_type=OrderType.MARKET, asset_class=AssetClass.EQUITY)


def set_mode(mode: str, monkeypatch, *, live_env: bool = False):
    """Write the mode directly — these tests are about what the gate does in a
    mode, not about how the mode was reached (that is test_trading_modes.py)."""
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    if live_env:
        monkeypatch.setenv(modes.LIVE_ENV, "true")
    else:
        monkeypatch.delenv(modes.LIVE_ENV, raising=False)


def paper_env(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "true")


# ── 1. RESEARCH places nothing ───────────────────────────────────────────────

def test_research_mode_places_no_order_even_on_a_paper_account(monkeypatch):
    paper_env(monkeypatch)
    set_mode("RESEARCH", monkeypatch)
    raw = FakeLegacyBroker(paper=True)
    gated = ModeGatedBroker(raw, ceiling=ExecutionMode.PAPER, queue_blocked=False)
    result = gated.submit_order(order())
    assert result.status == OrderStatus.REJECTED
    assert "RESEARCH" in result.error_message
    assert raw.submitted == []


def test_research_mode_still_allows_reads_and_cancels(monkeypatch):
    paper_env(monkeypatch)
    set_mode("RESEARCH", monkeypatch)
    raw = FakeLegacyBroker(paper=True)
    gated = ModeGatedBroker(raw, ceiling=ExecutionMode.PAPER, queue_blocked=False)
    assert gated.get_account().cash == 1000.0
    assert gated.cancel_order("broker-1") is True
    assert raw.cancelled == ["broker-1"], "a cancel was blocked — positions could " \
                                          "not be unwound"


def test_no_adapter_for_orders_exists_in_research(monkeypatch):
    set_mode("RESEARCH", monkeypatch)
    with pytest.raises(LiveAccessDenied):
        access.paper_adapter(FakeLegacyBroker(paper=True))
    with pytest.raises(LiveAccessDenied):
        access.live_adapter(FakeLegacyBroker(paper=False))


# ── 2. PAPER never reaches a live account ────────────────────────────────────

def test_paper_mode_submits_to_a_paper_account(monkeypatch):
    paper_env(monkeypatch)
    set_mode("PAPER", monkeypatch)
    raw = FakeLegacyBroker(paper=True)
    gated = ModeGatedBroker(raw, ceiling=ExecutionMode.PAPER, queue_blocked=False)
    assert gated.submit_order(order()).status == OrderStatus.FILLED
    assert len(raw.submitted) == 1


def test_paper_mode_refuses_a_live_account(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    set_mode("PAPER", monkeypatch)
    raw = FakeLegacyBroker(paper=False)
    gated = ModeGatedBroker(raw, ceiling=ExecutionMode.PAPER, queue_blocked=False)
    result = gated.submit_order(order())
    assert result.status == OrderStatus.REJECTED
    assert "paper-only" in result.error_message
    assert raw.submitted == []


def test_paper_mode_cannot_obtain_a_live_adapter(monkeypatch):
    paper_env(monkeypatch)
    set_mode("PAPER", monkeypatch)
    with pytest.raises(LiveAccessDenied) as exc:
        access.live_adapter(FakeLegacyBroker(paper=False))
    assert "LIVE_AUTONOMOUS" in str(exc.value)


def test_a_stored_live_mode_without_the_env_switch_is_only_paper(monkeypatch):
    paper_env(monkeypatch)
    set_mode("LIVE_AUTONOMOUS", monkeypatch, live_env=False)
    with pytest.raises(LiveAccessDenied):
        access.live_adapter(FakeLegacyBroker(paper=False))
    raw = FakeLegacyBroker(paper=False)
    gated = ModeGatedBroker(raw, ceiling=ExecutionMode.LIVE_AUTONOMOUS,
                            queue_blocked=False)
    assert gated.submit_order(order()).status == OrderStatus.REJECTED
    assert raw.submitted == []


# ── 3. only LIVE_AUTONOMOUS gets a live adapter ──────────────────────────────

def test_live_autonomous_is_the_only_way_to_a_live_adapter(monkeypatch):
    set_mode("LIVE_AUTONOMOUS", monkeypatch, live_env=True)
    adapter = access.live_adapter(FakeLegacyBroker(paper=False))
    assert adapter.is_live is True


def test_the_desk_never_reaches_real_money_in_any_mode(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    set_mode("LIVE_AUTONOMOUS", monkeypatch, live_env=True)
    raw = FakeLegacyBroker(paper=False)
    desk_broker = access.gate_for_desk(raw)
    result = desk_broker.submit_order(order())
    assert result.status == OrderStatus.REJECTED
    assert raw.submitted == [], "the legacy desk placed a live order"


def test_the_kill_switch_closes_the_live_adapter(monkeypatch):
    set_mode("LIVE_AUTONOMOUS", monkeypatch, live_env=True)
    from src.execution import kill_switch
    kill_switch.engage("testing", actor="test")
    try:
        with pytest.raises(LiveAccessDenied):
            access.live_adapter(FakeLegacyBroker(paper=False))
    finally:
        kill_switch.release(actor="test")


def test_an_adapter_obtained_while_live_stops_working_when_the_mode_drops(monkeypatch):
    set_mode("LIVE_AUTONOMOUS", monkeypatch, live_env=True)
    raw = FakeLegacyBroker(paper=False)
    gated = access.gate_for_approvals(raw)
    assert gated.submit_order(order()).status == OrderStatus.FILLED

    modes.force_down("RESEARCH", actor="test", reason="mode dropped")
    result = gated.submit_order(order(qty=0.4))
    assert result.status == OrderStatus.REJECTED
    assert len(raw.submitted) == 1, "the cached gate kept trading after the drop"


# ── 4. the hard limits apply in LIVE too ─────────────────────────────────────

def test_an_approved_live_order_still_passes_the_hard_limits(monkeypatch):
    set_mode("LIVE_AUTONOMOUS", monkeypatch, live_env=True)
    raw = FakeLegacyBroker(paper=False)
    gated = access.gate_for_approvals(raw)

    too_big = gated.submit_order(order(qty=5))       # ~500 on a 100 limit
    assert too_big.status == OrderStatus.REJECTED
    assert "max_order_value" in too_big.error_message
    assert raw.submitted == []

    ok = gated.submit_order(order(qty=0.5))
    assert ok.status == OrderStatus.FILLED


def test_a_live_order_is_refused_when_the_account_cannot_be_read(monkeypatch):
    set_mode("LIVE_AUTONOMOUS", monkeypatch, live_env=True)

    class Broken(FakeLegacyBroker):
        def get_account(self):
            raise RuntimeError("broker unreachable")

    raw = Broken(paper=False)
    result = access.gate_for_approvals(raw).submit_order(order())
    assert result.status == OrderStatus.REJECTED
    assert "refusing" in result.error_message
    assert raw.submitted == []


# ── the wrapper keeps the guarantees it inherited ────────────────────────────

def test_the_mode_gate_never_hands_out_the_raw_broker(monkeypatch):
    paper_env(monkeypatch)
    set_mode("PAPER", monkeypatch)
    gated = ModeGatedBroker(FakeLegacyBroker(paper=True), ceiling=ExecutionMode.PAPER)
    assert not any("broker" in name for name in vars(gated)), vars(gated)
    with pytest.raises(AttributeError):
        _ = gated._broker
