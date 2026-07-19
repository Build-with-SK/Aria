"""Module A4 — the push message format is a contract, not a suggestion."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.desk.notify import fmt_entry, fmt_exit


def test_entry_exact_shape():
    msg = fmt_entry("buy", 12, "AAPL", 312.05, 303.71, 325.97)
    assert msg == "▲ BUY 12 AAPL @ 312.05 | SL 303.71 | sell at 325.97 or on signal flip"


def test_exit_exact_shape():
    msg = fmt_exit("buy", 12, "AAPL", 318.40, 76.20, 2.0, "target hit")
    assert msg == "▼ SOLD 12 AAPL @ 318.40 | P&L +$76.20 (+2.0%) | reason: target hit"


def test_exit_loss_shape():
    msg = fmt_exit("buy", 5, "MSFT", 480.10, -123.45, -3.2, "stop hit")
    assert "| P&L -$123.45 (-3.2%) |" in msg and msg.startswith("▼ SOLD 5 MSFT")


def test_short_side_arrows():
    assert fmt_entry("sell", 5, "TSLA", 200.0, 210.0, 180.0).startswith("▼ SELL")
    assert fmt_exit("sell", 5, "TSLA", 190.0, 50.0, 5.0, "target hit").startswith("▲ COVERED")


def test_entry_no_stop_no_target():
    msg = fmt_entry("buy", 1, "BTC-USD", 60000.0, None, None)
    assert "SL none" in msg and "signal flip" in msg


def test_no_prose_no_extra_emojis():
    for msg in (fmt_entry("buy", 12, "AAPL", 312.05, 303.71, 325.97),
                fmt_exit("buy", 12, "AAPL", 318.4, 76.2, 2.0, "target hit")):
        assert "thesis" not in msg.lower() and "conviction" not in msg.lower()
        assert msg.count("▲") + msg.count("▼") == 1
