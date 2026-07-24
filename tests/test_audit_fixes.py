"""Regression tests for the external audit findings (C2, M1, M2-adjacent)."""
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution.broker_base import OrderStatus


# ── C2: IBKR absence-means-FILLED bug ────────────────────────────────────

class _FakeIB:
    def __init__(self, trades=()):
        self._trades = list(trades)

    def trades(self):
        return self._trades

    def openTrades(self):
        return []


def _ibkr_with(fake_ib):
    from src.execution.ibkr_broker import IBKRBroker
    b = IBKRBroker.__new__(IBKRBroker)      # skip __init__/connection
    b._ib = fake_ib
    b.is_connected = lambda: True
    return b


def test_c2_unknown_order_never_reports_filled():
    b = _ibkr_with(_FakeIB(trades=[]))
    r = b.get_order_status("12345")
    assert r.status != OrderStatus.FILLED
    assert r.status == OrderStatus.SUBMITTED
    assert "cannot confirm" in r.error_message


def test_c2_cancelled_order_reports_cancelled():
    class T:
        class order:
            orderId = 777

        class orderStatus:
            status = "Cancelled"
            filled = 0.0
            avgFillPrice = 0.0
    b = _ibkr_with(_FakeIB(trades=[T()]))
    r = b.get_order_status("777")
    assert r.status == OrderStatus.CANCELLED
    assert r.avg_fill_price == 0.0


# ── M1: complete_with concurrency ────────────────────────────────────────

def test_m1_complete_with_no_shared_state_race():
    from src.inference.providers import Provider
    from src.inference.router import InferenceRouter

    class Echo(Provider):
        name = "ollama"
        local = True

        def complete(self, model, messages, **kw):
            import time
            time.sleep(0.01)      # widen the race window
            return model          # echo back which model actually ran

    r = InferenceRouter(providers={"ollama": Echo()},
                        tiers={"STANDARD": [["ollama", "default"]]})
    r._sleep = lambda s: None
    results = {}

    def call(model):
        c = r.complete_with("ollama", model, [{"role": "user", "content": "x"}])
        results[model] = c.text

    threads = [threading.Thread(target=call, args=(f"model-{i}",))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # every call must have executed against ITS OWN model
    for model, got in results.items():
        assert got == model, f"{model} executed as {got}"
    assert "_EXPLICIT" not in r.tiers
