# Runtime and performance verification — 2026-10-17

**Release recommendation: BLOCKED.** No live-money approval or deployment.

`tests/test_final_runtime.py` runs real FastAPI startup/lifespan and Uvicorn on
an OS-selected ephemeral loopback port. ARIA_TESTING=1, fixture credentials,
throwaway state and fake broker transport remain in force. No production port,
account data, vault or .env is copied or replaced.

Two listener instances verify health/readiness, read UNKNOWN through the owner
API, reconstruct OrderManager after shutdown, refuse duplicate submission,
reconcile the original id to FILLED, then read persisted quantity/price/status
from the restarted listener. Exactly one mock submission occurs.

The network-disabled GET sweep exercised **158 routes**: 138 returned 200,
13 returned 422 for incomplete synthetic parameters, 2 returned 400, 4 returned
404, and `/api/brain/memories` exceeded 25 seconds. No 5xx or unhandled exception.
The existing sweep explicitly allows that memory timeout; it is an open latency
issue, not a successful response or a newly waived failure.

| Gate | Result and boundary |
| --- | --- |
| Backend startup/health/readiness/restart | Verified on actual isolated listener |
| Approval and reconciliation routes | Verified with real routes and mock broker |
| Concurrency/recovery/delayed acknowledgement | Actual manager/adapter code; process and fault-injection tests |
| Kill switch, paper-only, risk, reduce-only, sessions | Existing tests retained, new regressions added |
| Scanner/sentiment/strategy calculations | Deterministic fixtures and existing pipeline tests |
| Frontend status contract | Actual component SSR, route checks and staging build |
| External feeds/actual paper order/gateway restart/fees | BLOCKED: no external credentials/orders used in this worktree |
| Deployed commit and real browser action/hydration | BLOCKED: reserved for integration/runtime owner |
| Linux GitHub Actions | BLOCKED: no push or dispatch performed |
| Security advisories cleared | BLOCKED: dependency and static-analysis triage remains |

The main environment full suite passed 3,391 tests (9 skipped), followed by
47 final import/data regressions. The clean declared-CI-dependency full suite
passed 3,314 tests (13 skipped) on implementation `048f47b`. Extra skips reflect
optional SDK/runtime availability; actual broker transports and Uvicorn restart
were exercised in the primary environment.

## Measurements

Fixed RNG seed 17, local test harness, concurrent machine load:

- Scanner features + signals: 25 symbols × 400 bars, 5 repeats; median
  **483.91 ms**, maximum **923.17 ms**.
- Queue lookup: 1,000 synthetic rows, 10 repeats; median **17.36 ms**,
  maximum **31.03 ms**. JSON remains a linear scan.
- Staging Vite build: **15.80 s**; largest terminal chunk about 353 kB,
  96.7 kB gzip. Plugin timing advisory disclosed.

These are small synthetic samples, not production SLOs or before/after
improvement claims. No speculative speed tuning or trading threshold changes.
Cold memory access needs profiling with intended runtime/data before optimisation.

Read-only process inspection identified the visible training terminal as the
scheduled TradingIntelligenceSystem primary-checkout main.py run started at
19:49:51 Europe/London. This mission did not start, stop or alter that job.
