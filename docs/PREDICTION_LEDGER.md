# The prediction ledger: what is recorded, how it is graded, what it may claim

Last revised 2026-10-08 (see "2026-10-08" below and docs/MODEL_INTELLIGENCE.md).
Describes `src/core/ledger.py`, `src/core/ingest.py`,
`src/core/calibration.py`, `src/core/baseline.py`, `src/v5/learning.py`,
`src/v5/loop.py` and `src/data/technical_tracker.py` as they are.

## Sources

| source | written by | horizon | probability |
|---|---|---|---|
| `v5` | `src/v5/loop.py` (one call per watchlist name per settled NYSE session) and page views, via `learning.log_prediction` → `data/v5/predictions.jsonl` → `ingest_v5` | ~37 trading days | **stated** for bull/bear (`confidence` = P(direction right)); **none** for neutral |
| `technical` | `technical_tracker.snapshot_catchup` (one batch per settled NYSE session) → `data/technical/recommendations_log.jsonl` → `ingest_technical` | 10 trading days | **none** — an indicator vote count |
| `desk` | desk debates → `data/desk/backfilled_outcomes.jsonl` → `ingest_desk_outcomes` | 1 day | **mapped** — conviction squashed onto 0.5–0.95; orders calls, is not a probability |

## Columns that carry meaning

* `probability` — **legacy, as ingested.** For V5 it is P(bull), which is the
  *complement* of P(correct) on a bear call; for the desk it is mapped
  conviction. Nothing reads it as a probability any more.
* `probability_kind` — `stated` | `mapped` | `none`.
* `p_correct` — P(the stated direction is right), only when `probability_kind`
  is `stated` and the call is directional. Brier, ECE and calibration curves
  read this column and no other.
* `model_version` — `name|code=<12-hex sha256 of the package source>|…`
  (`src/core/versioning.py`; line endings normalised). Historic V5 rows carry
  `v5|weights=<n>` only: the code that ran then is not recoverable, so no hash
  is invented.
* `information_session` — the latest settled NYSE session at creation, for
  calls made outside the session window. A call made inside the window (09:30
  to 17:00 New York) has none: its reference is an intraday price.
* `data_as_of` — the last bar the call was built on (V5, from provenance).
* `regime_at` / `regime_basis` — the regime known **at creation** and where it
  came from (`src.macro.regime.stamp()`), including whether its inputs were
  stale. Historic rows were backfilled only from a world snapshot taken before
  the call and within 24 h of it.

## When predictions are made

The predict leg and the technical snapshot are keyed on **NYSE sessions**
(`src/trading/market.py`: `latest_completed_session`, `in_session_window`,
static holiday list through 2027). A session counts as settled 60 minutes after
the 16:00 close. Each leg runs at most once per settled session, never inside
the session window, and is checked hourly and at startup. It never backfills:
a missed session is made up late, timestamped when it is made, on the latest
close. Without a calendar for the year it refuses rather than guesses.

Why: the old 22:10 / 21:30 London crons needed the process alive at that time.
From 23 Sep to 6 Oct 2026 it never was, and both legs stopped silently. The
daily cron also fired at weekends, so Saturday and Sunday calls (70 V5 rows)
re-logged Friday's close as new evidence. `loop.health()` now reports
`expected_session`, `last_session` and `missed_sessions`.

## How predictions are graded

A return is measured on **one adjusted series**: the close of the anchor session
→ the close on `resolve_after`. The anchor is `information_session`, or, for US
listings, the session derived from the creation time. Intraday calls,
non-US instruments and crypto keep the recorded reference price.

Previously the return was (adjusted close at horizon − raw recorded price) /
raw recorded price, which dropped every dividend inside the window. Already
graded rows are **not** regraded; the grade note says which basis was used.

The identity check (`_reference_matches`) still runs first: a recorded price
more than 25% from the series is treated as a different instrument and left
unscored.

Neutral calls are right when the move stays inside a band. **V5's own grader
uses a flat ±2%; the core ledger uses ±2% × √(horizon/21).** A V5 grade carried
from the learning loop keeps V5's rule. Neither is a directional hit, so stats
report directional and neutral calls apart (`hit_rate_directional`).

## What may be claimed — the baseline

`docs/baselines/calibration_baseline.json` is the measured "before", frozen on
2026-10-06 with the corrected semantics. It holds aggregates only.

| | directional graded | hit rate | 95% CI | stated-probability Brier |
|---|---|---|---|---|
| desk | 61 | 45.9% | 34.0–58.3% | — (mapped) |
| technical | 459 | 45.5% | 41.0–50.1% | — (none) |
| v5 | 16 | 50.0% | 28.0–72.0% | 0.250, n=16, skill ≈ 0 |

All 560 graded rows fall in one regime label (Expansion (Goldilocks)), so
**per-regime performance cannot be evaluated yet.** The technical calls cluster
on a few dates, so their outcomes are not independent and the interval above is
narrower than the evidence supports.

The 2026-10-06 audit reported a Brier of 0.288 and "70–80% calls right 27%".
Both were artefacts of reading mapped conviction and P(bull) as P(correct). They
are not measurements of anything.

`scripts/calibration_baseline.py --compare` is the only sanctioned way to claim
an improvement (`src/core/baseline.compare`). It:

1. refuses unless current metrics are measured on predictions **created after
   the baseline cutoff** (out of sample);
2. reports `insufficient data` below 30 scored events on either side;
3. calls a hit-rate change real only on a two-proportion z-test at 95%, and a
   Brier change only beyond 1.96 standard errors of the difference.

## 2026-10-08: types, provenance, new sources, and two grading defects

* `probability_type` is stored explicitly (src/intelligence/semantics):
  STATED / CALIBRATED probability, SCORE, CONFIDENCE, NONE, UNKNOWN. Kinds
  `calibrated` and `score` join `stated | mapped | none`; only stated and
  calibrated keep a `p_correct`, and a calibrated one must name its
  `calibration_version`.
* New provenance columns: `venue`, `market_session` (derived from
  src/markets/sessions at write time), `feature_version`, `routing_decision`,
  `calibration_version`, `reference_basis`, `model_hash`. NULL means not
  recorded; nothing is back-filled.
* `reference_basis = next_close`: graded from the close of the first bar
  strictly after the call's day to the close `horizon_days` bars later.
* New sources: **desk** debates are recorded at creation (once per ticker /
  view / day, CONFIDENCE, 1 day, next_close) — previously only a one-off
  backfill reached the ledger and ~2,200 debates after 2026-08-01 were never
  graded; the **ML ensemble** is recorded as a SCORE when the pipeline runs.
* A row whose `external_ref` is already recorded returns its id without being
  re-stamped (the INSERT was already ignored).

| source | written by | horizon | number |
|---|---|---|---|
| `desk` (from 2026-10-08) | `src/desk/debate.record_in_ledger` | 1 day, next_close | CONFIDENCE (mapped conviction) |
| `ml-ensemble` | `src/intelligence/recorders.record_ml_predictions` via `main.run_ml` | 1 / 5 / 20 days | SCORE |

**Grading defects found 2026-10-08** (not repaired in the ledger; re-measured
in a view, src/intelligence/regrade.py):

* 318 technical and 9 V5 grades were made on the horizon date before that
  date's close existed, so they were graded on the previous session's close.
* Grades before 2026-10-06 ran from a raw recorded price to an adjusted close.

The canonical evaluation dataset (src/intelligence/dataset.py) excludes such
rows with a reason, or uses the same-series re-measurement in their place and
counts how many outcomes changed.

## Backfill

`scripts/backfill_ledger_provenance.py` (dry run by default, `--apply` to write)
fills NULL provenance columns from proven sources only, never overwrites, and
takes a SQLite backup first. Run on 2026-10-06: 928 rows; backup
`data/backups/aria_core_20261006-132530_pre_provenance.db`.
