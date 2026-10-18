# ARIA final operational test evidence — 2026-10-18

Source under test: **edfb5e6** on `codex/aria-operational-readiness`, based on `edc2796`. Later commits contain evidence/procedures only. Candidate source and synthetic data were used; the primary checkout, deployed services, production queues, broker account and real ML dataset were not operated on.

## Current results

| Check | Result |
| --- | --- |
| Windows full suite, private Python 3.14.8 | **3,543 passed, 12 skipped, 2,169 warnings, exit 0, 461.31s** |
| Ubuntu WSL full suite, private Python 3.14.4 | **3,545 passed, 10 skipped, 2,174 warnings, exit 0, 167.20s** |
| Focused XML/DNS/ML/research/backup | 113 passed, exit 0, 39.75s |
| Backup/WAL/traversal + security/ML timestamps | 28 passed, exit 0, 5.91s |
| Bandit baseline | 49: 4 high / 45 medium; exit 1 |
| Bandit after fixes | 43: 4 high / 39 medium; exit 1; zero scan errors; **FAIL retained** |
| Source review inventory | All 49 original findings: 7 confirmed, 23 acceptable documented risks, 10 false positives, 9 require further validation |
| Ruff E9,F63,F7,F82 | PASS, src/backend/scripts/tests |
| Six-module mypy | PASS, check-untyped-defs; imports skipped, missing imports ignored (not whole-repository typing) |
| compileall and protected-history fence | PASS |
| Windows base pip check / pip-audit | Clean / 101 distributions, zero vulnerabilities, exit 0 |
| npm ci / audit | Exact lock install, ignore-scripts; zero advisories, exit 0 |
| Staged frontend build | PASS, private outDir; no deployed assets changed |
| Frontend route verifier | PASS: 48 route states, 5 Research and 11 freshness cases |
| Technical/execution/shell verifiers | PASS: technical dashboard, 7 execution states plus 3 queue notices, PWA shell/cache contracts |
| Opt-in GET sweep | **3 passed**, 57.45s, exit 0; 158 routes: 139x200, 13x422, 2x400, 4x404; zero timeout/exception/5xx |
| External broker/history/paper orders | BLOCKED, no broker calls made |
| Actual production startup/cutover | BLOCKED, no service restart or production merge |

The new test inventory adds **19 cases**: 13 XML/DNS/timestamp tests and 6 backup/WAL/Windows-archive tests. No assertion or test was removed to obtain a pass. Two existing research fixtures now inject the public transport explicitly instead of patching the old global URL opener. No Bandit suppression was added.

## Operational coverage

| Requirement | Fixture evidence |
| --- | --- |
| Startup/shutdown, forwarding proxy and streaming health | test_final_runtime.py; test_integrated_web_acceptance.py with staged dist; test_readiness.py |
| Process supervision primitives | test_durable_state.py cross-process/dead-holder tests; test_desk_single_instance.py; test_backend_reliability.py late-model start and single backend rules |
| Database backup/recovery | test_backup.py: SQLite backup API, live WAL fixture, integrity_check, restored row/UNKNOWN queue; snapshot-failure gate; restore path checks |
| ML interruption/resume | test_ml_durable.py: actual hard-exit at 55 ticks leaves first 50, batch 25, resume/force/age/write-failure/concurrency; test_operational_security.py freshness anomalies |
| Stale signal rejection | test_signal_freshness_gate.py; test_session_bypass.py; quote age/risk cases |
| Broker disconnection / UNKNOWN | test_snapshot_positions_unreadable.py; test_final_execution_reliability.py; test_order_safety_review.py; persistent quarantine/recovery fixtures |
| Risk / approvals / live disabled | test_trading_modes.py; test_trading_access.py; test_portfolio_risk.py; security_live_gate / reduce_only / kill_switch / approval tests |
| UI/API consistency | Built React state verifiers, real ASGI/proxy/auth/SSE fixture; fake broker only |
| Logs/alerts/health | test_log_isolation.py, test_ownerauth.py redaction, test_readiness.py, test_reflex_observability.py and desk fail-closed cases |

External delivery of phone alerts, actual broker fill/fee behavior, installed OS supervisor uptime and hosted TLS/browser deployment remain BLOCKED. Existing real-browser results from the integrated candidate are historical, not rerun here. Local tests leave live disabled and fake all financial mutations; deployed process environment was not certified.

## Reproduction

Archived evidence directory: `C:/Users/sound/.codex/visualizations/2026/10/18/01a14d8c-22c6-7470-a62c-77e07562041b/aria-operational-evidence` (private, outside Git); working outputs also remain in `C:/Users/sound/AppData/Local/Temp/aria-operational-evidence`. It holds complete outputs, raw baseline/after scans, pip/npm JSON, staged build, source tar and Linux stage/result. Files under docs/security contain the shareable finding classifications only.

Windows uses the previously prepared private `aria-final-integration-venv` (Python 3.14.8, Expat 2.8.5, OpenSSL 3.5.9); tooling uses `aria-final-verification-tools`. Neither the production venv nor system interpreter was upgraded. Linux uses a fresh `git archive edfb5e6` source snapshot under a private cache directory and the previously prepared Linux app/test venv. A synthetic local Git commit (`Candidate Verification`) lets Git-dependent tests execute. This does not alter candidate Git history. Linux distro security backports remain unverified.

```powershell
# From the isolated candidate; $candidatePython and $toolPython point to private venvs.
$env:ARIA_ACCEPTANCE_DIST = '<private staged frontend-dist>'
$env:ARIA_SECURITY_AUDIT_OUT = '<private audit JSON>'
& $candidatePython -m pytest tests -q -p no:randomly -ra
& $toolPython -m bandit -r src backend main.py -ll -f json
& $toolPython -m ruff check src backend scripts tests --select E9,F63,F7,F82
& $toolPython -m mypy --follow-imports skip --ignore-missing-imports --check-untyped-defs src/execution/approval_queue.py src/execution/order_manager.py src/execution/alpaca_broker.py src/execution/ibkr_broker.py src/risk/pretrade.py src/trading/account.py
& $candidatePython -m compileall -q src backend scripts main.py
& $candidatePython scripts/check_protected_paths.py --require-git
& $candidatePython -m pip check
& $toolPython -m pip_audit --path '<candidate site-packages>' --format json
$env:ARIA_LAUNCH_SWEEP = '1'
$env:SWEEP_OUT = '<private sweep JSON>'
& $candidatePython -m pytest tests/test_launch_get_sweep.py -q -p no:randomly
```

Frontend: `npm ci --ignore-scripts`, all four `scripts/verify-*.mjs` verifiers (shell takes the private build directory), `npm run build -- --outDir <private-directory>` and `npm audit --json`. Linux runs the full pytest command against the archived source with ARIA_ACCEPTANCE_DIST translated to its /mnt/c path. No privileged installation, OS service change or external paper action is needed for these commands.

Exact skips and warnings remain in full logs. Skips cover opt-in profile/sweep, unavailable production reference data, and platform/optional modules. Deprecation warnings are not hidden: existing datetime.utcnow, websockets/Starlette test-client and eventkit asyncio-policy deprecations dominate; counts are retained above. The optional GET sweep is run separately; optional memory profiling was not repeated because no memory code changed. Prior Windows 3,524/Linux 3,526 reports are retained as baseline history, not substituted for current runs.
