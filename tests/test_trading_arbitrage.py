"""
tests/test_trading_arbitrage.py
===============================
Most "arbitrage" is a spread that disappears once you subtract what it costs to
take it. These tests are the subtraction, case by case:

    gross spread → fees → slippage → borrow → transfer → net edge
    then liquidity, then whether both legs will actually execute.

The verdicts that matter most are the refusals: STALE, ILLIQUID,
FALSE_ARBITRAGE, EXECUTION_RISK, and plain NOT_ARBITRAGE — which is the correct
answer nearly all of the time on a small account.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.trading import arbitrage as arb          # noqa: E402
from src.trading.costs import FeeSchedule          # noqa: E402
from src.trading.market import Quote               # noqa: E402

NOW = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
FEES = FeeSchedule("test_venue", taker_bps=5.0)


def quote(bid, ask, *, age_s=0.0, size=10_000.0, symbol="BTC/USD") -> Quote:
    return Quote(symbol=symbol, bid=bid, ask=ask,
                 timestamp=NOW - timedelta(seconds=age_s),
                 bid_size=size, ask_size=size, asset_class="crypto")


def legs(*, buy_ask=100.00, sell_bid=100.50, buy_age=0.0, sell_age=0.0,
         buy_size=10_000.0, sell_size=10_000.0, venues=("venue_a", "venue_b"),
         **sell_kw) -> list[arb.Leg]:
    return [
        arb.Leg(venue=venues[0], symbol="BTC/USD", side="buy",
                quote=quote(buy_ask - 0.05, buy_ask, age_s=buy_age, size=buy_size),
                fees=FEES),
        arb.Leg(venue=venues[1], symbol="BTC/USD", side="sell",
                quote=quote(sell_bid, sell_bid + 0.05, age_s=sell_age,
                            size=sell_size),
                fees=FEES, **sell_kw),
    ]


def evaluate(qty=1.0, kind=arb.CROSS_VENUE, ls=None, **kw):
    params = dict(now=NOW, max_quote_age_s=5.0, latency_ms=250.0,
                  min_net_edge_bps=10.0, participation_pct=10.0)
    params.update(kw)
    return arb.evaluate(kind, ls if ls is not None else legs(), qty, **params)


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_net_edge_is_gross_minus_every_cost():
    r = evaluate()
    assert r.gross_bps == pytest.approx(50.0, abs=0.5)
    assert r.costs["fees"] == pytest.approx(0.1003, abs=1e-4)   # 5 bps on each leg
    assert r.net_profit == pytest.approx(r.gross_profit - r.costs["total"])
    assert r.net_bps < r.gross_bps
    assert r.verdict == arb.CANDIDATE


def test_a_candidate_is_labelled_research_not_an_instruction():
    r = evaluate()
    assert r.research_only is True
    assert "not an instruction to trade" in " ".join(r.reasons)
    assert r.to_dict()["research_only"] is True


def test_a_spread_smaller_than_its_costs_is_not_arbitrage():
    r = evaluate(ls=legs(sell_bid=100.02))
    assert r.verdict == arb.NOT_ARBITRAGE
    assert "NO TRADE —" in " ".join(r.reasons)
    assert r.gross_bps > 0, "the gross spread was positive; only the net was not"


def test_the_spread_is_not_charged_twice():
    """Legs are priced at the touch they actually pay (ask for a buy, bid for a
    sell), so the crossing cost is already in the gross figure."""
    r = evaluate()
    assert "paid in the leg prices" in r.costs["spread"]


def test_borrow_cost_on_a_short_leg_can_remove_the_edge():
    r = evaluate(ls=legs(borrow_bps_per_day=50.0, held_qty=0.0), holding_days=2.0)
    assert r.costs["borrow"] > 0
    assert r.verdict == arb.NOT_ARBITRAGE


def test_transfer_costs_count():
    plain = evaluate()
    with_transfer = evaluate(ls=legs(transfer_cost=0.45))
    assert with_transfer.costs["transfer"] == 0.45
    assert with_transfer.net_profit < plain.net_profit


# ── the refusals ─────────────────────────────────────────────────────────────

def test_a_stale_quote_is_refused_before_the_arithmetic():
    r = evaluate(ls=legs(sell_age=60))
    assert r.verdict == arb.STALE
    assert r.net_profit == 0.0, "costs were computed on a price that no longer exists"


def test_an_implausible_spread_is_treated_as_a_data_problem():
    r = evaluate(ls=legs(sell_bid=110.0))
    assert r.verdict == arb.FALSE_ARBITRAGE
    assert "stale feed" in " ".join(r.reasons)


def test_size_beyond_the_displayed_depth_is_illiquid():
    r = evaluate(qty=1.0, ls=legs(sell_size=5.0))
    assert r.verdict == arb.ILLIQUID
    assert r.max_executable_qty == pytest.approx(0.5)


def test_no_displayed_size_means_no_size():
    r = evaluate(ls=legs(buy_size=0.0, sell_size=0.0))
    assert r.verdict == arb.ILLIQUID
    assert "treated as none" in " ".join(r.reasons)


def test_latency_that_outlives_the_quote_is_execution_risk():
    r = evaluate(latency_ms=4_000)
    assert r.verdict == arb.EXECUTION_RISK
    assert r.execution_probability < arb.MIN_EXECUTION_PROBABILITY


def test_legs_that_cannot_execute_together_are_charged_for_it():
    simultaneous = evaluate()
    delayed = evaluate(ls=legs(transfer_s=3600))
    assert delayed.simultaneous is False
    assert delayed.execution_probability < simultaneous.execution_probability
    assert delayed.verdict == arb.EXECUTION_RISK
    assert "half-filled" in " ".join(delayed.reasons)
    assert delayed.leg_failure_loss > 0


def test_one_venue_is_not_a_cross_venue_arbitrage():
    r = evaluate(ls=legs(venues=("venue_a", "venue_a")))
    assert r.verdict == arb.NOT_ARBITRAGE
    assert "two venues" in " ".join(r.reasons)


def test_two_legs_on_the_same_side_are_a_directional_position():
    same = legs()
    same[1] = arb.Leg(venue="venue_b", symbol="BTC/USD", side="buy",
                      quote=quote(100.5, 100.55), fees=FEES)
    r = evaluate(ls=same)
    assert r.verdict == arb.NOT_ARBITRAGE
    assert "directional" in " ".join(r.reasons)


def test_statistical_arbitrage_is_never_called_arbitrage():
    r = evaluate(kind=arb.STATISTICAL, ls=legs(sell_bid=101.0))
    assert r.verdict == arb.STATISTICAL_SPREAD
    assert "not arbitrage" in " ".join(r.reasons)
    assert r.tradable_candidate is False


def test_an_unknown_kind_is_rejected_outright():
    with pytest.raises(ValueError):
        evaluate(kind="free_money")


# ── helpers around the edges ─────────────────────────────────────────────────

def test_best_size_reports_why_when_nothing_works():
    out = arb.best_size(arb.CROSS_VENUE, legs(sell_bid=100.01), now=NOW,
                        max_quote_age_s=5.0, min_net_edge_bps=10.0)
    assert out["found"] is False and out["verdict"] == arb.NOT_ARBITRAGE


def test_best_size_finds_a_size_when_one_exists():
    out = arb.best_size(arb.CROSS_VENUE, legs(), now=NOW, max_quote_age_s=5.0,
                        min_net_edge_bps=10.0, participation_pct=10.0)
    assert out["found"] is True
    assert out["best"]["verdict"] == arb.CANDIDATE
    assert out["expected_value"] > 0


def test_a_scan_summarises_by_verdict():
    out = arb.scan([{"kind": arb.CROSS_VENUE, "legs": legs(), "qty": 1.0},
                    {"kind": arb.CROSS_VENUE, "legs": legs(sell_bid=100.01),
                     "qty": 1.0}],
                   now=NOW, max_quote_age_s=5.0, min_net_edge_bps=10.0)
    assert out["evaluated"] == 2
    assert out["by_verdict"][arb.CANDIDATE] == 1
    assert out["by_verdict"][arb.NOT_ARBITRAGE] == 1
    assert len(out["candidates"]) == 1


def test_the_arithmetic_helper_says_what_it_is():
    assert arb.total_return_check(40, 55)["positive"] is False
    assert "normal case" in arb.total_return_check(40, 55)["note"]
    assert arb.total_return_check(80, 20)["net_bps"] == 60
