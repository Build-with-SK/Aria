# Market sessions: where an instrument trades, when, and what that means for orders

Last revised 2026-10-06. Describes `src/markets/` and
`src/trading/session_orders.py` as they are.

## One abstraction, every consumer

`src/markets/sessions.py` answers, for any instrument at any instant: its
venue, timezone and calendar; its session state; when that state ends; when
the market next opens; and which of its daily bars could have been known.

| consumer | uses |
|---|---|
| execution pipeline (`src/trading/pipeline.py`) | `session(symbol)` as the **first** gate, before risk |
| order validator (`src/trading/validator.py`) | `market_open(..., symbol=)` → the instrument's own session |
| reflex lane, exit engine, held intents (`src/desk/`) | `instrument_open(symbol)` / `session(symbol)` per instrument |
| options chain (`src/options/chain.py`) | the US listed-options venue |
| V5 predict loop, technical tracker (`src/v5/loop.py`, `src/data/technical_tracker.py`) | settled sessions at the instrument's venue |
| ledger grading anchor (`src/core/ledger.py`) | the close of the session a call was built on, at its own venue |
| V5 point-in-time replay (`marketdata.as_of(<aware instant>)`) | `bar_known_by(venue, date, instant)` |
| live quote and UI (`/api/quote/live`, `/api/markets/session`) | the same `SessionInfo` |

`src/trading/market.py` keeps its old functions (`market_open`,
`latest_completed_session`, `NYSE_HOLIDAYS`, …) as thin adapters over the
engine, so no caller sees a different calendar.

## States

`OPEN · PRE_OPEN · AUCTION · BREAK · POST_MARKET · CLOSED · HOLIDAY · HALTED ·
EXPIRING · UNKNOWN`. **Only OPEN is tradable.** UNKNOWN is never treated as
open.

* **UNKNOWN** means one of these:
  * no venue spec (Borsa Italiana, BME, the Nordics, Oslo, Vienna, Athens);
  * no calendar provider can answer for that date (NSE, Shenzhen, beyond a
    calendar's recorded range, or a provider failure);
  * an invalid timezone;
  * a US futures holiday whose abbreviated schedule is not modelled.

  UNKNOWN is fail-safe. Only OPEN passes the execution pipeline's first gate,
  and every planner treats UNKNOWN as DEFERRED.
* A missing calendar can only remove trading hours. So outside the regular
  phases a venue reads CLOSED; inside them it reads UNKNOWN.
* **HALTED** comes only from a data feed (`halted=True`). A schedule cannot
  know about halts. Without the flag, the reasons say the halt status was not
  observed, and quote freshness in the validator is the backstop.
* **EXPIRING** applies on a contract's last trading day. An option's expiry is
  read from its OCC symbol.

## Venues (`src/markets/venues.py`)

Hours are local wall-clock times in each venue's IANA timezone, so daylight
saving is the timezone database's job. They are the published regular
schedules as understood on 2026-10-06; each spec names its source.

| venue | regular hours (local) | breaks / auctions |
|---|---|---|
| XNYS (NYSE/Nasdaq) | 09:30–16:00 | pre 04:00, post to 20:00 |
| XLON | 08:00–16:30 | opening auction 07:50, closing auction 16:30–16:35 |
| XNSE, XBOM | 09:15–15:30 | pre-open 09:00, post-close 15:40–16:00 |
| XETR | 09:00–17:30 | closing auction to 17:35 |
| XPAR, XAMS, XBRU, XLIS | 09:00–17:30 | pre-open 07:15, closing auction 17:30–17:35 |
| XSWX | 09:00–17:20 | closing auction to 17:30 |
| XTKS | 09:00–11:30, 12:30–15:25 | lunch 11:30–12:30, closing auction 15:25–15:30 |
| XHKG | 09:30–12:00, 13:00–16:00 | lunch, closing auction to 16:10 |
| XSHG, XSHE | 09:30–11:30, 13:00–14:57 | lunch, closing call 14:57–15:00 |
| XTSE | 09:30–16:00 | — |
| XASX | 10:00–16:00 | closing auction to ~16:12 |
| CME Globex | Sun–Fri 17:00–16:00 CT | daily break 16:00–17:00 |
| CBOT grains | 19:00–07:45, 08:30–13:20 CT | |
| ICE softs | KC 04:15–13:30, SB 03:30–13:00, CC 04:45–13:30, CT 21:00–14:20 ET | |
| US options | equity 09:30–16:00, index 09:30–16:15 ET | |
| FX | Sun 17:00 – Fri 17:00 New York | |
| Crypto | 24/7 | |

## Calendars: the provider chain (`src/markets/providers.py`, `calendars.py`)

```
Instrument -> Venue / Contract -> Session engine (sessions._day: the one integration point)
                                    -> 1. owner file          data/calendars/<CODE>.json, with a source
                                    -> 2. exchange_calendars  ExchangeCalendarsProvider
                                    -> 3. built-in static     XNYS, XLON 2026-2027
                                    -> 4. none                UNKNOWN (a weekend is still CLOSED)
                                  -> Session state -> trading / planning / backtesting / UI
```

A provider says whether a venue trades on a date, and gives that day's open,
close and break as `DayFacts`. Phase structure (pre-open, auctions,
post-market) stays in `venues.py`, because the library models none of it.

The engine applies the provider's *deviation from its own regular times* to the
venue's phases. A calendar whose close includes the closing auction (Tokyo)
and one whose close does not (London) are therefore both read correctly. A
date whose regular times differ from today's is a **historical schedule** and is
built from the facts directly. Tokyo, for example, closed at 15:00 until
2024-11-05, so a 2023 replay must not see 15:00–15:30 as open. A session on a
day the venue has no hours for is a **special session**.

**`exchange_calendars==4.13.2`** is pinned in `requirements.txt` and
`requirements-ci.txt`.

* It is local, rule- and table-based data, with no network access at runtime.
* Upgrading it can change financial facts, so bump the pin deliberately and
  re-run `tests/test_calendar_provider.py`.
* `providers.py` is the only module that imports it (a test enforces this).
* It is lazily imported (about 2 s), and each calendar is built once per
  process (about 0.3 s).
* Venue-day facts are cached; a warm `session()` call takes well under 1 ms.
* The range is fixed rather than "today + 1 year", so answers do not depend on
  when the process started: `START` 2006-01-01 to `HORIZON` 2027-12-31,
  clamped to each calendar's recorded `bound_max()`.

| venue | answered by | range |
|---|---|---|
| XNYS (and both US options venues) | exchange_calendars XNYS; built-in fallback | 2006 – 2027 |
| XLON | exchange_calendars XLON; built-in fallback | 2006 – 2027 |
| XBOM (BSE) | exchange_calendars XBOM | 2006 – **2026** (library records India to 2026) |
| XTKS, XHKG, XETR, XPAR, XAMS, XBRU, XLIS, XSWX, XTSE, XASX | exchange_calendars | 2006 – 2027 |
| XSHG (Shanghai) | exchange_calendars XSHG | 2006 – **2026** (library records China to 2026) |
| **XNSE (NSE)** | nothing → **UNKNOWN** | the library has no NSE calendar |
| **XSHE (Shenzhen)** | nothing → **UNKNOWN** | the library has no Shenzhen calendar |
| CME Globex, CBOT grains, ICE softs | contract hours in `venues.py`; US holidays from the XNYS chain → **UNKNOWN** | see Futures below |
| FX, crypto | weekly / continuous rules | no holidays |

The built-in XNYS and XLON 2026 calendars agree with `exchange_calendars` on
every weekday's session status and early close; a test checks this.

**Euronext half days.** The library reports Euronext's regular close as 17:30
(end of continuous trading) but its 24/31 December close as 14:05 (end of the
closing auction). Those venues carry `early_close_includes_auction=True`, so
continuous trading ends at 14:00, with the auction to 14:05.

**NSE and Shenzhen are not aliased in code.** Treating NSE as BSE, or Shenzhen
as Shanghai, is an inference, not coverage. The owner can make that call
explicitly with a file:

```json
{"alias_of": "XBOM", "source": "owner decision 2026-10-06: NSE and BSE share equity trading holidays"}
```

The file goes in `data/calendars/XNSE.json`. A file without a `source` is
ignored.

**Futures stay contract-specific.** The library's `CMES`, `IEPA` and
`us_futures` calendars are generic approximations. CMES, for example, models
one 17:00–17:00 CT session for every CME product, with no daily maintenance
break. Mapping onto them would replace correct contract hours with wrong ones.

**Special sessions not in the library** (NSE/BSE Muhurat trading on a Sunday)
come only from an owner file's `special_sessions`, which replaces that day's
phases.

### Adding a venue safely

1. Add its hours to `venues.py`: IANA timezone and phases, with a `source`.
2. Map its calendar code in `providers.XCALS_MAP`, but **only** if
   `exchange_calendars` covers that exchange itself. Otherwise supply
   `data/calendars/<CODE>.json` from the exchange's circular, or leave it
   UNKNOWN.
3. Add its identity suffix to `EXCHANGE_ALIASES`.
4. Add normal-day, weekend, holiday, early-close and DST cases to
   `tests/test_calendar_provider.py`, with dates taken from the provider's own
   data or the exchange's circular, never from memory.

`/api/markets/calendars` shows, per venue, which providers answer and over
what range.

## Point in time across markets

`bar_available_at(venue, date)` is the UTC instant a daily bar could first be
known: the venue's session close for that date, early closes included.
`marketdata.as_of(<aware instant>)` keeps only bars already known at that
instant. At 09:00 Tokyo on a Monday, New York's Monday bar does not exist; a
date-based cut (the old behaviour, still available with a naive date) would
have handed it to a Tokyo replay. A bar from an unknown venue counts as known
only once its date is over everywhere (00:00 UTC two days later).

## Closed markets, the next open, and the open itself (`src/trading/session_orders.py`)

Pure functions with no broker, clock reads or model calls. A plan is a
decision about an order; executing it still goes through every gate.

`plan_order(intent, session, policy)` turns a thesis into one of these:

* **EXECUTE_NOW** — the session is OPEN.
* **SCHEDULED** — re-evaluated `min_observation_minutes` (default 5) after the
  next open, never as a blind order at the opening print.
* **CONDITIONAL** — has a trigger, which fires only on an in-session price.
* **STANDING** — a GTC limit resting at the venue. Requires a patient
  strategy, a limit within 1 ATR of the reference price, and
  `allow_standing_orders`, which is **off by default**.
* **DEFERRED** — event-driven, UNKNOWN or HALTED.
* **REJECT** — the thesis expires before the reopen, or the order would open a
  position on an expiring contract.

`estimate_open(prev_close, gap_history, proxy)` gives an expected open with a
±1σ range from the instrument's own overnight gaps. It shifts only by a proxy
observed after the target's close and within 15 minutes (index futures, an
ADR's home market). `confidence` is the interval's nominal coverage and
`coverage_in_sample` is how often history landed inside it. Neither has been
validated out of sample, and the estimate says so.

`adapt_at_open(plan, observation)` measures the opening gap in ATRs against the
trade:

| condition | action |
|---|---|
| fewer than `min_observation_minutes` observed | WAIT |
| adverse gap under 0.5 ATR | NORMAL |
| adverse gap 0.5–1.5 ATR | REDUCE (×0.5) |
| gap of 1.5 ATR or more either way, or an open through the stop | REASSESS |
| material news | SUSPEND |
| spread or liquidity out of bounds, or not observed | DEFER |
| risk breach | REJECT |

`/api/markets/plan` previews a plan. It is owner-only and stores and executes
nothing.

## The execution chain

`TradingPipeline.run` checks, in order:

1. **SESSION** (stage `session`)
2. **DATA FRESH** (risk engine: account snapshot age, quote freshness, signal
   data age)
3. **PRICE / RISK** (risk engine)
4. **ORDER** (validator, which re-checks the session per instrument)
5. **PERMISSION** (kill switch, `can_place_orders`)
6. **MODE** (RESEARCH is a dry run)

A market moving from CLOSED to OPEN triggers nothing by itself.

## Not done, by design or for now

* Scheduled plans are not stored or executed automatically. Wiring
  `SCHEDULED` / `CONDITIONAL` plans into the pipeline at the open would be a
  new autonomous execution path, so it is left for an explicit owner decision.
* NSE and Shenzhen read UNKNOWN until an owner file covers them (see
  above). BSE and Shanghai read UNKNOWN from 2027 until the library records
  those years.
* Futures holiday schedules (CME/ICE abbreviated sessions) read UNKNOWN. So
  does the evening before a US holiday.
* The desk's daily budget still rolls on the New York date
  (`src/desk/day_state.py`). The desk trades US equities and crypto only.
