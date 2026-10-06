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

* **UNKNOWN** means one of: no venue spec (Borsa Italiana, BME, the Nordics,
  Oslo, Vienna, Athens), a year with no verified holiday calendar, or a US
  futures holiday whose abbreviated schedule is not modelled.
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

## Calendars (`src/markets/calendars.py`)

Two calendars are built in, dated and verified: **XNYS** (2026–2027 holidays
and 13:00 early closes) and **XLON** (England & Wales bank holidays 2026–2027,
12:30 half days on 24 and 31 December).

Every other venue reads UNKNOWN on weekdays inside its hours until a
`data/calendars/<CODE>.json` supplies the year from the exchange's own
circular. The file must state a `source`, or it is ignored. Its
`special_sessions` field replaces a day's phases entirely (for example, NSE
Muhurat trading on a Sunday). `/api/markets/calendars` shows the coverage.

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
* Holiday calendars for NSE, BSE, TSE, HKEX, Euronext, Xetra, SIX, SSE/SZSE,
  TSX and ASX are not built in.
  * Hand-typed holiday lists from memory would be fabricated reference data.
  * Install `exchange_calendars` (an owner decision: a new dependency), or add
    `data/calendars/*.json` from each exchange's circular.
* Futures holiday schedules (CME/ICE abbreviated sessions) read UNKNOWN. So
  does the evening before a US holiday.
* The desk's daily budget still rolls on the New York date
  (`src/desk/day_state.py`). The desk trades US equities and crypto only.
