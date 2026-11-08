# Quantitative validation

Reused research/backtest engines remain in `src/backtesting/`, `src/v5/walkforward.py`,
`src/intelligence/walkforward.py`, `src/evolution/` and trading scorecard/lifecycle.
This branch does not promote or retune an existing strategy.

Added `src/backtesting/experiments.py`: exact dataset hashes, versioned experiment
manifests (revision, strategy/version, parameter/cost JSON, seed, assumptions),
chronological overlap-purged splits with embargo, explicit net P/L after spread,
commission/slippage/financing and seeded moving-block resampling. Missing ruin
model returns no invented risk-of-ruin probability.

Added research definitions for ten families; MacroDriven explicitly abstains
because no verified PIT macro event model is configured. The other deterministic
triggers are educational research recipes, not optimised or validated strategies.
Regime suitability, completed candles, warm-up and identity/basis checks apply.
Counterevidence includes stretched RSI and alternative scenarios; independent
quantitative/provider verification remains required before any account action.

## Legacy backtester corrections (review QA-01..QA-03)

`src/backtesting/backtester.py` (the legacy signal backtester, off by default in
the runtime) now runs each bar in time order: at the open it acts on what was
decided at the previous close (a pending drawdown-breaker exit, a stop the open
gapped through, a signal flip), then entries; inside the bar a stop touched by the
low (long) or high (short) fills at the stop price; at the close it marks to market
and the breaker decides, acting at the **next** bar's open. Before (`7385130`) the
breaker read bar *i*'s close and exited at bar *i*'s earlier open (probe: exit at
100 after a close of 70, final equity 100,000 instead of 70,000), and stops were
close-only (a −10% low with a flat close never stopped out). Without High/Low
columns, stops can only see the open and close, and the result's `warning` says so.

`metrics.py` annualises by the bar size and the instrument calendar: 252
exchange days, 260 FX days, 365 crypto days, times bars per session day for
intraday bars (6.5 h exchange, 24 h FX and crypto), 52 for weekly and 12 for
monthly bars. `run_backtest` takes the calendar from the ticker (`=X` FX,
`-USD`-style crypto, else exchange) unless one is passed, and reports
`calendar` and `periods_per_year` with its metrics. An unknown frequency or
calendar leaves CAGR, Sharpe and Sortino as `None`, never a 252-day guess. CAGR
counts return periods (`len − 1`), so 365 daily crypto returns are one year.

## Path-level prop-firm replay (review QA-04)

`src/backtesting/challenge_replay.py` replays equity through a rule profile's
loss limits, using the same floors as `rule_profiles.evaluate_profile`: daily =
balance at the profile's reset (00:00 Europe/Prague for FTMO) minus the daily
allowance of initial size; overall = initial size (STATIC) or the highest
day-start balance so far (TRAILING_EOD_BALANCE) minus the overall allowance; a
breach is equity at or below a floor. `replay_path` takes dated balance/equity
observations; `replay_days` takes undated days (intraday equity P/L relative to
the day's opening balance, plus the day's balance change). A day that dips 5.1%
intraday but closes −3% is a daily breach.

`breach_probabilities` resamples whole historical days in moving blocks, replays
each synthetic path to a horizon, and reports P(daily breach), P(overall
breach), P(balance target reached) and P(still open), each with a Wilson 95%
interval. Those intervals cover the simulation's sampling error only — not
whether the historical days represent the future. "Target reached" is the
balance target alone; minimum trading days and consistency remain
`evaluate_objectives`' job. Status: UNVERIFIED research tooling.

The target needs explicit evidence that the account is flat (review A1/A2,
2026-11-08): a dated observation's `open_positions` of zero, or a sampled day's
`close_equity_pnl` together with `open_positions_at_close` of zero. Equity equal
to balance is not evidence, since open positions can net to zero. Missing
evidence never reaches the target; zero positions with equity different from
balance is refused as contradictory. A sampled day's stated closing equity and
its closing balance are both checked against the floors. After any sampled
close not shown flat, the target is refused for the rest of that path, because
the next resampled day knows nothing of the open position;
`breach_probabilities` reports how many input days are not shown flat.

Coverage limit (review A3, not addressed): both replays see only the equity
points supplied. A dip between two observations that recovers before the next
is invisible, so sparse observations understate breaches. Each day's worst
equity should be among the observations.

No new full contract-specific fill simulator, stop-gap/partial-fill model,
survivorship-safe universe or corporate-action replay was built. Existing indicator
and replay tests are retained, but these additions have **UNVERIFIED** validation
status. A definition or unit test cannot create OUT_OF_SAMPLE_VALIDATED,
PAPER_TESTED or BROKER_DEMO_TESTED evidence. No return target was fitted.

## Additional review corrections (2026-11-07)

Opening prices are mandatory and finite for every execution bar; absent/NaN
opens raise ValueError. Substituting that bar's close would allow an earlier low
to stop a position before it existed. Missing High/Low rows are disclosed, as
are missing range columns. Intraday trade records preserve ISO timestamps.
The equity curve includes starting capital and final closing costs.

A 21-hour crypto cadence annualizes as 365*24/21. The former 20–28 hour daily
heuristic is removed. Frequency inference remains a research assumption, not
proof that a provider supplied a complete calendar-aligned series.

Dated replay requires an exact reset observation on every subsequent day.
That snapshot's balance determines the new floor and trailing high-water.
Sparse paths without a reset balance are refused rather than carrying an
unproven prior balance across midnight. The first day still assumes a fresh
account with initial capital; intraday observations cannot rule out excursions
between samples. Bootstrap outputs remain UNVERIFIED research estimates.

## Research evidence and conflicting hypotheses

Caller classifications cannot grant source authority. External claims enter
support only after the configured server resolver fetched an approved HTTPS
source and retained a content hash, fetch timestamp and matching claim. This
confirms source attribution, not the truth of the source's statement. No API
route configures that network resolver. Unresolved client evidence is labelled
UNVERIFIED and returned separately. Sources fetched after research as-of are
excluded from verified support. Analytical text never grants execution authority.

Opposing family triggers on one completed bar force ABSTAIN and are returned as
counterevidence. SupportResistance requires a close inside support/resistance;
a close below support is a break. The original sinusoidal support-break fixture
now abstains across SupportResistance, Breakout and MeanReversion.

Cadence completeness: irregular or duplicate return timestamps now leave
annualised metrics unavailable. Continuous crypto daily grids and weekday
exchange/FX daily grids must match their expected frequency; fixed intraday grids
must have consistent intervals. Holiday-adjusted or session-gap intraday datasets
need a calendar-aware provider adapter instead of median-spacing inference.
