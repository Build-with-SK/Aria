"""
tests/test_roundtrip_and_focus.py
=================================
Two things the owner asked for in the same breath.

ROUND TRIPS. "Buy 1 AAPL and sell it off in 5 mins" — one sentence, two legs,
and the second only means anything once the first has happened. The dangerous
case has its own test: a timed exit firing against a position that a stop
already took does not close a long, it OPENS A SHORT.

FOCUS. The desk ranked signals.json on abs(composite_score) while the
41-module engine scored the same watchlist daily and was never asked what to
trade. Two programs sharing a database. The ranking comes from the engine now,
and where it falls back to the composite it says so.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.desk import focus as F
from src.desk import intents as I
from src.desk import roundtrip as R


# ── "and sell it off in 5 mins" ─────────────────────────────────────────────

def test_a_round_trip_is_understood():
    r = I.parse("buy 1 AAPL and sell it off in 5 mins")
    assert r["side"] == "buy" and r["ticker"] == "AAPL" and r["qty"] == 1
    assert r["exit_after_minutes"] == 5
    assert r["immediate"] is True


@pytest.mark.parametrize("said,minutes", [
    ("buy 1 AAPL and sell it in 30 seconds", 0.5),
    ("buy 1 AAPL and close it in 2 hours", 120),
    ("buy 1 AAPL then dump it in 1 day", 1440),
])
def test_the_units_are_understood(said, minutes):
    assert I.parse(said)["exit_after_minutes"] == pytest.approx(minutes)


def test_a_delayed_entry_is_not_a_round_trip():
    """"buy in 5 minutes" delays the ENTRY. "buy and sell in 5 minutes"
    schedules an EXIT. Confusing the two either trades early or never."""
    r = I.parse("buy 5 NVDA in 30 minutes")
    assert r.get("exit_after_minutes") is None
    assert r["not_before_minutes"] == 30
    assert r["immediate"] is False


def test_a_plain_order_is_immediate():
    assert I.parse("buy 1 AAPL")["immediate"] is True
    assert I.parse("buy 20 AAPL under 210")["immediate"] is False


# ── the exit is scheduled from the fill ─────────────────────────────────────

def test_no_fill_means_no_exit_leg():
    """Five minutes from a fill that never happened is a countdown against
    nothing."""
    entry = {"id": "int-1", "side": "buy", "ticker": "AAPL", "qty": 1,
             "exit_after_minutes": 5}
    assert R.on_fill(entry, {"ok": False, "error": "risk officer refused"}) is None


def test_the_exit_closes_what_was_filled_not_what_was_asked():
    """A partial fill of 1 share must not produce a sell of 20."""
    written = {}
    entry = {"id": "int-1", "side": "buy", "ticker": "AAPL", "qty": 20,
             "exit_after_minutes": 5}
    R.schedule_exit(entry, filled_qty=1, minutes=5,
                    record=lambda leg, **kw: written.update(leg) or {"id": "x"})
    assert written["qty"] == 1
    assert written["side"] == "sell"
    assert written["closes"] == "int-1"


def test_the_exit_leg_carries_its_clock():
    written = {}
    R.schedule_exit({"id": "e", "side": "buy", "ticker": "AAPL", "qty": 1},
                    filled_qty=1, minutes=5,
                    record=lambda leg, **kw: written.update(leg) or {"id": "x"})
    scheduled = datetime.fromisoformat(written["not_before"])
    assert timedelta(minutes=4) < scheduled - datetime.now() <= timedelta(minutes=5)


def test_a_sell_entry_exits_by_buying_back():
    written = {}
    R.schedule_exit({"id": "e", "side": "sell", "ticker": "AAPL", "qty": 2},
                    filled_qty=2, minutes=5,
                    record=lambda leg, **kw: written.update(leg) or {"id": "x"})
    assert written["side"] == "buy"


def test_a_leg_does_not_fire_before_its_clock():
    future = (datetime.now() + timedelta(minutes=5)).isoformat()
    assert R.due({"not_before": future}) is False
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    assert R.due({"not_before": past}) is True
    assert R.due({}) is True                 # no clock means now


# ── the bug that would turn a round trip into a short ───────────────────────

def test_a_timed_exit_will_not_short_a_flat_book():
    """THE test. If a stop took the shares first, the original sell opens a
    SHORT rather than closing a long."""
    account = {"positions": []}
    leg = R.check_exit_leg({"ticker": "AAPL", "qty": 1, "side": "sell"}, account)
    assert leg["act"] is False
    assert "would open a short" in leg["why"]


def test_a_partly_stopped_position_is_only_partly_closed():
    account = {"positions": [{"ticker": "AAPL", "qty": 3}]}
    leg = R.check_exit_leg({"ticker": "AAPL", "qty": 10, "side": "sell"}, account)
    assert leg["act"] is True and leg["qty"] == 3
    assert "that is what is left" in leg["why"]


def test_a_full_position_closes_in_full():
    account = {"positions": [{"ticker": "AAPL", "qty": 10}]}
    leg = R.check_exit_leg({"ticker": "AAPL", "qty": 10, "side": "sell"}, account)
    assert leg["act"] is True and leg["qty"] == 10


def test_closing_a_short_reads_the_negative_side():
    account = {"positions": [{"ticker": "AAPL", "qty": -5}]}
    leg = R.check_exit_leg({"ticker": "AAPL", "qty": 5, "side": "buy"}, account)
    assert leg["act"] is True and leg["qty"] == 5


def test_a_long_is_not_mistaken_for_a_short_to_cover():
    account = {"positions": [{"ticker": "AAPL", "qty": 5}]}
    leg = R.check_exit_leg({"ticker": "AAPL", "qty": 5, "side": "buy"}, account)
    assert leg["act"] is False


# ── the desk trades what the engine recommends ──────────────────────────────

def prediction(ticker, p_bull, at=None, **kw):
    return {"ticker": ticker, "p_bull": p_bull,
            "at": (at or datetime.now()).isoformat(timespec="seconds"),
            "confidence": 0.6, "direction": "bull" if p_bull >= 0.5 else "bear",
            **kw}


def test_conviction_ranks_not_direction(monkeypatch):
    """A bearish 0.28 is as interesting as a bullish 0.72. Preferring longs
    would be a thumb on the scale nobody asked for."""
    monkeypatch.setattr("src.v5.learning.load_predictions", lambda: [
        prediction("AAA", 0.55), prediction("BBB", 0.28), prediction("CCC", 0.72),
    ])
    ranked = F.recommended()
    assert [r["ticker"] for r in ranked[:2]] == ["BBB", "CCC"] or \
           [r["ticker"] for r in ranked[:2]] == ["CCC", "BBB"]
    assert ranked[-1]["ticker"] == "AAA"
    assert {r["side"] for r in ranked} == {"short", "long"}


def test_a_coin_flip_is_not_a_view(monkeypatch):
    monkeypatch.setattr("src.v5.learning.load_predictions",
                        lambda: [prediction("AAA", 0.51)])
    assert F.recommended() == []


def test_a_stale_prediction_is_not_a_current_view(monkeypatch):
    old = datetime.now() - timedelta(days=F.MAX_PREDICTION_AGE_DAYS + 1)
    monkeypatch.setattr("src.v5.learning.load_predictions",
                        lambda: [prediction("AAA", 0.9, at=old)])
    assert F.recommended() == []


def test_the_newest_view_per_ticker_wins(monkeypatch):
    old = datetime.now() - timedelta(days=1)
    monkeypatch.setattr("src.v5.learning.load_predictions", lambda: [
        prediction("AAA", 0.95, at=old), prediction("AAA", 0.60),
    ])
    ranked = F.recommended()
    assert len(ranked) == 1 and ranked[0]["p_bull"] == 0.60


def test_held_and_pending_names_are_skipped(monkeypatch):
    monkeypatch.setattr("src.v5.learning.load_predictions", lambda: [
        prediction("AAA", 0.9), prediction("BBB", 0.8),
    ])
    monkeypatch.setattr(F, "load_data_json", lambda *a: {}, raising=False)
    out = F.focus_tickers(2, {"positions": [{"ticker": "AAA"}]},
                          executable=lambda t: True)
    assert "AAA" not in out["tickers"] and "BBB" in out["tickers"]


def test_the_fallback_admits_it_is_a_fallback(monkeypatch):
    """A slate built from the technical composite must not pass itself off as
    the engine's picks."""
    monkeypatch.setattr("src.v5.learning.load_predictions", lambda: [])
    monkeypatch.setattr("src.desk.opinion.load_data_json", lambda *a: {
        "ZZZ": {"composite_score": 55.0, "current_price": 10.0},
    })
    out = F.focus_tickers(1, {"positions": []}, executable=lambda t: True)
    assert out["tickers"] == ["ZZZ"]
    assert out["from_recommendations"] == 0 and out["from_fallback"] == 1
    assert "composite" in out["note"]
    assert out["why"][0]["picked_by"] == "composite fallback"


def test_engine_picks_are_labelled_as_such(monkeypatch):
    monkeypatch.setattr("src.v5.learning.load_predictions",
                        lambda: [prediction("AAA", 0.85)])
    out = F.focus_tickers(1, {"positions": []}, executable=lambda t: True)
    assert out["from_recommendations"] == 1 and out["from_fallback"] == 0
    assert out["why"][0]["picked_by"] == "recommendation"
    assert out["why"][0]["source"] == "v5 research engine"
