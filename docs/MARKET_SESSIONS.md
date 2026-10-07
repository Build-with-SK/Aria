# Market sessions: where an instrument trades, when, and what that means for orders

Last revised 2026-10-07. Describes `src/markets/` and
`src/trading/session_orders.py` as they are.

> **UNKNOWN is fail-safe and must never authorize execution.** A venue, a
> date, a contract or a calendar the engine cannot vouch for reads UNKNOWN,
> and nothing in ARIA sends an order on UNKNOWN. Only `OPEN` is tradable.

## One authoritative engine, every consumer

```
instrument -> venue resolution -> session engine (src/markets/sessions.py)
                                   -> calendar-provider precedence (sessions._day)
                                   -> session state
                                   -> data / risk / order / permission / mode checks
```

`src/markets/sessions.py` answers, for any instrument at any instant: its
venue, timezone and calendar; its session state; when that state ends; when
the market next opens and next closes; and which of its daily bars could have
been known. There is no second engine: holiday logic lives only in
`src/markets/`, and every consumer asks it.

| consumer | uses |
|---|---|
| execution pipeline (`src/trading/pipeline.py`) | `session(symbol, asset_class=)` as the **first** gate, before risk |
| order validator (`src/trading/validator.py`) | `market_open(..., symbol=)` → the instrument's own session |
| approval queue / desk auto-executor / reflex lane (`src/execution/order_manager.py`) | `OrderManager.execute()` checks the session **at the moment of sending** |
| exit engine, reflex triggers, held intents (`src/desk/`) | `instrument_open(symbol, asset_class=)` / `session(symbol)` |
| options chain (`src/options/chain.py`) | the US listed-options venue |
| V5 predict loop, technical tracker (`src/v5/loop.py`, `src/data/technical_tracker.py`) | settled sessions at the instrument's venue |
| ledger grading anchor (`src/core/ledger.py`) | the close of the session a call was built on, at its own venue |
| V5 point-in-time replay (`marketdata.as_of(<aware instant>)`) | `bar_known_by(venue, date, instant)` |
| live quote and UI (`/api/quote/live`, `/api/markets/session`, `/api/markets/calendars`) | the same `SessionInfo`, `calendars.coverage()` |

`src/trading/market.py` keeps its old functions (`market_open`,
`latest_completed_session`, `NYSE_HOLIDAYS`, …) as thin adapters over the
engine, so no caller sees a different calendar. Its `session_close()` returns
None for a day the calendar cannot answer; it no longer substitutes 16:00.

## States

`OPEN · PRE_OPEN · AUCTION · BREAK · POST_MARKET · CLOSED · HOLIDAY · HALTED ·
EXPIRING · UNKNOWN`. **Only OPEN is tradable.**

* **BREAK** is the lunch break (Tokyo, Hong Kong, Shanghai, Shenzhen) and a
  futures maintenance break. A lunch break is not a close: `next_close` during
  the morning session is the day's close, and `phase_ends_at` is the start of
  lunch. For futures and FX, whose breaks really stop trading, `next_close` is
  the end of the contiguous trading stretch.
* **AUCTION** is a closing auction or closing call. It is not continuous
  trading and is not tradable. A daily bar's close includes it.
* **POST_MARKET** is extended or post-close trading. It is not OPEN.
* **HOLIDAY** is a listed closure on a weekday. `next_open` skips to the next
  trading day's open, never to later the same day.
* **HALTED** comes only from a data feed (`halted=True`). A schedule cannot know
  about halts. Without the flag, the reasons say the halt status was not
  observed, and quote freshness in the validator is the backstop.
* **EXPIRING** applies on a contract's last trading day. An option's expiry is
  read from its OCC symbol.
* **UNKNOWN** means one of these:
  * no venue spec (Borsa Italiana, BME, the Nordics, Oslo, Vienna, Athens);
  * no calendar provider covers that date: years outside an owner file, beyond
    a library calendar's recorded range, or before 2006;
  * a day the calendar itself declares unknown, such as NSE's 2026 Muhurat
    session, announced without hours. The whole day is UNKNOWN;
  * an owner calendar file that exists but fails validation;
  * an invalid timezone, or a provider that raises;
  * a US futures holiday, or its eve, that no contract calendar states;
  * a symbol whose asset-class label contradicts its shape (for example `SPY`
    labelled crypto).

  UNKNOWN has no `next_open` and no `next_close`, because the engine cannot see
  past an unknown day. A missing calendar can only remove trading hours, so
  outside a venue's regular phases it reads CLOSED; inside them it reads
  UNKNOWN. A day the calendar declares unknown is UNKNOWN at every hour.
  A weekend reads CLOSED without a calendar. Of these venues, only NSE is known
  to open on a weekend, and only on a session its calendar lists.

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
| XSHG, XSHE | 09:30–11:30, 13:00–14:57 | call auction 09:15, lunch, closing call 14:57–15:00 |
| XTSE | 09:30–16:00 | — |
| XASX | 10:00–16:00 | closing auction to ~16:12 |
| CME Globex | Sun–Fri 17:00–16:00 CT | daily break 16:00–17:00 |
| CBOT grains | 19:00–07:45, 08:30–13:20 CT | |
| ICE softs | KC 04:15–13:30, SB 03:30–13:00, CC 04:45–13:30, CT 21:00–14:20 ET | |
| US options | equity 09:30–16:00, index 09:30–16:15 ET | |
| FX | Sun 17:00 – Fri 17:00 New York | |
| Crypto | 24/7 | |

### Resolution and asset classes

`resolve(symbol)` reads the symbol's own shape (`.L`, `.NS`, `=F`, `-USD`,
OCC options, `^` indices). A caller that already knows the asset class (a
broker position, a queued trade) passes it, and it settles **format**
ambiguity only:

* `BTCUSD` labelled crypto is the BTC/USD pair. This is how Alpaca reports
  crypto positions, which used to resolve as a US ticker.
* `ES` labelled futures is the ES root, with its contract calendar.
* `EURUSD` labelled forex is the FX pair.
* A label that contradicts the symbol (`SPY` labelled crypto, `COIN` labelled
  crypto) resolves to **no venue**, so it reads UNKNOWN. A label never makes an
  equity 24/7.
* An option without an OCC symbol has no known expiry and reads UNKNOWN.
* When the shape says something (`EURUSD=X`), the shape wins.

## Calendars: the provider chain (`src/markets/providers.py`, `calendars.py`)

```
Instrument -> Venue / Contract -> Session engine (sessions._day: the one integration point)
                                    -> 1. owner file          data/calendars/<CODE>.json, with provenance
                                    -> 2. exchange_calendars  ExchangeCalendarsProvider
                                    -> 3. built-in static     XNYS, XLON 2026-2027
                                    -> 4. none                UNKNOWN (a weekend is still CLOSED)
                                  -> Session state -> trading / planning / backtesting / UI
```

A provider says whether a venue trades on a date, and gives that day's open,
close and break as `DayFacts`. **Phase structure** (pre-open, auctions,
post-market, lunch) stays in `venues.py`, because the library models none of
it. The engine applies the provider's *deviation from its own regular times*
to the venue's phases. So a calendar whose close includes the closing auction
(Tokyo) and one whose close does not (London) are both read correctly, and an
early close keeps the venue's closing auction. A date whose regular times
differ from today's is a **historical schedule** and is built from the facts
directly (Tokyo closed at 15:00 until 2024-11-05). A session on a day the venue
has no hours for is a **special session**.

### exchange_calendars

**`exchange_calendars==4.13.2`** is pinned in `requirements.txt` and
`requirements-ci.txt`.

* It is local, rule- and table-based data, with no network access at runtime.
* Upgrading it can change financial facts, so bump the pin deliberately and
  re-run `tests/test_calendar_provider.py` and `tests/test_global_calendars.py`.
* `providers.py` is the only module that imports it (a test enforces this).
* It is imported lazily. Importing `src.markets` loads neither it nor pandas,
  and NSE and Shenzhen answers (owner files) never load it.
* The range is fixed rather than "today + 1 year", so answers do not depend on
  when the process started: `START` 2006-01-01 to `HORIZON` 2027-12-31,
  clamped to each calendar's recorded `bound_max()`.
* Inside that built range, a date after the library's last *session* (31 Dec
  2027 is a holiday at Xetra, SIX and Tokyo) is a non-session. The library
  refuses to say so itself (`DateOutOfBounds`), and before 2026-10-07 such days
  read UNKNOWN.
* Only venues the library covers **authoritatively** are mapped (`XCALS_MAP`).
  It has no NSE and no Shenzhen calendar, and its futures calendars (`CMES`,
  `IEPA`, `us_futures`) are generic approximations, so none of those are mapped.

### Owner calendar files (`data/calendars/<CODE>.json`)

An owner file is the highest-precedence answer for the years it lists. An
exchange circular filed by the owner is more current than any library release.
The format (`calendars.parse`):

```json
{"source": "who published these dates",
 "years": [2026],
 "provenance": [{"ref": "NSE/CMTR/71775", "date": "2025-12-12", "url": "https://…", "sha256": "…"}],
 "holidays": ["2026-01-26", {"date": "2026-03-03", "name": "Holi"}],
 "closures": [{"from": "2026-02-15", "to": "2026-02-23", "name": "Spring Festival"}],
 "early_closes": {"2026-12-24": "12:30"},
 "special_sessions": {"2025-10-21": {"phases": [["PRE_OPEN", "13:30", "13:45"], ["OPEN", "13:45", "14:45"]]}},
 "unknown_days": {"2026-11-08": "Muhurat announced, timings to be notified"},
 "regular_days": ["2026-11-25"]}
```

* `special_sessions` **replaces** the day's phases, even on a listed holiday
  (NSE's Muhurat hour on Diwali).
* `unknown_days` are deviations the source announces without enough detail to
  model. The whole day is UNKNOWN.
* `closures` is the "closed from X to Y" form that Chinese exchanges publish.
* `regular_days` is for contract calendars only (see Futures).
* **Validation is strict.** A file is **invalid**, and its venue reads UNKNOWN,
  if any of these hold:
  * it has no `source` or no `years`;
  * a date or time is malformed;
  * a date falls outside the years the file claims;
  * a phase state is unknown, phases overlap, or a session has no OPEN phase;
  * a key is unknown (`"holiday"` for `"holidays"` would silently drop every
    closure);
  * a day is both unknown and given hours.

  An invalid file **does not fall through** to the library it meant to override.
  `/api/markets/calendars` shows `valid: false` and why.
* A runtime registration (`calendars.register`) beats a file. Tests use it.
* An explicit alias `{"alias_of": "XBOM", "source": "…"}` remains possible as
  an owner decision. None is shipped.

### Provenance requirements

Every shipped calendar file must:

1. name the issuing exchange's own document for every date: circular or
   notice reference, date, URL, and a SHA-256 digest of the copy read;
2. say how it was retrieved, and what could not be retrieved;
3. state its limitations (uncovered years, ad-hoc closures after the
   retrieval date);
4. record any cross-check against another source as a **check**, never as a
   source of dates.

Nothing may be typed from memory, inferred from a neighbouring venue,
extrapolated from a prior year, or taken from a blog or broker summary.

### Coverage and date bounds (`sessions.calendar_bounds(code)`)

| venue | answered by | known range | first UNKNOWN date |
|---|---|---|---|
| XNYS (and both US options venues) | exchange_calendars XNYS; built-in fallback | 2006 – 2027 | 2028-01-01 |
| XLON | exchange_calendars XLON; built-in fallback | 2006 – 2027 | 2028-01-01 |
| XTKS, XHKG, XETR, XPAR, XAMS, XBRU, XLIS, XSWX, XTSE, XASX | exchange_calendars | 2006 – 2027 | 2028-01-01 |
| **XNSE (NSE)** | **owner file** `data/calendars/XNSE.json` from NSE circulars | 2025 – 2026 | 2027-01-01 (and the whole of 2026-11-08) |
| XBOM (BSE) | exchange_calendars XBOM | 2006 – 2026 | 2027-01-01 |
| XSHG (Shanghai) | exchange_calendars XSHG | 2006 – 2026 | 2027-01-01 |
| **XSHE (Shenzhen)** | **owner file** `data/calendars/XSHE.json` from the SZSE notice | 2026 | 2027-01-01 |
| CME Globex, CBOT grains, ICE softs | contract hours + contract calendars (none shipped) + the US chain | 2006 – 2027-12-31 | 2028-01-01; US holidays and their eves inside the span |
| FX, crypto | weekly / continuous rules | — | — |

The engine never extrapolates past `final_known_date`. Every weekday after it
reads UNKNOWN inside trading hours, and the last known day's `next_open` is
None rather than a guessed next weekday. India and China end at 2026 because
neither the exchanges nor the library have published 2027 yet. NSE normally
publishes in December. China's exchanges follow the State Council's holiday
notice, normally issued in November.

### NSE (India)

`data/calendars/XNSE.json` transcribes the NSE **Capital Market segment**
circulars:

* NSE/CMTR/65587 (2025 holidays);
* NSE/CMTR/65729 (live session Sat 2025-02-01, Union Budget);
* NSE/CMTR/70319 (Muhurat 2025-10-21: pre-open 13:30–13:45, normal market
  13:45–14:45, closing session 14:55–15:05);
* NSE/CMTR/71775 (2026 holidays);
* NSE/CMTR/72260 (holiday 2026-01-15, in partial modification of 71775);
* NSE/CMTR/72349 (live session Sun 2026-02-01, Union Budget).

**Muhurat 2026** (Sunday 8 Nov) is announced but its timings "shall be notified
subsequently", so that day is UNKNOWN. When NSE publishes the timing circular,
replace the `unknown_days` entry with a `special_sessions` entry that cites it.

NSE is **not** aliased to BSE. exchange_calendars' BSE data agrees with NSE's
weekday closures for 2025 and 2026, which is recorded as a cross-check. It
records neither Muhurat session nor the 2026 Budget Sunday, though: BSE reads
HOLIDAY on 2025-10-21 and CLOSED on 2026-02-01. That is wrong in the safe
direction, and it is fixable only with an XBOM owner file built from BSE's
own notices.

### Shenzhen (China)

`data/calendars/XSHE.json` transcribes the SZSE notice 关于2026年部分节假日休市安排的通知
(2025-12-22) as closure ranges. Its status is **PROVISIONALLY VERIFIED**
(`"verification": {"status": "provisional"}`).

* szse.cn refused every connection from the build machine, on two attempts on
  2026-10-07. No date has been read from SZSE's own page.
* The dates come from the Securities Times republication of the joint
  SSE/SZSE/BSE notice. That copy was retrieved and its SHA-256 recorded; it
  matches the encoded ranges exactly.
* They were checked against the search engine's extraction of the szse.cn page
  itself.
* Every session reason drawn from this file says "PROVISIONALLY verified".

To upgrade it to `verified`, read the szse.cn URL in `provenance`, record its
checksum, and change the status. NSE's file is `verified`: every circular was
read from nsearchives.nseindia.com, with its checksum recorded.

* China's make-up working weekends (调休) are **not** trading days. The notice
  declares each of them closed.
* Shenzhen is **not** aliased to Shanghai. Their 2026 calendars agree, and a
  test checks that agreement as a consistency check only.
* The lunch break 11:30–13:00 is BREAK, and the closing call auction runs
  14:57–15:00.

### US futures: contract hours, contract calendars

Futures hours come from the venue (`FUTURES_ROOTS`). Holidays come from the
**contract calendar** of the root's product group (`FUTURES_CALENDARS`),
because CME's equity index, interest rate, FX, energy, metals, grains and
crypto products keep different holiday hours:

| contract calendar | roots |
|---|---|
| CME_EQUITY | ES NQ YM RTY MES MNQ MYM M2K |
| CME_ENERGY | CL NG RB HO BZ |
| CME_METALS | GC SI HG PL PA MGC |
| CME_RATES | ZN ZB ZF ZT UB |
| CME_FX | 6E 6J 6B 6A 6C 6S |
| CME_CRYPTO | BTC ETH MBT MET |
| CBOT_GRAINS | ZC ZS ZW ZM ZL KE ZO |
| ICE_KC, ICE_SB, ICE_CC, ICE_CT | KC, SB, CC, CT |

A contract calendar is an owner file (`data/calendars/CME_EQUITY.json`, …).
For the years it covers, it answers with:

* the day's full phases for an abbreviated session (`special_sessions`);
* a full closure (`holidays`);
* an announced day with no detail (`unknown_days`);
* a confirmed normal day (`regular_days`).

Every US holiday **and the evening before it** must be stated one way or the
other. A day the calendar is silent about is UNKNOWN, never assumed regular.
An `early_closes` entry alone is refused for futures, because it does not say
when the evening session reopens.

**No contract calendar is shipped.** No authoritative CME or ICE holiday
schedule is in the repository, and none was transcribed. So every US holiday
and its eve stay UNKNOWN for every root. On normal days the contract hours
apply: overnight sessions, the daily maintenance break, CBOT grains' morning
pause, and Friday's early stop. `next_open` and `next_close` work across
these, and stop at an unknown day.

A known limitation: a day on which a contract deviates while the NYSE trades
normally is not detected without that contract's calendar. SIFMA
bond-market holidays for Treasury futures are the obvious case.

## Point in time across markets

`bar_available_at(venue, date)` is the UTC instant a daily bar could first be
known: the venue's session close for that date, with early closes and closing
auctions included. `marketdata.as_of(<aware instant>)` keeps only bars already
known at that instant.

* At 09:00 Tokyo on a Monday, New York's Monday bar does not exist. A
  date-based cut, the old behaviour still available with a naive date, would
  hand it to a Tokyo replay.
* A lunch break is not a close.
* Daylight saving comes from the timezone database. In the US/UK gap weeks,
  London's close is 16:35 UTC, not 15:35.
* A half day releases the bar at the early close.
* A bar dated on a holiday, an UNKNOWN day or an unknown venue counts as known
  only once its date is over everywhere (00:00 UTC two days later).
* A futures bar waits for the end of its calendar date's last phase. That is
  later than settlement, never earlier.

`tests/test_pit_global.py` replays Sydney, Tokyo, Shanghai, Shenzhen, Hong
Kong, NSE, BSE, London, Xetra, Paris, New York, Toronto and CME. Each case is
adversarial both ways: rewriting a bar that had not closed must change
nothing, and rewriting one that had must change the result. With a date-based
cut injected in place of the engine, 24 of its 46 cases fail.

## Execution safety

Before an order is sent, the instrument's session is checked at the moment of
sending, and only OPEN passes:

| path | where the session is checked |
|---|---|
| `TradingPipeline.run` | stage `session` at `now`, before risk; in the validator; and **again at the send boundary** (`send_at`, default the send-time clock) |
| owner approve route, desk auto-executor, reflex lane | `OrderManager.execute(now=, halted=)` → `session_refusal()`. A closed, halted or UNKNOWN market leaves the trade **pending and unsent**. The desk records it as `queued` for the owner |
| queued manual exits (`reduce_only=True`) | the same session check, plus **reduce-only at send**: clipped to the broker's position, refused if it has gone |
| exit engine (`PositionManager._close`) | `instrument_open(ticker, asset_class)`, then cancels the brackets, then **reduce-only at send** against the broker's position now |
| held intents (`src/desk/intents.py`) | `evaluate(..., session=)`. Not tradable means HELD. An intent has no execution path of its own |
| session plans (`src/trading/session_orders.py`) | inert previews with no `execute`. No module in `src/` imports them |
| protective stop / target / OCO for a held position | deliberately **not** session-gated, because a stop must stand overnight. **Reduce-only** instead, and one OCO or a stop alone, never two independent exits |
| kill-switch halt flatten | deliberately not session-gated (an emergency). **Reduce-only**: nets out resting sells, skips what it cannot account for, never covers shorts |

### The reduce-only invariant (`src/execution/reduce_only.py`)

Any order allowed outside the entry gates must, at the moment it is sent, sit
on the reducing side of a position that exists now. Its quantity plus
everything already resting on that side must be at most the position:

    |position after every resting reducing order and this one fill| <= |position now|

The rules:

* OCO legs (linked by `parent_id`) count once; unlinked orders count in full.
* If the position or the resting orders cannot be read, **nothing is sent**.
* The exit engine waits briefly for its cancellations to settle before it
  checks. A "pending cancel" order can still fill, so until then it counts as
  resting.
* At a broker without OCO, a stop and a target are never placed as two
  full-size orders. The stop goes alone and the missing target is reported.

`tests/test_reduce_only.py` covers 15 scenarios:

1. long with a reducing sell
2. short with a reducing buy
3. no position
4. oversized order
5. wrong side
6. duplicate order
7. stale snapshot
8. position vanished
9. symbol changed
10. partial fill
11. rejection
12. OCO sibling left behind
13. flatten
14. halt outside the session
15. UNKNOWN session during a protective order

With the guard disabled, 27 of the file's 48 cases fail.

### The halt in production

The halt route passes `access.paper_adapter()` and does not pass `held`. Two
consequences:

* **Positions are now read from the broker.** Before 2026-10-07 the empty
  `held` made every stop look like an entry, so PRESERVE cancelled every
  protective order.
* **FLATTEN reaches the mode gate and stops there.** The halt has already
  forced RESEARCH, and the Alpaca adapter refuses everything while the kill
  switch is engaged. The report records each refusal. The positions keep
  their stops (PRESERVE behaviour), and the owner closes them at the broker.

No order path below the kill switch was opened for FLATTEN. Making it work in
production would need such a path, and that is an owner decision.

`tests/test_session_bypass.py` pins every call site in `src/` and `backend/`
that sends an order:

* `submit_order`, `submit_oco_exit`, `placeOrder`, `place_order`, `send_order`,
  `close_position`, `replace_order`, `liquidate`;
* in each gated path, the session or reduce-only check must come before the
  send.

A new call site fails the test until someone states how it passes the session
check. A provider failure, an engine exception, an invalid calendar and an
UNKNOWN day each refuse. `tests/test_send_time.py` plans in one state and
sends in another; the send-time state decides:

* OPEN → CLOSED
* OPEN → POST_MARKET
* OPEN → HALTED
* OPEN → UNKNOWN
* OPEN → holiday
* PRE_OPEN → OPEN
* a plan, a conditional plan or a held intent across a boundary
* an instrument that resolves differently at send
* a mode drop or kill switch between queue and send

`TradingPipeline.run` checks, in order:

1. **SESSION** (stage `session`). A closed or UNKNOWN market leaves an inert
   re-evaluation plan (`result.plan`) and sends nothing.
2. **DATA FRESH** (risk engine: account snapshot age, quote freshness, signal
   data age)
3. **PRICE / RISK** (risk engine)
4. **PORTFOLIO RISK** (stage `portfolio_risk`, `src/risk/pretrade`): exposure,
   concentration, currency, correlation, volatility, factors and liquidity. It
   may reduce the size and never enlarges it (`docs/PORTFOLIO_RISK.md`).
5. **ORDER** (validator, which re-checks the session per instrument)
6. **PERMISSION** (kill switch, `can_place_orders`)
7. **MODE** (RESEARCH is a dry run)
8. **SESSION, again, at the send boundary**

A market moving from CLOSED to OPEN triggers nothing by itself.

## Closed markets, the next open, and the open itself (`src/trading/session_orders.py`)

Pure functions with no broker, clock reads or model calls. A plan is a
decision about an order. Executing it would still go through every gate, and
nothing executes plans.

`plan_order(intent, session, policy)` turns a thesis into one of these:

* **EXECUTE_NOW**: the session is OPEN.
* **SCHEDULED**: re-evaluated `min_observation_minutes` (default 5) after the
  next open, never as a blind order at the opening print.
* **CONDITIONAL**: has a trigger, which fires only on an in-session price.
* **STANDING**: a GTC limit resting at the venue. Requires a patient strategy,
  a limit within 1 ATR of the reference price, and `allow_standing_orders`,
  which is **off by default**.
* **DEFERRED**: event-driven, UNKNOWN or HALTED.
* **REJECT**: the thesis expires before the reopen, or the order would open a
  position on an expiring contract.

`estimate_open(prev_close, gap_history, proxy)` gives an expected open with a
±1σ range from the instrument's own overnight gaps. It shifts only by a proxy
observed after the target's close and within 15 minutes (index futures, an
ADR's home market). `confidence` is the interval's nominal coverage, and
`coverage_in_sample` is how often history landed inside it. Neither has been
validated out of sample, and the estimate says so.

`adapt_at_open(plan, observation)` measures the opening gap in ATRs against
the trade:

| condition | action |
|---|---|
| fewer than `min_observation_minutes` observed | WAIT |
| adverse gap under 0.5 ATR | NORMAL |
| adverse gap 0.5–1.5 ATR | REDUCE (×0.5) |
| gap of 1.5 ATR or more either way, or an open through the stop | REASSESS |
| material news | SUSPEND |
| spread or liquidity out of bounds, or not observed | DEFER |
| risk breach | REJECT |

`/api/markets/plan` previews a plan. It is owner-only, and it stores and
executes nothing.

## How to add a venue

1. Add its hours to `venues.py`: IANA timezone and phases, with a `source`.
2. Choose its calendar, in this order:
   * map it in `providers.XCALS_MAP`, but **only** if `exchange_calendars`
     covers that exchange itself;
   * otherwise ship `data/calendars/<CODE>.json` built from the exchange's own
     circulars, with full provenance;
   * otherwise leave it UNKNOWN.
3. Add its identity suffix to `EXCHANGE_ALIASES`.
4. Add these cases to the tests: a normal day, a weekend, a holiday, an early
   close, DST, the bounds, and a point-in-time close. Take the dates and times
   from the provider's own data or the exchange's circular, never from memory.

## How to add a contract-specific futures calendar

1. Find the root's group in `FUTURES_CALENDARS`. If a product keeps its own
   holiday schedule, give it its own group; never widen one group to cover
   another.
2. From the exchange's holiday-hours notice for the year, write
   `data/calendars/<GROUP>.json` with `years`, `source` and `provenance`.
   State every US holiday and every eve, using:
   * `special_sessions` with the day's full phases, in the venue's timezone,
     for an abbreviated session;
   * `holidays` for a full closure;
   * `regular_days` where the notice confirms normal hours.
3. Add tests for the holiday, its eve, the next open across it, and a
   neighbouring group that must stay unaffected.

## Not done, by design or for now

* No CME/ICE contract calendars are shipped. US futures holidays and their
  eves read UNKNOWN.
* NSE before 2025 and from 2027; Shenzhen before 2026 and from 2027; BSE and
  Shanghai from 2027. All read UNKNOWN until a sourced calendar covers them.
* BSE's Muhurat and Budget-Sunday sessions are missing from the library.
  They read HOLIDAY or CLOSED, never OPEN.
* Scheduled plans are not stored or executed automatically. Wiring
  `SCHEDULED` / `CONDITIONAL` plans into the pipeline at the open would be a
  new autonomous execution path, so it is left for an explicit owner decision.
* The desk's daily budget still rolls on the New York date
  (`src/desk/day_state.py`). This is accounting, not a session decision. The
  desk trades US equities and crypto only.
* Weekday counts used as rulers rather than calendars are left as they are:
  playbook expiry in `reflex._expiry`, the exit engine's time stop in
  `position_manager.trading_age_days`, and the batch scheduler's
  `weekdays_only`. None of them decides whether a market is open.
