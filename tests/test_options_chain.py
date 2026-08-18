"""
tests/test_options_chain.py
===========================
The options chain view (src/options/chain.py).

Everything here is about not letting a number be mistaken for a price. The
first AAPL contract the vendor returned during development:

    strike 250, lastPrice 55.20, bid 0.00, ask 0.00,
    last traded four days ago, open interest 2

Fifty-five dollars is the memory of somebody's Thursday. A view that prints it
under "price" has misled the reader before they have read a second column.

The other half is refusing to trade. ARIA has no margin model, no assignment
handling and no risk gate that understands short gamma, so `tradeable` is
False on every response and the refusal travels with the data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.options import chain as C


# ── it will not trade ───────────────────────────────────────────────────────

def test_the_package_refuses_to_trade():
    assert C.TRADEABLE is False
    for phrase in ("margin model", "assignment", "short gamma"):
        assert phrase in C.REFUSAL


def test_the_refusal_travels_with_every_response():
    """A UI cannot quietly grow a buy button if the payload keeps saying no."""
    r = C.chain("NOPE", fetch=lambda kind, *a: [])
    assert r["tradeable"] is False and "does not" in r["refusal"]


# ── a number is not a price ─────────────────────────────────────────────────

def test_a_contract_nobody_quotes_is_dead():
    liq = C.liquidity(bid=0.0, ask=0.0, oi=2, last_trade_age_h=96, is_open=True)
    assert liq["verdict"] == "dead"
    assert any("nobody is quoting" in w for w in liq["why"])


def test_a_closed_market_is_not_a_dead_contract():
    """Outside hours every contract quotes zero on both sides. Calling a strike
    with 4,859 open interest dead is the view lying in the other direction."""
    liq = C.liquidity(bid=0.0, ask=0.0, oi=4859, last_trade_age_h=20,
                      is_open=False)
    assert liq["verdict"] == "market closed"
    assert liq["open_interest_hint"] == 4859


def test_a_wide_spread_is_a_quote_not_a_market():
    liq = C.liquidity(bid=1.0, ask=3.0, oi=500, last_trade_age_h=1, is_open=True)
    assert liq["verdict"] == "thin"
    assert liq["spread_pct"] == pytest.approx(1.0)
    assert any("spread is" in w for w in liq["why"])


def test_thin_open_interest_is_reported():
    liq = C.liquidity(bid=1.0, ask=1.05, oi=3, last_trade_age_h=1, is_open=True)
    assert liq["verdict"] == "thin"
    assert any("open interest 3" in w for w in liq["why"])


def test_a_stale_last_trade_is_reported():
    liq = C.liquidity(bid=1.0, ask=1.05, oi=900, last_trade_age_h=96, is_open=True)
    assert liq["verdict"] == "thin"
    assert any("96h ago" in w for w in liq["why"])


def test_a_real_market_passes_clean():
    liq = C.liquidity(bid=2.00, ask=2.05, oi=900, last_trade_age_h=0.5,
                      is_open=True)
    assert liq["verdict"] == "tradeable" and liq["why"] == []


# ── greeks, and when not to have them ───────────────────────────────────────

def test_greeks_are_computed_for_a_sane_contract():
    g = C.greeks(spot=100, strike=100, years=0.25, vol=0.30, rate=0.04,
                 is_call=True)
    assert 0.45 < g["delta"] < 0.65        # at the money
    assert g["gamma"] > 0 and g["vega"] > 0
    assert g["theta"] < 0                  # long options decay


def test_a_put_delta_is_negative():
    g = C.greeks(100, 100, 0.25, 0.30, 0.04, is_call=False)
    assert -0.65 < g["delta"] < -0.35


def test_call_and_put_delta_obey_parity():
    call = C.greeks(100, 100, 0.5, 0.25, 0.04, True)["delta"]
    put = C.greeks(100, 100, 0.5, 0.25, 0.04, False)["delta"]
    assert call - put == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("kw", [
    {"years": 0}, {"vol": 0}, {"spot": 0}, {"strike": 0},
])
def test_greeks_refuse_impossible_inputs(kw):
    """An expired contract has no meaningful delta, and 0.0 would be a number
    somebody uses."""
    args = {"spot": 100, "strike": 100, "years": 0.25, "vol": 0.3,
            "rate": 0.04, "is_call": True}
    args.update(kw)
    assert C.greeks(**args) == {}


def test_a_placeholder_implied_vol_is_not_a_volatility():
    """The vendor returns 0.00001 when it does not know. Black-Scholes turns
    that into delta 1.0 on a deep in-the-money call — arithmetically right,
    and a lie in effect: certainty manufactured from a missing input."""
    assert C.MIN_USABLE_IV > 0.00001
    g = C.greeks(100, 90, 0.25, 0.00001, 0.04, True)
    assert g["delta"] == pytest.approx(1.0)     # the maths, unguarded
    # the guard lives in _row, and the constant is what enforces it
    assert 0.00001 < C.MIN_USABLE_IV < 0.05


# ── the vendor gap ──────────────────────────────────────────────────────────

def test_a_whole_chain_without_quotes_blames_the_vendor(monkeypatch):
    """Free option feeds routinely return zero on both sides for an entire
    chain. Reporting sixteen strikes with thousands of open contracts as
    "dead" blames the market for the data source."""
    import pandas as pd

    frame = pd.DataFrame([
        {"contractSymbol": "X1", "strike": 100.0, "bid": 0.0, "ask": 0.0,
         "lastPrice": 5.0, "volume": float("nan"), "openInterest": 4859,
         "impliedVolatility": 0.25, "inTheMoney": True,
         "lastTradeDate": pd.Timestamp.now("UTC")},
    ])

    def fetch(kind, *a):
        return ["2099-01-01"] if kind == "expiries" else (frame, frame)

    monkeypatch.setattr(C, "market_open", lambda: True)
    monkeypatch.setattr(C, "_spot", lambda s: (100.0, "test"))
    r = C.chain("AAPL", fetch=fetch)
    assert r["vendor_quotes_missing"] is True
    assert "gap in the data source" in r["note"]
    assert "4,859" in r["note"]


def test_nan_open_interest_does_not_raise(monkeypatch):
    """`int(x or 0)` looks like it handles NaN and does not — NaN is truthy,
    so it passes through and raises on the cast. It did, on the first real put
    chain."""
    import pandas as pd

    frame = pd.DataFrame([
        {"contractSymbol": "X1", "strike": 100.0, "bid": 1.0, "ask": 1.1,
         "lastPrice": 1.05, "volume": float("nan"),
         "openInterest": float("nan"), "impliedVolatility": 0.25,
         "inTheMoney": False, "lastTradeDate": pd.Timestamp.now("UTC")},
    ])

    def fetch(kind, *a):
        return ["2099-01-01"] if kind == "expiries" else (frame, frame)

    monkeypatch.setattr(C, "_spot", lambda s: (100.0, "test"))
    r = C.chain("AAPL", fetch=fetch)
    assert r["calls"][0]["open_interest"] == 0
    assert r["calls"][0]["volume"] is None


def test_nan_is_not_a_number():
    assert C._num(float("nan")) == 0.0
    assert C._num("2.5") == 2.5
    assert C._int(float("nan")) is None
    assert C._int(7.0) == 7


# ── what the response says about itself ─────────────────────────────────────

def test_the_assumptions_ride_with_the_data(monkeypatch):
    import pandas as pd
    frame = pd.DataFrame([
        {"contractSymbol": "X1", "strike": 100.0, "bid": 1.0, "ask": 1.1,
         "lastPrice": 1.05, "volume": 10, "openInterest": 900,
         "impliedVolatility": 0.25, "inTheMoney": False,
         "lastTradeDate": pd.Timestamp.now("UTC")},
    ])

    def fetch(kind, *a):
        return ["2099-01-01"] if kind == "expiries" else (frame, frame)

    monkeypatch.setattr(C, "_spot", lambda s: (100.0, "test"))
    a = C.chain("AAPL", fetch=fetch)["assumptions"]
    assert "European exercise" in a["greeks"]
    assert "approximations" in a["greeks"]
    assert "vendor" in a["implied_vol"]
    assert a["risk_free_source"]


def test_an_unknown_symbol_says_so():
    r = C.chain("NOTATICKER", fetch=lambda kind, *a: [])
    assert "no listed options" in r["error"]
    assert r["expiries"] == []
