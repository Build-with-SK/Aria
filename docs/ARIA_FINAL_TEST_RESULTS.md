# ARIA final integrated test results

Runtime implementation: **89f8f4a**; final test inventory: **a079dff** on `codex/aria-final-integration`. Later commits contain reports and nonsemantic trailing-blank cleanup in test_reduce_only.py (AST unchanged). All final tests use frozen source and isolated state; primary training/backend/services and broker state are untouched.

## Final results

| Check | Actual result |
| --- | --- |
| Windows fresh app/test venv; CPython 3.14.8 | **3,524 passed,12 skipped,270.66s,exit0** |
| Ubuntu WSL fresh source/app/test venv; CPython 3.14.4 | **3,526 passed,10 skipped,153.35s,exit0**; clean source includes a local Git snapshot so fence/selfmod tests execute. |
| Fresh Windows/Linux pip-audit | 101/100 distributions, zero findings; pip check clean |
| npm audit | zero advisories |
| Full158 GET sweep | 3 passed,40.02s:139×200,13×422,2×400,4×404; no timeout/exception/5xx. Listing200/0.01s in base env (optional memory unprovisioned). |
| Existing anonymous API audit | 231 routes, no failures/warnings; final full-suite artifact |
| Ruff E9,F63,F7,F82 | PASS on src/backend/scripts/tests |
| Six-module mypy, check-untyped-defs, skip imports | PASS: approval_queue/order_manager/alpaca_broker/ibkr_broker/pretrade/account |
| compileall | PASS: src/backend/scripts/main.py |
| Protected-history fence and diff whitespace | PASS |
| Bandit medium/high scan | FAIL (exit1):49 assessed patterns,4 high/45 medium; scoped assessment in security report; no suppressions |
| Frontend production build | PASS; Vite8.2.0, staged output; no deployment |
| Offline frontend verifiers | PASS:48 route states,5 Research,11 freshness; technical dashboard;7 execution states+3 notices; shell/service-worker contracts |
| Actual Chromium UI | PASS: dashboard, scanner BEAR1/2, approval display, UNKNOWN, risk rejection, actual-fill history, initial/cached queue failures, health/scanner failure and recovery |
| Actual Chromium PWA | PASS: old shell worker/cache → current worker/cache; old cache removed, API network-only and uncached |
| Backend/frontend/proxy/auth/SSE | PASS in final full suites; real ASGI listener and local forwarding server, anonymous401, signed HttpOnly session, built shell/assets, first SSE ack before releasing model fixture |
| Cold-memory integrated remeasurement | First listing4.6048s, warm0.0318s; Python tracked peak42.84MiB; no embedding imports/error. Existing optional-heavy3.11.8 venv, isolated Chroma, network refused; not a patched optional-stack security certification |
| Approved Alpaca PAPER reads | Five GETs, active/unblocked account, history, broker/client-id agreement and positive integrated legacy recovery in disposable local queue; zero venue/production-state writes |

## Commands

From candidate root; `candidatePython` denotes the private3.14.8 venv, `toolPython` the isolated audit/static tooling venv. Artifacts/dist stay outside tracked production assets.

```powershell
& $candidatePython -m pip install --upgrade pip setuptools
& $candidatePython -m pip install -r requirements.txt -r requirements-ci.txt
& $candidatePython -m pip check
$env:ARIA_ACCEPTANCE_DIST='<private staged dist>'
$env:ARIA_SECURITY_AUDIT_OUT='<private audit JSON>'
& $candidatePython -m pytest tests -q -p no:randomly -ra
$env:ARIA_LAUNCH_SWEEP='1'
$env:SWEEP_OUT='<private sweep JSON>'
& $candidatePython -m pytest tests/test_launch_get_sweep.py -q -p no:randomly
& $toolPython -m pip_audit --path '<candidate site-packages>' --format json
& $toolPython -m ruff check src backend scripts tests --select E9,F63,F7,F82
& $toolPython -m mypy --follow-imports skip --ignore-missing-imports --check-untyped-defs src/execution/approval_queue.py src/execution/order_manager.py src/execution/alpaca_broker.py src/execution/ibkr_broker.py src/risk/pretrade.py src/trading/account.py
& $candidatePython -m compileall -q src backend scripts main.py
& $candidatePython scripts/check_protected_paths.py --require-git
& $toolPython -m bandit -r src backend main.py -ll -f json
```

Frontend directory: `npm ci --ignore-scripts`; `node scripts/verify-launch-routes.mjs`; `node scripts/verify-technical-dashboard.mjs`; `node scripts/verify-execution-status.mjs`; `npm run build -- --outDir <private-dist>`; `node scripts/verify-shell.mjs`; `npm audit --json`.

Linux: export tracked source using `git archive a079dff`; extract to a fresh user-cache source directory, create a minimal local Git snapshot under the synthetic author Candidate Verification so Git-dependent tests also execute. Create fresh venv (`--without-pip` plus official PyPA bootstrap where distro ensurepip is absent), install both manifests, pip check, then `ARIA_ACCEPTANCE_DIST=<staged-build> <venv>/bin/python -m pytest tests -q -p no:randomly -ra`. The Linux local snapshot is a test fixture, not a commit in the candidate's history. Real candidate history fence was checked in Windows. No sudo/apt/system service changes. Prepared CI 3.12/3.14 also builds frontend before full proxy acceptance; remote Actions not executed.

Browser: `scripts/serve_browser_fixtures.py --dist <staged-build> --control <private-JSON> --log <private-JSONL>`, ephemeral loopback, actual React effects/router. All mutating methods405. Inspect pending/UNKNOWN/history, inject queue/signals/health errors, restore, check polling/reload. PWA page `/app/acceptance-pwa.html`: control pwa_old=true → Prepare old cache, then false → Check upgrade. Real browser reports old cache absent, current cache present, API network and no cached API. Synthetic request journals were GET-only (592 critical-UI/13 PWA at checkpoint). Temporary tabs/listeners closed afterward. No approval/trading button clicked.

## Failures, repairs and limits

- Initial combined parent run:198 passed/1 failed; legacy IBKR status fallback bypass fixed. Follow-up88 and176 focused passes retain R1/R2/R3.
- Identity/terminal recovery follow-up113 passes. Later Claude ML integration101 passes. Ordered browser/security recheck159 passes. Final durability/recovery review101 passes.
- A provisional Windows full run finished3486 passes/7 failures/12 skips while source was being integrated. Source-inspection line mappings became stale; the new proxy fixture also leaked process-persistent proxy detection. Fixture now copies/restores that state. No test/assertion was removed to mask these errors.
- Provisional Linux passes3483 (before later ML) and3512 (before final recovery hardening) are superseded. Intermediate later Linux3507/2 failures was the same proxy fixture leak. The next Windows run was intentionally interrupted for second-review repairs and is not a final result.
- The first Git-backed Linux run exposed an older assertion expecting corrupt pending cancels to read as empty; strengthened it to require StateUnreadable, unchanged original and quarantine. The synthetic snapshot author ARIA Verification correctly triggered the protected-author fence; renamed the temporary fixture author, without modifying the fence.
- New anonymous-audit test initially used the wrong result key; corrected to existing `fails`, retaining zero-warning assertion. Static typing found mixed inferred row-error types; replaced string-based rejection with an explicit invalid-evidence flag.
- Final second-review repair strengthens the old damaged-position test to expect StateUnreadable, keeps quarantine and explicit-repair assertions, adds zero protection-change checks and failed-fsync preservation. No failing test disabled.
- Current cold measurement uses the existing optional package stack solely for isolated profiling. Historical before76.2556s/after4.2950s pair is in ARIA_COLD_MEMORY; the new4.6048s measurement is not a newly controlled before/after experiment. Native RSS/model provisioning/large-store scaling remain outside it.
- Final skips are opt-in profile/sweep (run separately), absent production reference data, POSIX-only checks on Windows and optional/platform modules. They do not authorize missing provider/venue acceptance. Full logs retain exact reasons; no skip is called a pass.

Private evidence directory `aria-final-integration` attached to this chat contains full logs, exact freezes/audits/runtime versions, parent diffs, profiles, redacted paper-read result and browser DOM/JPEG. No account/trade identifiers or secrets are published. Package audits exclude optional heavy deployment stacks. Remaining external/security gates are explicit in ARIA_REMAINING_BLOCKERS.
