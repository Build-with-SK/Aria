# Backtests, walk-forward and multiple testing — what the code actually does

Last revised 2026-10-06.

## Engines

| engine | where | costs | selection? | multiple-testing charge |
|---|---|---|---|---|
| signal backtester | `src/backtesting/backtester.py` | 10 bp commission + 5 bp slippage per side; entry/exit at next open, stop at close | no — one rule per asset | n/a |
| quant lab | `src/brain/quant_lab.py` | 10 bp per side on turnover | **yes** — library ranked by OOS Sharpe | deflated Sharpe over the whole library (`deflate_library`) |
| evolution lab | `src/evolution/` | net returns with costs | **yes** — ~1,700 bred trials | deflated Sharpe over every trial, deduped by genome |
| ML walk-forward | `src/models/walk_forward.py` | — | model fit | purged + embargoed splits |
| V5 walk-forward | `src/v5/walkforward.py` | — | module validation | point-in-time replay via `marketdata.as_of` |

`main.py`'s backtest step imports `Backtester` and `compute_metrics`, neither of
which exists, so it fails, is caught, and logs "Backtester failed". That is
why `data/backtest_results.json` has not moved since 2026-05-31 (its only other
writer is `main_phase4_backup.py`). Left as found and flagged: regenerating
legacy outputs was not in scope.

## Look-ahead: tested, not asserted

`tests/test_no_lookahead.py` rewrites every bar after a cutoff and requires
everything computed for the past to stay identical. It covers indicator
features, the signal backtester's equity curve, all five quant-lab and
evolution position templates, the walk-forward splits (a training label at row
*i* uses close *i + horizon*, which must fall before the first test row), and
point-in-time replay. Training labels are the only forward-looking columns, and
they see exactly `horizon` bars.

## The deflated Sharpe ratio — a unit correction

`src/evolution/statistics.deflated_sharpe` takes **annualised** Sharpes and a
count of return **periods**. Bailey & López de Prado's statistic pairs the
**per-period** Sharpe with √(T−1). Until 2026-10-06 the annualised figures
went straight into the formula. With daily data that inflated z about tenfold,
so the "probability" saturated at 0 or 1. A holdout Sharpe a hair above the
luck bar scored about 0.998 (the gate is 0.95) where the correct figure is
about 0.6.

Effect on recorded results (`data/evolution/campaigns.jsonl`, 12 campaigns):

* Ten campaigns reported **zero survivors**, and every finalist was *below* its
  luck bar. That fails under either formula, so those conclusions stand. Their
  stored probabilities are too extreme towards 0.
* Two campaigns reported **19 survivors** (9 + 10, Europe and Japan), the
  figures already withdrawn as artefacts of the cash-parking bug (commit
  5f512f0). Re-scored from their stored Sharpe, bar and T with the corrected
  formula (moments assumed normal, because skew and kurtosis were not stored),
  every one scores **0.81–0.87**: below the 0.95 gate. The unit error, not only
  cash parking, is what let them through.
* Stored probabilities are left as recorded; this note is the correction. Known-answer test:
annualised SR 1.0 over 252 daily returns, one trial → PSR ≈ 0.84.

## Ledger models: walk-forward, holdout and the charge for searching

Models evaluated from the prediction ledger (desk, technical, V5, ML) use
`src/intelligence/walkforward.py`: anchored, expanding windows in creation
order, never shuffled, PURGED on the label — a training call is used only if
its outcome was knowable (`resolve_after` passed) before the first validation
call was made, and likewise validation before test. Every fit or selection
appends a trial (model, purpose, hyperparameters, windows, n, score) to
`data/intelligence/trials.jsonl`; the count is what a multiple-testing charge
reads.

Selection among many is charged:

* per-model slices (horizon, asset class, version, regime) are re-read after a
  Holm correction across every slice of that model;
* the promotion gate's holdout test and the router's slice tests are Holm-
  corrected across every model / slice examined;
* rates use a cluster-robust interval (one cluster per information date) with
  Student's t on (clusters - 1) degrees of freedom, because thirty calls built
  on one session's bars are closer to one observation than to thirty.

Strategy-level selection keeps the deflated Sharpe below, unchanged.

## ML training labels (fixed 2026-10-08)

`src/models/targets.direction_label` / `binary_direction` and
`ml_model._prepare_features` labelled the last `horizon` rows — whose future
close does not exist — as 0 ("not up"): `NaN > x` is False. The trainer's
`y.notna()` filter, written for exactly those rows, kept them, so every model
was trained to say "not up" on the most recent conditions it had seen. Unknown
labels are now NaN and dropped; `triple_barrier_label` likewise leaves an
unfinished window NaN instead of "touched neither barrier". Models trained
before this date carry the bias; nothing was retrained here.

## Quant lab

`deflate_library` charges every strategy for the whole library. On the
2026-10-06 library (25 strategies on an NSE basket) the best out-of-sample
Sharpe is 0.46 against a luck bar of 1.30: DSR probability 0.20. **Nothing in
the library is distinguishable from luck.** Quant-lab Sharpe is raw, not
excess-return. On an INR basket with a ~6.5% risk-free rate that overstates
every figure. The evolution lab subtracts 2%.

## Survivorship

Every panel (`src/evolution/universe.py`, the quant-lab basket) is today's
index membership run backwards. That inflates long-only results. A *failure* to
clear the bar is therefore trustworthy; a success would not be.
