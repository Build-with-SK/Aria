# Walk-forward findings — what the 41 modules actually do

Written 2026-08-08. Regenerate the underlying numbers with
`python scripts/run_walkforward.py`; compare two runs with
`python scripts/compare_walkforward.py before.json after.json`.

## The short version

Almost nothing in this engine has demonstrable skill on the sample available,
and until tonight the harness would have told you otherwise.

That is the finding. The rest of this document is how it was established and
what was fixed along the way.

---

## 1. Thirteen modules had never expressed a single opinion

The first walk-forward run graded 41 modules across 8 tickers. Thirteen
produced zero gradable calls in 240 evaluations each. From the outside they
looked like modules with no view. They divide into four groups, and only one
group was a bug:

| Group | Modules | Verdict |
|---|---|---|
| Genuinely dead | `alternative_data`, `reinforcement_learning` | Honest. No dataset; no deployed policy. They will never contribute until someone builds the thing they depend on. |
| Correct silence | `event_detection` | It detects events. Most days have no event. Abstaining is the right answer. |
| Self-healing | `self_bias_audit` | Needs logged predictions; there are 5. Fixes itself as the loop runs. |
| **Mechanically silenced** | `momentum`, `market_regime`, `volatility`, `clustering` | **A bug.** See below. |
| Never confident | `factor_exposure`, `liquidity`, `options_surface`, `news_nlp` | They report, but always inside ±10 net, so they never take a side. Honest, but they contribute nothing directional. |

And one that deserves singling out: `seasonality` asks for 10 years and gets
10 Augusts. It can never reach a 20-observation threshold for a monthly effect.
Its silence is a *previous* fix working — an earlier version shipped +20.9 net
from ~10 real Junes dressed up as 210 daily observations.

## 2. The mechanical silencing

`hit_rate_probability` requires `min_n = 20` independent observations.
`conditional_hit_rate` correctly discounts overlapping forward windows:
`n_eff = n_raw / horizon`. So a conditional base-rate module's sample is

    bars x P(state) / horizon

For a tercile state at the 21-day house horizon:

    5 years  ~ 1256 x 0.33 / 21  ~  20     <- exactly ON the threshold
    10 years ~ 2513 x 0.33 / 21  ~  39     <- comfortably above

Four modules asked for `period="5y"`. They sat exactly on the bar and fell
under it whenever their state was slightly rarer than a third — permanently,
silently, on every evaluation.

The data was already in memory: `marketdata.FULL_PERIOD` fetches and caches ten
years. These modules were discarding half of what had already been paid for.
The fix is one shared constant, `_util.BASE_RATE_LOOKBACK`, overridable via
`ARIA_BASE_RATE_LOOKBACK` so both arms of the experiment below could run under
identical harness code.

Widening the universe from 8 to 40 tickers did **not** fix these — the
threshold is per-ticker. Only the lookback did.

## 3. The A/B, and what it showed

40 tickers across sectors and two index proxies, 6 folds, 10 evaluation dates,
identical harness, only the lookback differing.

| module | calls 5y | calls 10y | hit 5y | hit 10y | edge 5y | edge 10y | IC 5y | IC 10y |
|---|---|---|---|---|---|---|---|---|
| clustering | 20 | 599 | 85.0% | 63.4% | +17.6% | +3.3% | +0.174 | +0.045 |
| momentum | 0 | 1200 | — | 61.3% | — | +0.8% | — | +0.043 |
| market_regime | 0 | 1039 | — | 60.1% | — | −0.3% | — | +0.029 |
| volatility | 0 | 1108 | — | 59.8% | — | −1.6% | — | +0.009 |

**Nothing here is statistically significant, in either arm.**

Read `clustering` carefully, because it is the whole lesson: an 85% hit rate
and a 17.6% "edge" on 20 calls — 14 after discounting — collapses to +3.3% once
the sample is real. Had the harness reported only the point estimate, that row
would have looked like the best module in the system.

**Conclusion: the change buys coverage, not skill.** Three modules went from
permanently mute to expressing ~1,000 opinions each, which is worth having
because their silence was an artefact. Whether those opinions are any good is
unproven, and on this evidence they are not obviously good.

## 4. Two bugs found in the measuring instrument itself

Both were in code written *this session*, which is the argument for building the
comparison harness before trusting any number out of it.

**The cross-sectional discount was missing entirely.** Forty tickers evaluated
on the same date are forty observations of one market day. `_util` already
refuses to count overlapping windows as independent; the walk-forward harness
was making exactly that error across the cross-section. `effective_sample_size`
now decomposes outcome variance into within-date and total, and interpolates the
sample between its two honest extremes — when every name on a date tells the
same story, the *date* is the observation. Measured rho on 21-day forward
returns is ~0.21–0.40.

**The significance test used a raw success count with a discounted sample
size.** `hits/n_eff` reads 624 hits in 1,039 calls discounted to 824 as a 76%
hit rate instead of 60%, and returned `p = 0.0` for every module — including one
whose edge was *negative*. Every "significant" flag in the first run of that
code was wrong. Successes are now scaled with `n_eff`, exactly as
`conditional_hit_rate` does. After the fix, nothing in the A/B is significant,
which is the correct answer.

## 5. Language and field names that were overclaiming

- `_verdict` called anything past a 3-point edge "positive" or "NEGATIVE" on the
  raw call count. It now requires significance before either word, and quotes
  the discounted sample: *"not distinguishable from the base rate … only ~4
  independent after discounting for names that move together. Do not act on
  this number."*
- `walk_forward_validated` means **measured**, not **works**. A module with
  1,200 calls and no edge is "validated". `walk_forward_skill_demonstrated` is
  now a separate field, and it is separate because the answer is usually no.

## 5a. The full-registry result: twelve significant modules, all negative

40 tickers, 6 folds, 8 dates, corrected harness. 31 of 41 modules measured;
**12 cleared significance, and every one of them is negative.** No module in
this system has a statistically significant *positive* edge over the base rate.

Benjamini-Hochberg across the family of 41 tests **changed nothing**: only
`crowding` (0.001 → 0.0020) and `currency_strength` (0.001 → 0.0035) moved at
all, and both stayed under 0.05. The correction is still the right thing to
apply — it would have mattered had the results been marginal, and its absence
was a real defect — but it should not be presented as having rescued the
analysis. It did not.

| module | calls | n_eff | edge | adj. p | IC |
|---|---|---|---|---|---|
| carry | 716 | 626 | −31.8% | 0.0000 | −0.215 |
| commodity_cycle | 545 | 486 | −28.6% | 0.0000 | −0.064 |
| value | 1056 | 983 | −28.2% | 0.0000 | −0.226 |
| yield_curve | 183 | 142 | −27.2% | 0.0000 | −0.059 |
| quality | 800 | 619 | −18.6% | 0.0000 | **+0.188** |
| global_macro | 432 | 390 | −15.9% | 0.0000 | −0.001 |
| breadth | 664 | 513 | −12.4% | 0.0000 | −0.176 |
| crowding | 321 | 243 | −10.7% | 0.0020 | −0.008 |
| credit | 1244 | 1093 | −7.7% | 0.0000 | −0.036 |
| growth | 864 | 830 | −7.5% | 0.0000 | **+0.150** |
| currency_strength | 585 | 486 | −7.0% | 0.0035 | **+0.096** |
| kalman | 1346 | 1010 | −6.9% | 0.0000 | −0.062 |

**These numbers are not stable across runs.** Between two runs a day apart,
`yield_curve` moved from 119 calls at −18.5% to 183 at −27.2%, and `value`'s IC
from −0.254 to −0.226, purely from which symbols the vendors served that day
(yfinance began rate-limiting mid-sweep). Treat one decimal place as noise, and
treat the direction and rough magnitude as the finding.

That sentence is true and, on its own, misleading. Hit-rate-versus-base-rate
punishes anything that is not permanently long over a decade that rose. The
information coefficient does not, and splitting on it separates the twelve into
two groups that deserve opposite conclusions:

**Group A — genuinely anti-predictive.** Significant negative IC: the score
ranks outcomes *inversely*.

| module | edge | IC | bull calls | bull hit | bear calls | bear hit |
|---|---|---|---|---|---|---|
| carry | −31.8% | −0.215 | 35 | 28.6% | 681 | 30.0% |
| value | −29.0% | −0.254 | 528 | 60.6% | 480 | 20.0% |
| breadth | −12.4% | −0.177 | 469 | 44.4% | 194 | 34.5% |

`carry` is wrong in *both* directions — 28.6% on bull calls and 30.0% on bear
calls against a 61.6% base. That is not a bull-market artefact; that is a module
whose signal points the wrong way on this sample.

**Group B — real information, miscalibrated threshold.** Significant *positive*
IC alongside a negative edge:

| module | edge | IC | bull hit vs base | bear hit vs base |
|---|---|---|---|---|
| quality | −18.7% | **+0.192** | 80.4% vs 78.5% | 21.4% vs 21.5% |
| growth | −7.5% | **+0.150** | 73.1% vs 67.9% | 40.5% vs 32.1% |
| currency_strength | −7.0% | **+0.095** | 64.8% vs 62.2% | 48.7% vs 37.8% |

These modules rank outcomes correctly. Their bull calls beat the base rate.
Their aggregate looks terrible because they also emit bear calls that land near
chance in a sample that mostly rose, and the aggregate hit rate averages the
two. The headline metric is doing them an injustice.

**Nothing was changed on the strength of this.** Flipping a sign or moving a
threshold because of one decade of forty US large caps is exactly the
curve-fitting the weight-fitting harness refuses to do, and the sample here is
smaller than it looks — see the effective counts. This is recorded as a finding
to investigate, not a mandate to edit.

## 6. The operational blocker, which outranks all of the above

The research loop is scheduled inside the desk daemon, which starts with the
backend. **The backend is not running, and no scheduled task exists to start
it.** Checked on 2026-08-08:

    :8000            not responding
    Get-ScheduledTask *ARIA*   no task registered

So the schedule added this session — predict daily at 22:10, resolve every six
hours — has never fired and will not fire. Predictions accumulate only when
somebody runs the loop by hand, which is precisely the state that made "no
track record" look like a matter of waiting when it was actually a matter of
nothing running.

`AUTORUN_ARIA.bat` exists for this. Until it runs at boot (or the backend is
otherwise kept up), the calibration counter stays where it is no matter how
much time passes. This is a five-minute fix and it gates everything else.

Progress toward the first reportable number, as of tonight:

| | |
|---|---|
| Predictions logged | 15 |
| Of those, directional (the only kind that count) | 6 |
| Resolved | 0 |
| Needed before calibration reports | 20 resolved directional |
| Watchlist | 30 names (was 10) |
| Earliest possible first calibration | ~5 weeks, *if the loop runs daily* |

## 7. What is still unresolved

- **The bear-heavy modules.** `carry` (148 bear calls, 0 bull), `commodity_cycle`,
  `value` and others score terribly on hit-rate-versus-base-rate over a sample
  that rose 60–80% of the time. Their ICs are also negative, which is more
  interesting than the hit rate — but on a discounted sample none of it is
  established. Do not "fix" a sign convention on this evidence.
- **One decade, one market.** Every number here comes from US large caps over
  roughly 2016–2026. It does not transfer to other geographies, small caps, or a
  decade that falls.
- **Rule-based, not fitted.** "Walk-forward" here means evaluated out-of-sample
  across time with the data layer pinned to each evaluation date. It is not
  trained-here-tested-there, and the rules were written by someone who had lived
  through this period — which no amount of replay can undo.
