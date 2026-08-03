"""
Native-currency resolution and display conversion.

The two errors these tests exist to prevent, both of which were live in the app
before this module existed:

  * treating an INR-quoted NSE price as USD  → ~88× overstatement
  * treating a London price as GBP when the  → 100× overstatement
    exchange quotes in PENCE (GBp)

Both are silent: the number simply looks wrong to someone who knows the stock,
and looks fine to everyone else.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data import currency as cur


# ── native currency resolution ──────────────────────────────────────────────

@pytest.mark.parametrize("symbol,expected", [
    ("AAPL", "USD"),
    ("RELIANCE.NS", "INR"),
    ("TCS.NS", "INR"),
    ("INFY.BO", "INR"),
    ("HSBA.L", "GBp"),      # London quotes in PENCE, not pounds
    ("BP.L", "GBp"),
    ("SAP.DE", "EUR"),
    ("7203.T", "JPY"),
    ("BHP.AX", "AUD"),
    ("SHOP.TO", "CAD"),
    ("BTC-USD", "USD"),
    ("GC=F", "USD"),
])
def test_native_currency_from_symbol(symbol, expected):
    assert cur.native_currency(symbol) == expected


def test_london_is_pence_not_pounds():
    """The single most important assertion in this file."""
    assert cur.native_currency("HSBA.L") == "GBp"
    assert cur.native_currency("HSBA.L") != "GBP"


def test_an_index_has_no_currency():
    """An index level is not a price and must never be converted."""
    assert cur.native_currency("^GSPC") is None
    assert cur.native_currency("^VIX") is None


def test_fx_pair_reports_its_quote_currency():
    assert cur.native_currency("USDINR=X") == "INR"
    assert cur.native_currency("USDJPY=X") == "JPY"


def test_unknown_symbol_returns_none_rather_than_guessing():
    assert cur.native_currency("") is None
    assert cur.native_currency(None) is None


# ── conversion ──────────────────────────────────────────────────────────────

@pytest.fixture
def fixed_rates(monkeypatch):
    """Fixed rates so the arithmetic is checkable by hand: 1 USD = 90 INR = 0.75 GBP."""
    monkeypatch.setattr(cur, "rates", lambda base="USD": {
        "base": "USD",
        "rates": {"USD": 1.0, "INR": 90.0, "GBP": 0.75, "EUR": 0.9, "JPY": 150.0},
        "unavailable": [], "as_of": "test",
    })


def test_usd_to_inr(fixed_rates):
    assert cur.convert(100, "USD", "INR") == pytest.approx(9000.0)


def test_inr_to_usd_is_not_multiplied(fixed_rates):
    """An INR price converted to USD must go DOWN, not up 90×."""
    assert cur.convert(9000, "INR", "USD") == pytest.approx(100.0)


def test_a_native_currency_is_left_alone(fixed_rates):
    assert cur.convert(1307.8, "INR", "INR") == pytest.approx(1307.8)


def test_pence_converts_as_pounds_not_as_pounds_times_100(fixed_rates):
    """1576 GBp is £15.76. In USD that is ~$21, not ~$2,101."""
    usd = cur.convert(1576, "GBp", "USD")
    assert usd == pytest.approx(15.76 / 0.75, rel=1e-6)
    assert usd < 25, "pence was treated as pounds — the 100× bug is back"


def test_pence_to_inr(fixed_rates):
    inr = cur.convert(1576, "GBp", "INR")
    assert inr == pytest.approx(15.76 / 0.75 * 90, rel=1e-6)
    assert inr < 3000, "a £15.76 share must not show as ₹190,000"


def test_to_base_handles_pence(fixed_rates):
    assert cur.to_base(1576, "GBp", "USD") == pytest.approx(15.76 / 0.75, rel=1e-6)


def test_conversion_without_a_rate_returns_none(fixed_rates):
    assert cur.convert(100, "XYZ", "USD") is None
    assert cur.convert(100, "USD", "XYZ") is None


def test_conversion_of_none_is_none(fixed_rates):
    assert cur.convert(None, "USD", "INR") is None
    assert cur.to_base(None, "USD") is None


def test_batch_lookup_shape():
    out = cur.currencies_for(["AAPL", "RELIANCE.NS", "HSBA.L"])
    assert out == {"AAPL": "USD", "RELIANCE.NS": "INR", "HSBA.L": "GBp"}
