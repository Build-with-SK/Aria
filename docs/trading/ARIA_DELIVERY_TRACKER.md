# ARIA delivery tracker — multiasset trading remediation

Last updated: 2026-11-06T22:42Z (UTC)

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

Stage 9 — CR-06 (MT5 reads in a separate worker process, investor login
only), CR-14 (backup closes its connections) and SEC-01 (keyed, anchorable audit
chain). Stages 1–8 (CR-02, CR-05, CR-10, CR-03, CR-04, CR-13, CR-01, CR-07,
CR-12, CR-08, CR-09, CR-11) are committed and tested.

## Findings

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `FIXED`, `TESTED`, `BLOCKED`, `UNVERIFIED`.
`TESTED` means a regression test reproduces the review case and passes on the fix,
and the original failure was reproduced against `7385130`.

| ID | Sev | Finding | Status | Fix commit | Regression tests |
|---|---|---|---|---|---|
| CR-02 | HIGH | Store trusts a caller-built ALLOW | TESTED | 3f6ec66 | `tests/test_review_findings.py::test_cr02_*` (7); before: probe R1 reserved an intent at `7385130` |
| CR-05 | HIGH | Daily stop ignores balance vs equity | TESTED | e0d9c89 | `test_cr05_*` (2), `test_multiasset_risk.py::test_internal_daily_stop_*` (4); fail at `7385130` (E8 ALLOW) |
| CR-10 | MED | Trade may land exactly on the floor | TESTED | e0d9c89 | `test_cr10_*` (4); 3 fail at `7385130` (E7 ALLOW, equality ALLOW) |
| CR-03 | HIGH | Contract-size errors undetected | TESTED | 4027f07 | `test_cr03_*` (7); before: probe E5b gold 10 lots ALLOW at `7385130` |
| CR-04 | HIGH | FX conversion checked by label only | TESTED | ff1d23a | `test_cr04_*` (3), `test_multiasset_risk.py::test_cross_currency_requires_fresh_pair_quote`; before: probe E3b ALLOW $39.37 vs $63.50 |
| CR-01 | HIGH | Trailing high-water lowered by re-versioning | TESTED | 4e1cfde | `test_cr01_*` (4); before: probe R2 high-water 10,800 → 10,000 at `7385130` |
| CR-06 | HIGH | MT5 adapter not read-only | TESTED | stage 9 | `test_cr06_*` (7) + `test_trading_structure.py` SDK-import and adapter-construction tests (2); before: trade-enabled login and algo-trading terminal connected, raw `adapter._sdk.order_send` reached the SDK (1 call) at `7385130` |
| CR-07 | HIGH | "DEMO" is not a safety boundary at FTMO | TESTED | f7bf452 | `test_cr07_*` (13); before: paid FTMO Challenge on a DEMO server ALLOW and queued at `7385130` |
| CR-08 | HIGH | News/gap/weekend rules not enforced | TESTED | 2fe1d0b | `test_cr08_*` (33); before: no calendar/session inputs, Standard-account NFP-in-90 s case ALLOW, free-text rules counted as verified at `7385130` |
| QA-01 | HIGH | Backtester breaker look-ahead | NOT_STARTED | — | — |
| CR-09 | MED | Per-day server/position/order limits | TESTED | 2fe1d0b | `test_cr09_*` (13); before: only `max_server_requests_per_minute`, no daily counters at `7385130` |
| CR-11 | MED | Self-asserted book/operational inputs | TESTED | f103ace | `test_cr11_*` (19) + reservation re-check; before: `ARIA_KILL_SWITCH=1` engaged and `assess` ALLOW at `7385130` (13 result fields caller-stated) |
| RQ-01 | MED | Caller-asserted "verified" evidence | NOT_STARTED | — | — |
| RQ-02 | MED | Contradictory hypotheses; SR logic | NOT_STARTED | — | — |
| QA-02 | MED | Fixed 252-period annualisation | NOT_STARTED | — | — |
| QA-03 | MED | Close-only stops | NOT_STARTED | — | — |
| QA-04 | MED | No path-level FTMO breach simulation | NOT_STARTED | — | — |
| OP-01 | MED | MT5 needs a Windows host (ops) | NOT_STARTED | — | — |
| CR-12 | LOW | FTMO detection by exact strings | TESTED | f7bf452 | `test_cr12_*` (15); before: "FTMO s.r.o." + London validated; "FTMO Challenge 1-Step" with the wrong formula PASS at `7385130` |
| CR-13 | LOW | One FX rate at today's price | TESTED | ff1d23a | `test_cr13_*` (2); before: probe E2 converts at today's rate |
| CR-14 | LOW | Backup leaves connection open | TESTED | stage 9 | `test_cr14_*` (1); before: of the 2 connections `backup()` opened, the target was never closed at `7385130` |
| SEC-01 | LOW | Audit chain unkeyed | TESTED | stage 9 | `test_sec01_*` (6); before: a chain edited and rebuilt with plain SHA-256 passed `verify_audit` and still reserved an intent; a truncated chain passed at `7385130` |
| UI-01 | LOW | "EXECUTION DISABLED" banner wording | NOT_STARTED | — | — |

## Test evidence

| When (UTC) | Commit | Command | Result |
|---|---|---|---|
| 2026-11-06 21:45 | `7385130` (baseline) | full suite, shared venv, `-p no:cacheprovider` | **3,432 passed, 10 skipped**, exit 0, 392 s — identical to the review run |
| 2026-11-06 21:58 | stage 1 | multiasset + `test_trading_structure.py` | 147 passed |
| 2026-11-06 22:42 | `f103ace` | full suite on a `git archive` copy | **3,546 passed, 21 skipped**, exit 0, 375 s |
| 2026-11-06 22:42 | stage 9 | `before_cr06.py` on the `7385130` copy | trade-enabled login + algo-trading terminal: connected; `adapter.order_send` refused, but `adapter._sdk.order_send` reached the SDK (1 call) |
| 2026-11-06 22:42 | stage 9 | `before_cr14_sec01.py` on the `7385130` copy | backup target connection not closed; SHA-256-rebuilt chain ACCEPTED and intent RESERVED; truncated chain ACCEPTED; store takes no key |
| 2026-11-06 22:42 | stage 9 | multiasset + structure + findings + protected paths | **300 passed** |
| 2026-11-06 22:30 | `2fe1d0b` | full suite on a `git archive` copy | **3,528 passed, 21 skipped**, exit 0, 366 s |
| 2026-11-06 22:30 | stage 8 | `before_cr11.py` on the `7385130` copy | legacy kill switch engaged (environment), caller said clear: **ALLOW**; 13 caller-stated result fields present |
| 2026-11-06 22:30 | stage 8 | multiasset + structure + findings | **265 passed** |
| 2026-11-06 22:20 | `f7bf452` | full suite on a `git archive` copy | **3,482 passed, 21 skipped**, exit 0, 364 s |
| 2026-11-06 22:22 | stage 7 | `before_cr08_cr09.py` on the `7385130` copy | RiskInputs had no calendar, session or activity field; Standard-funded profile ALLOW; fixture rules "fixture:none" etc. ALLOW; only a per-minute limit |
| 2026-11-06 22:22 | stage 7 | multiasset + structure + findings | **247 passed** |
| 2026-11-06 22:10 | `4e1cfde` | full suite on a `git archive` copy | **3,454 passed, 21 skipped**, exit 0, 352 s. The 11 extra skips are git-only tests (`test_protected_paths`, `test_selfmod_sandbox`); run in the worktree: 40 passed |
| 2026-11-06 22:12 | stage 6 | `before_cr07_cr12.py` on the `7385130` copy | paid Challenge on DEMO: ALLOW, store QUEUED; "FTMO s.r.o." + Europe/London validates; "FTMO Challenge 1-Step" + legacy formula after cutover: PASS |
| 2026-11-06 22:12 | stage 6 | multiasset + structure + findings | **201 passed** |
| 2026-11-06 22:12 | stage 6 and `7385130` | the `test_trading_*.py` subset run alone | blocked write to `data/execution/approval_queue.json` (nothing written) on both — a pre-existing subset-isolation artifact; the full suite isolates it |
| 2026-11-06 21:50 | `7385130` | `probes.py` re-run (extracted copy in a temp folder) | R1 forged ALLOW reserved an intent; E5b gold 10 lots ALLOW; E3b inverted FX ALLOW; E8 ALLOW; R2 high-water 10,800 → 10,000; M1 raw SDK `order_send` reachable — all reproduced |

## Files changed in this delivery

Listed per commit in `git log 7385130..HEAD`; summarised here as stages land.

## Decisions

| Date | Decision | Reason |
|---|---|---|
| 2026-11-06 | Work in the existing feature worktree; never touch the main checkout's uncommitted files | The main checkout carries the live data files and the owner's uncommitted state; the review and log originals there stay as they are |
| 2026-11-06 | Review copied into the branch with hosting details generalised | The repository is public; the findings are unchanged |
| 2026-11-06 | The cloud-migration handover file is not committed | It names infrastructure access details; it stays local |
| 2026-11-06 | CR-06: read-only is a process boundary, not a proxy | `MetaTrader5` is process-global; any code in a process that initialised it can call `order_send`. Only a separate process with a plain-data pipe keeps ARIA's side free of the SDK |
| 2026-11-06 | CR-06: investor login required even for reads | A master-password login could trade through the terminal by other means; refusing it makes "read-only" a property of the account, not of ARIA's code |
| 2026-11-06 | SEC-01: HMAC with a key held outside the database; unkeyed stores may queue but not reserve | Writing the file must not be enough to rebuild the chain; existing unkeyed stores stay readable and are never silently re-keyed |
| 2026-11-06 | SEC-01: anchors and keys are the operator's to keep | Shipping a key manager or anchor service is out of scope and would need its own review |
| 2026-11-06 | CR-11: evidence in, results derived — instead of a sealed "snapshot builder" | Python cannot make one function the only constructor of a class; if no field can state a result, any constructor can only pass evidence the engine interprets |
| 2026-11-06 | CR-11: correlation = shares a currency with the instrument | No correlation data exists yet; sharing a currency over-counts, which only shrinks the budget |
| 2026-11-06 | CR-11: stated slippage floored at one live spread | A stop is a market exit; a caller cannot claim less than the spread it will cross |
| 2026-11-06 | CR-08: "major news" read as every HIGH/UNKNOWN release for the instrument's currencies (or its earnings) still ahead today | FTMO has not defined "major"; the review asked for the conservative reading until FTMO clarifies in writing |
| 2026-11-06 | CR-08: an FTMO profile may state only the published rule value or UNVERIFIED | Keeps the profile a true record of FTMO's rules; ARIA's own extra caution lives in the research policy (15-minute pre-break margin, 80% headroom) |
| 2026-11-06 | CR-09: a new trade counts as 3 server requests | Send, stop/target change and close all count toward FTMO's daily request figure |
| 2026-11-06 | CR-07: research sizing runs on any account class; only the store's order stages are PRACTICE-only | Sizing a paid Challenge is useful analysis; reaching an order stage is the consequence the review is about |
| 2026-11-06 | Every trust boundary rebuilds its contracts through validation (`revalidated`) | `model_copy(update=...)` skips validators, which is how the CR-12 spellings could have been smuggled past enums |
| 2026-11-06 | CR-02: the store runs `assess()` itself, under a class-level policy, and refuses a caller clock > 5 s from its own | A verdict, a policy and a clock are all things a caller could forge; the store now takes none of them |

## Outstanding blockers (external)

- Written FTMO clarification on the AI/software and third-party clauses (review §FTMO) — needed before any order stage, not for this delivery.
- An always-on Windows host for any future MT5 worker (OP-01) — owner decision.

## Next exact action

Stage 10: UI-01 — relabel the Portfolio banner "EXTENSION: NO ORDER SENDER";
OP-01 — document the separate always-on Windows host an MT5 worker would need.
Then §3: QA-01..QA-04 and RQ-01/RQ-02.
