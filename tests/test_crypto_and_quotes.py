"""
tests/test_crypto_and_quotes.py
===============================
Two bugs in the same twenty lines of src/execution/alpaca_broker.py, both
silent, both live.

CRYPTO NEVER WORKED. Alpaca's crypto endpoint validates symbols against
^[A-Z]+x?/[A-Z]+$ and this codebase speaks yfinance's `BTC-USD`. Nothing
translated between them, so every crypto quote was rejected — 13,293 times in
one backend log, once every three seconds from the reflex poll, for as long as
a crypto name sat on the watchlist. The only symptom was a warning line.

A MISSING SIDE WAS PRICED AT ZERO. `mid = (ask + bid) / 2` with an absent ask
returns half the bid. AAPL bid 290.54 produced a mid of 145.27. Anything
priced off that mid sits fifty percent below the market.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

pytest.importorskip("alpaca")
from src.execution.alpaca_broker import (AlpacaBroker,      # noqa: E402
                                         crypto_symbol)


class FakeQuote:
    def __init__(self, ask, bid):
        self.ask_price, self.bid_price = ask, bid


class FakeCrypto:
    """Stands in for Alpaca: rejects the hyphen form exactly as it does, and
    keys its reply by the symbol IT was given."""
    def __init__(self):
        self.asked = None

    def get_crypto_latest_quote(self, req):
        symbol = req["symbol_or_symbols"][0]
        self.asked = symbol
        if "-" in symbol:
            raise ValueError(f"invalid symbol: {symbol} does not match "
                             f"^[A-Z]+x?/[A-Z]+$")
        return {symbol: FakeQuote(64320.66, 64252.27)}


class FakeStock:
    def __init__(self, ask=290.0, bid=289.0):
        self.ask, self.bid = ask, bid

    def get_stock_latest_quote(self, req):
        symbol = req["symbol_or_symbols"][0]
        return {symbol: FakeQuote(self.ask, self.bid)}


@pytest.fixture
def broker(monkeypatch):
    b = AlpacaBroker.__new__(AlpacaBroker)
    b._crypto_data = FakeCrypto()
    b._stock_data = FakeStock()
    monkeypatch.setattr("src.execution.alpaca_broker.CryptoLatestQuoteRequest",
                        lambda **kw: kw)
    monkeypatch.setattr("src.execution.alpaca_broker.StockLatestQuoteRequest",
                        lambda **kw: kw)
    return b


# ── the symbol translation ──────────────────────────────────────────────────

def test_the_translator_is_not_an_adapter_member():
    """live_guard.py refuses any unclassified public member of a broker
    adapter, and it is right to. A string translator that touches nothing
    belongs outside the class rather than inside the live-money gate's
    classification table."""
    assert not hasattr(AlpacaBroker, "crypto_symbol")


@pytest.mark.parametrize("given,sent", [
    ("BTC-USD", "BTC/USD"),
    ("INJ-USD", "INJ/USD"),
    ("eth-usd", "ETH/USD"),
    ("BTC/USD", "BTC/USD"),      # already correct, left alone
    ("AAPL", "AAPL"),            # equities are untouched
])
def test_symbols_are_translated_at_the_boundary(given, sent):
    assert crypto_symbol(given) == sent


def test_a_crypto_quote_actually_returns_a_price(broker):
    """The regression. This raised for every crypto name, every three
    seconds."""
    q = broker.get_quote("BTC-USD")
    assert q["bid"] == pytest.approx(64252.27)
    assert broker._crypto_data.asked == "BTC/USD"


def test_the_reply_is_read_by_the_symbol_alpaca_was_given(broker):
    """Alpaca keys its response by ITS symbol, so a hyphenated lookup would
    miss even after the request succeeded — the same bug one layer down."""
    assert broker.get_quote("BTC-USD") != {}


def test_equities_do_not_go_down_the_crypto_path(broker):
    broker.get_quote("AAPL")
    assert broker._crypto_data.asked is None


# ── a missing side is not a price ───────────────────────────────────────────

def test_a_zero_ask_does_not_halve_the_mid(broker):
    """AAPL bid 290.54 with no ask returned a mid of 145.27. Anything priced
    off that sits fifty percent below the market."""
    broker._stock_data = FakeStock(ask=0.0, bid=290.54)
    q = broker.get_quote("AAPL")
    assert q["mid"] == pytest.approx(290.54)
    assert q["one_sided"] is True


def test_a_zero_bid_does_not_halve_the_mid(broker):
    broker._stock_data = FakeStock(ask=290.54, bid=0.0)
    assert broker.get_quote("AAPL")["mid"] == pytest.approx(290.54)


def test_a_two_sided_quote_still_averages(broker):
    broker._stock_data = FakeStock(ask=291.0, bid=289.0)
    q = broker.get_quote("AAPL")
    assert q["mid"] == pytest.approx(290.0) and q["one_sided"] is False


def test_no_quote_at_all_returns_nothing(broker):
    """Empty means "no price", which callers already handle. A zero mid would
    be a number, and a number gets used."""
    broker._stock_data = FakeStock(ask=0.0, bid=0.0)
    assert broker.get_quote("AAPL") == {}
