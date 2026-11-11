# Response to the dac263f remediation review

Date: 2026-11-07. Code validated through `4165e71`.
Gate: **CONDITIONAL_REVIEW — disconnected research only**.

The independent remediation review correctly found 15 missing invariants despite
its green full suite. This response records fixes and their limits; it is not a
claim that the new code has received another independent approval.

| Reviewer reproduction | Current behavior | Evidence |
|---|---|---|
| SQL rows invent approval without approve() | Transaction rejects state/approval rows inconsistent with authenticated events | test_database_writer_cannot_invent_approval |
| Reset consumed flag/delete intent to reuse token | Complete consumption/intent projection rejects edit | test_database_writer_cannot_replay_consumed_token |
| Delete boundaries and rebuild lower history | Complete authenticated boundary set must match tables | test_deleted_history_cannot_be_reseeded |
| Submit lower RuleState than stored history | Configured source is reacquired; stored history supersedes reconstructed baseline | test_store_history_overrides_transport_reconstructed_state |
| Replace midnight balance | Submitted evidence must match configured source acquisition | test_caller_cannot_replace_authoritative_account_evidence[midnight] |
| Hide existing position with empty complete book | Same acquisition boundary rejects substituted book | test_caller_cannot_replace_authoritative_account_evidence[book] |
| Reset 1,600 daily requests to zero | Same boundary rejects substituted activity | test_caller_cannot_replace_authoritative_account_evidence[activity] |
| Wrong-provider/account/symbol/research calculator | Calculator identity and permission bound in engine and source | test_calculator_provider_account_symbol_and_permission_are_bound |
| Relabel paid Challenge as Free Trial | Registered account profile is independent of request; registration event persists across reopen | test_paid_account_cannot_be_relabelled_by_submission; test_reopening_same_account_under_new_registration_is_refused |
| NONE gap rule applies all-day block | All-day branch applies only if gap rule enabled | test_no_gap_rule_does_not_block_whole_news_day |
| Missing Open executes retrospectively | Missing/nonfinite Open raises ValueError before simulation | test_missing_open_cannot_create_retroactive_fill |
| Final equity excludes closing cost | Initial capital included; final curve updated after closing fee | test_final_curve_includes_initial_capital_and_exit_fees |
| NaN range row silently loses coverage | Result warning identifies missing range values | test_missing_range_value_is_reported |
| Midnight replay uses earlier balance | Exact boundary observation sets daily balance before evaluating floors; missing reset refused | test_midnight_snapshot_sets_daily_floor; test_sparse_new_day_needs_reset_snapshot |
| 21-hour crypto bars counted as daily | 365*24/21 periods/year; incomplete grids unavailable | test_twenty_one_hour_crypto_bars_are_not_daily; test_irregular_return_grid_is_not_annualised |

These tests are in `tests/test_remediation_followup.py` (32 cases with additional
identity, timing and crash/rollback variants). RQ source/conflict tests are in
`tests/test_research_review_followup.py` (10 cases). The original 167 finding cases
remain; they still represent narrower invariants and are supplemented, not treated
as sufficient certification.

Whole-database rollback is also covered: a valid old HMAC prefix plus corresponding
tables cannot reuse an intent because reservation requires an independently held
checkpoint. The checkpoint is persisted before SQLite commit. An interrupted
commit intentionally stops subsequent mutations pending verified recovery.

## Boundaries still requiring a separate milestone

- `BoundRiskSource` is configured by trusted server code and reads fresh evidence
  on each queue/reservation. No HTTP/model/plugin path can install one. Its actual
  transport must be implemented and validated for the provider; fixture readers
  do not certify any real broker account, specification, book or activity history.
  Without a configured source the store refuses order review.
- Only PRACTICE registrations may enter order stages. Paid evaluation, funded and
  live admission remain disabled. An arbitrary local process able to replace code,
  registry, key and checkpoint is outside the database-only attacker model.
- No sender or existing-position execution monitor is deployed. The pre-break
  flatten helper cannot guarantee liquidation. Position monitoring/reconciliation
  and provider restrictions must be integrated before any execution gate opens.
- Genuine investor login, a separate Windows host, terminal/specification mapping,
  checkpoint ACLs/power-loss recovery, provider AI/EA/remote-operation clarifications
  and real-demo observations remain BLOCKED or UNVERIFIED.
- Target statistics are monetary targets, not challenge pass probabilities. Sparse
  equity samples cannot prove absence of unobserved intraday breaches. No strategy
  edge, out-of-sample performance or 25% return is established by this delivery.
- Shared runtime Python advisories remain; only the isolated research lock and
  updated frontend dependencies have current zero-advisory results.

## Reproduction

Run the full repository suite with the existing full-stack test interpreter:
`python -m pytest -q -p no:cacheprovider --tb=short -rs`.
The final run returned **3,648 passed, 10 skipped, 1 warning**, exit 0.
The separate hash-pinned research environment returned **314 passed, 1 warning**.

The original review probes are frozen for `7385130`. They were rerun against that
baseline. `claude_review_probes/probes_after_remediation.py` and its recorded JSON
rebuild the principal financial/approval/reasoning examples on the current API.
Original `probe_bt.py` also runs unchanged on the current branch: exit 70,
final equity 70,000, one intrabar stop.

Frontend clean install/build and four offline verifiers passed using the exact
updated lock. npm audit and the 38-package research lock audit report zero known
advisories. These checks do not represent a deployed-browser or real-broker test.
