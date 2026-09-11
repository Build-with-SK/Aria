# ARIA — completion pass, 11 September 2026

**Status: PARTIAL.** Four defects found and fixed, three of them safety-relevant,
one of them live in the execution path against a real paper broker. The system
was in far better shape than a clearance pass usually finds, so most of this
document is about what was *verified* rather than what was repaired. The scope
I did not attempt is listed at the end, plainly, rather than folded into a
claim of completeness.

## Where this started

The working tree was clean apart from regenerated market data. There was no
half-finished ARIA work to rescue and nothing to preserve from destruction —
the premise that substantial uncommitted work was at risk did not hold. The
baseline test suite was **1568 passed, 0 failed, 0 skipped**, which is not a
codebase in need of a rescue.

So the useful work was not "fix what is broken" but "find what is claimed and
not true, and what is missing and load-bearing". Four things came out of that.

## Bugs found and fixed

### 1. Every Alpaca order status was misread — found at runtime, not by reading

This is the one that mattered, and it was only findable by placing a real
order. `alpaca-py` returns an enum whose `str()` is `"OrderStatus.FILLED"`,
not `"filled"`. The adapter did `_parse_status(str(order.status))` against a
table keyed on the lowercase wire words, so **every lookup missed and fell
through to the `SUBMITTED` default** — in both `submit_order` and
`get_order_status`.

The visible symptom was cosmetic: a filled order reporting `SUBMITTED` with
`filled_qty=1`. The consequence was not. `OrderManager._await_fill()` polls
for `FILLED` before placing the protective stop-loss and take-profit, and that
condition could never become true. A filled position therefore sat **naked**
until the poll timed out, with no stop and no target. In the other direction,
a rejected order read as one still working.

Fixed in `src/execution/alpaca_broker.py` by normalising the token from the
enum, its `.value`, or either string spelling, and by letting the fill counter
break ties the way the IBKR adapter already did — a cancel or rejection after
a part-fill is `PARTIAL`, not "you own nothing". Alpaca's full pre-fill
vocabulary is now enumerated so the unknown-status warning stays meaningful;
`pending_new` turned up on a real paper sell during this work.

Verified against the same live order that exposed it: re-read after the fix,
it reports `FILLED / 1.0 / 765.48`.

### 2. No kill switch existed

Nothing in the repository stopped orders. `auto_execute` in the desk config
was described in a docstring as "a kill switch", but it only gates the
autonomous hunt loop — it does not stop a manually approved trade, the exit
engine, the reflex lane, or bracket healing, and there was no single control
that did.

`src/execution/kill_switch.py` is new. It is deliberately the dumbest thing in
the stack: a file on disk plus `ARIA_KILL_SWITCH`, with no dependency on the
desk, the brain or any broker, because the moment you want a kill switch is
the moment you have stopped trusting those. The check sits inside each broker
adapter's `submit_order`, *below* every other gate, so a future code path that
reaches a broker is covered without having to know this file exists.

Engaged, it refuses every new order submission. Reads and cancellations still
work on purpose — "stop trading" must not also mean "and leave the resting
brackets you can no longer manage". It fails closed: an unreadable or
malformed state file engages the switch. An env-engaged switch cannot be
released through the API, so ARIA cannot talk its way out of a halt imposed
from outside the process.

### 3. An unsupported asset class was silently coerced to equity

In `PositionManager._close()`, the exit path built its order as:

```python
asset_class=AssetClass(pos.get("asset_class", "equity")
                       if pos.get("asset_class") in ("equity", "crypto")
                       else "equity")
```

An option or futures position in the book would have been market-sold with
`qty` read as **shares rather than contracts** — a contract-multiplier error,
in the direction of a position nobody intended. It is exactly the
`unknown → known` substitution the safety contract forbids, and it was sitting
in the one code path that runs unattended every five minutes.

Now refused and routed to the existing manual-approval escape hatch, which
already existed one branch above for live accounts. The desk only ever opens
equity and crypto, so reaching that branch means the book holds something that
did not come from here — which is precisely when a human should look.

### 4. Infinite equity passed a positivity check

Found by my own test, in code I had just written: `sized_risk_amount()`
guarded with `if not (eq > 0)`, and `inf > 0` is `True`. An infinite risk
budget is the same class of failure as a NaN cap — which this codebase had
already been bitten by and documented in `src/desk/config.py`. Fixed with an
explicit `math.isfinite` check. Worth recording because it is the failure mode
the project already knows about, reappearing in new code.

## What was built

**Risk tolerance (`src/risk/profiles.py`)** — `CONSERVATIVE` / `MODERATE` /
`AGGRESSIVE`, selected by `risk_profile` in `data/desk_config.json`. These move
real numbers that the risk officer and position sizer already enforce in code:
risk per trade, position and sector caps, portfolio heat, drawdown halt, trade
count, conviction bar, gross exposure. The table is in the README.

The design point is the clamp. Every profile passes through `HARD_LIMITS`
before it is returned, so a profile may sit below the platform ceiling and
never above it — edit `AGGRESSIVE` to allow a 40% position and `clamp()` still
returns 10%, the tests still pass, and the desk still refuses. An unknown
profile name falls back to `MODERATE`, never to the most permissive one. No
profile permits margin. `EXECUTABLE_ASSET_CLASSES` lives here and is now the
single place that says what can actually be traded.

**API** — five endpoints, all owner-gated, all additive:
`GET/POST /api/execute/kill-switch{,/engage,/release}`, `GET /api/risk/profiles`,
`GET /api/risk/profile`. The switch does not depend on these being reachable;
adapters read it from disk, so a dead API cannot re-enable trading.

## Runtime verification — what actually happened

A live Alpaca **paper** account was connected throughout ($10,008.34).

1. Server started, `/api/health` returned 401 to an unauthenticated caller —
   the owner guard working.
2. Kill switch over HTTP: clear → engaged → status reflects it. An
   unauthenticated engage attempt got 401.
3. **With the switch engaged**, a real order to a genuinely connected, willing
   paper broker: `REJECTED`, empty order id, `"KILL SWITCH ENGAGED (file):
   runtime verification"`. The client object was never touched — nothing left
   the machine.
4. Switch released. **BUY 1 SPY** → filled at **765.48**. Position confirmed
   held at the broker.
5. That fill is what exposed bug #1, which was then fixed and re-verified
   against the same order id.
6. **SELL 1 SPY** → filled at **765.56**. Book returned flat, account
   $10,008.34 → $10,008.42.

That is the full chain — order creation, submission, fill, status
reconciliation, position update — exercised against a real broker rather than
simulated. The kill switch was proven by a refusal, not by a unit test alone.

## Tests

| | before | after |
|---|---|---|
| passed | 1568 | **1646** |
| failed | 0 | **0** |
| skipped | 0 | **0** |

78 new tests, no regressions, nothing weakened, skipped or xfailed. The new
tests are written pessimistically — each asserts that something did **not**
reach a broker. They cover the switch's file and env halves, corrupt-state
fail-closed behaviour, both broker adapters refusing while engaged, profile
monotonicity, the clamp against a deliberately reckless profile, and the exit
path both refusing an option and still executing a normal crypto exit (so the
guard is not just welded shut).

Frontend: `npm run build` succeeds in 14s, no errors.

## Market support

Honest status, in the README as a table. Short version: **US equities and
crypto spot are paper-executable via Alpaca. Nothing else is.** Options,
futures and commodities have real analysis modules and no execution route
whatsoever — nothing sizes or routes a contract. UK equities are fully modelled
on the data side, including the GBp/GBP minor-unit trap, but have no execution
route: Alpaca is US-only and the IBKR adapter hardcodes `Stock(ticker, "SMART",
"USD")`, which would route an LSE name against the wrong currency.

**LIVE TRADING: NOT ENABLED — requires broker authorization.** No customer
money is held, custodied or represented anywhere. Every balance in the system
is an Alpaca paper balance.

## SENTINEL

Untouched, and verified as still independent: it cannot execute, cannot veto,
and silence is still not agreement. No changes were made to the bridge.

## What I did not do

The brief asked for considerably more than this. Rather than imply coverage I
did not achieve:

- **No canonical instrument model extension.** `src/core/identity.py` is strong
  for equities — symbol, provider symbol, exchange, effective currency with
  minor units. It has no `multiplier`, `expiry`, `strike`, `option_type`,
  `underlying`, `tick_size` or `lot_size`. Adding those is the right next
  piece of work, but it is only *needed* once derivatives are executable, and
  the fail-closed gate in fix #3 is what makes it safe to not have them yet.
- **No multi-tenant user isolation.** ARIA is single-owner today; `require_owner`
  is a binary gate, not a tenancy boundary. Building `UserAccount` /
  `Portfolio A vs B` scaffolding would have produced interfaces with no
  implementation behind them, which the brief explicitly warned against.
- **No LSE execution fix.** Documented as a known limitation instead, because
  it requires threading canonical identity through the IBKR contract builder
  and I could not verify it without live IBKR credentials.
- **No broad security re-audit.** I read the auth guard closely enough to use
  it and found its design sound — ownership must be *proven*, not inferred
  from a loopback address. I did not systematically re-audit SSRF, path
  traversal or deserialization; existing tests cover several of those and pass.
- **No frontend UI for the kill switch.** The API is there and truthful; the
  deck does not yet surface it. Wiring a button is small and safe, but it is
  unverified work and I am not going to describe it as done.

## Commits

One commit on `fix/signal-breadth-discount`. `data/macro_data.json` was left
uncommitted — it is regenerated pipeline output that predates this work and
does not belong in it.
