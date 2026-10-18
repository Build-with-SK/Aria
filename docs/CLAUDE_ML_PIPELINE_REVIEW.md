# Claude ML pipeline review — ARIA final candidate

Reviewer: Claude Code. Date: 2026-10-18. Candidate `edc2796`. My earlier ML-resume work
(`4ffda39`, branch `claude/loving-gauss-c5dcd5`) is in the candidate as Codex's
cherry-pick `1fd6426`, plus Codex's shared writer lock (`24a0c36`). No model was trained:
every test below uses synthetic frames and a fake trainer. No production or private data
was read.

## 1. What was verified

| Requirement | Verdict | Evidence |
| --- | --- | --- |
| Predictions checkpointed incrementally | **PASS** | `main._MLBatchWriter` merges every `ml.persist_every_tickers` (25) finished tickers through `ml_store.merge_rows`: read, update, atomic replace, under a cross-process `ProcessSharedLock` (`src/models/ml_store.py:183-205`). Tests: `test_a_run_stopped_after_batch_two_…`, `test_a_hard_kill_keeps_the_batches_already_merged`, `test_concurrent_batch_merges_do_not_drop_finished_tickers`. |
| Interrupted processing resumes safely | **PASS** | A rerun skips rows younger than `ml.resume_max_age_hours` (20 h). An interrupted pass prunes nothing (`keep_only` runs only after a pass finishes). Ctrl+C flushes finished tickers in `finally`. A failed write keeps the batch for the next attempt. Tests: `test_a_rerun_trains_only_the_tickers_still_missing`, `test_a_finished_pass_drops_rows_no_pass_produced_and_an_interrupted_one_does_not`. |
| Duplicate predictions prevented | **PASS** | The file is keyed by ticker, so a merge replaces rather than appends. The ledger records each batch only after its merge succeeds, and resumed tickers are not re-recorded. Residual: a crash between `merge_rows` and `_record` leaves rows in the file but missing from the ledger. That is a gap, not a duplicate. |
| Freshness timestamps are correct | **PARTIAL** | `generated_at` is stamped when the ticker finished and `data_as_of` is its last bar. Two gaps: stamps are **naive local time** (F-06 root cause), and resume ignores `data_as_of` (F-04). |
| Stale predictions not presented as current | **PARTIAL** | Rows are aged individually in the technical analyst (each horizon cited only inside its own window), the world model, chat context, `/api/data/freshness` and launch_check. **Brain perception does not age ML at all** (F-05). |
| ML failures cannot bypass risk controls | **PASS** | ML output is cited evidence only. It sets no size, stop, target or approval. Sizing, the risk officer, the capital allocator, the pre-trade gate and reduce-only are independent of it. `run_ml` turns any exception into `{}`, and an empty run leaves the last file untouched. No ML consumer in `src/risk`, `src/execution` or `src/trading` (grep of `ml_predictions` / `ml_store` consumers). |
| Model outputs validated before use | **PARTIAL (LOW)** | `bullish_prob` is read without a finite/[0,1] check in `desk/analysts/technical_agent.py:84-98` and `brain/cognitive/perception.py:150-158`. A malformed value cannot size anything, but it can label a debate evidence line bull or bear. The writer (`_ml_to_json`) produces model probabilities, so the exposure is to file corruption or a future writer. |
| No look-ahead or leakage in training | **PASS (code review)** | Targets use `pct_change(h).shift(-h)` and the last `h` rows are NaN and dropped (`targets.py:18-66`). `PurgedWalkForwardCV`: embargo = max(1% of the test fold, horizon), and training rows whose forward window overlaps the test start are purged (`walk_forward.py:86-109`). `StandardScaler` sits inside each learner's Pipeline, so it is fitted per fold. The meta-learner trains on out-of-fold predictions. Final refit on all labelled rows serves live prediction only. No negative shifts, centred windows or backfills in `feature_engine.py` or `targets.py`. Not re-derived numerically: no training run was permitted or needed. |

Suites: `tests/test_ml_durable.py` (20 cases) and `tests/test_ml_persistence.py` (32 cases)
pass inside the full candidate run (3,523 passed).

## 2. Findings

### F-06 — MEDIUM (regression in Codex `edfb5e6`) — A future-dated ML row crashes the chat context's freshness label

- **Component**: `src/models/ml_store.py` `dataset_age` (new early return in `edfb5e6`), `backend/main.py:2820-2856` (`_source_age`), called by the chat-context builder (`:2913-2915`) and `:3144`.
- **Evidence**: The new branch returns `basis="row_generated_at"` with `age_hours=None` when any row is invalidly stamped or dated in the future. `_source_age` copies `age_h = a["age_hours"]` inside its `try`, then evaluates `if age_h < 36` **outside** it. Probe run on a detached `edfb5e6` checkout: `TypeError: '<' not supported between instances of 'NoneType' and 'int'` at `backend\main.py:2852`. The same probe on the candidate does not crash. There the future row is called fresh, the old behaviour `edfb5e6` set out to fix.
- **Trigger in practice**: `generated_at` is written with `datetime.now()`, naive local time. On **2026-10-25** UK clocks go back at 02:00 BST. Rows stamped between 01:00 and 02:00 BST read as up to an hour "in the future" during the repeated hour. Any clock correction does the same.
- **Fix**: (1) In `_source_age`, treat `age_hours is None` as stale with an "age unknown" label; never compare it. (2) Stamp `generated_at` in UTC with an offset (`datetime.now(timezone.utc).isoformat()`). `stamped()` already converts aware times. (3) Add a test that drives `_source_age` and `/api/data/freshness` with a future-dated row.
- **Owner**: Codex. **Status**: OPEN (blocks acceptance of `edfb5e6`'s ML part).

### F-05 — MEDIUM (known open item) — Brain perception reads ML predictions with no age

- **Component**: `src/brain/cognitive/perception.py:135-158`.
- **Evidence**: `MLPrediction` has no timestamp. Every row in `ml_predictions.json` becomes part of "what the market looks like RIGHT NOW" whatever its `generated_at`. The brain proposes trades into the approval queue (`brain/cognitive/executor.py`), so a months-old forecast can shape a proposal the owner then reads as current.
- **Fix**: Carry `generated_at`/`data_as_of` into `MLPrediction` and drop or label rows outside their horizon, with the same rule as `technical_agent.ml_horizon_live`. Codex already lists this under "Per-row signal age / ML perception consumers" in ARIA_REMAINING_BLOCKERS.
- **Owner**: Codex. **Status**: OPEN (acknowledged).

### F-08 — MEDIUM — Nothing enforces a single pipeline writer

- **Component**: `main.py` (no process lock), `backend/main.py:3487-3514` (`/api/run`).
- **Evidence**: Codex's report says "single full pipeline writer remains an operational requirement", but nothing enforces it. `_run_in_progress` is a per-process flag, and it is set **inside** the background task, not when the request is accepted, so two quick POSTs can both start a run. It cannot see the `TradingIntelligenceSystem` scheduled-task run either, which is a separate process. Two complete passes then each prune the other's rows (`keep_only(skip | written | pending)`) and interleave writes of `signals.json` and the other datasets.
- **Fix**: Take a `src.core.durable.SingleInstance` lock at the top of `main.py` and exit with a clear message when it is held. In `/api/run`, set the flag before scheduling, or better, check the same lock.
- **Owner**: Codex. **Status**: OPEN.

### F-04 — LOW — ML resume reuses a row trained before the latest bar

- **Component**: `src/models/ml_store.py:149-170` (`fresh_tickers`), `main.py:608-618`.
- **Evidence**: The skip decision looks only at `generated_at`. Probe `test_f04_resume_retrains_a_row_whose_bar_is_behind`: a row made 2 h ago from the 2026-10-15 bar is skipped although the frame now ends 2026-10-16.
- **Impact**: After a new close, a rerun inside 20 h mixes previous-bar forecasts into a pass that is then reported as fresh. Rare today, because scheduled runs are about 24 h apart.
- **Fix**: Skip only when `row["data_as_of"] == _last_bar_date(featured[ticker])`.
- **Owner**: Codex. **Status**: OPEN.

## 3. Note on my own earlier work

`1fd6426` faithfully carries `4ffda39`. Codex's additions (shared writer lock,
`concurrent_batch_merges` test) close the lost-update window I had left between merges. I
had recorded that resume does not bridge daily runs (the 20 h budget is shorter than the
24 h schedule). That is still true, and choosing the budget remains an owner decision.
