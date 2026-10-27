# ARIA Phase 2 — Claude integration handoff

**Recommendation: BLOCKED for release.** Local implementation and validation
gates below pass; historical broker evidence, deployment security/runtime work
and independent integration review remain. No merge, push, deploy, production
restart, live enablement or external order mutation was performed.

## 1. Branch, commits and provenance

- Branch: `codex/aria-final-verification`; Phase 2 base `2a4be8f` (verified clean).
- Verified implementation tip: `a5e4043`; backend Python implementation through
  `9862967`. Later changes are documentation only.
- Report anchor: `f42e73e` (HEAD immediately before this handoff-only commit).
  Resolve the handoff commit itself with
  `git log -1 --format=%H -- docs/ARIA_PHASE2_HANDOFF.md`.
- Claude branch inspected read-only at `659a75c`; integration branch still
  `b5b47f2`. Claude's reported suite result is not substituted for our runs.
- Original `codex/work` remains dirty in exactly the same three S2.2 paths:
  evaluation.py, its new ledger-source test and Codex's original log. Not moved,
  reset, committed or overwritten. No merge from Claude was made in Phase 2.

| Commit | Reviewable purpose |
| --- | --- |
| a9a6f1d | Dependency floors, remove unused Tailwind, patched router/lock |
| 2dd1f7c | Legacy quarantine/recovery, monotonic fills, owner evidence binding |
| 2d44157 | Explicitly classify new pure broker lookup as READ |
| 7dde78a | Retain Plotly/Anthropic majors; clean candidate audit evidence |
| b7b789c | Lazy single-flight embedding model; cold listing fix/profile |
| ce54441 | POSIX runtime tests and clean Linux CI |
| 9862967 | Python 3.14 legacy SDK loop setup; scoped scheduler fixture |
| a5e4043 | Queue failure/staleness handling, unresolved notice, real-browser fixture |
| 043b213 | Linux and owner-approved read-only paper broker report |
| f42e73e | Complete regression report and remaining interpreter security risk |

## 2. Task verdicts

| Task | Verdict and limits |
| --- | --- |
| 1 Dependency security | Candidate PASS: npm 0, fresh Windows/Linux pip-audit 0. Existing deployed optional-heavy env and old Windows interpreter remain BLOCKED for remediation. |
| 2 Legacy reconciliation | Local implementation/tests PASS. Actual historical rows with missing/unmatched broker evidence remain BLOCKED; no production migration/release was run. |
| 3 Cold memory | PASS for reproduced /memories listing timeout: 76.2556s to 4.2950s; no timeout increase. First semantic-model load remains a separate expensive operation. |
| 4 Linux | PASS on real Ubuntu WSL2 with fresh source/venv; full suite and actual process restart pass. Prepared 3.12/3.14 CI not pushed/executed remotely. |
| 5 Browser | PASS for actual built React app in real Chromium against read-only synthetic APIs. Deployed host/auth/proxy/PWA/broker acceptance remains separate. |
| 6 External paper broker | Approved Alpaca account/history/positive identifier reads PASS. Historical local reconciliation and IBKR gateway validation BLOCKED by insufficient evidence/infrastructure. No order permission assumed. |
| 7 Full regression | PASS in fresh Windows and Linux environments; source scan retains documented legacy patterns, and interpreter security remains a release blocker. |
| 8 Integration preparation | Complete reports, exact manifest, small commits and integration order. Claude owns review/integration/final release. |

## 3. Defects fixed

Historical no-key approved/rejected/cancelled/executed/interrupted rows no longer
silently authorize another send. First access quarantines them with the entire
original row and append-only audit intact. Truly unsent pending proposals remain
usable. New proposals record durable protocol version 1. Lookup by persisted
Alpaca broker id requires matching identity, symbol, side and quantity; missing,
conflicting, duplicate or unavailable evidence never clears uncertainty. Bare
IBKR session IDs remain manual. An explicit owner/evidence-note method can bind
an original identifier while leaving UNKNOWN until broker confirmation.

Cumulative fills and terminal evidence cannot roll backwards on duplicate/stale
callbacks. Invalid fills and changed identities fail closed. No lookup resubmits.
Crash/restart and partial/delayed-ack tests exercise those properties.

Memory listing no longer imports/allocates the embedding stack. Actual semantic
operations share a locked process-wide model load; failed initialization can
retry. API listing is still usable without the model. The GET sweep now rejects
a listing timeout rather than allowing it.

Python 3.14's removal of implicit event-loop creation no longer breaks the
installed IBKR/eventkit SDK import. Existing/running loops are preserved; only
new owned loops are closed at process exit. A scheduler test now stubs its own
module's platform instead of changing global os.name and breaking Linux paths.

Browser verification found ignored queue errors: a failed initial fetch appeared
empty, and stale records retained approval controls. The UI now reports failed
reads, removes stale approval actions, retains cached records and exposes
unresolved broker orders from the default queue view.

## 4. Tests and measurements

| Environment/check | Result |
| --- | --- |
| Fresh Windows full app/test venv, Python 3.11.8 | **3417 passed, 12 skipped, 323.43s, exit 0** |
| Fresh Ubuntu WSL full app/test venv, Python 3.14.4 | **3408 passed, 21 skipped, 121.39s, exit 0** |
| Existing Windows environment, execution/legacy/reduce-only | 157 passed |
| Upgraded Windows targeted memory/security/runtime/calendar | 154 passed |
| Linux process/restart/database/config/Unicode/permissions | 25 passed; portability/SDK/scheduler follow-up 58 passed |
| Existing Windows environment, 158 GET routes | 139x200, 13x422, 2x400, 4x404; no timeout/exception/5xx; 3 tests passed |
| Cold listing instrumented fresh processes | 76.2556s → 4.2950s; warm 1.5195s → 0.0403s; Python tracked peak 248.2 → 42.3MiB |
| Memory listing in final GET sweep | 200 in 2.8s |
| Security/API/static | anonymous audit231 routes clean; Ruff fatal checks, six-module mypy, compileall and protected history fence clean |
| Frontend | actual browser workflows; route/Research/freshness, technical, execution7+notices3, shell and staging build pass |

Exact commands, failures that prompted repairs, skip/scope explanations and
evidence limits: `ARIA_PHASE2_TEST_RESULTS.md`. No tests were disabled or
assertions weakened. Do not pool these counts with another branch or call this
a new full run of the unchanged optional-heavy production venv.

## 5. Security findings

Npm affected nodes reduced 9 to 0. Tailwind was unused; removing its build chain
left generated CSS identical before the router change. Router 7.18.4 is the one
necessary major upgrade because no patched 6.x release is reported. Real client
navigation and the frontend checks pass; review what ships before integrating.
Compatible Python floors are included by all manifests. Fresh audits: Windows
104 distributions/0 findings; Linux100/0; pip check clean. Plotly/Anthropic major
lines and the exchange-calendar pin are retained.

The old application venv still has the baseline package findings, including
optional Chroma server-mode vulnerabilities with no reported fixed version,
OAuth and Torch consumers outside the clean base install. Restrictions and
package-by-package triage are in `ARIA_DEPENDENCY_SECURITY.md`; no blanket waiver.

**Windows runtime blocker:** Python 3.11.8 bundles Expat 2.5.0 (OpenSSL 3.0.13).
The application parses external XML/RSS; Python documents XML exhaustion risk
below Expat 2.7.2. A clean pip audit does not repair bundled runtime libraries.
The deployment owner must upgrade the interpreter, verify actual library
versions, recreate the environment and rerun checks. Tested Linux uses Expat
2.7.4/OpenSSL3.5.5. No production runtime upgrade was attempted.

Bandit exits 1 with 48 medium/high patterns, no new normalized pattern in the
scanned baseline inventory. Four high SHA1 sites are non-authentication identity
keys; other URL/SQL/XML/local-pickle sites retain scoped review requirements.
This is not a clean source-security certification. Changed tracked paths show no
configured-secret/bounded-signature match; private broker/account data was not
added to Git. See the dependency report for assessment and constraints.

## 6. Linux, browser and broker evidence

Linux executed in a non-root WSL Ubuntu 26.04 user session, with clean source
and venv. Real child servers served health/readiness and preserved UNKNOWN
across two SIGTERM shutdown/restarts. Database initialization, configuration,
Unicode paths, permission failures and POSIX locks ran. No system package or
service changed. CI is reproducible but not claimed remotely executed.

The real browser exercised dashboard, scanner/filter, approval display, UNKNOWN,
risk rejection/history, health/queue/scanner failures, polling recovery and
refresh. Its local fixture origin has no broker/backend imports and refuses
mutations; the request log is GET-only. No approve/cancel/trading button clicked.
DOM snapshots/screenshots are private evidence. This verifies browser behavior
without claiming production cookies, SSE/proxy or installed PWA cache upgrades.

Owner approved read-only Alpaca PAPER use before credential use. HTTPS requests
were restricted to the approved hostname, GET and no redirects. Account ACTIVE,
unblocked; history readable. Candidate adapter broker-id and original client-id
queries agree on a real historical fill. Local historical references in the
bounded sample are absent or lack identifiers, and persisted ids did not match
retrieved history. **404 is not proof of rejection.** No local queue was changed.
No new order, cancellation, replacement, real-money call or IBKR connection.
See `ARIA_PAPER_BROKER_VALIDATION.md`; exact account/trade details stay outside Git.

## 7. Remaining blockers and integration order

1. Review this Phase 2 range separately from already integrated Phase 1 work.
   Claude has merged/reworked earlier Codex changes. Do not reapply their copied
   S1.3 commits or replace whole files with this branch's older architecture.
2. Review `a9a6f1d` + `7dde78a` dependency changes, including the router major,
   against Claude's current frontend. Run verifiers/build/full suite on the
   integrated candidate. No shipped assets were replaced here.
3. Integrate `2dd1f7c` + `2d44157` legacy logic/tests deliberately with Claude's
   durability/backup and newer execution fixes. Retain R1 terminal bracket-claim
   checks, R2 typed definite OCO refusal (`OrderRefused`), and R3 unreadable
   position snapshots failing closed. This branch is not a replacement for them.
4. Integrate `9862967` loop compatibility/fixture repair, `b7b789c` lazy memory
   and stricter GET check, then `a5e4043` queue UI/fixture checks. Retest concurrency,
   claim ownership, late/partial outcomes, broker read errors and frontend status.
5. Add POSIX/CI verification from `ce54441`; run clean 3.12/3.14 jobs and all
   existing Claude tests after integration. Prepared workflow alone is no pass.
6. Before any production cutover, prepare/review an owner-approved state backup,
   interpreter/dependency upgrade and historical reconciliation plan. Migration
   is intentionally conservative and will quarantine old rows beyond a single
   legacy rejected example. Preserve audit/preimages and keep unsafe re-arming
   blocked. Obtain original account/environment/order statements; never mass-
   release missing/404 references. No primary migration was performed here.
7. Separate approval is still needed for controlled new paper-order acceptance
   scenarios: accepted/lost-ack/partial/restart/fill/fee reconciliation. Cumulative
   partial-exit P&L/fee accounting and IBKR gateway/permanent identity remain
   unproven externally. Do not infer permission from read-only approval.
8. Complete deployed-host browser/auth/proxy/PWA and supervision acceptance;
   Claude makes the final independent release decision. Keep release BLOCKED
   until these gates and the documented runtime/security exceptions are resolved.

## 8. Overlaps requiring Claude's review

Execution: approval_queue/order_manager/alpaca_broker/ibkr_broker/live_guard,
their schema and tests overlap S1.3 and subsequent reliability/durability work.
The new read-method classification must survive wrapper composition. Migration
must preserve Claude's backup and launch invariants; empty broker reads remain
errors/uncertain, never proof permitting a retry.

UI: ExecutionStatus, Execution, execution verifier and dependency lock overlap
the completion branch. Memory/GET-sweep, scheduler fixture and new CI should be
reviewed alongside current readiness/launch checks. No WORK_PLAN, Claude log,
Claude worktree, architecture file or integration branch was edited here.

## 9. Exact Phase 2 file manifest

Relative to `2a4be8f`, including this handoff commit; D = removed unused file.

```text
.github/workflows/linux-runtime.yml
backend/requirements_backend.txt
constraints-security.txt
docs/ARIA_BROWSER_VALIDATION.md
docs/ARIA_COLD_MEMORY.md
docs/ARIA_DEPENDENCY_SECURITY.md
docs/ARIA_LEGACY_RECONCILIATION.md
docs/ARIA_LINUX_VALIDATION.md
docs/ARIA_PAPER_BROKER_VALIDATION.md
docs/ARIA_PHASE2_HANDOFF.md
docs/ARIA_PHASE2_TEST_RESULTS.md
docs/collab/log-codex.md
frontend/package-lock.json
frontend/package.json
frontend/postcss.config.js
frontend/scripts/verify-execution-status.mjs
frontend/src/components/ExecutionStatus.jsx
frontend/src/pages/Execution.jsx
D frontend/tailwind.config.js
requirements-ci.txt
requirements.txt
scripts/serve_browser_fixtures.py
src/brain/cognitive/long_term_memory.py
src/execution/alpaca_broker.py
src/execution/approval_queue.py
src/execution/ibkr_broker.py
src/execution/live_guard.py
src/execution/order_manager.py
tests/test_cold_memory_profile.py
tests/test_data_freshness_api.py
tests/test_ibkr_loop_compat.py
tests/test_launch_get_sweep.py
tests/test_legacy_reconciliation.py
tests/test_memory_lazy_loading.py
tests/test_posix_runtime.py
```

Private external evidence is retained in this chat's `aria-phase2` directory:
full test logs, package freezes/audits, cold profiles, browser DOM/screenshots,
GET sweep and redacted paper-read reports. No personal vault, .env or real
execution snapshot was copied into the public source tree.
