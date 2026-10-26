# Claude release recommendation — ARIA final candidate

Reviewer: Claude Code (independent of the integrator's reasoning). Date: 2026-10-18.
Candidate: `codex/aria-final-integration` @ `edc2796`. Later Codex commit `edfb5e6` reviewed.
Evidence: CLAUDE_FINAL_ARCHITECTURE_REVIEW, CLAUDE_TRADING_SAFETY_REVIEW,
CLAUDE_ML_PIPELINE_REVIEW, CLAUDE_SECURITY_REVIEW, ARIA_REVIEW_HANDOFF, and
`tests/test_claude_final_review_probes.py`.

## Recommendation: **B — Requires additional engineering fixes**

Then **C**: once the fixes below land and are re-reviewed, the remaining gates are blocked
by external infrastructure and owner approval, not by code.

ARIA is **not** production-ready, and passing tests do not make it so. Live trading stays
disabled. Nothing in this review authorises a broker write.

### Must fix before the next controlled paper-acceptance session

| Id | Sev | Why it blocks paper acceptance |
| --- | --- | --- |
| F-01 | HIGH | The emergency halt cancels no resting Alpaca order, and its report calls the survivors "kept_protective". The acceptance plan relies on being able to stop. |
| F-10 | HIGH | The auto-execution freshness gate passes weeks-old signals after any checkout or merge. The cutover itself triggers it: the candidate's `signals.json` dates from 2026-09-27. |
| F-02 | MEDIUM | A failed desk exit leaves the position with no protective order for at least one tick. |
| F-09 | MEDIUM | Bracket healing can protect only part of a position, permanently. |
| F-06 | MEDIUM | In `edfb5e6` only: the chat context crashes on a future-dated ML row (UK DST change 2026-10-25). Fix before `edfb5e6` is integrated. |

Should fix in the same round: F-08 (single pipeline writer), F-05 (perception ML age),
S-01 procedure (recreate the runtime venv at cutover). LOW items can follow.

## Release gates

| Gate | Verdict | Evidence / reason |
| --- | --- | --- |
| Claude/Codex branch reconciliation | **PASS** | Candidate contains `claude/aria-completion` and every `codex/aria-final-verification` change (rebased). ML resume `4ffda39` carried as `1fd6426`. |
| Independent full regression (candidate) | **PASS** | Windows, runtime venv CPython 3.11.8: 3,523 passed, 13 skipped, exit 0. |
| Independent full regression (`edfb5e6`) | **PASS** | 3,542 passed, 13 skipped, exit 0. Does not catch F-06. |
| Order identity, duplicate approval, UNKNOWN, timeout, absence (inv. 1-5) | **PASS** | Code review + existing suites. |
| Legacy quarantine (inv. 11), unreadable positions (inv. 7), risk-path coverage (inv. 9), single desk (inv. 10) | **PASS** | Same. |
| R1/R2/R3 retained in integration | **PASS** | Trading safety review §2. |
| Protective orders preserved (inv. 8) | **FAIL** | F-02, F-09. |
| Partial fills after restart (inv. 6) | **PARTIAL → FAIL** | Queue PASS. Desk protection sizing FAIL (F-09). |
| Auditability (inv. 12) | **PARTIAL** | Queue audit PASS. Halt report misstates outcomes (F-01). Desk positions not audited. |
| Emergency halt / kill switch | **FAIL** | F-01. FLATTEN inert (F-13, safe but misleading). |
| Auto-execution safety gates | **FAIL** | F-10. |
| ML durability and resume | **PASS** | Batch checkpoints, safe resume, no duplicates, no look-ahead found. |
| ML freshness presentation | **FAIL** | F-05 (perception). F-06 regression in `edfb5e6`. F-04 LOW. |
| Source security scan (Bandit) | **FAIL** | 49 findings, exit 1, matching Codex. Real XML and SSRF classes are remediated only in `edfb5e6` (43 remain, assessed as false positive or accepted). |
| Dependency security, audited fresh env | **PASS (Codex, not re-run)** | Codex: fresh 3.14.8 env, zero findings. |
| Dependency security, deployed runtime venv | **FAIL** | S-01: 74 known vulnerabilities in 14 packages, including starlette, python-multipart and urllib3. |
| Authentication / authorisation | **PASS** | Deny-by-default role guard; proven ownership for execution; step-up for approve/release. S-03 LOW. |
| Browser / proxy / SSE (local) | **NOT TESTED by Claude** | Codex reports PASS. Skipped in my run (needs a staged build). |
| Linux runtime (local) | **NOT TESTED by Claude** | Codex reports PASS (WSL). POSIX tests skipped on Windows. |
| Database migrations | **NOT TESTED** | `test_migration_freeze` skipped (no production ledger in an isolated checkout). |
| Historical migration and reconciliation | **BLOCKED** | Needs the original account evidence and statements. |
| External new paper-order acceptance | **BLOCKED** | Needs separate order-level owner approval. Do not start until F-01 and F-10 are fixed. |
| IBKR gateway validation | **BLOCKED** | No approved gateway session. F-15 to fix first. |
| Fee and partial-exit P&L reconciliation | **BLOCKED** | Broker statements needed. F-16 also affects it. |
| Hosted deployment acceptance | **BLOCKED** | No target cutover approved. TLS, proxy, supervision and 7-day uptime unmeasured. |
| Production cutover (runtime venv, state migration) | **BLOCKED** | Owner approval. Must include S-01 and the F-10 step (auto-execute disarmed until a pipeline run rewrites `signals.json`). |
| Measured trading edge | **BLOCKED** | Not a software gate. No out-of-sample, cost-adjusted evidence. |
| Live release | **BLOCKED** | Not permitted. Live trading stays off. |

## Path to "A — ready for further controlled paper acceptance"

1. Codex fixes F-01, F-10, F-02, F-09, and F-06 in `edfb5e6`. It removes the matching
   `xfail` markers and runs the full suite (ARIA_REVIEW_HANDOFF §2 lists done-when criteria).
2. Claude re-reviews each commit on a detached checkout and records the verdict in the handoff.
3. Owner decisions: untrack generated `data/*.json`; approve the cutover plan (recreated
   venv, single-version downtime, auto-execute disarmed until fresh signals); confirm the
   ownership change in WORK_PLAN.
4. Only then: the owner-approved, order-level paper acceptance session from
   ARIA_RELEASE_CANDIDATE (lost ack, partial + cancel, restart, fees), with the halt
   rehearsed at the start of the session.
