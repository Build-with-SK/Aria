# Engineering handover — multiasset research remediation

Published branch: `feature/aria-multiasset-trading`.
Validated source revision: `1c7e94e6356f4de36ab4f54912fd641f45f0617c`.
Repository: https://github.com/Build-with-SK/Aria
Draft pull request to main: https://github.com/Build-with-SK/Aria/pull/5

## Delivered

- Deterministic sizing, independent calculator/spec checks, currency-pair
  conversion, daily equity loss, buffered loss floors and monotonic account history.
- Fresh account-bound evidence acquisition before order review, persisted account
  registration, immutable-event verification of approval/consumption/history rows,
  single-use tokens and mandatory external rollback checkpoint before reservation.
- Consequence-based account classes; PRACTICE-only review; enum provider restrictions,
  news/break rules and daily activity headroom. Global kill switch read directly.
- Investor-only MT5 reads behind a spawned worker. No order sender or enabled
  approval endpoint. Wrong account/provider/permission evidence fails closed.
- Correct bar ordering, gap/intrabar stops, required finite opening prices,
  initial/final equity accounting, calendar/cadence annualisation and reset replay.
- Server-verified source attribution, preserved unverified client claims and
  abstention when strategy families disagree. Fixed support-break/bounce logic.
- Patched frontend router advisories, clarified extension-only banner, updated
  risk/security/operations documentation and reproducible review probes.

## Evidence

| Run | Actual result |
|---|---|
| Windows full suite at `4165e71` | 3,648 passed, 10 skipped, 1 deprecation warning; exit 0; 453.19 s |
| Latest scheduler/acquisition-clock follow-up | 65 passed; no test skips or relaxed assertions |
| Linux PR full suite at `1c7e94e` | 3,636 passed, 11 skipped, 390 warnings; exit 0; 125.59 s |
| Linux push + PR workflows at `1c7e94e` | protected-paths, security-regression, full-suite all SUCCESS |
| Hash-pinned isolated research environment | 314 passed, 1 warning at the prior checkpoint source; fixture/mock coverage |
| Exact-lock frontend | build plus all four offline verifiers passed with router 7.18.5 |
| Dependency audits | frontend zero known advisories; research lock zero across 38 packages |
| Publication secret scan | historical candidates triaged as dependency integrity hashes or synthetic invalid test URLs; no real credential identified |

Linux skips/counts differ because CI omits optional stack/production state available
in the Windows test environment. Warnings include dependency deprecations; they
were not suppressed to produce the result. Initial CI failed due to missing
yfinance, then a legacy test's global os.name mutation. The dependency prerequisite
and scheduler-local mock were fixed; workflows and test assertions stayed intact.

CI evidence for the validated source:
https://github.com/Build-with-SK/Aria/actions/runs/42409891844 (PR)
https://github.com/Build-with-SK/Aria/actions/runs/42409885570 (push)

The earlier independent remediation review correctly found defects despite passing
tests. The response and added cases are in REMEDIATION_REVIEW_RESPONSE.md. The
follow-up has implementation evidence, not a new independent approval.

## Remaining gates and exact next milestone

Gate: **CONDITIONAL_REVIEW — disconnected research only**. No practice-account,
paid-evaluation, funded or live execution approval is implied.

A concrete production evidence reader/terminal specification mapper, genuine
investor login, separate Windows host, verified provider account classification,
checkpoint permissions/power-loss recovery, provider AI/EA/remote-operation
clarifications and actual execution monitoring/reconciliation remain outside this
validated delivery. The pre-break helper cannot liquidate an existing position.
The shared runtime Python environment still has previously documented advisories.
There is no out-of-sample strategy edge, challenge-pass forecast or demonstrated
25% return. The legacy optional root backtest hook remains a documented pre-existing
integration defect; the tested backtester is its standalone library API.

Next milestone: owner review of draft PR #5 and the disclosed inherited integration
ancestry; then a separately authorized Windows/practice read-only integration and
its own release gate. No merge, Oracle deployment, writer-task restart or broker
connection was performed. Existing local AGENT_HANDOFF.md remains untracked and
preserved. Documentation-only commits may follow the validated source revision;
the current publication commit is verified against the remote in the delivery log
and closing handover.
