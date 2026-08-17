"""
tests/test_vendor_deadline.py
=============================
The wall clock on a vendor fetch (src/v5/vendors.py).

Every vendor here except one passes a timeout to urllib. yfinance owns its own
HTTP stack and `history()` takes no timeout argument, so a socket that
connects and then goes quiet blocks forever.

It did. A baseline capture on 2026-08-16 spent 45,704 seconds — twelve and a
half hours, overnight — inside one debate, because a fetch stalled while the
link was busy and nothing was holding a clock. The desk's hunt cycle takes a
lock and runs `max_instances=1`, so the same stall there stops it hunting
until someone restarts the process, and the watchdog does not notice: it
checks that jobs EXIST, and a job wedged mid-execution still exists.

The fix turns an indefinite hang into an ordinary vendor failure, which the
failover chain already knows how to handle. These tests pin that, and pin that
a slow-but-working fetch is not punished for being slow.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.v5 import vendors as V


def test_a_stalled_call_is_abandoned_not_waited_on():
    """The whole point. Without this the caller waits as long as the socket
    feels like being quiet."""
    started = time.time()
    with pytest.raises(TimeoutError) as ei:
        V._with_deadline(lambda: time.sleep(30), 1, "a stalled vendor")
    assert time.time() - started < 5
    assert "abandoned" in str(ei.value)
    assert "a stalled vendor" in str(ei.value)


def test_a_fetch_that_answers_in_time_returns_normally():
    assert V._with_deadline(lambda: "some data", 5, "quick vendor") == "some data"


def test_a_slow_but_working_fetch_is_not_punished():
    """A ten-year daily history over a slow link is legitimately slow. The
    deadline exists to end hangs, not to fail honest work."""
    def slow():
        time.sleep(0.4)
        return "arrived"

    assert V._with_deadline(slow, 5, "slow vendor") == "arrived"


def test_the_vendors_own_error_is_re_raised_unchanged():
    """A 404 must stay a 404 rather than becoming a timeout — the failover
    chain records the reason, and 'timed out' would be a lie about why."""
    def boom():
        raise ValueError("empty response from the vendor")

    with pytest.raises(ValueError, match="empty response"):
        V._with_deadline(boom, 5, "broken vendor")


def test_a_hung_fetch_becomes_an_ordinary_vendor_failure(monkeypatch):
    """End to end: the stall must arrive at the caller as a VendorResult with
    a reason, so the chain fails over instead of stopping."""
    class HangingTicker:
        def __init__(self, symbol):
            pass

        def history(self, **kw):
            time.sleep(30)

    fake = type("yf", (), {"Ticker": HangingTicker})
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    monkeypatch.setattr(V, "YF_DEADLINE", 1)

    started = time.time()
    result = V.fetch_yfinance("AAPL")
    assert time.time() - started < 6
    assert not result.ok
    assert "did not answer" in result.error
    assert result.vendor == "yfinance"


def test_the_deadline_is_finite_and_generous():
    """Finite is the requirement. Generous is so a slow link does not look
    like an outage."""
    assert 15 <= V.YF_DEADLINE <= 120


def test_the_abandoned_thread_cannot_hold_the_process_open():
    """A stuck call cannot be killed, only left behind — and a non-daemon
    thread left behind would hang interpreter exit, trading a stalled cycle
    for a machine that will not shut down."""
    import threading

    before = set(threading.enumerate())
    with pytest.raises(TimeoutError):
        V._with_deadline(lambda: time.sleep(5), 1, "abandoned vendor")
    leaked = [t for t in threading.enumerate() if t not in before]
    assert leaked, "the test needs the thread to still be running"
    assert all(t.daemon for t in leaked)
