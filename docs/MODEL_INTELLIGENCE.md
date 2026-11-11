# Model intelligence: which prediction to trust, and how much

Last revised 2026-10-08. Describes `src/intelligence/` and the changes it made
to the ledger, the desk, the ML path and the pre-trade gate. The measured
numbers are in `docs/baselines/model_intelligence_baseline.json` (aggregates
only), produced by `scripts/model_intelligence_baseline.py`.

The question this layer answers is not "how confident is ARIA" but:

> Which prediction should I trust, how much, under what regime, from which
> model — and should it influence the portfolio at all?

Today the honest answer is: **no model has earned trust, so none is routed,
and no prediction can enlarge a position.** The rest of this document is how
that answer is reached and what would change it.

## 1. Probability semantics (`semantics.py`)

| type | meaning | may enter Brier / log loss / ECE |
|---|---|---|
| `STATED_PROBABILITY` | the source asserted P(its direction is right). V5's `confidence` on bull/bear calls | yes |
| `CALIBRATED_PROBABILITY` | a stated probability mapped by a calibration fitted on earlier calls and validated on later ones; must carry `calibration_version` | yes |
| `SCORE` | orders outcomes, no demonstrated probability meaning: the ML ensemble's `predict_proba` (shifted ±0.03 by a hard-coded regime rule), V5's pre-review P(bull), the signal engine's `bullish_prob` | **no** |
| `CONFIDENCE` | a strength: the desk's 0–100 conviction (stored squashed onto 0.5–0.95 as `mapped`) | **no** |
| `NONE` | the source states no number (the technical composite is a vote count) | — |
| `UNKNOWN` | a number may exist but its meaning was never recorded (30 V5 rows of 2026-10-06) | **no** |

`brier_score` and `log_loss` raise `NotAProbability` for anything that is not
stated or calibrated: a confidence cannot silently enter a Brier score. The
ledger stores `probability_type` explicitly; an explicit type wins over the
legacy `probability_kind`; a "calibrated" probability without a
`calibration_version` is stored as `UNKNOWN` and never scored; a `SCORE` never
gets a `p_correct`. A structural test requires every production caller of
`record_prediction` to declare what its number is.

## 2. The prediction ledger and provenance (`provenance.py`)

New columns (additive migration, NULL = not recorded):
`probability_type, venue, market_session, feature_version, routing_decision,
calibration_version, reference_basis, model_hash`.

* `venue` / `market_session` are derived at write time from the session
  engine (`src/markets/sessions`); a failure leaves them NULL.
* `model_hash` is read out of `model_version` (`name|code=<12-hex>|…`).
* `routing_decision` and `calibration_version` are set by the ingest paths to
  what can be **proved**: no router existed before this layer
  (`DIRECT: recorded by its own source`), and no calibration has ever been
  deployed (`none`).
* `feature_version` is set only where the model's code hash covers the code
  that computed its features (`src/v5@<hash>`, `src/data/technical_summary.py@<hash>`).
* `reference_basis = next_close`: no reference price was recorded; the return
  runs from the close of the first bar strictly after the call's calendar day.

`provenance.completeness(row)` lists what a row is missing. Nothing is
back-filled; historical rows stay incomplete and are counted as such.

### New predictions that used to leave no trace

* **Desk debates** (`src/desk/debate.record_in_ledger`): every judge call is
  recorded at creation — once per (ticker, view, calendar day), as a
  `CONFIDENCE`, horizon 1, `next_close` (the judge read `signals.json`, whose
  prices can be a day old, so a recorded reference would credit it with moves
  it could have seen). Neutral views are not recorded. Every debate now also
  records `timings_ms`.
* **ML ensemble** (`src/intelligence/recorders.record_ml_predictions`, called
  from `main.run_ml`): each (ticker, horizon) call as a `SCORE`, inside the
  model's own dead zone (bull > 0.55, bear < 0.45), anchored on the last
  feature bar it saw. Forward only: the May 2026 file has no per-row times.
* The **signal engine** (`signals.json`) states no horizon, so it makes no
  falsifiable claim and is not recorded; the registry says so.

## 3. Grading, and the canonical evaluation dataset (`dataset.py`, `regrade.py`)

One dataset, derived from the ledger and never written back. For every model:
`graded = included + excluded`, and every exclusion has a reason code:

| reason | meaning | point-in-time? |
|---|---|---|
| `UNRESOLVABLE` | graded as undecidable (identity, no reference, price source disagreed) | |
| `CREATED_AFTER_RESOLUTION` | the grade predates the call | yes |
| `GRADED_BEFORE_HORIZON` | graded on or before its horizon date | yes |
| `HORIZON_INCONSISTENT` | `resolve_after` does not follow from the call and horizon | |
| `DATA_AFTER_CREATION` | `data_as_of` later than the call | yes |
| `MIXED_PRICE_BASIS` | raw recorded price -> adjusted close, on an instrument that can pay distributions | |
| `DUPLICATE_EVENT` | a repeat of an event already counted | |

`GET /api/intelligence/exclusions` lists them row by row.

**Two grading defects were found in the real ledger on 2026-10-08**, both in
the measurement, not the claim:

* **318 technical and 9 V5 grades were made ON the horizon date, before that
  date's close existed** — e.g. graded 11:01 London on 2026-08-25 "on the close
  of 2026-08-25". `_price_on` returned the previous session's close, so the
  call was graded over a shorter horizon than it claimed. (The grader was
  fixed on 2026-10-06; these rows were never re-measured.)
* Every grade before 2026-10-06 measured from a raw recorded price to an
  adjusted close, dropping distributions inside the window and every
  adjustment after it.

`regrade.py` re-measures such calls exactly as the corrected grader would —
identity first, the anchor session the call was built on, both ends from one
adjusted series, the horizon close only once it exists — as a **view** saved
in `data/intelligence/regrades.json`. The dataset uses the re-measurement in
place of the defective grade (flagged `grade_source = regraded_same_series`,
with `cured_defects`) and reports how many outcomes changed. A regrade never
replaces a valid grade and never cures a claim defect (`DATA_AFTER_CREATION`).
Two extensions, each stated on the row: a call made in the settle buffer
after the close (the technical snapshots ran at 16:30 New York) is anchored on
the close that had already printed; crypto (no distributions) keeps its
recorded reference and only has its horizon date corrected.

`as_of=T` makes the dataset point in time: only calls made by T, and only
grades made by T (later grades are pending).

## 4. Model evaluation (`evaluation.py`)

Per model: sample, hit rate (all calls), directional accuracy, neutral band
rate, comparison with **always-long** (exact McNemar on the calls where they
disagree), Brier / log loss / ECE (stated or calibrated probabilities only),
and slices by horizon, asset class, model version, macro regime and market
regime.

Floors: 30 graded calls **and** 5 distinct information dates for a rate; 30
probabilities for Brier / log loss; 50 for a calibration curve, 12 per bucket;
20 disagreements for McNemar. Below a floor the answer is `INSUFFICIENT_DATA`
with the n, never a number.

Calls made from the same session's data share every market-wide move, so a
rate carries two intervals — Wilson (calls independent) and cluster-robust
(one cluster per information date, Student's t on clusters − 1 degrees of
freedom) — and the verdict uses the wider. Slices are re-read after a Holm
correction across every slice of the model.

## 5. Calibration (`calibration.py`)

Strictly temporal 60/20/20 train / validation / test, purged on the label.
Method by training-window size, chosen before any fit: < 100 nothing; 100–999
Platt only; ≥ 1000 Platt or isotonic. Beta calibration is not implemented.
A map is a `CALIBRATION_CANDIDATE` only if it beats raw on the test window by
more than 1.96 paired standard errors — and it is still **not applied**:
deploying one is an owner decision that produces a `calibration_version`.

Statuses: `CALIBRATION_NOT_APPLICABLE` (the model states no probability),
`CALIBRATION_INSUFFICIENT_DATA`, `RAW_RETAINED`, `CALIBRATION_CANDIDATE`.

## 6. Model registry and promotion (`registry.py`)

| model | kind | number it states | ledger |
|---|---|---|---|
| `desk` | deterministic weighted analyst vote; LLM writes prose only | CONFIDENCE | yes |
| `technical` | indicator vote count | NONE | yes |
| `v5` | 41-module ensemble + meta-review | STATED_PROBABILITY | yes |
| `ml-ensemble` | per-ticker stacked classifiers | SCORE | from 2026-10-08 |
| `signal-engine` | composite score + heuristic bullish_prob | SCORE | no (no horizon) |

Each card: version in the record, code hash now, probability type,
calibration status, supported / observed asset classes, horizons and regimes,
feature set, training description (ML: checkpoint range, rows, in-sample CV),
sample, holdout window, and the promotion check criterion by criterion.

**Evidence alone can only say `INSUFFICIENT_DATA` or `EXPERIMENTAL`.**
`VALIDATED` and `PRODUCTION` are owner decisions
(`POST /api/intelligence/registry/{model}/status`, owner + step-up), appended
to `data/intelligence/model_status.jsonl`, and refused unless the promotion
gate passes at that moment; `PRODUCTION` requires `VALIDATED` first. `RETIRED`
and `EXPERIMENTAL` never need evidence. If the evidence later fails, the card
reports `owner_status_lapsed` and the router stops treating the model as
validated.

The promotion gate — every criterion must pass, and it takes no text input:

| criterion | requirement |
|---|---|
| sufficient sample | ≥ 200 directional graded calls on ≥ 20 information dates |
| temporal holdout | the most recent 30%, label-purged: better than chance on the cluster-robust interval AND better than always-long (McNemar), Holm-adjusted across the models examined |
| no PIT violation | no row excluded for a point-in-time reason; no regime label from after a call |
| stable performance | three chronological blocks, none worse than chance, first and last not significantly apart |
| known probability semantics | no UNKNOWN probability type in the model's ledger rows |
| reproducible model version | every evaluated call carries a code hash, and all the same one |

A model cannot become production because its hit rate rose, its backtest
Sharpe rose, or a language model said it was better.

## 7. Routing (`routing.py`)

No hand-set weights. A model is routable for a request only if it is
VALIDATED / PRODUCTION with a passing gate, covers the asset class, has a
measurable and better-than-chance-and-always-long record in exactly that
(asset class, horizon bucket[, regime]) slice, and that survives a Holm
correction across every (model, slice) tested. The best lower confidence bound
wins; nothing is averaged. Otherwise: `ROUTING_INSUFFICIENT_EVIDENCE`, and the
DEFAULT route — *the source that produced the idea keeps its existing role; no
model is up-weighted; reliability is UNKNOWN; nothing on this route may
increase a position.* POOR data short-circuits to
`ROUTING_DATA_QUALITY_ABSTAIN`.

`routing.table()` precomputes a decision per slice into the evidence snapshot,
so the order path does a dict lookup, not an evaluation.

## 8. Regimes (`regime.py`)

**Macro regime** (`src/macro/regime`, CPI / 10Y-2Y / VIX / unemployment
quadrant): stamped at creation from the snapshot then held, or backfilled only
from a world snapshot taken before the call. Point in time — and constant:
every labelled graded call is "Expansion (Goldilocks)". The quadrant is
monthly-slow and crossed no threshold in the record. Per-macro-regime
performance is not measurable, and is reported as such.

**Market regime** (new): trend (SMA50/SMA200: BULL / BEAR / SIDEWAYS) and
volatility (20-day realised vol against its trailing 252-day 80th / 20th
percentiles: HIGH / NORMAL / LOW), plus RISK_ON / RISK_OFF, on SPY, from only
the bars whose session had closed when the call was made. Every input is a
price ratio from one series, so a dividend adjustment made after the call
cannot change it. Under 200 known bars it is UNKNOWN.

Regime slices are reported only when two regimes each meet the rate floors,
and are Holm-corrected with the other slices.

## 9. Ensemble independence (`ensemble.py`)

For every pair of sources, aligned on (instrument, information date):
agreement and the phi coefficient of their directions (all calls), error
correlation and conditional accuracy (graded calls), and incremental
information — a likelihood-ratio test of logit(up) ~ A vs ~ A + B — only on a
common target (same horizon bucket). "Adds information" requires that test;
"redundant" requires agreement ≥ 85% and an insignificant test.

## 10. The debate desk (`debate_eval.py`)

From its code: the judge's view, conviction and verdict are a deterministic
weighted vote of the analysts. **The language model writes prose and decides
nothing** — a test feeds it adversarial text and the verdict does not move.
Its incremental predictive value is therefore zero by construction, and the
evaluation says so (`ZERO_BY_CONSTRUCTION`). What is measured is whether the
judge's aggregation beats its own technical input, on the same events (exact
McNemar on disagreements), graded on the first bar after each debate's day,
one event per (ticker, view, entry bar).

## 11. Prediction -> portfolio (`conviction.py`, `src/risk/pretrade.py`)

```
MARKET DATA -> FEATURES -> MODELS -> PREDICTIONS -> CALIBRATION -> ROUTING
  -> CONVICTION -> PORTFOLIO CONTEXT -> PORTFOLIO RISK -> SESSION -> EXECUTION
```

A conviction keeps every component (direction, edge, probability and type,
calibrated probability, calibration quality, reliability, disagreement, data
quality, regime fit, routing, and — from 2026-10-09 — `research_context`: how
the cached research thesis relates to it, AGREES / CONTRADICTS / NEUTRAL /
UNKNOWN, recorded and labelled but read by no verdict or size; see
docs/RESEARCH_ENGINE.md) and has no single score: there is no evidence to
weigh them against each other. `labels()` states what must not be assumed —
`UNCALIBRATED`, `NOT A PROBABILITY (CONFIDENCE)`, `RELIABILITY INSUFFICIENT
DATA`, `NO DEMONSTRATED EDGE`, `INPUTS DISAGREE`, `DATA POOR`,
`ROUTING_INSUFFICIENT_EVIDENCE`.

At the pre-trade gate it can only REJECT (the prediction does not support the
side) or DEFER (its data are POOR — e.g. a 1-day desk call older than 1.4
calendar days). `may_increase_size` is always False; a test proves a maximal
conviction leaves a REDUCE_SIZE decision exactly where it was. Exits are never
blocked. Building it is bounded and local: a debate transcript, the
`signals.json` age, and the saved evidence snapshot (rebuilt by the ledger loop
every six hours or when grades move) — no network, no model, no ledger scan.

## 12. Point in time (`pit.py`, tests/test_pit_adversarial.py)

`PointInTimeData` hands out each input only as known at T: bars once their
session closed (today's open session is future), FX and factor returns from
00:00 UTC the next day, fundamentals from their release time, news from its
publication time, macro readings from their release, and model outcomes only
if graded by T. `predict_at` is the technical composite plus its whole
context, hashed. The adversarial suite rewrites every input after T — price,
volume, today's unfinished bar, FX, fundamentals, news, factor returns,
regime, model performance — and requires an identical prediction; then
rewrites each before T and requires it to change.

## 13. Walk-forward and multiple testing (`walkforward.py`, `multiple_testing.py`)

See docs/EVALUATION.md: anchored expanding windows, never shuffled, purged on
the label; every trial recorded; Holm for promotion and routing, BH for
research summaries; the deflated Sharpe (per-period units) unchanged.

## 14. API and UI

Owner-only: `GET /api/intelligence/semantics`, `/baseline`, `/models`,
`/registry`, `/routing`, `/conviction?symbol=`, `/ensemble`, `/debate`,
`/exclusions`; `POST /api/intelligence/refresh`;
`POST /api/intelligence/registry/{model}/status` (also step-up). The Track
Record page shows a MODEL INTELLIGENCE panel: status, the number each model
states and its type, UNCALIBRATED, the directional record only when measured,
always-long, the promotion gate and routing, and the debate-desk conclusion.

## 15. Measured baseline, 2026-10-08

From `docs/baselines/model_intelligence_baseline.json` (re-generate with the
script; the numbers move as calls resolve).

**The record.** 1,081 predictions (desk 65, technical 578, V5 438); 590
graded, 491 pending, 0 undecidable, 0 duplicate references. Probability
types: NONE 898, STATED 88 (all V5), CONFIDENCE 65 (desk), UNKNOWN 30 (V5,
kind never recorded). Horizons: 1 day 65, 10 days 578, 22–48 days 436, 252
days 2. Asset classes: equity 928, crypto 153; venues XNYS 890, XLON 38,
CRYPTO 153. Provenance complete on 4 rows — the first desk calls recorded
under the new rules; no historical row is complete (venue, session, feature
version, routing and calibration were never recorded before).

**Regrade.** 511 graded calls carried a measurement defect; 510 were
re-measured on one series (1 identity mismatch stays excluded) and **24
outcomes changed**.

| model | graded included | directional rate (cluster 95% CI) | vs always-long | probabilities |
|---|---|---|---|---|
| desk (ledger, Jul–Aug) | 61 on 12 dates | 45.9% (30.3–61.5%), indistinguishable from chance | no detectable difference (p 0.72) | none stated (CONFIDENCE) |
| technical | 488 on 17 dates | 44.5% (37.6–51.3%), indistinguishable from chance | **worse** (65 vs 91 disagreements, p 0.045, single test) | none stated |
| V5 | 39 (16 directional) on 4 dates | INSUFFICIENT_DATA | INSUFFICIENT_DATA | 16 stated — INSUFFICIENT_DATA |
| ML ensemble | 0 | no graded record (recorded from today) | — | SCORE |
| signal engine | — | NOT_RECORDED (no horizon) | — | SCORE |

Slices that survive the Holm correction: technical on **equities is worse
than chance** (39.7%, n 398 on 17 dates, Holm p 0.033); every other slice is
indistinguishable from chance after correction. Market regime: technical
calls fall in two labels (BULL/NORMAL_VOL 229, BULL/LOW_VOL 259), neither
distinguishable from chance after correction; no BEAR or HIGH_VOL period
exists in the record. Macro regime: one label, not measurable.

**Calibration.** desk and technical: NOT_APPLICABLE. V5: INSUFFICIENT_DATA
(16 probabilities; after the label purge no training window survives, because
45-day horizons all overlap). Calibration version: none, everywhere.

**Registry / routing.** desk and technical EXPERIMENTAL; V5 and ML
INSUFFICIENT_DATA; signal engine NOT_RECORDED. No model passes the promotion
gate (each fails at least sample size, holdout, stability and reproducible
version — historic rows carry no code hash). Every routing slice is
ROUTING_INSUFFICIENT_EVIDENCE -> DEFAULT.

**Ensemble.** desk–technical 5 overlapping calls, technical–V5 22 (7 both
directional), desk–V5 0: insufficient overlap to say whether any pair adds
independent information.

**Debate desk.** 2,276 debates (2026-07-18 to 2026-10-08); verdicts NO_TRADE
1,690, BUY 508, SELL 78; LLM prose fell back to the composer in 28 (1.2%);
0 frontier consults; latency measured on the first 8 timed debates: median
14.5 s, p95 29.5 s (all of it the local LLM writing prose). Graded: 322
independent events — judge 50.6% (44.5–56.8%), its technical input 47.1% on
the same events; on 42 disagreements the judge was right 27 vs 15 (McNemar p
0.088). Conviction ordering undetermined. **No measurable incremental value**;
the LLM's is zero by construction.

**Performance** (this machine, warm, p50): record a prediction 21–25 ms (the
SQLite commit; the new venue / session derivation adds nothing measurable);
canonical dataset over 1,081 rows 11 ms; evaluate all models 4.9 ms;
calibration on 800 probabilities 5.5 ms; one full route 1.1 ms, a table
lookup 1 µs; ensemble 4.4 ms; conviction from a debate 0.16 ms, or 16.7 ms
end to end on the order path (reading the debate, the signals file's age and
the snapshot from disk); `pretrade.assess` 52 ms with or without a
conviction. Network-bound steps run off the trade path: the regrade (~100
symbols) 6–125 s and the debate grading 7–36 s, depending on the vendor cache.

## 16. Open decisions for the owner

* The desk's judge weights (`src/desk/scorecard`) tilt after 20 closed trades
  on raw hit rates with no significance test. Flagged, not changed: changing
  it changes the desk.
* A queued desk entry whose debate is older than its 1-day horizon now DEFERs
  at send time instead of executing on a stale call.
* ML models trained before 2026-10-08 carry the label bias; retraining is a
  pipeline run, not done here. `data/ml_predictions.json` is from 2026-05-31.
* `regrades.json` is a view; whether to write corrected grades into the
  ledger is a decision, not done.
