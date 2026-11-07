# ARIA delivery tracker — multiasset trading remediation

Last updated: 2026-11-07T23:10Z (UTC) — Claude, QA-04 replay follow-up (`f414e96`) after RV2 (`c8acaa2`)

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
| Validated implementation commit | `1c7e94e6356f4de36ab4f54912fd641f45f0617c` (Linux CI green; final documentation-only successor records results) |
| First verified publication commit | `d133ddded54760392a273e745eff91a6228706fb` (PR head at creation) |
| Reviewed commit | `7385130` (6 commits on integration `58f5f25`) |
| Initial base relationships at 7385130 | 0 behind / 6 ahead of `aria/integrity-upgrade`; 0 behind / 117 ahead of `main` |
| Remote | `origin` = `github.com/Build-with-SK/Aria` (**public** repository) |
| Branch on GitHub | pushed and remote-verified; [draft PR #5](https://github.com/Build-with-SK/Aria/pull/5) to main |
| Implementation owner | Claude Code; independent reviewer Codex (`docs/collab/WORK_PLAN.md` §9, 2026-11-07). Commits `0abe76b`..`268af1d` were authored by a Codex implementation agent before §9 and are not independently verified |

Review report: [`CLAUDE_INDEPENDENT_REVIEW.md`](CLAUDE_INDEPENDENT_REVIEW.md) (frozen at `7385130`).
Original reproductions: [`claude_review_probes/`](claude_review_probes/) (`probes_out.json`).

## Programme status — two-agent mission (current)

Statuses: NOT_STARTED, IN_PROGRESS, FIXED, TESTED, VERIFIED_FIXED, BLOCKED,
UNVERIFIED, REGRESSION_FOUND. VERIFIED_FIXED is Codex's verdict only.
`dac263f` review results are marked † where the per-finding mapping is inferred:
Codex's report file was 0 bytes when read (2026-11-07 20:21 UTC); the mapping comes
from its reproduction scripts and the stated totals (7 / 12 / 1). Handover for the
current stage: [AGENT_HANDOFF.md](AGENT_HANDOFF.md).

Codex's review of `268af1d` (report `CODEX_INDEPENDENT_REVIEW.md`, 22,046 bytes, in
Codex's own review worktree): **REQUEST CHANGES** — 7 VERIFIED_FIXED (carried
forward), 5 PARTIALLY_FIXED (CR-08, QA-01..QA-04), 8 UNVERIFIED as to non-author
independence (CR-01, CR-02, CR-03, CR-05, CR-07, CR-09, CR-11, SEC-01), and four
new defects RV2-01..RV2-04. That review predates `3bb9f8d`, `fbea136` and `c8acaa2`.

| ID | Priority | Status | Agent | Implementation commit | Regression tests | Independent review | Blockers | Next action | Updated (UTC) |
|---|---|---|---|---|---|---|---|---|---|
| CR-02 | P0 | TESTED | Claude | 3f6ec66; 0abe76b, 4165e71 (Codex impl.); 3bb9f8d | `test_cr02_*` (7); approval/replay cases in `test_remediation_followup.py`; checkpoint cases in `test_authority_followup.py` | dac263f: PARTIALLY_FIXED† (SQL forge, replay); 268af1d (Codex): UNVERIFIED (non-author independence); 3bb9f8d: pending | — | Codex reviews 3bb9f8d | 2026-11-07T22:39Z |
| SEC-01 | P0 | TESTED | Claude | 7bfe3bd; 0abe76b, 4165e71 (Codex impl.); 3bb9f8d | `test_sec01_*` (6); rollback/checkpoint cases in `test_remediation_followup.py`; `test_authority_followup.py` (3 checkpoint cases) | dac263f: PARTIALLY_FIXED†; 268af1d: unchecked writer + rollback released a hold (Claude probe); 268af1d (Codex): UNVERIFIED (non-author independence); 3bb9f8d: pending | checkpoint ACLs, power-loss recovery (operational, UNVERIFIED) | Codex reviews 3bb9f8d | 2026-11-07T22:39Z |
| CR-01 | P0 | TESTED | Claude | 4e1cfde; 0abe76b, 962d8d3 (Codex impl.); 3bb9f8d | `test_cr01_*` (4); `test_deleted_history_cannot_be_reseeded`; `test_authority_followup.py` (2 high-water cases) | dac263f: PARTIALLY_FIXED†; 268af1d: regression — recorded high-water below source gave ALLOW (Claude probe); 268af1d (Codex): UNVERIFIED (non-author independence); 3bb9f8d: pending | — | Codex reviews 3bb9f8d | 2026-11-07T22:39Z |
| CR-05 | P0 | TESTED | Claude | e0d9c89; 962d8d3 (Codex impl.); 3bb9f8d | `test_cr05_*`; `test_caller_cannot_replace_authoritative_account_evidence[midnight]`; `test_authority_followup.py` (midnight + 2 cashflow/reset) | dac263f: PARTIALLY_FIXED†; 268af1d: regression — recorded midnight below source, source cashflow/reset dropped, all ALLOW (Claude probe); 268af1d (Codex): UNVERIFIED (non-author independence); 3bb9f8d: pending | — | Codex reviews 3bb9f8d | 2026-11-07T22:39Z |
| CR-09 | P0 | TESTED | Claude | 2fe1d0b; 962d8d3 (Codex impl.) | `test_cr09_*` (13); `[activity]` substitution case | dac263f: PARTIALLY_FIXED†; 268af1d substitution refused (Claude probe); 268af1d (Codex): UNVERIFIED (non-author independence) | activity persistence is P2-ACT | Codex reviews | 2026-11-07T22:39Z |
| CR-11 | P0 | TESTED | Claude | f103ace; 962d8d3 (Codex impl.); 3bb9f8d (source flags kept) | `test_cr11_*` (19); `[book]` substitution case; kill-switch probe | dac263f: PARTIALLY_FIXED†; 268af1d book substitution and kill switch refused (Claude probe); 268af1d (Codex): UNVERIFIED (non-author independence) | — | Codex reviews | 2026-11-07T22:39Z |
| CR-03 | P0 | TESTED | Claude | 4027f07; 962d8d3 (Codex impl.) | `test_cr03_*` (7); `test_calculator_provider_account_symbol_and_permission_are_bound` | dac263f: PARTIALLY_FIXED†; reviewer script at 268af1d: UNVERIFIED (refused); 268af1d (Codex): UNVERIFIED (non-author independence) | — | Codex reviews | 2026-11-07T22:39Z |
| CR-07 | P0 | TESTED | Claude | f7bf452; 962d8d3 (Codex impl.) | `test_cr07_*` (12); relabel and re-registration cases in `test_remediation_followup.py` | dac263f: PARTIALLY_FIXED†; 268af1d relabel refused (Claude probe); 268af1d (Codex): UNVERIFIED (non-author independence) | — | Codex reviews | 2026-11-07T22:39Z |
| CL-01 | P0 | NOT_STARTED | Claude | — | probe `APPROVE_ACTOR_ATTESTED` | — | owner/coordinator decision: `approve()` trusts its caller for identity; no endpoint exposes it | bind to owner step-up session before any endpoint exposes approval | 2026-11-07T20:39Z |
| CL-02 | P0 | UNVERIFIED | — | `snapshot_source.BoundRiskSource` (fixture readers) | fixture acquisition cases | — | no real provider reader, investor login or Windows host (OP-01) | separately authorised read-only integration milestone | 2026-11-07T20:39Z |
| QA-01 | P1 | TESTED | Claude (re-verify) | dac263f; abb89f2 (Codex impl.); c8acaa2 (RV2-01) | `test_qa01_*`; `test_missing_open_cannot_create_retroactive_fill` (2) | dac263f: REGRESSION_FOUND† (missing Open); 268af1d (Codex): PARTIALLY_FIXED — missing Close still skipped a gap stop (RV2-01); c8acaa2: pending | — | Codex re-reviews c8acaa2 | 2026-11-07T22:39Z |
| QA-03 | P1 | TESTED | Claude (re-verify) | dac263f; abb89f2 (Codex impl.); c8acaa2 (RV2-01) | `test_qa03_*`; `test_missing_range_value_is_reported`; `test_hourly_trade_records_keep_time` | dac263f: PARTIALLY_FIXED†; 268af1d (Codex): PARTIALLY_FIXED — missing Close suppressed gap/low stops (RV2-01); c8acaa2: pending | — | Codex re-reviews c8acaa2 | 2026-11-07T22:39Z |
| QA-04 | P1 | TESTED | Claude | dac263f; abb89f2 (Codex impl.); c8acaa2 (RV2-03/04); f414e96 (close balance, flat-account target) | `test_qa04_*` (7); midnight/sparse-day cases; `tests/test_qa04_followup.py` (13: 6 red before f414e96, 7 pins incl. DST and trailing reset coverage) | dac263f: PARTIALLY_FIXED†; 268af1d (Codex): PARTIALLY_FIXED — skipped reset days, conflicting reset samples (RV2-03/04); Codex probes REPLAY_CLOSE_BALANCE (8,900 replayed OPEN) and REPLAY_TARGET_OPEN_LOSS (target with 1,400 open loss) — now OVERALL_BREACH and DAILY_BREACH; c8acaa2, f414e96: pending | — | Codex re-reviews c8acaa2 and f414e96 | 2026-11-07T23:10Z |
| QA-02 | P1 | TESTED | Claude (re-verify) | dac263f; abb89f2, e82c986 (Codex impl.); c8acaa2 (RV2-02) | `test_qa02_*` (15); 21-hour and irregular-grid cases | dac263f: PARTIALLY_FIXED†; 268af1d (Codex): PARTIALLY_FIXED — weekly/monthly series missing a period still annualised (RV2-02); c8acaa2: pending | — | Codex re-reviews c8acaa2 | 2026-11-07T22:39Z |
| RV2-01 | P1 | TESTED | Claude | c8acaa2 | `tests/test_rv2_followup.py`: `test_missing_close_is_refused` (3); `test_missing_signal_*` (5) | found by Codex at 268af1d (HIGH: missing Close or missing signal skipped the bar's gap stop, intrabar stop and pending breaker exit and marked the position at entry value); Codex's reviewer case passes at c8acaa2; verdict pending | — | Codex re-reviews c8acaa2 | 2026-11-07T22:39Z |
| RV2-02 | P1 | TESTED | Claude | c8acaa2 | `tests/test_rv2_followup.py`: `test_weekly_or_monthly_grid_missing_a_period_*` (6); `test_two_bars_in_one_period_*` (2); grid and short-week pins (7) | found by Codex at 268af1d (MED: weekly/monthly series missing a period still annualised); Codex's reviewer case passes at c8acaa2; verdict pending | — | Codex re-reviews c8acaa2 | 2026-11-07T22:39Z |
| RV2-03 | P1 | TESTED | Claude | c8acaa2 | `tests/test_rv2_followup.py`: `test_replay_refuses_skipped_rule_days`; `test_replay_refuses_one_skipped_day_in_the_middle`; weekend pin | found by Codex at 268af1d (MED: replay jumped over whole rule days with no reset observation); Codex's reviewer case passes at c8acaa2; verdict pending | — | Codex re-reviews c8acaa2 | 2026-11-07T22:39Z |
| RV2-04 | P1 | TESTED | Claude | c8acaa2 | `tests/test_rv2_followup.py`: `test_replay_refuses_conflicting_reset_observations`; `test_replay_refuses_any_repeated_instant` (2) | found by Codex at 268af1d (MED: replay accepted conflicting observations at one instant); Codex's reviewer case passes at c8acaa2; verdict pending | — | Codex re-reviews c8acaa2 | 2026-11-07T22:39Z |
| QA-EQ | P1 | TESTED | Claude (re-verify) | abb89f2 (Codex impl.) | `test_final_curve_includes_initial_capital_and_exit_fees` | reviewer script at 268af1d: curve vs cash difference 0.002 (was 990.10) | — | Stage 2: re-check | 2026-11-07T20:39Z |
| CR-08 | P2 | TESTED | Claude (re-verify) | 2fe1d0b; abb89f2 (Codex impl.) | `test_cr08_*` (33); `test_no_gap_rule_does_not_block_whole_news_day` | dac263f: PARTIALLY_FIXED†; 268af1d (Codex): PARTIALLY_FIXED — open-position news/break monitoring and flattening absent | — | Stage 3: re-check | 2026-11-07T22:39Z |
| P2-ACT | P2 | NOT_STARTED | Claude | — | — | — | — | Stage 3: the store counts its own queues/reservations toward daily limits | 2026-11-07T20:39Z |
| P2-OPS | P2 | NOT_STARTED | Claude | — | — | — | — | Stage 3: instrument controls, explicit account/broker identity, unavailable data, supported/unsupported/unverified matrix | 2026-11-07T20:39Z |
| RQ-01 | — | TESTED | Claude | first version Claude (uncommitted); 7e8570a (Codex impl.) | `test_research_review_followup.py` (8) | not yet reviewed | — | Codex reviews 7e8570a | 2026-11-07T20:39Z |
| RQ-02 | — | TESTED | Claude | as RQ-01 | `test_research_review_followup.py` (2) | not yet reviewed | — | Codex reviews 7e8570a | 2026-11-07T20:39Z |
| CR-10, CR-04, CR-06, CR-12, CR-13, CR-14, UI-01 | mixed | VERIFIED_FIXED | — | see historical table | see historical table | dac263f: VERIFIED_FIXED†; 268af1d (Codex): VERIFIED_FIXED, carried forward | — | — | 2026-11-07T22:39Z |
| OP-01 | P2 | BLOCKED | owner | e717ea6 | `test_op01_*` (3) | — | always-on Windows host | owner decision | 2026-11-07T20:39Z |
| P3 | P3 | IN_PROGRESS | Claude | — | — | — | branch already published at 268af1d by the Codex implementation agent (draft PR #5); Stage 1, RV2 and QA-04 commits are local | release checks after Codex verifies P0–P2; no push or merge before then | 2026-11-07T23:10Z |
| P3-PII | P3 | NOT_STARTED | owner | — | — | — | `docs/collab/CODEX_HANDOVER.md` (since 6cd5357, 2026-10-11) and `docs/collab/log-codex.md` (since 2026-10-16) contain local Windows paths with the owner's username; already public on several remote branches. Not introduced by this branch. History removal would need a rewrite (owner decision) | owner decides: generalise in a new commit and/or rewrite history | 2026-11-07T20:39Z |

Latest full suite: `f414e96` working tree, **3,689 passed, 21 skipped, 1 warning, exit 0**,
381 s, on a scratch copy; the 11 git-only skips pass in the worktree (58 passed, 2 skipped
for production data). Previous: `c8acaa2`, 3,676 passed, 21 skipped, exit 0, 244 s.

Codex's reviewer suite (`review_tests/test_independent_followup.py`, run unchanged with
`ARIA_REVIEW_ROOT` pointing at this worktree): `268af1d` 19 passed / 5 failed (Codex);
`fbea136` 18 / 6; **`c8acaa2` and `f414e96` 23 passed / 1 failed**. The one failure,
`test_additional_no_checkpoint_disables_reservation`, stops at its setup `queue()`:
since `3bb9f8d` a keyed store opened without its external checkpoint refuses every
change, so nothing is queued and nothing can be reserved. **Owner decision
(2026-11-07): keep the stricter rule exactly as implemented; the reviewer case encodes
the older API and is to be updated by Codex, not the code weakened.**

## Current stage (historical — before the two-agent mission)

Research-only release checks complete through code commit `1c7e94e`.
Follow-up implementation responds to the **REQUEST CHANGES** review of `dac263f`;
new code has regression evidence, but no new independent reviewer certification.
The reviewed original 15 failures are retained as requirements, with adapted
current-API cases in `tests/test_remediation_followup.py`. See
[REMEDIATION_REVIEW_RESPONSE.md](REMEDIATION_REVIEW_RESPONSE.md) for scope/limits.

Full suite: **3,648 passed / 10 skipped / 1 warning**, 453.19 s. Isolated research
environment: **314 passed / 1 warning**. Frontend exact-lock build plus four
offline verifiers passed. Current frontend and research-lock audits: zero known
advisories. Secret publication-range scan candidates were lockfile integrity
hashes and synthetic refusal-test URLs; no real credential identified.

Current release gate: **CONDITIONAL_REVIEW**, research-only, disconnected.
There is no sender, production broker evidence reader, enabled approval endpoint,
or practice-account release approval. Real MT5, provider state/classification,
checkpoint ACL/recovery, Windows host and written provider clarification remain
external milestones. No runtime configuration, broker, Oracle deployment or
integration branch was changed by remediation.

## Findings — stage view through 268af1d (historical; current status is in "Programme status" above)

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `FIXED`, `TESTED`, `BLOCKED`, `UNVERIFIED`.
`TESTED` means a regression test reproduces the review case and passes on the fix,
and the original failure was reproduced against `7385130`.

| ID | Sev | Finding | Status | Fix commit | Regression tests |
|---|---|---|---|---|---|
| CR-02 | HIGH | Store trusts a caller-built ALLOW | TESTED | 3f6ec66 / 0abe76b / 4165e71 | `tests/test_review_findings.py::test_cr02_*` (7); before: probe R1 reserved an intent at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| CR-05 | HIGH | Daily stop ignores balance vs equity | TESTED | e0d9c89 / 962d8d3 | `test_cr05_*` (2), `test_multiasset_risk.py::test_internal_daily_stop_*` (4); fail at `7385130` (E8 ALLOW); additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| CR-10 | MED | Trade may land exactly on the floor | TESTED | e0d9c89 | `test_cr10_*` (4); 3 fail at `7385130` (E7 ALLOW, equality ALLOW) |
| CR-03 | HIGH | Contract-size errors undetected | TESTED | 4027f07 / 962d8d3 | `test_cr03_*` (7); before: probe E5b gold 10 lots ALLOW at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| CR-04 | HIGH | FX conversion checked by label only | TESTED | ff1d23a | `test_cr04_*` (3), `test_multiasset_risk.py::test_cross_currency_requires_fresh_pair_quote`; before: probe E3b ALLOW $39.37 vs $63.50 |
| CR-01 | HIGH | Trailing high-water lowered by re-versioning | TESTED | 4e1cfde / 0abe76b / 962d8d3 / 4165e71 | `test_cr01_*` (4); before: probe R2 high-water 10,800 → 10,000 at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| CR-06 | HIGH | MT5 adapter not read-only | TESTED | 7bfe3bd | `test_cr06_*` (7) + `test_trading_structure.py` SDK-import and adapter-construction tests (2); before: trade-enabled login and algo-trading terminal connected, raw `adapter._sdk.order_send` reached the SDK (1 call) at `7385130` |
| CR-07 | HIGH | "DEMO" is not a safety boundary at FTMO | TESTED | f7bf452 / 962d8d3 | `test_cr07_*` (12); before: paid FTMO Challenge on a DEMO server ALLOW and queued at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| CR-08 | HIGH | News/gap/weekend rules not enforced | TESTED | 2fe1d0b / abb89f2 | `test_cr08_*` (33); before: no calendar/session inputs, Standard-account NFP-in-90 s case ALLOW, free-text rules counted as verified at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| QA-01 | HIGH | Backtester breaker look-ahead | TESTED | dac263f / abb89f2 | `test_qa01_*` (2) incl. an adversarial change-only-this-bar's-close test; `test_no_lookahead.py` still passes; before: `probe_bt.py` breaker exit at 100 after a close of 70, final equity 100,000 at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| CR-09 | MED | Per-day server/position/order limits | TESTED | 2fe1d0b / 962d8d3 | `test_cr09_*` (13); before: only `max_server_requests_per_minute`, no daily counters at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| CR-11 | MED | Self-asserted book/operational inputs | TESTED | f103ace / 962d8d3 | `test_cr11_*` (19) + reservation re-check; before: `ARIA_KILL_SWITCH=1` engaged and `assess` ALLOW at `7385130` (13 result fields caller-stated); additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| RQ-01 | MED | Caller-asserted "verified" evidence | TESTED | 7e8570a | tests/test_research_review_followup.py (8 RQ-01 / 2 RQ-02 cases); original frozen probe evidence plus current-API output |
| RQ-02 | MED | Contradictory hypotheses; SR logic | TESTED | 7e8570a | tests/test_research_review_followup.py (8 RQ-01 / 2 RQ-02 cases); original frozen probe evidence plus current-API output |
| QA-02 | MED | Fixed 252-period annualisation | TESTED | dac263f / abb89f2 / e82c986 | `test_qa02_*` (15); before: a one-year doubling of 365 daily returns reported CAGR 0.6116 (1.4524 years assumed), no frequency parameter at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| QA-03 | MED | Close-only stops | TESTED | dac263f / abb89f2 | `test_qa03_*` (4); before: `probe_bt.py` low −10% with a flat close, 5% stop: 0 stop exits at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| QA-04 | MED | No path-level FTMO breach simulation | TESTED | dac263f / abb89f2 | `test_qa04_*` (7); before: no path replay existed; the only resampler (`block_bootstrap`) saw the −5.1%-intraday day as its −300 close at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| OP-01 | MED | MT5 needs a Windows host (ops) | BLOCKED | e717ea6 | Documented (OPERATIONS.md "MT5 worker host"); `test_op01_*` (3): the worker refuses clearly on a non-Windows host or without the package. Needs an owner-provided Windows host; nothing to reproduce in code at `7385130` (an operational finding) |
| CR-12 | LOW | FTMO detection by exact strings | TESTED | f7bf452 | `test_cr12_*` (16); before: "FTMO s.r.o." + London validated; "FTMO Challenge 1-Step" with the wrong formula PASS at `7385130` |
| CR-13 | LOW | One FX rate at today's price | TESTED | ff1d23a | `test_cr13_*` (2); before: probe E2 converts at today's rate |
| CR-14 | LOW | Backup leaves connection open | TESTED | 7bfe3bd | `test_cr14_*` (1); before: of the 2 connections `backup()` opened, the target was never closed at `7385130` |
| SEC-01 | LOW | Audit chain unkeyed | TESTED | 7bfe3bd / 0abe76b / 4165e71 | `test_sec01_*` (6); before: a chain edited and rebuilt with plain SHA-256 passed `verify_audit` and still reserved an intent; a truncated chain passed at `7385130`; additional invariants: tests/test_remediation_followup.py (32 cases), fixture scope; see review response |
| UI-01 | LOW | "EXECUTION DISABLED" banner wording | TESTED | e717ea6 | `frontend/scripts/verify-multiasset.mjs` banner assertions; before: the `7385130` component fails "banner must say the extension has no order sender" |

## Test evidence

| When (UTC) | Commit | Command | Result |
|---|---|---|---|
| 2026-11-06 21:45 | `7385130` (baseline) | full suite, shared venv, `-p no:cacheprovider` | **3,432 passed, 10 skipped**, exit 0, 392 s — identical to the review run |
| 2026-11-06 21:58 | stage 1 | multiasset + `test_trading_structure.py` | 147 passed |
| 2026-11-06 22:42 | `f103ace` | full suite on a `git archive` copy | **3,546 passed, 21 skipped**, exit 0, 375 s |
| 2026-11-06 22:42 | stage 9 | `before_cr06.py` on the `7385130` copy | trade-enabled login + algo-trading terminal: connected; `adapter.order_send` refused, but `adapter._sdk.order_send` reached the SDK (1 call) |
| 2026-11-06 22:42 | stage 9 | `before_cr14_sec01.py` on the `7385130` copy | backup target connection not closed; SHA-256-rebuilt chain ACCEPTED and intent RESERVED; truncated chain ACCEPTED; store takes no key |
| 2026-11-06 22:42 | stage 9 | multiasset + structure + findings + protected paths | **300 passed** |
| 2026-11-06 22:45 | stage 10 | `node scripts/verify-multiasset.mjs` (frontend) | PASS; the same verifier on the `7385130` component: exit 1, "banner must say the extension has no order sender" |
| 2026-11-06 22:45 | stage 10 | findings `-k "cr06 or op01"` | **10 passed** |
| 2026-11-06 22:58 | `7bfe3bd` | full suite on a `git archive` copy | **3,563 passed, 21 skipped**, exit 0, 388 s |
| 2026-11-06 22:58 | stage 11 | `probe_bt.py` on the worktree and on the `7385130` copy | after: breaker exit 70.0 on the next bar, final equity 70,000, 1 stop exit; before: exit 100.0, equity 100,000, 0 stop exits |
| 2026-11-06 22:58 | stage 11 | `before_qa.py` on the `7385130` copy | CAGR 0.6116 for a one-year doubling; no frequency parameter; no path replay |
| 2026-11-06 22:58 | stage 11 | findings + look-ahead + evolution + technical + multiasset + structure | **443 passed** |
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
| 2026-11-07 ~21:40 | `fbea136` | Codex reviewer suite, unchanged (`ARIA_REVIEW_ROOT` = worktree) | 18 passed, 6 failed: the 5 RV2 cases plus `test_additional_no_checkpoint_disables_reservation` (refused at `queue`, stricter rule from `3bb9f8d`) |
| 2026-11-07 21:52 | `fbea136` + `tests/test_rv2_followup.py`, code unchanged | the new RV2 tests (failing-first) | **20 failed, 9 passed**; every failure at the attack step (DID NOT RAISE; missing stop exit; mark 100,000 instead of 97,000; breaker exit a day late at 100 instead of 96); the 9 passing cases pin unchanged behaviour |
| 2026-11-07 ~21:55 | RV2 working tree (= `c8acaa2`) | RV2 tests; backtest/replay/authority/technical/multiasset modules | **29 passed**; **412 passed** |
| 2026-11-07 21:59 | RV2 working tree (= `c8acaa2`) | full suite on a scratch copy of the working tree | **3,676 passed, 21 skipped, 1 warning**, exit 0, 244 s |
| 2026-11-07 ~22:00 | `c8acaa2` | git-only + docs tests in the worktree (`test_protected_paths`, `test_selfmod_sandbox`, `test_docs_match_code`) | **58 passed, 2 skipped** (production data absent) |
| 2026-11-07 ~22:00 | `c8acaa2` | Codex reviewer suite, unchanged | **23 passed, 1 failed** — all 5 RV2 cases pass; the failure is the checkpoint case above, kept by owner decision |
| 2026-11-07 ~22:50 | `324a2e0` + `tests/test_qa04_followup.py`, code unchanged | the new QA-04 tests (failing-first) | **6 failed, 7 passed**; failures at the defect step: close 8,900 and close at 9,500 → OPEN; target claimed at balance 11,000 / equity 9,600 (and 11,000 / 11,300) instead of the breach at 9,400 or the first flat point |
| 2026-11-07 ~23:00 | QA-04 working tree (= `f414e96`) | QA-04 + RV2 + findings + remediation tests; targeted modules; Codex reviewer suite | **242 passed**; **425 passed**; reviewer **23 passed, 1 failed** (checkpoint case, unchanged) |
| 2026-11-07 ~23:05 | QA-04 working tree (= `f414e96`) | full suite on a scratch copy; git-only + docs tests in the worktree | **3,689 passed, 21 skipped, 1 warning**, exit 0, 381 s; **58 passed, 2 skipped** |

## Files changed in this delivery

Listed per commit in `git log 7385130..HEAD`; summarised here as stages land.

## Decisions

| Date | Decision | Reason |
|---|---|---|
| 2026-11-06 | Work in the existing feature worktree; never touch the main checkout's uncommitted files | The main checkout carries the live data files and the owner's uncommitted state; the review and log originals there stay as they are |
| 2026-11-06 | Review copied into the branch with hosting details generalised | The repository is public; the findings are unchanged |
| 2026-11-06 | The cloud-migration handover file is not committed | It names infrastructure access details; it stays local |
| 2026-11-06 | QA-01: a breaker decided at a close acts at the next open; entries stay at the open, before any intrabar stop | Each fill must use only prices that existed when it happened; the change-one-close test checks exactly that |
| 2026-11-06 | QA-02: calendar from yfinance ticker conventions unless passed; unknown frequency reports None | The legacy universe mixes equities, FX, futures and crypto; a wrong annualisation is worse than none |
| 2026-11-06 | QA-04: replay uses the same floors and `<=` breach rule as `evaluate_profile`; overall breach reported before daily | One rule definition for live checks and research; the overall limit ends the account |
| 2026-11-06 | Stage 10 restores the "Windows only" refusal in the worker | Stage 9 moved SDK loading out of the adapter and dropped its platform check; found while doing OP-01, restored with a clearer message and a test |
| 2026-11-06 | OP-01 stays BLOCKED, not TESTED | It is an operational gap; documentation and a clear refusal do not provide the Windows host |
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
| 2026-11-07 | Owner: keep the external-checkpoint requirement exactly as implemented in `3bb9f8d`; do not weaken it to satisfy the reviewer case written for the older API | A keyed store that accepts changes without its checkpoint lets those changes be rolled back unnoticed |
| 2026-11-07 | RV2-01: a non-finite Close refuses the run, like a non-finite Open; a missing signal blocks only entries and signal exits | Without a close the bar's mark, breaker, next signal and benchmark are undefined; stops and the breaker depend on prices, not on the signal |
| 2026-11-07 | RV2-02: weekly/monthly completeness is one bar per calendar week (Mon–Sun) or month, not a day-gap tolerance | Business month-ends are up to 33 days apart and short weeks end on a Thursday; a gap tolerance either rejects those or hides a missing period |
| 2026-11-07 | RV2-03/04: every rule day, weekends included, needs its exact reset observation; observation times strictly increase, identical repeats included | A skipped day's floor and equity are unknown; at one instant the order, and so the reset snapshot, is unknowable |
| 2026-11-07 | QA-04: a sampled day (`DayPath`) ends flat, so its closing balance is checked against the floors | The next sampled day starts from that balance with no positions; a realised loss below a floor cannot be offset by positions the resampler does not carry. Histories held overnight belong in `replay_path` |
| 2026-11-07 | QA-04: TARGET_REACHED needs the balance at the target on a flat account (equity == balance) | Mirrors `evaluate_objectives` (PROFIT_TARGET_NOT_MET, OPEN_POSITIONS_REMAIN); a claimed target must not stop a replay before a breach |

## Outstanding blockers (external)

- Written FTMO clarification on the AI/software and third-party clauses (review §FTMO) — needed before any order stage, not for this delivery.
- An always-on Windows host for any future MT5 worker (OP-01) — owner decision.

## Next exact action

Current (2026-11-07T23:10Z): Codex re-reviews `3bb9f8d`, `fbea136`, `c8acaa2` and `f414e96`
(QA-04 close balance and flat-account target). Nothing is pushed or merged before
Codex's verdict; execution stays disabled.

Earlier: owner review of draft PR #5; then a separately scoped practice-reader integration gate.
The current release gate and remaining provider/runtime blockers are summarized
at the top and in REMEDIATION_REVIEW_RESPONSE.md. Earlier stage entries below
are historical progress, not current activation permission.

## Remediation review follow-up (2026-11-07 UTC)

Next exact action: reproduce and close mutable approval-table authority and
consumed-token replay, reconstruct account history from authenticated events,
and acquire reservation inputs through a trusted account-bound source. Repair
missing-open fills, final exit-cost accounting, exact midnight replay and 21-hour
cadence. Re-run the reviewer scenarios before changing reopened items to TESTED.

Latest completed archived suite: `dac263f`, 3,594 passed / 21 skipped / 1 warning,
exit 0. Reviewer worktree suite has 11 fewer git-only skips. Passing suites do
not supersede the new adversarial failures.

Follow-up implementation: `review_store._verify_projection` checks mutable
proposal, approval, intent and complete account-boundary state against authenticated
events at transaction entry and commit; `rule_state` also checks completeness.
Six new adversarial cases cover invented approval, consumed token replay, changed
token/actor/expiry and deleted history. Store-focused suite: **19 passed**.
CR-01/02/SEC-01 remain IN_PROGRESS until all reviewer cases and authoritative
snapshot integration are closed. Uncommitted RQ files and AGENT_HANDOFF preserved.

Quantitative review follow-up: mandatory finite Open, row-level missing-range
warning, initial/final curve accounting, hourly trade timestamps, 21-hour
cadence, exact-midnight replay and missing-reset refusal implemented.
The unrelated-provider NONE gap-rule overblock is corrected.
Targeted regression selection: **48 passed**. New cases are in
`tests/test_remediation_followup.py`; existing QA cases remain green.
Reopened findings retain IN_PROGRESS pending the complete independent scenarios
and full-suite release run. No source-authority claim is inferred from these tests.

Research RQ-01/RQ-02: completed prior uncommitted implementation, resolving a
module collision with the existing `src/research/sources/` package by naming the
new module `source_resolver.py`. Client-labelled facts/observations are downgraded;
only a server resolver's fetched claim record can support an external statement.
Exact-source text presence proves attribution only, never economic truth. Resolver
network integration is absent; fixtures use injected bounded fetchers. Cross-family
conflict abstains and support breaks cannot count as bounces. Original conflicting
fixture now asserts abstention; a separate agreeing fixture retains positive-path
coverage. Research/API regression suite: **21 passed**. Ten new RQ cases.

Authority follow-up: `snapshot_source.BoundRiskSource` fixes account/provider/profile
outside submitted requests. Queue and reservation reacquire evidence and reject
substitution. Existing authenticated boundary history overrides transport baseline;
first queue persists registration in the audit and changes require verified
migration. No source configured => review disabled. Calculator evidence additionally
binds account/provider/symbol/permission/side. Nine new acquisition cases plus
existing security cases: follow-up suite **24 passed**; relevant risk/review suite
**248 passed** before these additional cases. Concrete production snapshot adapters
and genuine broker validation remain UNVERIFIED; this is a fixture-tested acquisition
boundary, not a completed provider integration. The pre-break holding obligation
remains a future execution release blocker; no flatten sender is introduced.

Dependency release checks (2026-11-07): frontend package + lock updated to router
7.18.5 after isolated build and all four SSR verifiers passed. npm audit: zero
advisories. Research lock pip-audit: 38 packages, zero known advisories. Shared
runtime dependency environment remains unchanged and is not approved for execution.
Tracked text scan had 24 candidates, one new synthetic refusal-test URL; no new
actual credential identified. Existing candidate values were not printed.

Whole-database rollback follow-up: external checkpoint is now mandatory before
intent reservation. Authenticated head persisted before SQLite commit; old HMAC
prefix restoration, checkpoint loss/corruption and interrupted commit all fail
closed. Follow-up suite **32 passed**. Existing security selection **177 passed**.
Concrete operational checkpoint ACLs/power-loss recovery and real provider readers
remain UNVERIFIED; neither is implied by local fixture coverage.

## Final release-check record (2026-11-07 UTC)

- Full repository tests at `4165e71`: 3,648 passed, 10 skipped, 1 warning in
  453.19 s; exit 0. Ten skips require production data or the opt-in runtime sweep.
- Isolated 38-package hash-pinned environment: 314 passed, 1 warning; no real SDK.
- Exact updated frontend lock: clean install, production build, all four SSR
  verifiers passed. npm audit zero advisories. Research lock audit zero in 38.
- Protected-path history check passed. Publication range includes no added or
  modified generated databases, model files, live data, .env or vault files.
- Historical blob scan: 216 versions from 28 unpublished-range commits;
  candidates triaged as SHA-512 npm integrity strings and intentionally invalid
  basic-auth test URLs. No secret values emitted. Older unchanged repository
  candidates are not certified by this range scan.
- Original probes.py, probe_e7.py, probe_bt.py and challenge_mc.py reran against
  `7385130`, all exit 0. Current probe_bt exits at 70 with final 70,000 and one
  intrabar stop. Current-API fixtures/results: claude_review_probes/probes_after_*.
- Existing untracked AGENT_HANDOFF.md is preserved and excluded from publication.

Next exact action: publish feature branch without force, verify remote commit,
and open a draft pull request. Then owner review and a separately scoped
Windows/practice-reader integration gate. No integration merge or runtime deploy.

## GitHub delivery — verified 2026-11-07 UTC

`git push --set-upstream origin feature/aria-multiasset-trading` succeeded without
force. `git ls-remote` exactly matched local commit
`d133ddded54760392a273e745eff91a6228706fb`. Draft PR #5 was created through the
GitHub connector and its returned head matched that hash:
https://github.com/Build-with-SK/Aria/pull/5 . Base main remains `0ec8c3f`.
The PR has inherited integration ancestry (137 commits / 477 files at creation);
this is disclosed in its body rather than presented as just the follow-up diff.
A final documentation-only commit records delivery and is pushed/verified next.
No merge or deployment. The untracked AGENT_HANDOFF.md remains preserved locally.

GitHub CI follow-up: the first Linux full-suite job failed during collection
because requirements-ci omitted yfinance, which sentiment tests import eagerly.
Added yfinance==1.3.0, the exact version used by the verified local full suite.
No tests skipped or relaxed. GitHub rerun is pending; local results do not imply
Linux CI success. This is a CI dependency fix, not a runtime or workflow change.

CI second run after the dependency fix: 3,634 passed, 11 skipped, one failure
in the legacy scheduler-generation test. That test globally changed os.name to
nt, breaking pathlib on Linux under the production-write guard. Replaced only
the scheduler module's os dependency with a local SimpleNamespace; all task
script assertions and the guard remain active. No scheduler source, installed
task, platform skip or runtime configuration changed. Also added a clock check
after evidence acquisition and before intent creation; an injected slow-source
case proves acquisition cannot authorize against an earlier timestamp.

## Final hosted verification

Push run 42409885570 and PR run 42409891844 at
`1c7e94e6356f4de36ab4f54912fd641f45f0617c` both SUCCESS.
Linux PR full suite: **3,636 passed / 11 skipped / 390 warnings**, 125.59 s,
exit 0. Protected paths and security-regression jobs also passed. Follow-up local
scheduler/risk tests: **65 passed**. The source commit is remote-verified.
Draft PR #5 remains OPEN, MERGEABLE and unmerged. See ENGINEERING_HANDOVER.md for
scope, actual commands/results, remaining activation gates and next exact milestone.
