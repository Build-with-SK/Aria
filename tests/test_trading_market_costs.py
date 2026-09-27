"""
tests/test_trading_market_costs.py
==================================
Two small modules that decide more than they look like they do.

`market.check` is the stale-data protection: every "never trade from a stale
price" claim in this system reduces to this function, so its edge cases —
missing timestamp, naive timestamp, a quote from the future, a crossed book —
are tested one by one rather than assumed.

`costs` is why a small account gets "NO TRADE" so often. The test that matters
most is the one about a per-order minimum on a tiny order: 1.00 on a 4.00 trade
is 250 bps, and any model that treats fees as a percentage hides it.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from trading_helpers import make_quote                     # noqa: E402
from src.trading import costs as _costs                    # noqa: E402
from src.trading.market import Quote, check, market_open    # noqa: E402

NOW = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)     # Tuesday, 10:00 NY


# ── freshness ────────────────────────────────────────────────────────────────

def test_a_fresh_quote_passes():
    fr = check(make_quote(age_s=1, now=NOW), max_age_s=15, now=NOW)
    assert fr.ok and fr.age_s == pytest.approx(1.0)


def test_no_quote_at_all_is_not_fresh():
    assert check(None, max_age_s=15, now=NOW).ok is False


def test_a_quote_without_a_timestamp_is_stale_not_assumed_current():
    fr = check(make_quote(timestamp=None, now=NOW), max_age_s=15, now=NOW)
    assert not fr.ok and "no timestamp" in fr.reason


def test_a_naive_timestamp_is_refused():
    naive = Quote("AAPL", 99.9, 100.1, datetime(2026, 9, 22, 14, 0))
    fr = check(naive, max_age_s=15, now=NOW)
    assert not fr.ok and "timezone" in fr.reason


def test_a_quote_from_the_future_is_a_fault_not_a_bargain():
    fr = check(make_quote(timestamp=NOW + timedelta(seconds=30)), max_age_s=15,
               now=NOW)
    assert not fr.ok and "future" in fr.reason


def test_small_clock_skew_is_tolerated():
    fr = check(make_quote(timestamp=NOW + timedelta(seconds=1)), max_age_s=15,
               now=NOW)
    assert fr.ok


def test_a_crossed_or_empty_book_is_refused():
    assert not check(make_quote(bid=101, ask=100, now=NOW), max_age_s=15, now=NOW).ok
    assert not check(make_quote(bid=0, ask=100, now=NOW), max_age_s=15, now=NOW).ok


def test_an_old_quote_is_refused_and_says_how_old():
    fr = check(make_quote(age_s=45, now=NOW), max_age_s=15, now=NOW)
    assert not fr.ok and "45" in fr.reason


# ── trading hours ────────────────────────────────────────────────────────────

def test_crypto_trades_always():
    assert market_open("crypto", NOW)[0] is True
    assert market_open("crypto", NOW + timedelta(days=5))[0] is True


def test_equities_respect_the_session():
    assert market_open("equity", NOW)[0] is True
    assert market_open("equity", NOW.replace(hour=2))[0] is False        # pre-market
    assert market_open("equity", NOW.replace(hour=21))[0] is False       # after hours
    assert market_open("equity", NOW - timedelta(days=2))[0] is False    # Sunday


def test_a_holiday_is_closed():
    thanksgiving = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)
    ok, why = market_open("equity", thanksgiving)
    assert not ok and "holiday" in why


def test_a_year_with_no_calendar_is_refused_rather_than_guessed():
    ok, why = market_open("equity", datetime(2029, 3, 1, 15, 0, tzinfo=timezone.utc))
    assert not ok and "refusing" in why


def test_an_unknown_asset_class_is_closed():
    assert market_open("wheat_futures", NOW)[0] is False


def test_fx_closes_at_the_weekend():
    saturday = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    assert market_open("forex", saturday)[0] is False
    assert market_open("forex", NOW)[0] is True


# ── costs ────────────────────────────────────────────────────────────────────

def test_a_minimum_commission_dominates_a_tiny_order():
    ibkr = _costs.SCHEDULES["ibkr_equity_fixed"]
    fee = _costs.order_fee(ibkr, side="buy", qty=1, price=4.0)
    assert fee == 1.0
    assert fee / 4.0 * 1e4 == 2500, "a 1.00 minimum on a 4.00 order is 2500 bps"


def test_commission_free_still_costs_the_spread():
    alpaca = _costs.SCHEDULES["alpaca_equity"]
    assert _costs.order_fee(alpaca, side="buy", qty=1, price=100) == 0.0
    est = _costs.round_trip(alpaca, make_quote(bid=99.5, ask=100.5), 1, "buy")
    assert est.total > 0 and est.total_bps > 0


def test_a_sell_pays_the_regulatory_fee_a_buy_does_not():
    alpaca = _costs.SCHEDULES["alpaca_equity"]
    buy = _costs.order_fee(alpaca, side="buy", qty=10, price=100)
    sell = _costs.order_fee(alpaca, side="sell", qty=10, price=100)
    assert sell > buy == 0.0


def test_maker_and_taker_differ_where_the_venue_says_so():
    crypto = _costs.SCHEDULES["alpaca_crypto"]
    taker = _costs.order_fee(crypto, side="buy", qty=1, price=100, maker=False)
    maker = _costs.order_fee(crypto, side="buy", qty=1, price=100, maker=True)
    assert taker > maker > 0


def test_an_unknown_book_is_charged_a_full_spread_not_nothing():
    blind = make_quote(bid=99.5, ask=100.5, bid_size=0, ask_size=0)
    known = make_quote(bid=99.5, ask=100.5, bid_size=10_000, ask_size=10_000)
    assert _costs.slippage_estimate(blind, 10) > _costs.slippage_estimate(known, 10)
    assert _costs.slippage_estimate(known, 10) == 0.0


def test_size_beyond_the_touch_costs_more():
    q = make_quote(bid=99.5, ask=100.5, bid_size=10, ask_size=10)
    assert _costs.slippage_estimate(q, 100) > _costs.slippage_estimate(q, 10)


def test_impact_grows_with_the_square_root_of_participation():
    q = make_quote(bid=99.95, ask=100.05, bid_size=10_000, ask_size=10_000)
    small = _costs.slippage_estimate(q, 100, adv=1_000_000, daily_vol=0.02)
    big = _costs.slippage_estimate(q, 400, adv=1_000_000, daily_vol=0.02)
    assert big > small
    assert big / small == pytest.approx(8.0, rel=0.01)   # 4x size, 2x sqrt, 4*2


def test_a_round_trip_charges_both_ends():
    sched = _costs.SCHEDULES["simulated"]
    q = make_quote(bid=99.95, ask=100.05)
    est = _costs.round_trip(sched, q, 1, "buy")
    assert est.entry_fee > 0 and est.exit_fee > 0
    assert est.spread == pytest.approx(q.spread)          # half-spread twice
    assert est.total_bps == pytest.approx(est.total / est.notional * 1e4)


def test_the_fee_schedules_say_when_they_were_written():
    for name, sched in _costs.SCHEDULES.items():
        assert sched.as_of, f"{name} has no as_of date"
        assert sched.note, f"{name} does not say it must be verified"
