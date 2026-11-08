"""Adversarial findings A1 and A2 on the QA-04 replay (2026-11-08).

A1. A sampled day (`DayPath`) carried no closing-equity evidence, and the
    replay assumed every day ended flat. A day that banked +1,000 (balance at
    the 11,000 target) while carrying a 1,400 open loss replayed as
    TARGET_REACHED; that loss breached the next day's floor.
A2. A dated observation counted as flat whenever equity equalled balance.
    Open positions that net to zero are not flat.

The target now needs explicit evidence: a sampled day's closing equity and
open-position count, a dated observation's open-position count. Missing
evidence never reaches the target; contradictory evidence is refused.
"""
from datetime import timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from test_multiasset_risk import case, NOW  # noqa: F401  (fixture)

from src.backtesting.challenge_replay import (
    DayPath, EquityObservation, breach_probabilities, replay_days, replay_path)

H = timedelta(hours=1)
BAD_COUNTS = [False, True, "0", 0.0, -1]


def target(case):
    """10,000 account, 5% daily / 10% overall static floors, 11,000 target."""
    return case[1].profile.model_copy(update={"profit_target_fraction": D("0.10")})


def day(equity, balance, close_equity=None, positions=None):
    evidence = {}
    if close_equity is not None:
        evidence["close_equity_pnl"] = D(close_equity)
    if positions is not None:
        evidence["open_positions_at_close"] = positions
    return DayPath(equity_pnl=tuple(D(e) for e in equity), balance_pnl=D(balance), **evidence)


def observe(at, balance, equity=None, positions=None):
    evidence = {} if positions is None else {"open_positions": positions}
    return EquityObservation(at=at, balance=D(balance),
                             equity=D(equity if equity is not None else balance), **evidence)


def refused_as_a_count(caught):
    return bool({e["type"] for e in caught.value.errors()} & {"int_type", "greater_than_equal"})


# --- A1: a sampled day's close needs explicit evidence ------------------------

def test_a1_banked_profit_with_an_open_loss_is_not_a_target(case):
    """The adversarial case, with no close evidence. Before: TARGET_REACHED on
    day 0. The account was never shown flat, so the replay goes on to day 1,
    where the carried loss takes equity to 9,600, below that day's 10,500 floor."""
    days = (day(["-300"], "1000"), day(["-1400"], "0"))
    result = replay_days(target(case), days)
    assert (result.outcome, result.day_index, result.floor, result.equity) == (
        "DAILY_BREACH", 1, D("10500"), D("9600"))


def test_a1_the_same_days_with_their_close_evidence(case):
    """Day 0 states its close: equity 9,600 with two positions open."""
    days = (day(["-300"], "1000", close_equity="-400", positions=2), day(["-1400"], "0"))
    result = replay_days(target(case), days)
    assert (result.outcome, result.day_index, result.equity) == ("DAILY_BREACH", 1, D("9600"))


def test_a1_closing_equity_below_the_floor_is_a_breach(case):
    """The stated closing equity is a point the account reached."""
    result = replay_days(target(case), (day(["-300"], "1000", close_equity="-600", positions=1),))
    assert (result.outcome, result.day_index, result.floor, result.equity, result.final_balance) == (
        "DAILY_BREACH", 0, D("9500"), D("9400"), D("11000"))


def test_a1_a_flat_close_with_evidence_reaches_the_target(case):
    result = replay_days(target(case), (day(["-300"], "1000", close_equity="1000", positions=0),))
    assert (result.outcome, result.day_index, result.final_balance) == ("TARGET_REACHED", 0, D("11000"))


@pytest.mark.parametrize("evidence", [
    {},                                          # nothing stated
    {"positions": 0},                            # flat claimed, closing equity missing
    {"close_equity": "1000"},                    # equity equals balance, count missing
    {"close_equity": "1000", "positions": 1},    # an open position at break-even
], ids=["none", "count-only", "equity-only", "open-at-break-even"])
def test_a1_missing_or_open_close_evidence_never_reaches_the_target(case, evidence):
    result = replay_days(target(case), (day(["-300"], "1000", **evidence),))
    assert (result.outcome, result.final_balance) == ("OPEN", D("11000"))


def test_a1_no_target_after_a_close_not_shown_flat(case):
    """Day 0 closes holding a position. A resampled day 1 comes from other
    history and knows nothing of it, so day 1's own flat close does not show
    this account flat: the target is refused for the rest of the path."""
    days = (day(["0"], "500", close_equity="300", positions=1),
            day(["0"], "600", close_equity="600", positions=0))
    result = replay_days(target(case), days)
    assert (result.outcome, result.final_balance) == ("OPEN", D("11100"))


def test_closing_balance_is_still_checked_when_positions_stay_open(case):
    """Pins the conservative check kept from f414e96: the closing balance is
    also tested against the floors, even when open profit holds equity above it."""
    result = replay_days(target(case), (day(["100"], "-500", close_equity="-300", positions=1),))
    assert (result.outcome, result.equity, result.floor) == ("DAILY_BREACH", D("9500"), D("9500"))


def test_a1_contradictory_close_evidence_is_refused():
    """No open positions, yet closing equity differs from closing balance."""
    with pytest.raises(ValidationError, match="contradictory"):
        day(["0"], "1000", close_equity="900", positions=0)


def test_a1_replay_revalidates_days_built_without_validation(case):
    forged = DayPath.model_construct(equity_pnl=(D("0"),), balance_pnl=D("1000"),
                                     close_equity_pnl=D("900"), open_positions_at_close=0)
    with pytest.raises(ValueError, match="contradictory"):
        replay_days(target(case), (forged,))
    with pytest.raises(ValueError, match="contradictory"):
        breach_probabilities(target(case), (forged,), horizon_days=1, block_size=1, samples=1)


@pytest.mark.parametrize("count", BAD_COUNTS)
def test_a1_open_position_count_is_a_whole_non_negative_number(count):
    with pytest.raises(ValidationError) as caught:
        day(["0"], "0", close_equity="0", positions=count)
    assert refused_as_a_count(caught)


def test_a1_simulated_target_needs_close_evidence(case):
    profile = target(case)
    bare = day(["-100", "300"], "300")
    shown = day(["-100", "300"], "300", close_equity="300", positions=0)
    without = breach_probabilities(profile, (bare,), horizon_days=5, block_size=1, samples=20)
    with_evidence = breach_probabilities(profile, (shown,), horizon_days=5, block_size=1, samples=20)
    assert without["target_reached"]["probability"] == 0.0
    assert without["open_at_horizon"]["probability"] == 1.0
    assert with_evidence["target_reached"]["probability"] == 1.0
    assert (without["historical_days_not_shown_flat"],
            with_evidence["historical_days_not_shown_flat"]) == (1, 0)


# --- A2: a dated observation needs an explicit open-position count -------------

def test_a2_equity_equal_to_balance_is_not_proof_of_a_flat_account(case):
    """The adversarial case: at 11,000/11,000 the positions net to zero; the next
    observation shows them. Before: TARGET_REACHED at the 11,000 point."""
    path = (observe(NOW, "10000"), observe(NOW + H, "11000"), observe(NOW + 2 * H, "11000", "9400"))
    result = replay_path(target(case), path)
    assert (result.outcome, result.at, result.floor, result.equity) == (
        "DAILY_BREACH", NOW + 2 * H, D("9500"), D("9400"))


def test_a2_netted_positions_stated_open_are_not_flat(case):
    path = (observe(NOW, "10000"), observe(NOW + H, "11000", positions=2),
            observe(NOW + 2 * H, "11000", "9400", positions=2))
    assert replay_path(target(case), path).outcome == "DAILY_BREACH"


def test_a2_an_explicit_flat_account_reaches_the_target(case):
    path = (observe(NOW, "10000"), observe(NOW + H, "11000", "10800", positions=1),
            observe(NOW + 2 * H, "11000", positions=0))
    result = replay_path(target(case), path)
    assert (result.outcome, result.at, result.final_balance) == (
        "TARGET_REACHED", NOW + 2 * H, D("11000"))


def test_a2_contradictory_position_count_is_refused():
    with pytest.raises(ValidationError, match="contradictory"):
        observe(NOW, "11000", "10800", positions=0)


def test_a2_replay_revalidates_observations_built_without_validation(case):
    forged = EquityObservation.model_construct(at=NOW + H, balance=D("11000"), equity=D("10800"),
                                               open_positions=0)
    with pytest.raises(ValueError, match="contradictory"):
        replay_path(target(case), (observe(NOW, "10000"), forged))


@pytest.mark.parametrize("count", BAD_COUNTS)
def test_a2_open_position_count_is_a_whole_non_negative_number(count):
    with pytest.raises(ValidationError) as caught:
        observe(NOW, "10000", positions=count)
    assert refused_as_a_count(caught)
