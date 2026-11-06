"""Provider operational rules, checked against calendar, session and activity evidence.

The rule values are closed enums on the RuleProfile (review CR-08, CR-09). This
module turns each one into a check. Missing, stale or incomplete evidence makes
the result UNVERIFIED; a rule that applies makes it DENY. A rule set to NONE
needs no evidence; a rule left UNVERIFIED is already refused by the profile.

Interim reading of "major news" (FTMO has not defined it): every HIGH or
UNKNOWN impact release for one of the instrument's currencies, or about the
instrument itself, from two minutes ago to the end of the rule day blocks new
entries. A release more than two minutes in the past no longer blocks.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .contracts import (AssetClass, DailyActivity, EconomicCalendar, EconomicEvent,
                        Instrument, TradingSession)
from .rule_profiles import LONG_BREAK, NEWS_WINDOW, PRE_BREAK, RuleProfile, rule_day

NEWS_RULE = "NO_EXECUTION_2MIN_AROUND_NEWS"
HOLD_RULE = "NO_HOLD_THROUGH_BREAKS_OVER_2H"
GAP_RULE = "NO_ENTRY_2H_BEFORE_LONG_BREAK_OR_MAJOR_NEWS"


def day_bounds(profile: RuleProfile, now: datetime) -> tuple[datetime, datetime]:
    """Start and end of the rule day containing `now`, in wall-clock terms (DST-safe)."""
    day = rule_day(profile, now)
    zone = ZoneInfo(profile.reset_timezone)
    return (datetime.combine(day, time(profile.reset_hour), zone),
            datetime.combine(day + timedelta(days=1), time(profile.reset_hour), zone))


def affects(event: EconomicEvent, instrument: Instrument) -> bool:
    if event.instrument_id is not None:
        return event.instrument_id == instrument.instrument_id
    return event.currency in {instrument.base_currency, instrument.quote_currency,
                              instrument.settlement_currency}


def news_checks(profile: RuleProfile, instrument: Instrument, calendar: EconomicCalendar | None,
                *, now: datetime, max_age_seconds: int) -> tuple[list[str], list[str]]:
    news, gap = profile.news_rule == NEWS_RULE, profile.gap_rule == GAP_RULE
    if not (news or gap):
        return [], []
    if calendar is None:
        return ["ECONOMIC_CALENDAR_REQUIRED"], []
    unverified = []
    source = calendar.provenance
    age = (now - source.retrieved_at).total_seconds()
    if source.quality != "VALID" or not 0 <= age <= max_age_seconds:
        unverified.append("ECONOMIC_CALENDAR_UNVERIFIED")
    start, end = day_bounds(profile, now)
    if calendar.covers_from > min(start, now - NEWS_WINDOW) or calendar.covers_until < end + NEWS_WINDOW:
        unverified.append("ECONOMIC_CALENDAR_DOES_NOT_COVER_THE_DAY")
    if instrument.asset_class == AssetClass.EQUITIES and not calendar.covers_earnings:
        unverified.append("EARNINGS_CALENDAR_REQUIRED")
    if unverified:
        return unverified, []
    relevant = [e for e in calendar.events if affects(e, instrument)]
    denied = []
    if news and any(abs(e.scheduled_at - now) <= NEWS_WINDOW for e in relevant):
        denied.append("NEWS_EXECUTION_WINDOW")
    if any(e.impact in ("HIGH", "UNKNOWN") and now - NEWS_WINDOW <= e.scheduled_at <= end + NEWS_WINDOW
           for e in relevant):
        denied.append("MAJOR_NEWS_SCHEDULED_TODAY")
    return unverified, denied


def break_checks(profile: RuleProfile, instrument: Instrument, session: TradingSession | None,
                 *, now: datetime, max_age_seconds: int, margin: timedelta) -> tuple[list[str], list[str]]:
    hold, gap = profile.weekend_rule == HOLD_RULE, profile.gap_rule == GAP_RULE
    if not (hold or gap):
        return [], []
    if session is None:
        return ["SESSION_EVIDENCE_REQUIRED"], []
    source = session.provenance
    if (source is None or source.quality != "VALID" or session.instrument_id != instrument.instrument_id
            or not 0 <= (now - source.retrieved_at).total_seconds() <= max_age_seconds
            or session.next_opens_at is None or session.state == "UNKNOWN"):
        return ["SESSION_EVIDENCE_UNVERIFIED"], []
    if session.state != "OPEN" or not session.opens_at <= now < session.closes_at:
        return [], ["SESSION_NOT_OPEN"]
    denied = []
    long_break = session.next_opens_at - session.closes_at >= LONG_BREAK
    if long_break and session.closes_at - now <= PRE_BREAK + margin:
        if gap:
            denied.append("GAP_TRADING_WINDOW_BEFORE_LONG_BREAK")
        if hold:
            denied.append("MUST_BE_FLAT_BEFORE_LONG_BREAK")
    return [], denied


def flatten_deadline(profile: RuleProfile, session: TradingSession, *, margin: timedelta) -> datetime | None:
    """When a Standard FTMO Account must be flat, if the coming break is long.

    For a future execution stage: the break-holding rule is about positions
    already open, which no entry check can enforce on its own.
    """
    if profile.weekend_rule != HOLD_RULE or session.next_opens_at is None:
        return None
    if session.next_opens_at - session.closes_at < LONG_BREAK:
        return None
    return session.closes_at - margin


def activity_checks(profile: RuleProfile, activity: DailyActivity, account_id: str, *, now: datetime,
                    max_age_seconds: int, headroom, requests_per_trade: int,
                    max_requests_per_minute: int) -> tuple[list[str], list[str]]:
    """Per-day position, server-request and open-order limits, denied at `headroom` of each."""
    age = (now - activity.observed_at).total_seconds()
    if (activity.account_id != account_id or activity.day != rule_day(profile, now)
            or not activity.complete or not 0 <= age <= max_age_seconds):
        return ["DAILY_ACTIVITY_UNVERIFIED"], []
    denied = []
    if activity.positions_opened + 1 > profile.max_positions_per_day * headroom:
        denied.append("DAILY_POSITION_LIMIT_HEADROOM")
    if activity.server_requests + requests_per_trade > profile.max_server_requests_per_day * headroom:
        denied.append("DAILY_SERVER_REQUEST_HEADROOM")
    if activity.open_orders + 1 > profile.max_open_orders * headroom:
        denied.append("OPEN_ORDER_LIMIT_HEADROOM")
    per_minute = max_requests_per_minute
    if profile.max_server_requests_per_minute is not None:
        per_minute = min(per_minute, profile.max_server_requests_per_minute)
    if activity.requests_last_minute >= per_minute:
        denied.append("REQUEST_RATE_CAP")
    return [], denied


def instrument_checks(profile: RuleProfile, instrument: Instrument) -> list[str]:
    if profile.instrument_rule == "ALLOWLIST" and instrument.instrument_id not in profile.allowed_instruments:
        return ["INSTRUMENT_NOT_PERMITTED_BY_PROFILE"]
    return []
