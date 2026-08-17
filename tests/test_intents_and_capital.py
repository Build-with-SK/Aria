"""
tests/test_intents_and_capital.py
=================================
"Buy 20 AAPL" (src/desk/intents.py) and the £100 capital base
(src/desk/capital.py).

The parser is the dangerous half. It reads free text — typed, and worse,
TRANSCRIBED — and turns it into something that can become an order. So most of
what follows is about what it must NOT find: a trade in a question, a ticker in
the word "shares", a fill when her data is stale or her own read disagrees.

Nothing here reaches a broker. `execute()` refuses outright unless the paper
contract is confirmed, and that refusal is tested too.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.desk import capital as C
from src.desk import intents as I


# ── understanding what he said ──────────────────────────────────────────────

@pytest.mark.parametrize("said,side,ticker,qty", [
    ("buy 20 shares of AAPL", "buy", "AAPL", 20),
    ("buy me 20 shares of AAPL", "buy", "AAPL", 20),
    ("buy 20 shares AAPL", "buy", "AAPL", 20),
    ("sell 5 NVDA", "sell", "NVDA", 5),
    ("grab me 10 TSLA", "buy", "TSLA", 10),
    ("get us 5 NVDA", "buy", "NVDA", 5),
    ("dump 3 META", "sell", "META", 3),
    ("buy AAPL 20", "buy", "AAPL", 20),
])
def test_an_order_is_understood(said, side, ticker, qty):
    r = I.parse(said)
    assert r and r["side"] == side and r["ticker"] == ticker and r["qty"] == qty


@pytest.mark.parametrize("said", [
    "what do you think about Apple?",
    "can you analyse NVDA for me",
    "I bought a house last year",
    "sell everything",
    "buy 20 of shares",
    "how many shares of AAPL do I own",
    "",
])
def test_a_sentence_that_is_not_an_order_is_not_one(said):
    """The common and correct outcome. A parser that found a trade in "what do
    you think about Apple" would be far worse than one that misses."""
    assert I.parse(said) is None


def test_a_short_is_a_sell():
    assert I.parse("short 3 SPY")["side"] == "sell"


def test_his_conditions_are_captured():
    r = I.parse("buy 20 AAPL when it dips under 210")
    assert r["limit_price"] == 210.0
    r = I.parse("sell 5 NVDA above 130")
    assert r["side"] == "sell" and r["min_price"] == 130.0


def test_a_notional_amount_is_not_read_as_a_share_count():
    """"buy £50 of AAPL" is fifty POUNDS, not fifty shares — at ~$230 a share
    that is the difference between £50 and £11,500."""
    r = I.parse("buy £50 of AAPL")
    assert r["qty"] == 50.0 and r["notional"] is True
    assert I.parse("buy 50 AAPL")["notional"] is False


def test_filler_words_are_never_tickers():
    for said in ("buy 20 shares", "sell 3 units", "buy 10 more"):
        r = I.parse(said)
        assert r is None or r["ticker"] not in I._NOT_TICKERS


# ── when the moment is right ────────────────────────────────────────────────

def intent(**kw):
    base = {"id": "int-test", "status": I.OPEN, "side": "buy", "ticker": "AAPL",
            "qty": 20, "limit_price": None, "min_price": None,
            "expires_at": (datetime.now() + timedelta(days=3)).isoformat()}
    base.update(kw)
    return base


def test_it_will_not_fill_without_a_price():
    d = I.evaluate(intent(), price=None)
    assert not d["ready"] and "will not fill blind" in d["reasons"][0]


def test_his_limit_is_respected():
    assert not I.evaluate(intent(limit_price=210), price=225)["ready"]
    assert I.evaluate(intent(limit_price=210), price=205)["ready"]


def test_a_floor_price_is_respected():
    assert not I.evaluate(intent(side="sell", min_price=130), price=120)["ready"]
    assert I.evaluate(intent(side="sell", min_price=130), price=140)["ready"]


def test_she_holds_when_her_own_read_disagrees():
    """He can overrule her. He cannot be un-told."""
    d = I.evaluate(intent(), price=200, opinion={"direction": "bear"})
    assert not d["ready"]
    assert any("points the other way" in r for r in d["reasons"])
    assert any("Confirm and she will fill it" in r for r in d["reasons"])


def test_agreement_does_not_block():
    assert I.evaluate(intent(), price=200,
                      opinion={"direction": "bull"})["ready"]


def test_she_will_not_time_an_entry_on_stale_prices():
    d = I.evaluate(intent(), price=200,
                   opinion={"direction": "bull", "stale": True})
    assert not d["ready"]
    assert any("stale" in r for r in d["reasons"])


def test_an_expired_intent_does_not_fire():
    old = intent(expires_at=(datetime.now() - timedelta(days=1)).isoformat())
    d = I.evaluate(old, price=100)
    assert not d["ready"] and d["expired"]


def test_a_closed_intent_does_not_fire():
    assert not I.evaluate(intent(status=I.FILLED), price=100)["ready"]


# ── the book ────────────────────────────────────────────────────────────────

@pytest.fixture
def book(tmp_path, monkeypatch):
    path = tmp_path / "intents.json"
    monkeypatch.setattr(I, "INTENTS_FILE", path)
    return path


def test_recording_is_not_filling(book):
    row = I.record(I.parse("buy 20 AAPL"), source="voice", said="buy 20 AAPL")
    assert row["status"] == I.OPEN
    assert row["source"] == "voice"
    assert len(I.open_intents()) == 1


def test_an_intent_expires_rather_than_lingering(book):
    row = I.record(I.parse("buy 20 AAPL"), expiry_days=I.DEFAULT_EXPIRY_DAYS)
    assert datetime.fromisoformat(row["expires_at"]) > datetime.now()


def test_cancelling_closes_it(book):
    row = I.record(I.parse("buy 20 AAPL"))
    assert I.cancel(row["id"], "changed my mind")["status"] == I.CANCELLED
    assert I.open_intents() == []
    assert I.cancel(row["id"]) is None          # already closed


def test_a_corrupt_book_does_not_read_as_no_instructions(book):
    """"He asked for nothing" and "the file is broken" must not look the
    same — one of them means an order he expects is never placed."""
    book.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        I.open_intents()


# ── where it stops ──────────────────────────────────────────────────────────

def test_it_refuses_to_fill_without_the_paper_contract(monkeypatch):
    monkeypatch.setattr("src.desk.config.paper_mode_confirmed", lambda: False)
    r = I.execute(intent(), price=200)
    assert not r["ok"]
    assert "live-money gate" in r["error"]


def test_a_confirmed_paper_contract_reaches_the_executor(monkeypatch):
    monkeypatch.setattr("src.desk.config.paper_mode_confirmed", lambda: True)
    seen = {}
    r = I.execute(intent(), price=200,
                  fill=lambda i, p: seen.update(ticker=i["ticker"], price=p)
                  or {"ok": True})
    assert r["ok"] and seen == {"ticker": "AAPL", "price": 200}


# ── the capital base ────────────────────────────────────────────────────────

def test_no_base_means_size_against_the_broker():
    b = C.sizing_base({"equity": 10000.0}, {"capital_base": 0})
    assert b["equity"] == 10000.0 and b["source"] == "broker"


def test_a_declared_base_is_converted_and_used(monkeypatch):
    monkeypatch.setattr("src.data.currency.convert",
                        lambda amt, a, b: amt * 1.35)
    b = C.sizing_base({"equity": 10000.0},
                      {"capital_base": 100.0, "capital_currency": "GBP"})
    assert b["source"] == "capital_base"
    assert b["equity"] == pytest.approx(135.0)
    assert b["declared"] == {"amount": 100.0, "currency": "GBP"}


def test_a_missing_rate_is_refused_not_guessed(monkeypatch):
    """A base silently sized at the wrong rate is worse than one that says it
    does not know."""
    monkeypatch.setattr("src.data.currency.convert", lambda *a: None)
    b = C.sizing_base({"equity": 10000.0},
                      {"capital_base": 100.0, "capital_currency": "GBP"})
    assert b["source"] == "broker"
    assert "could not be read" in b["note"]


def test_the_account_caps_the_declared_base(monkeypatch):
    """£100 against an account holding $10 is $10. Otherwise the caps describe
    money that is not there."""
    monkeypatch.setattr("src.data.currency.convert", lambda amt, a, b: amt * 1.35)
    b = C.sizing_base({"equity": 10.0},
                      {"capital_base": 100.0, "capital_currency": "GBP"})
    assert b["equity"] == 10.0
    assert "Capped at the broker" in b["note"]


def test_a_base_too_small_to_buy_anything_is_refused():
    b = C.sizing_base({"equity": 10000.0},
                      {"capital_base": 1.0, "capital_currency": "USD"})
    assert b["source"] == "broker"
    assert "round to zero" in b["note"]


def test_the_base_implies_realistic_caps(monkeypatch):
    monkeypatch.setattr("src.data.currency.convert", lambda amt, a, b: amt * 1.35)
    monkeypatch.setattr("src.desk.config.load_config", lambda: {
        "capital_base": 100.0, "capital_currency": "GBP",
        "max_name_pct": 5.0, "max_sector_pct": 25.0, "heat_cap_pct": 10.0,
        "drawdown_halt_pct": 2.0, "daily_notional_budget": 20000.0})
    d = C.describe({"equity": 10000.0})
    assert d["implies"]["max_per_name"] == pytest.approx(6.75, abs=0.05)
    assert d["implies"]["drawdown_halt_at"] == pytest.approx(2.70, abs=0.05)
    # A $20,000 daily budget against a £100 base is not a budget.
    assert d["implies"]["daily_budget"] == pytest.approx(135.0)


def test_sizing_equity_and_drawdown_equity_are_not_the_same_number():
    """Conflating them halted the desk: the day state had recorded the
    broker's $10,005 as the morning's start, and sizing against £100 read as a
    98.65% intraday loss. The circuit breaker fired on every candidate."""
    import inspect
    from src.desk import risk_officer
    src = inspect.getsource(risk_officer.RiskOfficer.review)
    assert "broker_equity" in src
    assert "(start_eq - broker_equity)" in src
