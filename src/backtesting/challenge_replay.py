"""Path-level replay of an account through a rule profile's loss limits (review QA-04).

`block_bootstrap` in experiments.py resamples total P/L; a prop-firm account is
lost on the *path*: one intraday dip below the day's floor ends it, however the
day closes. This module replays equity observations through the same limits
`rule_profiles.evaluate_profile` applies to a live snapshot:

- daily floor = balance at the profile's reset (00:00 Europe/Prague for FTMO)
  minus `daily_loss_fraction` x initial account size;
- overall floor = initial size (STATIC) or the highest day-start balance so far
  (TRAILING_EOD_BALANCE), minus `overall_loss_fraction` x initial size;
- a breach is equity AT or below a floor, as in `evaluate_profile`;
- the profit target counts only when explicit evidence shows the account flat
  and its balance meets the target, the two conditions `evaluate_objectives`
  checks (PROFIT_TARGET_NOT_MET, OPEN_POSITIONS_REMAIN). Flat means a stated
  open-position count of zero; equity equal to balance is not enough, since
  open positions can net to zero (review A2). Missing evidence never reaches
  the target, and zero positions with equity different from balance is
  contradictory and refused. TARGET_REACHED is that point, not a pass: minimum
  days and consistency are not replayed.

A sampled day (`DayPath`) states its close: closing equity and open positions
(review A1). Its closing balance and stated closing equity are both checked
against the floors; the balance is a conservative stand-in, so open profit at
the close does not save a balance below a floor. The next sampled day starts
from the closing balance but comes from another part of history, so a position
still open at a close is lost to the replay: after any close not shown flat the
target is refused for the rest of that path, and the path's later breaches see
only the sampled days' own equity. A history that holds positions overnight
should be replayed with `replay_path`, where carried positions are observed.

Coverage (review A3, not addressed here): both replays see only the equity
points they are given. A dip between two observations that recovers before the
next one is invisible, so sparse observations understate breaches; nothing here
interpolates or bounds the unobserved path. Each day's worst equity (the
broker's intraday low) should be among the observations.

`replay_path` replays one dated history. `breach_probabilities` resamples whole
historical days (moving blocks, so streaky days stay together), replays each
synthetic path and reports P(daily breach), P(overall breach) and P(target),
each with a Wilson 95% interval for the simulation's own sampling error. Those
intervals do not cover the deeper uncertainty: that the past days are a fair
sample of the future. Nothing here is a pass forecast or a trading mandate.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
import math
import random
from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.trading.contracts import Contract, revalidated
from src.trading.rule_profiles import RuleProfile, rule_day

Outcome = Literal["DAILY_BREACH", "OVERALL_BREACH", "TARGET_REACHED", "OPEN"]

# A count of open positions is the evidence that an account is flat. Whole
# numbers only: False, "0" or 0.0 must not pass for "no positions".
PositionCount = Annotated[int, Field(ge=0, strict=True)]


class EquityObservation(Contract):
    """Account balance and equity at one moment, in the account currency.

    `open_positions` is the number of positions open at that moment. Without it
    the account is not known to be flat (review A2).
    """
    at: datetime
    balance: Decimal
    equity: Decimal
    open_positions: PositionCount | None = None

    @model_validator(mode="after")
    def aware(self):
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("timezone-aware observation time required")
        if not (self.balance.is_finite() and self.equity.is_finite()):
            raise ValueError("finite balance and equity required")
        if self.open_positions == 0 and self.equity != self.balance:
            raise ValueError("contradictory evidence: no open positions, but equity differs from balance")
        return self

    @property
    def shown_flat(self) -> bool:
        return self.open_positions == 0 and self.equity == self.balance


class DayPath(Contract):
    """One historical rule day, relative to that day's opening balance.

    `equity_pnl` is equity minus the opening balance at each observation through
    the day, in time order, including the worst point; `balance_pnl` is the
    change in balance by the day's end. The close is stated, not assumed
    (review A1): `close_equity_pnl` is equity minus the opening balance at the
    close and `open_positions_at_close` the positions still open then. The day
    is shown flat only when both are given, the count is zero and the closing
    equity equals the closing balance.
    """
    equity_pnl: tuple[Decimal, ...] = Field(min_length=1)
    balance_pnl: Decimal
    close_equity_pnl: Decimal | None = None
    open_positions_at_close: PositionCount | None = None

    @model_validator(mode="after")
    def finite(self):
        stated = () if self.close_equity_pnl is None else (self.close_equity_pnl,)
        if not all(v.is_finite() for v in (*self.equity_pnl, self.balance_pnl, *stated)):
            raise ValueError("finite P/L required")
        if (self.open_positions_at_close == 0 and self.close_equity_pnl is not None
                and self.close_equity_pnl != self.balance_pnl):
            raise ValueError("contradictory close evidence: no open positions, "
                             "but closing equity differs from closing balance")
        return self

    @property
    def closes_flat(self) -> bool:
        return (self.open_positions_at_close == 0 and self.close_equity_pnl is not None
                and self.close_equity_pnl == self.balance_pnl)


class ReplayResult(Contract):
    outcome: Outcome
    day_index: int | None = None     # 0-based rule day of the outcome
    at: datetime | None = None       # observation time, for a dated replay
    equity: Decimal | None = None
    floor: Decimal | None = None
    final_balance: Decimal


def _checked(profile: RuleProfile) -> RuleProfile:
    profile = revalidated(profile)
    if profile.daily_method != "MIDNIGHT_BALANCE_MINUS_INITIAL_ALLOWANCE":
        raise ValueError("replay needs a profile with a known daily-loss method")
    return profile


class _Account:
    """The rule kernel shared by the dated replay and the simulation."""

    def __init__(self, profile: RuleProfile):
        self.p = profile
        self.size = profile.account_size
        self.balance = profile.account_size
        self.high_water = profile.account_size
        self.day_start = profile.account_size
        self.target = (None if profile.profit_target_fraction is None
                       else self.size * (1 + profile.profit_target_fraction))

    def new_day(self):
        self.day_start = self.balance
        self.high_water = max(self.high_water, self.day_start)

    def floors(self) -> tuple[Decimal, Decimal]:
        daily = self.day_start - self.size * self.p.daily_loss_fraction
        anchor = self.size if self.p.drawdown_mode == "STATIC" else self.high_water
        return daily, anchor - self.size * self.p.overall_loss_fraction

    def breach(self, equity: Decimal) -> tuple[Outcome, Decimal] | None:
        daily, overall = self.floors()
        # The overall limit is the account-ending one; report it first.
        if equity <= overall:
            return "OVERALL_BREACH", overall
        if equity <= daily:
            return "DAILY_BREACH", daily
        return None

    def reached_target(self, shown_flat: bool) -> bool:
        """Balance at the target on an account that explicit evidence shows flat.
        Open positions, or no evidence either way, mean it is not reached."""
        return shown_flat is True and self.target is not None and self.balance >= self.target


def replay_path(profile: RuleProfile, observations: tuple[EquityObservation, ...]) -> ReplayResult:
    """Replay dated observations; stops at the first breach or the profit target.

    The first observation's balance must be the account's initial size: the
    replay starts from a fresh account, as the profile's floors assume.

    Every rule day after the first, weekends included, needs its observation at
    the exact reset instant: that balance sets the day's floor, and a skipped
    day's floor and equity are unknown (review RV2-03). Times are strictly
    increasing: two observations at one instant leave their order, and so which
    one is the reset snapshot, unknown (review RV2-04).
    """
    profile = _checked(profile)
    observations = tuple(revalidated(o) for o in observations)
    if not observations or observations[0].balance != profile.account_size:
        raise ValueError("replay starts from a fresh account at its initial size")
    if any(b.at <= a.at for a, b in zip(observations, observations[1:])):
        raise ValueError("observations must be in strictly increasing time order")
    account = _Account(profile)
    day, index = None, -1
    for o in observations:
        today = rule_day(profile, o.at)
        if today != day:
            if day is not None and today != day + timedelta(days=1):
                raise ValueError("exact reset balance observation required for every rule day; "
                                 f"none for {day + timedelta(days=1)}")
            boundary = datetime.combine(today, time(profile.reset_hour), ZoneInfo(profile.reset_timezone))
            if day is not None and o.at != boundary:
                raise ValueError("exact reset balance observation required for every new rule day")
            if day is not None:
                account.balance = o.balance  # the midnight snapshot sets today's floor
            day, index = today, index + 1
            account.new_day()
        hit = account.breach(o.equity)
        account.balance = o.balance
        if hit:
            return ReplayResult(outcome=hit[0], day_index=index, at=o.at, equity=o.equity,
                                floor=hit[1], final_balance=o.balance)
        if account.reached_target(o.shown_flat):
            return ReplayResult(outcome="TARGET_REACHED", day_index=index, at=o.at,
                                equity=o.equity, final_balance=o.balance)
    return ReplayResult(outcome="OPEN", final_balance=account.balance)


def replay_days(profile: RuleProfile, days: tuple[DayPath, ...]) -> ReplayResult:
    """Replay undated rule days in order, from a fresh account.

    Days are revalidated first, so one built without validation cannot carry
    contradictory close evidence past the contract.
    """
    return _replay_days(_checked(profile), tuple(revalidated(d) for d in days))


def _replay_days(profile: RuleProfile, days: tuple[DayPath, ...]) -> ReplayResult:
    account = _Account(profile)
    shown_flat = True  # a fresh account holds no positions
    for index, d in enumerate(days):
        account.new_day()
        for pnl in d.equity_pnl:
            equity = account.day_start + pnl
            hit = account.breach(equity)
            if hit:
                account.balance = account.day_start + d.balance_pnl
                return ReplayResult(outcome=hit[0], day_index=index, equity=equity,
                                    floor=hit[1], final_balance=account.balance)
        # The stated closing equity is a point the account reached; the closing
        # balance is checked too, as a conservative stand-in (module docstring).
        close = account.day_start + d.balance_pnl
        low = close if d.close_equity_pnl is None else min(close, account.day_start + d.close_equity_pnl)
        hit = account.breach(low)
        account.balance = close
        if hit:
            return ReplayResult(outcome=hit[0], day_index=index, equity=low,
                                floor=hit[1], final_balance=close)
        # A close not shown flat leaves a position the next sampled day knows
        # nothing of, so the account is never shown flat again on this path.
        shown_flat = shown_flat and d.closes_flat
        if account.reached_target(shown_flat):
            return ReplayResult(outcome="TARGET_REACHED", day_index=index,
                                final_balance=account.balance)
    return ReplayResult(outcome="OPEN", final_balance=account.balance)


def wilson_interval(hits: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if n <= 0:
        raise ValueError("at least one trial required")
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def breach_probabilities(profile: RuleProfile, days: tuple[DayPath, ...], *, horizon_days: int,
                         block_size: int = 5, samples: int = 2000, seed: int = 0) -> dict:
    """Resample historical days into `samples` paths of `horizon_days` and replay each."""
    profile = _checked(profile)
    days = tuple(revalidated(d) for d in days)
    if not days or not 1 <= block_size <= len(days):
        raise ValueError("historical days and a block size within them are required")
    if not 1 <= horizon_days <= 400 or not 1 <= samples <= 20000:
        raise ValueError("bounded horizon and sample count required")
    rng = random.Random(seed)
    counts = {"DAILY_BREACH": 0, "OVERALL_BREACH": 0, "TARGET_REACHED": 0, "OPEN": 0}
    for _ in range(samples):
        path: list[DayPath] = []
        while len(path) < horizon_days:
            start = rng.randrange(len(days) - block_size + 1)
            path.extend(days[start:start + block_size])
        counts[_replay_days(profile, tuple(path[:horizon_days])).outcome] += 1

    def estimate(outcome):
        low, high = wilson_interval(counts[outcome], samples)
        return {"probability": counts[outcome] / samples, "interval_95": [low, high]}

    return {"seed": seed, "samples": samples, "block_size": block_size,
            "horizon_days": horizon_days, "historical_days": len(days),
            "historical_days_not_shown_flat": sum(not d.closes_flat for d in days),
            "profile_hash": profile.profile_hash,
            "daily_breach": estimate("DAILY_BREACH"), "overall_breach": estimate("OVERALL_BREACH"),
            "target_reached": estimate("TARGET_REACHED"), "open_at_horizon": estimate("OPEN"),
            "assumption": ("moving-block resampling of supplied historical days; intervals cover "
                           "simulation error only, not whether those days represent the future; "
                           "a path counts the target only while every day so far closed shown "
                           "flat by its stated close evidence"),
            "validation_status": "UNVERIFIED"}
