"""
src/markets/venues.py
=====================
Trading venues as DATA: timezone, the phases of a trading day, which holiday
calendar applies. Nothing here knows what "now" is — src/markets/sessions.py
does the arithmetic, so every venue goes through the same code.

Hours are local wall-clock times in the venue's IANA timezone, so daylight
saving is the timezone database's job, not ours. They are the published
regular schedules as understood on 2026-10-06 (`HOURS_AS_OF`); exchanges change
them (TSE extended its afternoon to 15:30 in Nov 2024), so each spec carries
its source note. A venue that is not listed here is UNKNOWN, never assumed.

Phases
------
  PRE_OPEN      order entry / opening auction call, no continuous trading
  OPEN          continuous trading
  BREAK         intraday break (Tokyo, Hong Kong, Shanghai lunch; futures
                daily maintenance)
  AUCTION       closing auction / closing price crossing
  POST_MARKET   extended hours after the close
Anything outside a phase on a trading day is CLOSED.

Holiday rules
-------------
  calendar     the venue's own calendar (src/markets/calendars.py); a year
               without a verified calendar is UNKNOWN on weekdays
  us_futures   CME/ICE US: weekly hours from the venue; holidays from the
               contract's own calendar (FUTURES_CALENDARS below — one per
               product group, because CME's equity, rates, FX, energy, metals,
               grains and crypto products keep different holiday hours). With
               no contract calendar for the year, any US holiday (or the
               evening before one) is UNKNOWN — guessing an abbreviated
               schedule is exactly what must not happen
  none         FX and crypto: no exchange holidays
"""
from __future__ import annotations

from dataclasses import dataclass, field

HOURS_AS_OF = "2026-10-06"

# phase names — the values of sessions.SessionState for in-session states
PRE_OPEN, OPEN, BREAK, AUCTION, POST_MARKET = (
    "PRE_OPEN", "OPEN", "BREAK", "AUCTION", "POST_MARKET")


def _m(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@dataclass(frozen=True)
class Phase:
    state: str
    start: int          # minutes after local midnight
    end: int            # exclusive; 1440 = midnight

    @classmethod
    def of(cls, state: str, start: str, end: str) -> "Phase":
        return cls(state, _m(start), _m(end))


@dataclass(frozen=True)
class VenueSpec:
    code: str
    name: str
    country: str
    tz: str
    kind: str                                   # exchange | futures | options | fx | crypto
    week: dict = field(default_factory=dict)    # weekday (0=Mon) -> tuple[Phase]
    holiday_rule: str = "calendar"              # calendar | us_futures | none
    calendar: str | None = None                 # calendar code when rule is "calendar"
    closing_auction_minutes: int = 0            # re-anchored to an early close
    source: str = ""
    #: How the CALENDAR PROVIDER reports this venue's early closes: True when
    #: its half-day close is the end of the closing auction although its
    #: regular close is the end of continuous trading. exchange_calendars
    #: 4.13.2 reports Euronext's regular close as 17:30 but its 24/31 Dec
    #: close as 14:05 (continuous trading ends 14:00, auction to 14:05).
    early_close_includes_auction: bool = False

    def phases(self, weekday: int) -> tuple:
        return self.week.get(weekday, ())


def _weekdays(*phases: Phase) -> dict:
    return {d: tuple(phases) for d in range(5)}


def _overnight_week(open_from: str, close_at: str, break_to: str | None = None) -> dict:
    """Sunday-evening to Friday futures/FX week, one maintenance break a day.

    Trading resumes at `open_from` each evening (Sunday through Thursday) and
    runs until `close_at` the next day; on Friday it stops at `close_at`.
    """
    o, c = _m(open_from), _m(close_at)
    evening = Phase(OPEN, o, 1440)
    morning = Phase(OPEN, 0, c)
    gap = (Phase(BREAK, c, o),) if break_to else ()
    week = {6: (evening,)}                                # Sunday evening
    for d in range(4):                                    # Mon–Thu
        week[d] = (morning,) + gap + (evening,)
    week[4] = (morning,)                                  # Friday
    return week


VENUES: dict[str, VenueSpec] = {}


def _add(spec: VenueSpec) -> None:
    VENUES[spec.code] = spec


# ── equities ────────────────────────────────────────────────────────────────

_add(VenueSpec("XNYS", "NYSE / Nasdaq (US equities)", "US", "America/New_York",
               "exchange", _weekdays(Phase.of(PRE_OPEN, "04:00", "09:30"),
                                     Phase.of(OPEN, "09:30", "16:00"),
                                     Phase.of(POST_MARKET, "16:00", "20:00")),
               calendar="XNYS",
               source="NYSE/Nasdaq regular 09:30-16:00 ET; extended 04:00-09:30 and 16:00-20:00"))
_add(VenueSpec("XLON", "London Stock Exchange", "GB", "Europe/London", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "07:50", "08:00"),
                         Phase.of(OPEN, "08:00", "16:30"),
                         Phase.of(AUCTION, "16:30", "16:35")),
               calendar="XLON", closing_auction_minutes=5,
               source="LSE SETS: opening auction 07:50, continuous 08:00-16:30, closing auction 16:30-16:35"))
_add(VenueSpec("XNSE", "National Stock Exchange of India", "IN", "Asia/Kolkata", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "09:00", "09:15"),
                         Phase.of(OPEN, "09:15", "15:30"),
                         Phase.of(POST_MARKET, "15:40", "16:00")),
               calendar="XNSE",
               source="NSE: pre-open 09:00-09:15, normal 09:15-15:30, post-close 15:40-16:00 IST"))
_add(VenueSpec("XBOM", "BSE (Bombay Stock Exchange)", "IN", "Asia/Kolkata", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "09:00", "09:15"),
                         Phase.of(OPEN, "09:15", "15:30"),
                         Phase.of(POST_MARKET, "15:40", "16:00")),
               calendar="XBOM", source="BSE: same session structure as NSE"))
_add(VenueSpec("XETR", "Xetra (Deutsche Börse)", "DE", "Europe/Berlin", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "08:00", "09:00"),
                         Phase.of(OPEN, "09:00", "17:30"),
                         Phase.of(AUCTION, "17:30", "17:35")),
               calendar="XETR", closing_auction_minutes=5,
               source="Xetra: continuous 09:00-17:30 CET, closing auction to ~17:35"))
for _code, _name, _country, _tz in (("XPAR", "Euronext Paris", "FR", "Europe/Paris"),
                                    ("XAMS", "Euronext Amsterdam", "NL", "Europe/Amsterdam"),
                                    ("XBRU", "Euronext Brussels", "BE", "Europe/Brussels"),
                                    ("XLIS", "Euronext Lisbon", "PT", "Europe/Lisbon")):
    _add(VenueSpec(_code, _name, _country, _tz, "exchange",
                   _weekdays(Phase.of(PRE_OPEN, "07:15", "09:00"),
                             Phase.of(OPEN, "09:00", "17:30"),
                             Phase.of(AUCTION, "17:30", "17:35")),
                   calendar=_code, closing_auction_minutes=5,
                   early_close_includes_auction=True,
                   source="Euronext: pre-open 07:15, continuous 09:00-17:30, closing auction 17:30-17:35"))
_add(VenueSpec("XSWX", "SIX Swiss Exchange", "CH", "Europe/Zurich", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "06:00", "09:00"),
                         Phase.of(OPEN, "09:00", "17:20"),
                         Phase.of(AUCTION, "17:20", "17:30")),
               calendar="XSWX", closing_auction_minutes=10,
               source="SIX: continuous 09:00-17:20, closing auction 17:20-17:30"))
_add(VenueSpec("XTKS", "Tokyo Stock Exchange", "JP", "Asia/Tokyo", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "08:00", "09:00"),
                         Phase.of(OPEN, "09:00", "11:30"),
                         Phase.of(BREAK, "11:30", "12:30"),
                         Phase.of(OPEN, "12:30", "15:25"),
                         Phase.of(AUCTION, "15:25", "15:30")),
               calendar="XTKS", closing_auction_minutes=5,
               source="TSE since 2024-11-05: 09:00-11:30, 12:30-15:30 (closing auction 15:25-15:30)"))
_add(VenueSpec("XHKG", "Hong Kong Exchanges", "HK", "Asia/Hong_Kong", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "09:00", "09:30"),
                         Phase.of(OPEN, "09:30", "12:00"),
                         Phase.of(BREAK, "12:00", "13:00"),
                         Phase.of(OPEN, "13:00", "16:00"),
                         Phase.of(AUCTION, "16:00", "16:10")),
               calendar="XHKG", closing_auction_minutes=10,
               source="HKEX: pre-opening 09:00-09:30, 09:30-12:00, 13:00-16:00, CAS 16:00-16:10"))
for _code, _name in (("XSHG", "Shanghai Stock Exchange"), ("XSHE", "Shenzhen Stock Exchange")):
    _add(VenueSpec(_code, _name, "CN", "Asia/Shanghai", "exchange",
                   _weekdays(Phase.of(PRE_OPEN, "09:15", "09:30"),
                             Phase.of(OPEN, "09:30", "11:30"),
                             Phase.of(BREAK, "11:30", "13:00"),
                             Phase.of(OPEN, "13:00", "14:57"),
                             Phase.of(AUCTION, "14:57", "15:00")),
                   calendar=_code, closing_auction_minutes=3,
                   source="SSE/SZSE: call auction 09:15-09:25, 09:30-11:30, 13:00-14:57, closing call 14:57-15:00"))
_add(VenueSpec("XTSE", "Toronto Stock Exchange", "CA", "America/Toronto", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "07:00", "09:30"),
                         Phase.of(OPEN, "09:30", "16:00")),
               calendar="XTSE", source="TSX: 09:30-16:00 ET"))
_add(VenueSpec("XASX", "Australian Securities Exchange", "AU", "Australia/Sydney", "exchange",
               _weekdays(Phase.of(PRE_OPEN, "07:00", "10:00"),
                         Phase.of(OPEN, "10:00", "16:00"),
                         Phase.of(AUCTION, "16:00", "16:12")),
               calendar="XASX", closing_auction_minutes=12,
               source="ASX: pre-open 07:00, open 10:00 (staggered), close 16:00, CSPA ~16:10"))

# ── options ─────────────────────────────────────────────────────────────────

_add(VenueSpec("US_EQUITY_OPTIONS", "US listed equity options", "US", "America/New_York",
               "options", _weekdays(Phase.of(OPEN, "09:30", "16:00")), calendar="XNYS",
               source="US equity options: 09:30-16:00 ET on NYSE trading days"))
_add(VenueSpec("US_INDEX_OPTIONS", "US index options (SPX, NDX, RUT, XSP)", "US",
               "America/New_York", "options",
               _weekdays(Phase.of(OPEN, "09:30", "16:15")), calendar="XNYS",
               source="Cboe index options regular hours 09:30-16:15 ET (global trading hours not modelled)"))

# ── futures (contract-specific) ─────────────────────────────────────────────

_add(VenueSpec("CME_GLOBEX", "CME Globex (equity index, energy, metals, rates, FX, crypto)",
               "US", "America/Chicago", "futures",
               _overnight_week("17:00", "16:00", break_to="17:00"),
               holiday_rule="us_futures",
               source="CME Globex: Sun-Fri 17:00-16:00 CT, daily break 16:00-17:00 CT"))
_add(VenueSpec("CBOT_GRAINS", "CBOT grains (corn, soybeans, wheat)", "US", "America/Chicago",
               "futures",
               {6: (Phase.of(OPEN, "19:00", "24:00"),),
                **{d: (Phase.of(OPEN, "00:00", "07:45"), Phase.of(BREAK, "07:45", "08:30"),
                       Phase.of(OPEN, "08:30", "13:20"), Phase.of(OPEN, "19:00", "24:00"))
                   for d in range(4)},
                4: (Phase.of(OPEN, "00:00", "07:45"), Phase.of(BREAK, "07:45", "08:30"),
                    Phase.of(OPEN, "08:30", "13:20"))},
               holiday_rule="us_futures",
               source="CBOT grains: 19:00-07:45 and 08:30-13:20 CT, Sun-Fri"))
for _code, _name, _o, _c in (("ICE_KC", "ICE US coffee", "04:15", "13:30"),
                             ("ICE_SB", "ICE US sugar No.11", "03:30", "13:00"),
                             ("ICE_CC", "ICE US cocoa", "04:45", "13:30")):
    _add(VenueSpec(_code, _name, "US", "America/New_York", "futures",
                   _weekdays(Phase.of(OPEN, _o, _c)), holiday_rule="us_futures",
                   source=f"{_name}: {_o}-{_c} ET"))
_add(VenueSpec("ICE_CT", "ICE US cotton No.2", "US", "America/New_York", "futures",
               {6: (Phase.of(OPEN, "21:00", "24:00"),),
                **{d: (Phase.of(OPEN, "00:00", "14:20"), Phase.of(OPEN, "21:00", "24:00"))
                   for d in range(4)},
                4: (Phase.of(OPEN, "00:00", "14:20"),)},
               holiday_rule="us_futures", source="ICE cotton: 21:00-14:20 ET"))

# ── FX and crypto ───────────────────────────────────────────────────────────

_add(VenueSpec("FX", "Spot FX (global OTC)", "GLOBAL", "America/New_York", "fx",
               _overnight_week("17:00", "17:00"), holiday_rule="none",
               source="FX week: Sunday 17:00 to Friday 17:00 New York; holiday liquidity not modelled"))
_add(VenueSpec("CRYPTO", "Crypto (24/7)", "GLOBAL", "UTC", "crypto",
               {d: (Phase(OPEN, 0, 1440),) for d in range(7)}, holiday_rule="none",
               source="continuous trading"))


# ── how symbols map onto venues ─────────────────────────────────────────────

#: src/core/identity.py's exchange labels -> venue codes. A label with no
#: entry here (BORSA_IT, BME, OMX_*, OSLO, ...) resolves to UNKNOWN.
EXCHANGE_ALIASES = {
    "US": "XNYS", "NYSE": "XNYS", "NASDAQ": "XNYS", "LSE": "XLON", "NSE": "XNSE",
    "BSE": "XBOM", "XETRA": "XETR", "EURONEXT_PA": "XPAR", "EURONEXT_AS": "XAMS",
    "EURONEXT_BR": "XBRU", "EURONEXT_LS": "XLIS", "SIX": "XSWX", "TSE": "XTKS",
    "HKEX": "XHKG", "SSE": "XSHG", "SZSE": "XSHE", "TSX": "XTSE", "ASX": "XASX",
}

#: Continuous-future roots (Yahoo "XX=F") -> the venue whose hours they trade.
FUTURES_ROOTS = {
    **{r: "CME_GLOBEX" for r in ("ES", "NQ", "YM", "RTY", "MES", "MNQ", "MYM", "M2K",
                                 "CL", "NG", "RB", "HO", "BZ", "GC", "SI", "HG", "PL",
                                 "PA", "MGC", "ZN", "ZB", "ZF", "ZT", "UB", "6E", "6J",
                                 "6B", "6A", "6C", "6S", "BTC", "ETH", "MBT", "MET")},
    **{r: "CBOT_GRAINS" for r in ("ZC", "ZS", "ZW", "ZM", "ZL", "KE", "ZO")},
    "KC": "ICE_KC", "SB": "ICE_SB", "CC": "ICE_CC", "CT": "ICE_CT",
}

#: Continuous-future roots -> the CONTRACT CALENDAR (product group) whose
#: holiday schedule applies. Hours come from FUTURES_ROOTS' venue; holidays
#: do not, because one exchange's products do not share them — CME publishes
#: separate holiday hours for equity index, interest rate, FX, energy, metals,
#: grains and crypto contracts, and an ICE soft keeps its own. A group has a
#: calendar only when data/calendars/<GROUP>.json (with a source) covers the
#: year; until then every US holiday and its eve read UNKNOWN for its roots.
FUTURES_CALENDARS = {
    **{r: "CME_EQUITY" for r in ("ES", "NQ", "YM", "RTY", "MES", "MNQ", "MYM", "M2K")},
    **{r: "CME_ENERGY" for r in ("CL", "NG", "RB", "HO", "BZ")},
    **{r: "CME_METALS" for r in ("GC", "SI", "HG", "PL", "PA", "MGC")},
    **{r: "CME_RATES" for r in ("ZN", "ZB", "ZF", "ZT", "UB")},
    **{r: "CME_FX" for r in ("6E", "6J", "6B", "6A", "6C", "6S")},
    **{r: "CME_CRYPTO" for r in ("BTC", "ETH", "MBT", "MET")},
    **{r: "CBOT_GRAINS" for r in ("ZC", "ZS", "ZW", "ZM", "ZL", "KE", "ZO")},
    "KC": "ICE_KC", "SB": "ICE_SB", "CC": "ICE_CC", "CT": "ICE_CT",
}

#: Index levels are not tradable, but their bars have a time: the venue whose
#: session produces them.
INDEX_VENUES = {
    "^GSPC": "XNYS", "^DJI": "XNYS", "^IXIC": "XNYS", "^NDX": "XNYS", "^RUT": "XNYS",
    "^VIX": "XNYS", "^TNX": "XNYS", "^FTSE": "XLON", "^FTMC": "XLON", "^N225": "XTKS",
    "^HSI": "XHKG", "^GDAXI": "XETR", "^FCHI": "XPAR", "^NSEI": "XNSE",
    "^NSEBANK": "XNSE", "^BSESN": "XBOM", "^AXJO": "XASX", "^GSPTSE": "XTSE",
    "^SSMI": "XSWX", "^AEX": "XAMS",
}

#: Index option roots trade index-option hours.
INDEX_OPTION_ROOTS = ("SPX", "SPXW", "NDX", "NDXP", "RUT", "RUTW", "XSP", "VIX", "VIXW")
