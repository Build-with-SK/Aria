# Test results — 2026-10-17

Worktree: codex/aria-final-verification. `$PY` is the primary-checkout
venv/Scripts/python.exe. `$CI` is a separate temporary aria-final-ci venv;
`$TOOLS` is a separate temporary aria-final-verification-tools venv. No packages
were installed into the shared primary environment. Commands below omit only
the machine-specific interpreter prefix and output redirection.

| Command / phase | Actual result |
| --- | --- |
| `$PY -m pytest -q --durations=20`, baseline | 3,296 passed, 1 failed, 9 skipped; 338.44 s; exit 1 (shared universe fixture) |
| First eight new reliability probes | 8 failed, exit 1 before repairs |
| Corrected pending-entry/future-quote probes | 2 failed; 3.69 s; exit 1 before repairs |
| `$PY -m pytest tests/test_order_settlement.py tests/test_order_safety_review.py -q` | 63 passed; 19.54 s; exit 0 |
| Execution/reduce-only/live-gate/IBKR/risk focused set | 274 passed; 44.12 s; exit 0 |
| Exit/journal/desk focused set | 191 passed; 20.21 s; exit 0 |
| First expanded full suite | 3,383 passed, 6 failed, 9 skipped; 262.99 s; exit 1. Fake broker contract, exact sender inventory and runtime-fixture logging failures repaired |
| Runtime/ownerauth/profile/session/new-probe compatibility set | 167 passed; 16.55 s; exit 0; no assertions disabled |
| Final `$PY -m pytest -q --durations=20` | 3,391 passed, 9 skipped; 335.70 s; exit 0 |
| Clean venv: `pip install -r requirements-ci.txt`; `$CI -m pytest -q --durations=10` | Initial collection failed on sentiment yfinance import; first rerun found the same global import in data_downloader metadata. Both repaired; the intermediate full run was 3,313 passed, 1 failed, 13 skipped in 634.52 s. The 47 focused minimal-environment import/data tests then passed in 6.59 s. Final full run: 3,314 passed, 13 skipped; 626.25 s; exit 0 (implementation 048f47b) |
| `ARIA_LAUNCH_SWEEP=1`, `SWEEP_OUT=...`, `$PY -m pytest tests/test_launch_get_sweep.py -q` | 3 passed; 72.02 s; exit 0; 158 routes, one explicitly allowed cold-memory timeout |
| `ARIA_PROFILE_OUT=...`, `$PY -m pytest tests/test_final_profile.py -q` | 1 passed; 6.51 s; exit 0; measurements in verification record |
| `$PY -m compileall -q src backend scripts main.py` | exit 0 |
| `$TOOLS -m ruff check src backend main.py scripts tests --select E9,F63,F7,F82 --output-format concise` | pass after chat-handler repair |
| `$TOOLS -m mypy --check-untyped-defs --follow-imports=skip --ignore-missing-imports` on approval_queue/order_manager/alpaca_broker/ibkr_broker/pretrade/account | pass, 6 source files; imported dependencies skipped, no global type-safety claim |
| `$PY scripts/check_protected_paths.py --require-git` | exit 0; reachable-history fence clean |
| `git check-ignore data/execution/approval_queue.lock data/execution/approval_queue.audit.jsonl data/trading/order_journal.lock` | all three excluded; private runtime artifacts cannot be accidentally staged |
| `$PY -m pip check` | exit 0; no broken requirements, not a vulnerability scan |
| `$TOOLS -m pip_audit --path <primary-venv>/Lib/site-packages --format json` | exit 1; 119 advisories across 14 installed distributions |
| `$TOOLS -m bandit -r src backend main.py scripts -ll -f json` | exit 1; 58 medium/high pattern findings; applicability triage outstanding |
| Reachable Git blob credential-signature scan | 1638 source blobs at 048f47b, zero bounded token/private-key signature matches; .env/research_keys.json not tracked. Not exhaustive detection |
| `npm ci --ignore-scripts` | exit 0; no new direct package |
| `npm audit fix --ignore-scripts` | compatible lock updates only; 12 → 9 advisories, exit 1 because findings remain |
| `npm audit --json` / `npm audit --omit=dev --json` | 9 total (5 high, 4 moderate) / 2 moderate production |
| `node scripts/verify-execution-status.mjs` | exit 0; 7 actual component SSR states |
| `node scripts/verify-launch-routes.mjs` | exit 0; 48 route, 5 Research, 11 freshness states, unknown desk toggle |
| `node scripts/verify-technical-dashboard.mjs` | exit 0; full/withheld/error/exact-contract cases |
| `npm run build -- --outDir ../verification-frontend-dist` | exit 0; 15.80 s; staging only |
| `node scripts/verify-shell.mjs ../verification-frontend-dist` | exit 0; cache scope, auth/API bypass, offline navigation and built URLs |

Both Python environments run Python 3.11.8 on Windows; the resolved clean dependency inventory is saved with the evidence. Linux/Python 3.12 remote Actions is
not claimed. Real external orders, deployed runtime and browser hydration remain
outside these counts; advisory and memory-latency gaps are disclosed separately.

Import-location fixes were completed after the last primary full collection;
47 persistence/sentiment/data regressions passed on final source in the primary
environment (8.13 s) and clean CI environment (6.59 s). The final clean full run
tests implementation commit `048f47b`. No Linux/Python 3.12 result is implied.
