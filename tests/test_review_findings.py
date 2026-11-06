"""Regression tests for docs/trading/CLAUDE_INDEPENDENT_REVIEW.md, one section per finding.

Each test rebuilds the review's reproduction case and asserts the corrected
behaviour. The original failing output at 7385130 is recorded in
docs/trading/claude_review_probes/probes_out.json.
"""
from datetime import timedelta
from decimal import Decimal as D
import json
import sqlite3

import pytest

from test_multiasset_risk import case, NOW
from src.trading.contracts import AssetClass, RiskAssessment, content_hash
from src.trading.multiasset_risk import assess
from src.trading.review_store import ReviewRefused, ReviewStore


def clock():
    return NOW


def store_at(tmp_path, name="review.sqlite"):
    return ReviewStore(tmp_path / name, clock=clock)


def forged_allow(proposal):
    """The probe R1 forgery: a hand-built ALLOW naming the proposal's hash."""
    return RiskAssessment(verdict="ALLOW", reasons=("forged",), policy_hash="x" * 64,
        proposal_hash=content_hash(proposal), assessed_at=NOW,
        expires_at=NOW + timedelta(seconds=30), quantity=proposal.quantity, monetary_risk="1")


# --- CR-02: the approval store recomputes risk; it never trusts a caller verdict ---

def test_cr02_forged_allow_for_a_denied_trade_is_refused_by_queue(tmp_path, case):
    p, i = case
    # Probe R1: 5 lots EURUSD with a 50-pip stop on a $10k account (~$2,550 at risk).
    big = p.model_copy(update={"quantity": D("5.00")})
    assert assess(big, i, now=NOW).verdict == "DENY"
    store = store_at(tmp_path)
    with pytest.raises(ReviewRefused, match="caller-built assessment"):
        store.queue(big, forged_allow(big), now=NOW)
    with pytest.raises(ReviewRefused, match="verdict DENY"):
        store.queue(big, i, now=NOW)
    assert store.inspect(big.proposal_id) is None


def test_cr02_forged_allow_is_refused_by_reserve_intent(tmp_path, case):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    with pytest.raises(ReviewRefused, match="caller-built assessment"):
        store.reserve_intent(p, token, forged_allow(p), now=NOW)
    assert store.inspect(p.proposal_id)["state"] == "APPROVED"
    assert len(store.reserve_intent(p, token, i, now=NOW)) == 64


@pytest.mark.parametrize("change,why", [
    ({"kill_switch_clear": False}, "EMERGENCY_HALT"),
    ({"spread_cost": D("5")}, "authorised risk changed"),
])
def test_cr02_reservation_reassesses_fresh_evidence(tmp_path, case, change, why):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)
    with pytest.raises(ReviewRefused, match=why):
        store.reserve_intent(p, token, i.model_copy(update=change), now=NOW)
    # A refused reservation consumes nothing.
    assert store.inspect(p.proposal_id)["state"] == "APPROVED"


def test_cr02_caller_clock_must_agree_with_store_clock(tmp_path, case):
    p, i = case
    store = ReviewStore(tmp_path / "review.sqlite", clock=lambda: NOW + timedelta(hours=1))
    with pytest.raises(ReviewRefused, match="clock"):
        store.queue(p, i, now=NOW)


def test_cr02_store_assesses_under_its_own_policy_only(tmp_path, case):
    p, i = case
    store = store_at(tmp_path)
    with pytest.raises(TypeError):
        store.queue(p, i, now=NOW, policy=None)  # no caller-chosen policy parameter
    risk = store.queue(p, i, now=NOW)
    assert risk.policy_hash == assess(p, i, now=NOW, policy=ReviewStore.POLICY).policy_hash


def test_cr02_tampered_stored_assessment_is_refused(tmp_path, case):
    p, i = case
    store = store_at(tmp_path)
    store.queue(p, i, now=NOW)
    with sqlite3.connect(str(store.path)) as db:
        raw = json.loads(db.execute("SELECT risk FROM proposals WHERE id=?", (p.proposal_id,)).fetchone()[0])
        raw["monetary_risk"] = "999999"
        db.execute("UPDATE proposals SET risk=? WHERE id=?", (json.dumps(raw), p.proposal_id))
    with pytest.raises(ReviewRefused, match="audit record"):
        store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)


# --- shared: the review's E7/E8 shape (0.10 lot EURUSD, 20-pip stop, no costs => $20 risk) ---

def twenty_dollar_trade(case, *, equity, daily_balance, high_water="10000"):
    p, i = case
    p = p.model_copy(update={"quantity": D("0.10"), "price_tolerance": D("0"),
                             "stop_loss": D("1.09820"), "take_profit": D("1.10420")})
    eq, mid = D(equity), D(daily_balance)
    i = i.model_copy(update={
        "account": i.account.model_copy(update={"equity": eq, "balance": eq}),
        "rule_state": i.rule_state.model_copy(update={"equity": eq, "daily_balance": mid,
                                                      "balance_high_water": D(high_water)}),
        "spread_cost": D("0"), "commission": D("0"), "slippage_allowance": D("0")})
    return p, i


# --- CR-05: today's loss comes from the midnight balance and equity, not a caller field ---

def test_cr05_floating_loss_carried_over_midnight_halts(case):
    # E8: 00:00 balance 10,000, equity 9,520 (a -$480 floating loss carried over midnight).
    # 480 >= 2% of 9,520 => internal daily stop. At 7385130 this was ALLOWED because a
    # separate "today's P&L" field said 0.
    p, i = twenty_dollar_trade(case, equity="9520", daily_balance="10000")
    r = assess(p, i, now=NOW)
    assert r.verdict == "EMERGENCY_HALT" and r.reasons == ("INTERNAL_DAILY_STOP",)


def test_cr05_no_caller_daily_pnl_field_exists():
    from src.trading.multiasset_risk import RiskInputs
    assert "daily_net_pnl" not in RiskInputs.model_fields


# --- CR-10: a buffer above the provider floor, and risk == budget is refused ---

def test_cr10_trade_sized_to_land_on_the_floor_is_denied(case):
    # E7: equity 9,020, static floor 9,000, $20 risk => equity at the stop = 9,000.00 exactly.
    p, i = twenty_dollar_trade(case, equity="9020", daily_balance="9020")
    r = assess(p, i, now=NOW)
    assert r.verdict == "DENY" and "WITHIN_FLOOR_BUFFER" in r.reasons


def test_cr10_risk_equal_to_budget_is_denied(case):
    # Floor 9,000 + $50 buffer (0.5% of 10,000) leaves exactly $20 of room at equity 9,070.
    p, i = twenty_dollar_trade(case, equity="9070", daily_balance="9070")
    r = assess(p, i, now=NOW)
    assert r.verdict == "DENY" and "RISK_BUDGET_EXCEEDED" in r.reasons


def test_cr10_one_dollar_inside_the_buffered_room_is_allowed(case):
    p, i = twenty_dollar_trade(case, equity="9071", daily_balance="9071")
    assert assess(p, i, now=NOW).verdict == "ALLOW"


def test_cr10_buffer_scales_with_slippage_allowance(case):
    # 3 x $30 slippage = $90 buffer > $50: equity 9,071 is now inside the buffer.
    p, i = twenty_dollar_trade(case, equity="9071", daily_balance="9071")
    i = i.model_copy(update={"slippage_allowance": D("30")})
    assert "WITHIN_FLOOR_BUFFER" in assess(p, i, now=NOW).reasons


# --- CR-03: contract size is checked against the broker's own calculation ---

def metal_or_index_case(case, *, symbol, asset_class, base, contract, quantity,
                        bid, ask, stop, target, broker_one_lot_loss):
    """A USD-quoted instrument whose broker says one lot entry->stop loses `broker_one_lot_loss`."""
    from src.trading.contracts import Instrument, InstrumentSpecification, MarketQuote
    from test_multiasset_risk import broker_calc
    p, i = case
    prov = i.quote.provenance.model_copy(update={"instrument_id": f"fixture:{symbol}",
                                                 "original_symbol": symbol})
    instrument = Instrument(instrument_id=f"fixture:{symbol}", provider_symbol=symbol,
        asset_class=asset_class, product="CFD", venue="FIXTURE_OTC", base_currency=base,
        quote_currency="USD", settlement_currency="USD", calendar="fixture")
    spec = i.specification.model_copy(update={
        "instrument": instrument, "contract_size": D(contract), "tick_size": D("0.01"),
        "tick_value": D(contract) * D("0.01"), "minimum_stop_distance": D("0.01"),
        "volume_max": D("100"), "provenance": prov})
    p = p.model_copy(update={"instrument_id": instrument.instrument_id, "quantity": D(quantity),
        "entry": D(ask), "price_tolerance": D("0"), "stop_loss": D(stop), "take_profit": D(target)})
    i = i.model_copy(update={"specification": spec,
        "quote": MarketQuote(bid=bid, ask=ask, snapshot_id="q1", provenance=prov),
        "broker_calculation": broker_calc(instrument.instrument_id, f"-{broker_one_lot_loss}", "0",
                                          prov, entry=ask, stop=stop)})
    return p, i


def test_cr03_gold_with_wrong_contract_size_is_unverified(case):
    # E5b: true contract 100 oz, mapped as 1. Ten lots with a $5 stop look like $50;
    # the true loss is $5,000 on $2.6M notional. At 7385130 this was ALLOWED.
    p, i = metal_or_index_case(case, symbol="XAUUSD", asset_class=AssetClass.METALS, base="XAU",
        contract="1", quantity="10", bid="2600.00", ask="2600.00", stop="2595.00", target="2610.00",
        broker_one_lot_loss="500")
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED"
    assert "CONTRACT_SPEC_DISAGREES_WITH_BROKER" in r.reasons
    assert "CONTRACT_SIZE_OUTSIDE_REVIEWED_REGISTRY" in r.reasons


def test_cr03_unregistered_instrument_is_caught_by_the_broker_figure_alone(case):
    # An index CFD has no conventional size, so only the broker cross-check applies:
    # mapped as 1 per point, the broker says 10 per point.
    p, i = metal_or_index_case(case, symbol="US30", asset_class=AssetClass.INDICES, base="USD",
        contract="1", quantity="0.10", bid="42000.00", ask="42000.00", stop="41950.00",
        target="42100.00", broker_one_lot_loss="500")
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED" and r.reasons == ("CONTRACT_SPEC_DISAGREES_WITH_BROKER",)


def test_cr03_correct_gold_specification_is_allowed(case):
    # 100 oz, 0.05 lot, $5 stop: $25 + $2 costs, notional $13,000 (1.3x).
    p, i = metal_or_index_case(case, symbol="XAUUSD", asset_class=AssetClass.METALS, base="XAU",
        contract="100", quantity="0.05", bid="2600.00", ask="2600.20", stop="2595.20",
        target="2610.20", broker_one_lot_loss="500")
    r = assess(p, i, now=NOW)
    assert r.verdict == "ALLOW" and r.monetary_risk == D("27.00")


def test_cr03_broker_calculation_is_mandatory_and_must_be_fresh(case):
    p, i = case
    assert assess(p, i.model_copy(update={"broker_calculation": None}), now=NOW).reasons == (
        "BROKER_CALCULATION_REQUIRED",)
    stale = i.broker_calculation.model_copy(update={"provenance": i.broker_calculation.provenance.model_copy(
        update={"observed_at": NOW - timedelta(seconds=16), "retrieved_at": NOW - timedelta(seconds=16)})})
    other = i.broker_calculation.model_copy(update={"instrument_id": "fixture:GBPUSD"})
    for bad in (stale, other):
        assert assess(p, i.model_copy(update={"broker_calculation": bad}), now=NOW).reasons == (
            "BROKER_CALCULATION_UNVERIFIED",)


def test_cr03_small_disagreement_within_tolerance_is_accepted(case):
    p, i = case
    near = i.broker_calculation.model_copy(update={"profit_at_stop": D("-502.40")})  # 0.48%
    far = i.broker_calculation.model_copy(update={"profit_at_stop": D("-502.60")})   # 0.52%
    assert assess(p, i.model_copy(update={"broker_calculation": near}), now=NOW).verdict == "ALLOW"
    assert assess(p, i.model_copy(update={"broker_calculation": far}), now=NOW).verdict == "UNVERIFIED"


def test_cr03_lot_cap_backstop_by_asset_class(case):
    from src.trading.multiasset_risk import ResearchRiskPolicy
    policy = ResearchRiskPolicy()
    assert policy.lot_cap(AssetClass.METALS, D("10000")) == D("0.2")
    assert policy.lot_cap(AssetClass.FOREX, D("25000")) == D("2.5")
    assert policy.lot_cap(AssetClass.OTHER, D("10000")) == D("0")
    p, i = case
    r = assess(p.model_copy(update={"quantity": D("1.10")}), i, now=NOW)
    assert r.verdict == "DENY" and "ASSET_CLASS_LOT_CAP" in r.reasons


def test_cr03_broker_margin_is_used_when_larger(case):
    p, i = case
    # Our estimate: 1.1003 x 100,000 x 0.05 x 1% = $55; broker says $9,000 per lot => $450.
    heavy = i.broker_calculation.model_copy(update={"margin": D("9000")})
    tight = i.account.model_copy(update={"available_margin": D("500")})
    r = assess(p, i.model_copy(update={"broker_calculation": heavy, "account": tight}), now=NOW)
    assert r.verdict == "DENY" and "INSUFFICIENT_MARGIN" in r.reasons


# --- CR-04 / CR-13: conversion from a named pair quote, at the side that hurts ---

def fx_pair_case(case, *, symbol, base, quote, bid, ask, side, quantity, entry, stop, target,
                 broker_one_lot_profit, fx_quote=None):
    from src.trading.contracts import Instrument, MarketQuote
    from test_multiasset_risk import broker_calc
    p, i = case
    prov = i.quote.provenance.model_copy(update={"instrument_id": f"fixture:{symbol}",
                                                 "original_symbol": symbol})
    instrument = Instrument(instrument_id=f"fixture:{symbol}", provider_symbol=symbol,
        asset_class=AssetClass.FOREX, product="CFD", venue="FIXTURE_OTC", base_currency=base,
        quote_currency=quote, settlement_currency=quote, calendar="fixture")
    tick = D("0.001") if quote == "JPY" else D("0.00001")
    spec = i.specification.model_copy(update={"instrument": instrument, "tick_size": tick,
        "tick_value": D("100000") * tick, "tick_value_currency": quote, "provenance": prov})
    p = p.model_copy(update={"instrument_id": instrument.instrument_id, "side": side,
        "quantity": D(quantity), "entry": D(entry), "price_tolerance": D("0"),
        "stop_loss": D(stop), "take_profit": D(target)})
    i = i.model_copy(update={"specification": spec, "fx_quote": fx_quote,
        "quote": MarketQuote(bid=bid, ask=ask, snapshot_id="q1", provenance=prov),
        "broker_calculation": broker_calc(instrument.instrument_id, broker_one_lot_profit, "0", prov,
                                          entry=entry, stop=stop, side=side)})
    return p, i


def gbpusd(case, bid, ask):
    from src.trading.contracts import FXPairQuote
    prov = case[1].quote.provenance.model_copy(update={"instrument_id": "fixture:GBPUSD",
                                                       "original_symbol": "GBPUSD"})
    return FXPairQuote(base="GBP", quote="USD", bid=bid, ask=ask, provenance=prov)


def eurgbp(case, fx, quantity):
    # One lot from 0.85000 to 0.84750 loses 250 GBP; the broker, at GBPUSD 1.2701, says $317.53.
    return fx_pair_case(case, symbol="EURGBP", base="EUR", quote="GBP", bid="0.85000",
        ask="0.85000", side="BUY", quantity=quantity, entry="0.85000", stop="0.84750",
        target="0.85500", broker_one_lot_profit="-317.53", fx_quote=fx)


def test_cr04_inverted_rate_with_correct_labels_is_unverified(case):
    # E3b: GBP->USD supplied as 0.7874 (the USDGBP value) under GBPUSD labels.
    # At 7385130 this ALLOWED $39.37 where the true risk is $63.50 (-38%).
    p, i = eurgbp(case, gbpusd(case, "0.7874", "0.7874"), "0.20")
    r = assess(p, i, now=NOW)
    assert r.verdict == "UNVERIFIED" and "CONTRACT_SPEC_DISAGREES_WITH_BROKER" in r.reasons


def test_cr04_correct_pair_uses_the_conservative_side(case):
    p, i = eurgbp(case, gbpusd(case, "1.2700", "1.2702"), "0.10")
    r = assess(p, i, now=NOW)
    # GBP -> USD multiplies by GBPUSD: the ask is the larger loss. 25 GBP x 1.2702 + $2 costs.
    assert r.verdict == "ALLOW" and r.monetary_risk == D("0.0025") * 100000 * D("0.10") * D("1.2702") + 2


def test_cr04_no_caller_rate_input_exists():
    from src.trading.multiasset_risk import RiskInputs
    assert "conversion" not in RiskInputs.model_fields and "fx_quote" in RiskInputs.model_fields


def usdjpy(case, side, entry, stop, target, broker_profit):
    return fx_pair_case(case, symbol="USDJPY", base="USD", quote="JPY", bid="150.000",
        ask="150.010", side=side, quantity="0.04", entry=entry, stop=stop, target=target,
        broker_one_lot_profit=broker_profit)


def test_cr13_base_is_account_currency_converts_at_the_stop(case):
    # USDJPY on a USD account, BUY 0.04 lot, 1.5-yen stop: 6,000 JPY. The loss is
    # realised at USDJPY 148.51, not today's 150.00: $40.40, not $40.00.
    p, i = usdjpy(case, "BUY", "150.010", "148.510", "151.510", "-999.97")
    r = assess(p, i, now=NOW)
    assert r.verdict == "ALLOW"
    assert abs(r.monetary_risk - (D("6000") / D("148.510") + 2)) < D("1e-20")
    assert r.monetary_risk > D("6000") / D("150.000") + 2


def test_cr13_sell_side_uses_todays_bid_when_it_is_lower(case):
    p, i = usdjpy(case, "SELL", "150.000", "151.500", "148.500", "-999.97")
    r = assess(p, i, now=NOW)
    assert r.verdict == "ALLOW" and abs(r.monetary_risk - (D("6000") / D("150.000") + 2)) < D("1e-20")
