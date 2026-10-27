> Current reconciliation packet: [2026-10-26/27 ARIA RC](release/2026-10-26/ARIA_RC.md). The report below is historical and does not supersede its open gates.

# ARIA operational release candidate — 2026-10-18

**Overall verdict: BLOCKED for deployment/production release.** Authorized local implementation and validation are complete as recorded below. No merge into production, push, service restart, broker request or live enablement was performed.

Review branch: `codex/aria-operational-readiness`, based on integrated candidate `edc2796`. Implementation commit: `edfb5e6`; later commits contain evidence/procedures only. The original candidate and primary deployed checkout remain untouched.

## Integration verification

- `claude/aria-completion` at `659a75c` is an ancestor of the candidate.
- Claude ML commit `4ffda39` is incorporated as `1fd6426`; its `main.py`, `ml_store.py` and launch-check implementation match at incorporation. Later `24a0c36` adds batch serialization. The existing integration report records the identity/partial/state-recovery adaptations rather than silently replacing the completion branch.
- `_MLBatchWriter` flushes every 25 finished tickers by default; rows carry their own `generated_at` and training `data_as_of`. `merge_rows` is locked and atomically replaces the snapshot. Default resume budget is 20h; `--force-ml` retrains. Completed-pass pruning is distinct from interruption recovery.
- Tests force an actual subprocess `os._exit` after ticker 55 and verify the first 50 remain on disk; interruption/resume, force, timestamps, failed writes, mixed row ages and concurrent batch merges pass. New regressions reject empty, malformed and future-stamped dataset freshness. No real training or live data was used.

## Release gates

| Gate | Verdict | Evidence / boundary |
| --- | --- | --- |
| Candidate identity and integration | PASS | Clean isolated source from edc2796; completion ancestry and ML cherry-pick inspected |
| ML incremental writes/resume/freshness | PASS | Actual abrupt-exit subprocess, batch/resume/atomic-write fixtures; new timestamp regression |
| Windows full regression | PASS | 3,543 passed, 12 skipped, exit 0; 461.31s |
| Linux full regression | PASS | 3,545 passed, 10 skipped, exit 0; fresh source fixture, reused private Linux venv |
| Startup/shutdown and local transport | PASS | Real ephemeral ASGI/proxy/sign-in/SSE fixtures, lifecycle shutdown; no deployed service touched |
| Process exclusion / supervision primitives | PASS | Cross-process lock exclusion, dead-holder recovery, one desk, late model availability and stop/restart fixtures |
| Target-host sustained supervision | BLOCKED | Actual supervisor/host and long uptime need approved deployment |
| Backup/database restore | PASS | Synthetic WAL snapshot integrity, retained UNKNOWN queue, failed snapshot rejection and Windows traversal checks |
| Stale signal and unavailable broker handling | PASS | Existing freshness, risk, unreadable-snapshot and disconnected-adapter regressions |
| UNKNOWN and duplicate prevention | PASS | Durable claims/negative lookup/alias/concurrency/restart fixtures; no automatic release |
| Risk, kill switch, reduce-only, approval | PASS | Full risk/order/session/security suites; no gates weakened |
| Frontend/backend state consistency | PASS | Staged build/offline state verifiers plus real local backend/proxy fixture; acknowledgements distinct from fills |
| Logs/alerts/health | PASS | Readiness failure, log isolation/redaction and reflex attempt/notification-boundary fixtures; external push delivery not measured |
| Live disabled locally | PASS | RESEARCH default, external-switch/live-ceiling tests; no real execution calls made |
| Deployed process live configuration | BLOCKED | Running production environment not certified by local fixtures; no credentials/services changed |
| Static/type/compile/history fence | PASS | Fatal Ruff, six-module mypy, compileall and protected-history fence |
| Base dependency audit | PASS | Reused Windows base: 101 distributions, zero pip findings; pip check clean; npm zero |
| Raw source-security scanner | FAIL | Bandit 43 remaining patterns, exit 1; every original finding classified, none suppressed |
| Manual source-security acceptance | BLOCKED | DNS rebinding egress and nine further-validation items retained |
| Historical account reconciliation | BLOCKED | Prepared read-only procedure, including dfe2a885/AAPL; no broker call made |
| Controlled paper venue acceptance | BLOCKED | Prepared reproducible protocol; requires new explicit owner order/cancel authorization |
| IBKR / actual fees and cumulative net P&L | BLOCKED | External gateway, execution and fee statement evidence missing |
| Hosted browser/TLS/optional packages | BLOCKED | Prior local browser evidence retained, no new hosted acceptance; optional packages outside base audit |
| Independent review / data-age contracts / measured edge | BLOCKED | Unresolved requirements retained in ARIA_REMAINING_BLOCKERS |
| Production/live release | BLOCKED | Owner authority plus unresolved security/external gates |

PASS means the specified local fixture scope passed, not that its external venue/host equivalent was exercised. Skips are reported separately. Prior Windows 3,524/Linux 3,526 runs remain historical evidence; they are not the result of this mission.

## Review material and acceptance procedures

- ARIA_SECURITY_READINESS.md and security/BANDIT_TRIAGE.md: all 49 findings, fixes and residual limits.
- ARIA_FINAL_TEST_RESULTS.md: exact current commands, versions, outcomes and evidence location.
- ARIA_HISTORICAL_RECONCILIATION.md: read-only account/statement evidence; no absence-to-rejection inference, no queue mutation.
- ARIA_PAPER_ACCEPTANCE.md: approval/submission/lost-ack/partial/cancel/restart/duplicate/fee/final-account protocol, abort criteria and private evidence fields.
- ARIA_REMAINING_BLOCKERS.md: every unresolved deployment, credentials, venue and owner decision.

Before an approved cutover, stop all old queue/desk/pipeline writers and preserve claim/quarantine/audit state. Do not use rollback to erase uncertain submissions. Only one complete ML pass may prune its universe at a time. Neither this document nor a green regression result authorizes broker writes or production changes.
