# ARIA release candidate

**Candidate for constrained review; release/deployment BLOCKED.** Branch `codex/aria-final-integration`, isolated checkout. Runtime implementation is `89f8f4a`, final test inventory `a079dff`; current source anchor is recorded in ARIA_FINAL_TEST_RESULTS; later commits are reports/CI only. No protected-branch merge, push, production restart, broker mutation or live enablement.

| Gate | Verdict | Scope |
| --- | --- | --- |
| Claude/Codex reconciliation | PASS | Completion ancestry, Phase2 and later Claude ML work; traceability in final integration report. |
| R1/R2/R3 and local order safety | PASS | Identity-checked bracket recovery, typed refusal, unreadable snapshots; legacy/UNKNOWN/claims/partial/concurrency/restart fixtures. |
| Patched Windows runtime preparation | PASS | Private3.14.8, actual Expat 2.8.5/OpenSSL 3.5.9; fresh dependencies and regression. |
| Full integrated regression | PASS | Windows 3,524 passed/12 skipped; Linux 3,526/10. Frozen runtime89f8f4a, final tests a079dff; both exit0. |
| Dependency audits | PASS | Fresh base Windows/Linux and npm:zero advisories; optional/deployed stack excluded. |
| Static checks | PASS | Fatal Ruff, six-module mypy, compile/fence. |
| Source security scanner | FAIL | Bandit 49 patterns assessed; exit1 retained. This is not a clean-source security claim. |
| Local Linux execution | PASS | Real Ubuntu WSL source/venv, install/config/db/paths/permissions/process shutdown/restart; full final result in test report. |
| Real browser | PASS | Actual built React dashboard/scanner/approvals/UNKNOWN/risk/history/outage/recovery and real SW cache upgrade, synthetic APIs. |
| Local backend/frontend/proxy/auth/SSE | PASS | Real ASGI listener and forwarding proxy, built shell/asset, signed session, anonymous denial, incremental SSE and clean shutdown. |
| Approved external PAPER reads | PASS | Five GETs:active unblocked account/history, broker/client identifier agreement, positive legacy recovery in disposable queue. |
| Historical migration, new venue orders, IBKR, fees, hosted acceptance | BLOCKED | Exact prerequisites in ARIA_REMAINING_BLOCKERS. |
| Production/live release | BLOCKED | No edge claim; owner approval and external validation absent. |

## Controlled paper acceptance plan — not permission to execute

1. Owner selects a paper account, instrument, tiny size/notional ceiling, time window and exact allowed order/cancel operations. Confirm paper hostname/account, backup state, enable monitoring and abort conditions.
2. Lost acknowledgement: allow one venue acceptance, drop only the response, restart the app; original deterministic client ID must find the same order. Assert one venue order and one durable claim, no second POST.
3. Partial fill plus cancel: inspect executions and cumulative quantities, cancel remainder only with explicit approval, keep actual fill/price and terminal remainder. Restart/replay callbacks; position and reservations must not duplicate.
4. Restart/disconnect: lose gateway/network during working order, preserve UNKNOWN/claims and protections, reconnect and use original/permanent identities. A404 remains unresolved.
5. Fees: compare every execution, cumulative VWAP, commissions and final net P&L to the broker statement. Unknown fees remain explicitly unreconciled, never silently zero/estimated truth.
6. Reconcile all created orders, positions, residuals and audit transitions before ending. Stop on any identity mismatch, unresolved acceptance, risk bypass or duplicate send. Preserve the evidence and keep automation blocked until owner review.

Isolated mocks already exercise timeout, delayed visibility, partial/terminal/cancel, duplicate callbacks, crash and restart. The plan's external fee/venue outcomes remain BLOCKED; no external acceptance pass is inferred from mocks.
