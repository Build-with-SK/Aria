# ARIA delivery tracker — multiasset trading remediation

Last updated: 2026-11-06T21:55Z (UTC)

## Objective

Take the multiasset trading extension (`feature/aria-multiasset-trading`) from the
independent review's **CONDITIONAL_REVIEW** to a verified, documented branch on
GitHub: every review finding fixed with a regression test, backtesting corrected,
existing ARIA behaviour preserved, all trading execution still disabled.

The long-term goal is a reliable multi-asset research and risk platform that can
progress, gate by gate, toward human-confirmed orders on a **practice** account.
This delivery does not connect to any broker, FTMO account or live account, and
changes no deployment or production configuration.

## Where the work is

| | |
|---|---|
| Branch | `feature/aria-multiasset-trading` |
| Worktree | `aria-multiasset-trading` (sibling of the main checkout) |
| Reviewed commit | `7385130` (6 commits on integration `58f5f25`) |
| Base relationships | 0 behind / 6 ahead of `aria/integrity-upgrade`; 0 behind / 117 ahead of `main` |
| Remote | `origin` = `github.com/Build-with-SK/Aria` (**public** repository) |
| Branch on GitHub | not yet pushed |
| Implementation owner | Claude Code, sole owner from 2026-11-06 (`docs/collab/WORK_PLAN.md` §8) |

Review report: [`CLAUDE_INDEPENDENT_REVIEW.md`](CLAUDE_INDEPENDENT_REVIEW.md) (frozen at `7385130`).
Original reproductions: [`claude_review_probes/`](claude_review_probes/) (`probes_out.json`).

## Current stage

Stage 1 — CR-02 (approval store recomputes risk).

## Findings

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `FIXED`, `TESTED`, `BLOCKED`, `UNVERIFIED`.
`TESTED` means a regression test reproduces the review case and passes on the fix,
and the original failure was reproduced against `7385130`.

| ID | Sev | Finding | Status | Fix commit | Regression tests |
|---|---|---|---|---|---|
| CR-02 | HIGH | Store trusts a caller-built ALLOW | IN_PROGRESS | — | `tests/test_review_findings.py::test_cr02_*` |
| CR-05 | HIGH | Daily stop ignores balance vs equity | NOT_STARTED | — | — |
| CR-10 | MED | Trade may land exactly on the floor | NOT_STARTED | — | — |
| CR-03 | HIGH | Contract-size errors undetected | NOT_STARTED | — | — |
| CR-04 | HIGH | FX conversion checked by label only | NOT_STARTED | — | — |
| CR-01 | HIGH | Trailing high-water lowered by re-versioning | NOT_STARTED | — | — |
| CR-06 | HIGH | MT5 adapter not read-only | NOT_STARTED | — | — |
| CR-07 | HIGH | "DEMO" is not a safety boundary at FTMO | NOT_STARTED | — | — |
| CR-08 | HIGH | News/gap/weekend rules not enforced | NOT_STARTED | — | — |
| QA-01 | HIGH | Backtester breaker look-ahead | NOT_STARTED | — | — |
| CR-09 | MED | Per-day server/position/order limits | NOT_STARTED | — | — |
| CR-11 | MED | Self-asserted book/operational inputs | NOT_STARTED | — | — |
| RQ-01 | MED | Caller-asserted "verified" evidence | NOT_STARTED | — | — |
| RQ-02 | MED | Contradictory hypotheses; SR logic | NOT_STARTED | — | — |
| QA-02 | MED | Fixed 252-period annualisation | NOT_STARTED | — | — |
| QA-03 | MED | Close-only stops | NOT_STARTED | — | — |
| QA-04 | MED | No path-level FTMO breach simulation | NOT_STARTED | — | — |
| OP-01 | MED | MT5 needs a Windows host (ops) | NOT_STARTED | — | — |
| CR-12 | LOW | FTMO detection by exact strings | NOT_STARTED | — | — |
| CR-13 | LOW | One FX rate at today's price | NOT_STARTED | — | — |
| CR-14 | LOW | Backup leaves connection open | NOT_STARTED | — | — |
| SEC-01 | LOW | Audit chain unkeyed | NOT_STARTED | — | — |
| UI-01 | LOW | "EXECUTION DISABLED" banner wording | NOT_STARTED | — | — |

## Test evidence

| When (UTC) | Commit | Command | Result |
|---|---|---|---|
| 2026-11-06 21:45 | `7385130` (baseline) | full suite, shared venv, `-p no:cacheprovider` | **3,432 passed, 10 skipped**, exit 0, 392 s — identical to the review run |
| 2026-11-06 21:50 | `7385130` | `probes.py` re-run (extracted copy in a temp folder) | R1 forged ALLOW reserved an intent; E5b gold 10 lots ALLOW; E3b inverted FX ALLOW; E8 ALLOW; R2 high-water 10,800 → 10,000; M1 raw SDK `order_send` reachable — all reproduced |

## Files changed in this delivery

Listed per commit in `git log 7385130..HEAD`; summarised here as stages land.

## Decisions

| Date | Decision | Reason |
|---|---|---|
| 2026-11-06 | Work in the existing feature worktree; never touch the main checkout's uncommitted files | The main checkout carries the live data files and the owner's uncommitted state; the review and log originals there stay as they are |
| 2026-11-06 | Review copied into the branch with hosting details generalised | The repository is public; the findings are unchanged |
| 2026-11-06 | The cloud-migration handover file is not committed | It names infrastructure access details; it stays local |

## Outstanding blockers (external)

- Written FTMO clarification on the AI/software and third-party clauses (review §FTMO) — needed before any order stage, not for this delivery.
- An always-on Windows host for any future MT5 worker (OP-01) — owner decision.

## Next exact action

Finish CR-02: integration tests around the store, update the store docs, commit.
