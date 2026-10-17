# Phase 2 verification evidence — 2026-10-17

Implementation under test: a5e4043 (runtime Python logic through 9862967).
Subsequent commits add documentation only. All runs use isolated state; no
assertions/test cases were disabled to obtain a pass.

| Check | Result |
| --- | --- |
| Fresh full application + test venv, Windows Python 3.11.8 | **3417 passed, 12 skipped, 323.43s, exit 0** |
| Fresh full application + test venv, Ubuntu WSL Python 3.14.4 | **3408 passed, 21 skipped, 121.39s, exit 0** |
| Legacy + settlement + independent safety + reliability + reducing exits, existing Windows environment | 157 passed, 21.59s |
| Upgraded environment: legacy/memory/security/live gate/runtime/calendar | 154 passed, 21.54s |
| Linux real process/database/permissions + recovery | 25 passed, 2.91s |
| Linux portability/SDK status/scheduler/real process rerun | 58 passed, 2.93s |
| 158 GET routes in existing optional-heavy Windows env | 3 sweep tests pass, 58.02s; 139x200, 13x422, 2x400, 4x404; **no timeouts, exceptions or 5xx** |
| /api/brain/memories in that sweep | 200 in 2.8s; timeout allowance removed |
| Fresh-process cold memory before/after | 76.2556s → 4.2950s; tracked Python peak 248.2 → 42.3MiB; details in ARIA_COLD_MEMORY |
| npm audit | 0 findings (was 9 affected nodes) |
| Python pip-audit, fresh Windows env | 104 distributions, 0 findings; pip check clean |
| Python pip-audit, fresh Linux env | 100 distributions, 0 findings; pip check clean |
| Existing project security_audit.py against isolated ASGI app | 231 anonymous route probes, no failures/warnings, exit 0 |
| Ruff E9,F63,F7,F82, src/backend/scripts/tests | clean |
| mypy with untyped bodies, six execution/risk/account modules | clean |
| compileall src/backend/scripts/main.py | exit 0 |
| Protected history fence | clean, all reachable history checked |
| Bandit medium/high patterns | 48 patterns (4 high/44 medium), exit 1; no new pattern in scanned baseline inventory; triage/runtime blocker in dependency report |
| Changed tracked file secret check | 33 paths checked, no configured-secret/signature match; bounded scope |
| Actual browser | PASS: dashboard, scanner/filter, approvals display, UNKNOWN, risk/history, failures/reconnect/refresh; synthetic GET-only API, details in ARIA_BROWSER_VALIDATION |
| Frontend checks | route/Research/freshness, technical dashboard, execution receipts7 + notices3, shell/offline checks and staging build pass |
| Approved external Alpaca PAPER | account/history GETs pass; actual adapter broker/client-id reads agree on a historical fill; unresolved local references remain BLOCKED |

Windows skips include POSIX-only process/permission checks and opt-in profiling;
Linux has different optional/platform dependencies. Read the actual skip reasons
when reproducing; skipped external/provider scenarios are not claimed verified.
The fresh Windows env installed broker SDKs; it is not the smaller Phase 1 CI
dependency environment. The existing optional-heavy Windows environment was
used for targeted compatibility, cold profiling and the GET sweep, not claimed
as another Phase 2 full-suite run.

## Failures that led to repairs

- First legacy run: 150 passed, two test-fixture failures caused by argument
  names shadowing raw evidence fields. Corrected fixture parameters; assertions
  retained. Final expanded run 157 passed.
- First upgraded focused run: 153 passed, one failure because new broker-ID
  read was absent from the explicit adapter method inventory. Classified that
  pure read; write/approval gates unchanged. Final focused run 154 passed.
- First Linux full run: 3367 passed, 37 failures, three errors, 21 skipped.
  SDK implicit-loop assumption failed on Python 3.14; scheduler fixture mutated
  global os.name. Repaired both; complete rerun passes as above.
- New POSIX process test initially expected exit 0. Installed Uvicorn source
  restores/re-raises SIGTERM after graceful shutdown. Corrected that new test
  to assert signal exit **and** a completed lifespan shutdown for each process.
- A preliminary Linux run overlapped unfinished package installation; missing
  imports there are setup failures, not the final Linux runtime verdict.
- Browser malformed successful pricing fixture reached the error boundary;
  corrected fixture to explicit unavailable/503 and repeated final workflows.

## Limits and evidence location

Private machine-readable logs, freezes, JSON audits, browser DOM/screenshots and
redacted broker-read reports are stored in this chat's external `aria-phase2`
evidence directory. They are deliberately not committed with account/host data.
Public reports contain reproducible commands and aggregate verdicts.

Zero package advisories and passing suites do not resolve the old Windows
interpreter's Expat risk, optional-heavy deployment package exceptions, missing
historical broker evidence, real new-order acceptance/fees, or integrated-host
review. CI jobs are prepared, not claimed as remote GitHub runs. No deployment,
merge, push, production restart, live enablement or broker mutation occurred.
