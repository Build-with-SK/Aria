"""QA-04 follow-up: the two replay defects left after RV2-03/04.

1. A sampled day whose balance closed below the floors replayed as OPEN
   (Codex probe REPLAY_CLOSE_BALANCE: 8,900, below both floors).
2. The profit target counted while the account still held a 1,400 open loss,
   and the replay stopped there, before the breach that followed
   (Codex probe REPLAY_TARGET_OPEN_LOSS).

Plus reset-coverage cases across the Prague DST change and on a trailing
profile, which Codex asked for in its RV2-03 note.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from test_multiasset_risk import case, NOW  # noqa: F401  (fixture)
from test_review_findings import ftmo

from src.backtesting.challenge_replay import (
    DayPath, EquityObservation, breach_probabilities, replay_days, replay_path)

UTC = timezone.utc


def observe(at, balance, equity=None, positions=None):
    evidence = {} if positions is None else {"open_positions": positions}
    return EquityObservation(at=at, balance=D(balance),
                             equity=D(equity if equity is not None else balance), **evidence)


def with_target(case):
    return case[1].profile.model_copy(update={"profit_target_fraction": D("0.10")})


# --- 1. a sampled day's close is a point the equity reached -----------------

def test_day_closing_below_both_floors_is_a_breach(case):
    """The reviewer's case: the only equity point is flat, the balance closes at 8,900."""
    day = DayPath(equity_pnl=(D("0"),), balance_pnl=D("-1100"))
    result = replay_days(case[1].profile, (day,))
    assert (result.outcome, result.floor, result.equity, result.final_balance) == (
        "OVERALL_BREACH", D("9000"), D("8900"), D("8900"))
    odds = breach_probabilities(case[1].profile, (day,), horizon_days=1, block_size=1, samples=10)
    assert odds["overall_breach"]["probability"] == 1.0
    assert odds["open_at_horizon"]["probability"] == 0.0


def test_day_closing_at_the_daily_floor_is_a_daily_breach(case):
    day = DayPath(equity_pnl=(D("100"),), balance_pnl=D("-500"))
    result = replay_days(case[1].profile, (day,))
    assert (result.outcome, result.floor, result.equity) == ("DAILY_BREACH", D("9500"), D("9500"))


def test_breach_on_a_later_days_close_names_that_day(case):
    calm = DayPath(equity_pnl=(D("-100"), D("50")), balance_pnl=D("50"))
    loss = DayPath(equity_pnl=(D("-200"),), balance_pnl=D("-600"))
    result = replay_days(case[1].profile, (calm, loss))
    assert (result.outcome, result.day_index, result.floor) == ("DAILY_BREACH", 1, D("9550"))


def test_day_closing_inside_the_floors_is_unchanged(case):
    """Pins existing behaviour."""
    day = DayPath(equity_pnl=(D("100"),), balance_pnl=D("-400"))
    result = replay_days(case[1].profile, (day,))
    assert (result.outcome, result.final_balance) == ("OPEN", D("9600"))


# --- 2. the target counts only on a flat account ------------------------------

def test_target_with_an_open_loss_is_not_reached(case):
    """The reviewer's case: balance 11,000 with equity 9,600, then 9,400. The
    target was claimed at the first point; the account then breached its floor."""
    path = (observe(NOW, "10000"), observe(NOW + timedelta(hours=1), "11000", "9600"),
            observe(NOW + timedelta(hours=2), "11000", "9400"))
    result = replay_path(with_target(case), path)
    assert (result.outcome, result.at, result.floor, result.equity) == (
        "DAILY_BREACH", NOW + timedelta(hours=2), D("9500"), D("9400"))


def test_target_is_reached_at_the_first_flat_observation(case):
    """Flat is stated by the open-position count (review A2), not inferred."""
    path = (observe(NOW, "10000"), observe(NOW + timedelta(hours=1), "11000", "10800", positions=1),
            observe(NOW + timedelta(hours=2), "11000", positions=0))
    result = replay_path(with_target(case), path)
    assert (result.outcome, result.at, result.final_balance) == (
        "TARGET_REACHED", NOW + timedelta(hours=2), D("11000"))


def test_open_profit_does_not_reach_the_target(case):
    """Pins existing behaviour: equity above the target with the balance below it."""
    path = (observe(NOW, "10000"), observe(NOW + timedelta(hours=1), "10500", "11200"))
    assert replay_path(with_target(case), path).outcome == "OPEN"


def test_target_with_open_profit_above_balance_is_not_reached(case):
    """Balance at the target but positions still open (equity above it): the
    live objective check says OPEN_POSITIONS_REMAIN, so the replay does too."""
    path = (observe(NOW, "10000"), observe(NOW + timedelta(hours=1), "11000", "11300"))
    assert replay_path(with_target(case), path).outcome == "OPEN"


def test_sampled_target_is_still_reached_on_a_flat_close(case):
    """A day whose close evidence shows it flat (review A1): its closing
    balance is the account's value."""
    profile = ftmo(case, profit_target_fraction="0.10")
    days = tuple(DayPath(equity_pnl=(D("-100"), D("300")), balance_pnl=D("300"),
                         close_equity_pnl=D("300"), open_positions_at_close=0) for _ in range(5))
    result = replay_days(profile, days)
    assert (result.outcome, result.day_index, result.final_balance) == ("TARGET_REACHED", 3, D("11200"))


# --- reset coverage across DST and on a trailing profile (RV2-03 follow-up) ---

# Prague leaves summer time on Sunday 2026-10-25: midnight is 22:00 UTC before, 23:00 UTC after.
OCT23_NOON = datetime(2026, 10, 23, 12, tzinfo=UTC)
RESET = {24: datetime(2026, 10, 23, 22, tzinfo=UTC), 25: datetime(2026, 10, 24, 22, tzinfo=UTC),
         26: datetime(2026, 10, 25, 23, tzinfo=UTC)}


def test_reset_observations_follow_prague_midnight_across_dst(case):
    path = (observe(OCT23_NOON, "10000"), observe(RESET[24], "10000"),
            observe(RESET[25], "10000"), observe(RESET[26], "10000"),
            observe(RESET[26] + timedelta(hours=10), "10000", "9600"))
    result = replay_path(case[1].profile, path)
    assert (result.outcome, result.final_balance) == ("OPEN", D("10000"))


def test_reset_at_the_old_utc_hour_after_dst_is_refused(case):
    """22:00 UTC on 25 October is 23:00 Prague, still the 25th: not a reset."""
    path = (observe(OCT23_NOON, "10000"), observe(RESET[24], "10000"),
            observe(RESET[25], "10000"), observe(datetime(2026, 10, 25, 22, tzinfo=UTC), "10000"),
            observe(RESET[26] + timedelta(hours=1), "10000"))
    with pytest.raises(ValueError, match="exact reset balance"):
        replay_path(case[1].profile, path)


def test_skipping_the_dst_day_is_refused(case):
    path = (observe(OCT23_NOON, "10000"), observe(RESET[24], "10000"), observe(RESET[26], "10000"))
    with pytest.raises(ValueError, match="2026-10-25"):
        replay_path(case[1].profile, path)


def test_trailing_floor_follows_the_reset_balances(case):
    """Day 2 resets at 10,800: the trailing overall floor rises to 9,800 and is
    reported first; on a static profile only the daily floor (10,300) is hit."""
    trailing = case[1].profile.model_copy(update={"drawdown_mode": "TRAILING_EOD_BALANCE"})
    path = (observe(NOW, "10000"), observe(NOW + timedelta(hours=3), "10800"),
            observe(datetime(2026, 11, 6, 23, tzinfo=UTC), "10800"),
            observe(datetime(2026, 11, 7, 9, tzinfo=UTC), "10800", "9800"))
    result = replay_path(trailing, path)
    assert (result.outcome, result.floor, result.day_index) == ("OVERALL_BREACH", D("9800"), 1)
    static = replay_path(case[1].profile, path)
    assert (static.outcome, static.floor, static.day_index) == ("DAILY_BREACH", D("10300"), 1)
