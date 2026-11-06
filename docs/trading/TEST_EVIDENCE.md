# Test evidence

## Baseline — revision 58f5f25

Windows Python 3.11.8, pytest 9.1.1, Pydantic 2.13.4; shared existing venv,
isolated worktree `aria-multiasset-trading`, no production server started.

`..\trading-intelligence-system\venv\Scripts\python.exe -m pytest -q --disable-warnings --tb=short`

Actual result: **3321 passed, 10 skipped, 1 failed**, 1 warning, 401.49 seconds.
Failure: `tests/test_stabilization.py::test_the_universe_audit_reports_without_repairing`:
audit saw 29 rows while the initial session fallback contained 16. Earlier tests
had added instruments to the session-wide temporary master. The fixture itself
still contains 16 rows. This is test-order contamination, not a market-data defect.

Correction: audit a newly built fallback database in this test's own `tmp_path`.
Keep exact row count, name/duplicate/identity and byte-for-byte no-write assertions.
`pytest -q tests/test_stabilization.py --disable-warnings --tb=short`:
**22 passed in 1.04s**. The complete final run is recorded below when available.

Existing isolation redirected 283 ARIA modules during baseline; no blocked
production writes reported. Ten skips and the warning remain baseline limitations;
no claim that a skipped external integration passed.

## New functionality in an independent audited environment

Created a separate temporary Python 3.11.8 environment from the 38-package
hash-pinned `requirements-trading-research.lock`; no shared packages changed.
Ran the seven explicit `tests/test_multiasset_*.py` file paths:
**104 passed, 1 warning in 7.06s**, no skips/failures. The warning is Starlette's
httpx TestClient deprecation; real broker SDKs were absent by design.

An earlier attempt using `tests -k multiasset` collected the entire legacy suite
and failed collection because `test_pipeline_sentiment.py` imports yfinance,
which is intentionally absent from the minimal research environment. The explicit
file list fixes the command without weakening tests or adding unused packages.
The full legacy suite is tested separately with the existing full-stack venv.

Focused financial/rule/review/adapter run: **93 passed in 2.86s**. Focused new
features plus existing structural/auth/live-gate regressions: **204 passed,
1 warning in 16.05s** before the final two snapshot-store tests. First full suite
after implementation: **3430 passed, 10 skipped, 1 warning in 294.74s**. A final
full run including the last persistence tests follows below.

## Frontend evidence

Initial build passed against existing installed dependencies. Then installed the
updated lock with `npm ci --ignore-scripts` into a fresh separate temporary frontend
copy. Exact updated-lock build: **passed**, Vite 8.3.6, 703 transformed modules.
All four verifiers passed:

- `node scripts/verify-multiasset.mjs`: loading, disconnected, unavailable, empty,
  unknown schema and measured-zero cases; no order controls or fabricated account.
- `node scripts/verify-launch-routes.mjs`: 48 renders (8 routes × 6 states),
  5 research snapshot states, 11 freshness cases and unknown desk state.
- `node scripts/verify-shell.mjs`: shell assets/cache/API-auth-health bypass.
- `node scripts/verify-technical-dashboard.mjs`: nine horizons, unavailable
  contract, conflicts and review provenance; 8834-byte encoded fixture evidence.

Offline SSR does not test hydration, effects, user actions, remote authentication,
real account data or broker execution. Technical SSR emitted the existing
useLayoutEffect warnings; installation reported Recharts 2 maintenance deprecation.
No new screenshot or real runtime UI claim is made.

## Security / dependencies

- `git diff --check`: passed.
- `scripts/check_protected_paths.py --require-git`: passed over reachable history.
- `pip check` on existing venv: passed (not a vulnerability claim).
- Existing shared-vendor environment audit: 188 advisories in 18 packages.
- Minimal research lock audit: no known vulnerabilities found (registry lookup).
- Full npm audit after fixes: 0 high/critical, 2 moderate router findings.
- Offline current tracked-file secret scan: 23 baseline candidates in 10 unchanged
  files, zero in changed feature code; no candidate values printed.

See DEPENDENCY_AUDIT.md for exact scope, remaining findings and remediation limits.

## Final complete regression run

Final product-code state, existing full-stack test venv:
`python -m pytest -q -rs --tb=short --durations=10`

**3432 passed, 10 skipped, 1 warning in 225.56s. Zero failures.**
291 ARIA modules redirected away from production state; no blocked production
writes reported. The warning is the installed websockets.legacy deprecation.
Timing is observational, not a before/after performance claim.

Actual skip reasons:

| Tests | Count | Reason |
|---|---:|---|
| test_docs_match_code.py | 1 | No production symbol master in checkout |
| test_docs_match_code.py | 1 | No production ledger in checkout |
| test_identity_namespaces.py | 2 | No production ledger in checkout |
| test_identity_safety.py | 1 | No production ledger in checkout |
| test_launch_get_sweep.py | 1 | Optional full GET sweep requires ARIA_LAUNCH_SWEEP=1 |
| test_live_news.py | 1 | BNO absent from fixture symbol master |
| test_migration_freeze.py | 3 | No production ledger in checkout |

No skip was reclassified as a pass. No MT5/IBKR session or actual fill is inferred.
Static AST compatibility check: **all 228 baseline route declarations preserved,
2 added**. Documentation relative-link check passed. Final branch product changes
are listed in CHANGED_FILES.md. No production restart or deployment was performed.
