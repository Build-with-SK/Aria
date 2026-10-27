# ARIA final reliability audit — 2026-10-17

Mission opened 2026-10-17. Integration baseline: `b5b47f2` (verified Git history).
Working branch: `codex/aria-final-verification`, isolated managed worktree.

The owner's final mission expands Codex's remit to backend/execution reliability;
Claude retains architecture, integration, UI coordination and release decisions.
Existing unfinished edits in the original Codex worktree are preserved.

Recommendation: **BLOCKED for release**, pending independent integration review,
dependency remediation and external PAPER broker validation.
No real orders, live enablement, production data writes, merge, push or deployment.

## Repository facts

Read AGENTS, collaboration plan, README, dependency/config files, CI workflows,
test isolation and execution callers; inventoried the project and ran its suite.
This is a project-wide test audit with targeted code review, not a formal proof
of every module. Baseline already contains the sentiment config fix (`a029d90`)
and reflex work through `afde34e`; historical reports were not treated as current.
The S1.3 follow-up remained unmerged. Four Claude code commits were reused with
attribution in this isolated branch; see the handoff. No other agent log edited.

## Confirmed defects repaired

| Priority | Finding | Candidate behavior |
| --- | --- | --- |
| P0 | Approval sends lacked client ids; submit timeout could look rejected | Deterministic id and persistent claim before POST; found-only broker settlement; remote 404 stays UNKNOWN |
| P0 | Raised submit exceptions escaped the manager | Convert send uncertainty to UNKNOWN; claimed trade cannot resend |
| P0 | Concurrent workers could lose/truncate queue state | Thread + OS locks, fsync, atomic replacement, corrupt state fails closed; 3-process/36-proposal regression |
| P0 | Protective OCO bypassed kill switch and ambiguous result invited another stop | Kill switch, durable protection claims/ids, no fallback or blind retry; hidden exits reserve reducing capacity |
| P0 | IBKR timeout declared rejected and reference discarded | Distinguish request construction from send; UNKNOWN on send error; orderRef lookup over open/completed snapshots |
| P0 | Broker read failures returned an empty book | Adapters raise so risk/reduce-only checks refuse to trade blind |
| P0 | Desk exit bypassed durable submission tracking | Reuse queue/manager, re-read reduce-only after claiming, retain unknown exits across restart |
| P1 | Working/terminal partial fills were conflated | Keep quantity/price and terminal broker state separately; reconcile working acknowledgements |
| P1 | Crypto aliases evaded in-flight comparison | Canonical symbol match at the atomic claim boundary |
| P1 | Pending market entries were omitted from risk | Defer new exposure when pending entries/unreadable orders cannot be valued; preserve reducing/OCO grouping |
| P1 | Future quote ages passed freshness | Reject implausibly future quotes/snapshots; explicit five-second skew/fetch tolerance |
| P1 | Non-production OrderEngine forgot crash-before-halt or trusted remote absence | Durable locked journal; infer halt from unsettled claims; corrupt journal blocks; only synchronous simulator can prove absence |
| P1 | Estimated closes could enter outcome memory as realised observations | Exclude estimates; retain existing scorecard exclusion |
| P1 | UI presented accepted orders as fills | Distinct working, partial, filled, submitting and UNKNOWN states; no fabricated fill price |
| P1 | Chat failure handler used out-of-scope exception | Local import; controlled 503 regression |
| P1 | Minimal CI could not load sentiment/pure ticker metadata without yfinance | Lazy provider imports; scoring/contracts and pure metadata load independently |
| P2 | Shared universe fixture mutated across tests | Reused pristine fixture repair without weakening assertions |

Eight initial independent probes failed against baseline. Corrected risk probes
also reproduced both risk defects. No failing assertions were removed or skipped.
The exact sender inventory now reflects consolidated sends; session ordering is
still asserted. Runtime-test logging contamination was fixed in the fixture,
without modifying production auth or redaction.

## Verified existing capabilities

The suite exercises approval/auth/step-up, paper-only execution, kill switch,
UNKNOWN sessions, reduce-only, risk profiles/limits, data failover/identity,
indicators, model persistence, signal/sentiment contracts, database recovery,
chronological splits, future rewrites, walk-forward costs and portfolio math.
Adapter mocks exercise delayed visibility, lost acknowledgements, duplicates,
partial/cancel/reject states and restart recovery. Existing V5 direction tests
remain symmetric; no thresholds were tuned or performance edge claimed.

## Open release gates

1. npm: 9 advisories remain (5 high development-chain, 4 moderate); production
   subset has 2 moderate router advisories. Compatible updates reduced 12 to 9.
   Tailwind/router remediation needs major-version review, not forced upgrades.
2. Shared runtime: read-only pip-audit found 119 advisories in 14 installed
   distributions, including tooling/optional libraries. This is not 119 proven
   exploitable ARIA defects. Triage and rebuild a tested runtime; this mission
   did not upgrade the shared venv used by Claude/production.
3. Bandit: 58 medium/high pattern findings need triage (including non-security
   SHA1 ids, URL/parser/deserialisation sites); not 58 confirmed vulnerabilities.
4. External paper account/gateway restarts, real fills/fees and cumulative
   partial-exit P&L reconciliation were not exercised. Passing fixtures do not
   establish net trading performance or complete broker accounting.
   Pre-S1.3 approved/rejected records lacking client ids need an external
   broker comparison before cutover; their local label cannot prove absence.
5. News/fundamentals still lack complete point-in-time snapshots for full
   ensemble replay; no measured edge is established.
6. One cold memory GET exceeded 25 seconds; real UI hydration/action handlers,
   deployment cutover and Linux/Python 3.12 remote CI remain unverified.

See the verification, test-results and handoff documents for exact evidence.

## Changed file manifest

- `.gitignore`
- `.github/workflows/security.yml`
- `backend/main.py`
- `docs/ARIA_HANDOFF.md`
- `docs/CODEX_ARIA_AUDIT.md`
- `docs/CODEX_TEST_RESULTS.md`
- `docs/CODEX_VERIFICATION.md`
- `docs/collab/log-codex.md`
- `frontend/package-lock.json`
- `frontend/scripts/verify-execution-status.mjs`
- `frontend/src/components/ExecutionStatus.jsx`
- `frontend/src/pages/Desk.jsx`
- `frontend/src/pages/Execution.jsx`
- `src/data/data_downloader.py`
- `src/desk/auto_executor.py`
- `src/desk/desk_daemon.py`
- `src/desk/position_manager.py`
- `src/desk/reflex.py`
- `src/execution/alpaca_broker.py`
- `src/execution/approval_queue.py`
- `src/execution/broker_base.py`
- `src/execution/ibkr_broker.py`
- `src/execution/order_manager.py`
- `src/execution/reduce_only.py`
- `src/risk/pretrade.py`
- `src/sentiment/sentiment_analyzer.py`
- `src/trading/account.py`
- `src/trading/broker.py`
- `src/trading/orders.py`
- `tests/conftest.py`
- `tests/test_final_execution_reliability.py`
- `tests/test_final_profile.py`
- `tests/test_final_runtime.py`
- `tests/test_kill_switch_and_profiles.py`
- `tests/test_order_safety_review.py`
- `tests/test_order_settlement.py`
- `tests/test_reduce_only.py`
- `tests/test_session_bypass.py`
- `tests/test_stabilization.py`
- `tests/test_step_up.py`
