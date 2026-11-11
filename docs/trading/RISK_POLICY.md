# Research risk policy

`src/trading/multiasset_risk.py` is deterministic and imports no model/web layer.
Ordinary research cap: 0.5% of current equity. The 1% exceptional value is metadata
only; no exception approval or escalation path is implemented. Changes to source
policy invalidate the assessment hash. No API permits policy mutation.

Required: exact account identity/environment/currency; complete positions and
pending-order risk; fresh executable quote; verified linear specification; correctly
fresh FX pair quote when needed; confirmed profile and exact current
Prague reset snapshot; today's complete activity record; a complete, fresh book
of positions and orders; session evidence for the instrument; and the calendar
the profile's operational rules need. Unknown inputs return
UNVERIFIED. Kill/financial-limit breaches return EMERGENCY_HALT.

Per-volume stop risk = (absolute entry-stop distance + allowed entry tolerance)
× contract size × quote-to-account conversion. Conversion comes from a named
two-sided pair quote (`FXPairQuote`), never a caller-supplied rate: direction is
derived from the pair, losses convert at the side that makes them larger (ask
for GBPUSD-style multiply, bid for USDJPY-style divide), the provenance symbol
must name the pair, and a spread over 1% is refused (review CR-04). When the
instrument is itself the account/quote pair (USDJPY on a USD account), the stop
price is used too, since that is the rate at which the loss is realised (CR-13).
An inverted value under correct labels is caught by the broker cross-check.
Total risk includes spread,
commission, slippage and financing allowances in account currency. Volume is
floored to the broker step; minimum lot beyond budget is denied. Contract tick
value must agree with the linear model or a broker calculator is required.

Contract size is checked three ways (review CR-03). (1) Mandatory: the broker's
own one-order figures (MT5 `order_calc_profit` / `order_calc_margin`, as a
`BrokerCalculation`, fresh within 15 s) must match the engine's loss within 0.5%,
or the verdict is UNVERIFIED; the larger of the two margin figures is used. (2) A
registry of conventional sizes (`instrument_registry.py`: FX 100,000; gold 100 oz;
silver 5,000 or 1,000 oz) returns UNVERIFIED for anything else; instruments with
no convention rely on (1). (3) A per-asset-class lot cap per $10,000 of equity
(FX 1.0, metals 0.2, energy/agriculture 0.5, indices 2.0, equity/ETF CFDs 50,
crypto 0.1, other 0) denies sizes a mapping error could make look small.
Nonlinear settlement, inverse contracts and limit-fill modelling are unverified.

Internal daily stop: today's loss is the policy-local 00:00 balance minus current
equity (floating P/L included), never a separately reported P&L figure; at 2% of
equity the engine returns EMERGENCY_HALT (review CR-05). Floor buffer: new risk
must leave equity above the provider floor plus max(0.5% of initial capital, 3 ×
slippage allowance), and risk equal to the remaining budget is refused (CR-10).

Operational rules (review CR-08, CR-09; `operational_rules.py`) replace the old
caller flag `operational_constraints_verified`. Each profile rule is a closed
enum checked against evidence:

| Rule value | Evidence | Denies |
|---|---|---|
| `news_rule=NO_EXECUTION_2MIN_AROUND_NEWS` | `EconomicCalendar` covering the rule day | any release for the instrument's currencies within 2 minutes |
| `gap_rule=NO_ENTRY_2H_BEFORE_LONG_BREAK_OR_MAJOR_NEWS` | calendar and `TradingSession` with `next_opens_at` | entry within 2 h + 15 min of a break of 2 h or more; any HIGH/UNKNOWN release still ahead today |
| `weekend_rule=NO_HOLD_THROUGH_BREAKS_OVER_2H` | `TradingSession` | the same pre-break window; `flatten_deadline()` gives the time a future execution stage must be flat |
| `instrument_rule=ALLOWLIST` | `allowed_instruments` | any other instrument |

Calendars older than 6 h, sessions older than 1 h, a calendar that does not
cover the whole rule day, a session for another instrument, an UNKNOWN state or
an equity without an earnings calendar are UNVERIFIED. Rules set to NONE need no
evidence.

Daily limits: `DailyActivity` (positions opened, server requests including every
modification and cancellation, open orders, requests in the last minute) must be
for this account and this rule day, complete, and at most 15 s old. A trade is
refused when it would take positions or open orders past 80% of the profile's
daily limit, or server requests past 80% counting 3 requests per trade (send,
stop/target change, close). FTMO profiles may not set limits above FTMO's
published 2,000 positions, 2,000 requests and 200 open orders.

Other checks: aggregate/correlated/pending risk, position count,
request rate, available margin, leverage, spread, stop distance, exact price
ticks, duplicates, cooldown, abnormal markets and closed/unknown session. Sizing
never rounds upward. ALLOW means research checks passed, never broker authority.

Evidence, not assertions (review CR-11). `RiskInputs` holds evidence only; no
field states a result. `assess` itself:

- reads the legacy global kill switch (`src/execution/kill_switch.is_engaged()`,
  file or `ARIA_KILL_SWITCH`); engaged or unreadable is EMERGENCY_HALT;
- derives open and pending risk, notional, position count and duplicates from a
  `BookSnapshot` of every position and pending order (each with the broker's
  own loss to its stop; a position without a stop is UNVERIFIED). A position is
  "correlated" when it shares a currency with the instrument — conservative;
- prices the spread from the live quote and commission from the instrument
  specification's published round-turn figure (missing, or not in the account
  currency, is UNVERIFIED); stated slippage is raised to at least one spread;
- takes market state from session evidence, which is always required: CLOSED,
  HALTED or outside the session is DENY, UNKNOWN is UNVERIFIED;
- applies a 30-minute cooldown after the account's last losing close.

Financing remains a caller allowance that can only add risk. Broker-specific
margin, gap/liquidity shocks, currency concentration, loss-streak logic and
real-time book reconciliation still need dedicated adapters/testing before
execution. No component yet maps MT5 positions and orders into a `BookSnapshot`.

`rule_profiles.py` calculates equity floors and objective eligibility separately.
Failure to reach a profit/consistency objective never forces another trade.
`ReviewStore` persists consecutive exact-day balances **per account** (one row per
account per policy day, whichever profile version recorded it) and a high-water
mark equal to the highest balance ever recorded for the account. A re-confirmed
or re-versioned profile continues the same history; it cannot re-seed a lower
high-water or redefine the account's starting capital (review CR-01). History
must start at the original capital on the creation day. Missing days, cashflows
and resets are held for manual baseline verification. Schema-1 databases (keyed
by profile) are set aside unmigrated and must be rebuilt from broker evidence.
