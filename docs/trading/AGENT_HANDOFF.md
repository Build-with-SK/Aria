# ARIA — agent handover (current)

Updated 2026-11-07T20:39Z by Claude, implementation owner (`docs/collab/WORK_PLAN.md` §9).
Previous handover (at `dac263f`, now historical):
[AGENT_HANDOFF_2026-11-07_dac263f.md](AGENT_HANDOFF_2026-11-07_dac263f.md).

## 1. Where things are

| | |
|---|---|
| Branch / worktree | `feature/aria-multiasset-trading` in worktree `aria-multiasset-trading` |
| Published | `origin/feature/aria-multiasset-trading` = `268af1d`, draft PR #5 (pushed by the Codex implementation agent) |
| Stage 1 commits (local, **not pushed**) | `3bb9f8d` code + tests; the documentation commit that follows it |
| Execution | disabled; no sender, no broker, no FTMO account, no Oracle or runtime change |

## 2. Authorship disclosure

| Commits | Author | Independent review |
|---|---|---|
| `105fbc6`..`dac263f` (12) | Claude | Codex review of `dac263f`: REQUEST CHANGES — 7 VERIFIED_FIXED, 12 PARTIALLY_FIXED, 1 REGRESSION_FOUND |
| RQ-01/RQ-02 first version (uncommitted at `dac263f`) | Claude | completed and committed by the Codex implementation agent in `7e8570a`; not reviewed |
| `0abe76b`..`268af1d` (11) | Codex implementation agent, in Claude's worktree, before WORK_PLAN §9 | none yet; Claude re-tested the P0 parts in Stage 1 (below) and found a regression |
| `3bb9f8d` and its documentation commit | Claude | **awaiting Codex** |

## 3. The review report is empty on disk

`CODEX_INDEPENDENT_REVIEW.md` in the review worktree was 0 bytes when read at
2026-11-07 20:21 UTC (modified 20:17, a minute after `268af1d`). Claude worked
from the reviewer's own artefacts in that worktree, read only:
`review_adversarial.py`, `review_negative_tests.py`, `review-adversarial-results.json`,
`baseline-additional-results.json`, `REVIEW_COORDINATION.md`, and the owner's
summary (7 / 12 / 1, 15 failing safety tests). The per-finding classification in
the tracker is inferred from those scripts and totals. **Codex: please restore the
report**; the mission names it as the source of truth.

Re-running `review_adversarial.py` unchanged:
- on `dac263f` reproduces the recorded `review-adversarial-results.json` exactly;
- on `268af1d` every store attack stops at "trusted account-bound evidence source
  required" because the script builds a store without the source that is now
  mandatory. 9 of the 15 negative tests then fail on result shape (KeyError), not
  because an attack succeeded. Those tests verify nothing about the new store
  until they configure a source and checkpoint; Claude's probe below does.

## 4. Stage 1 — P0 approval and evidence authority

### What Claude checked

`docs/trading/claude_review_probes/p0_configured_store_probe.py` (fixture-only)
attacks a **configured** store: trusted `BoundRiskSource` plus external
checkpoint. Results for both commits are in `p0_configured_store_results.json`.

| Attack | `268af1d` | Stage 1 |
|---|---|---|
| SQL-only: invent approval, replay consumed token, swap payload, lower high-water row | refused | refused |
| Roll the database back to before a reservation | refused | refused |
| Caller substitutes empty book / zero activity / other calculator / FUNDED class | refused | refused |
| Kill switch engaged; proposal for another account | refused | refused |
| **Recorded midnight balance below the source's** (source: EMERGENCY_HALT) | **ALLOW** | refused: disagrees with source |
| **Recorded high-water below the source's** (source: DENY) | **ALLOW** | refused: DENY |
| **Source reports a withdrawal** (source: UNVERIFIED) | **ALLOW** | refused: UNVERIFIED |
| **Source reports an account reset** (source: UNVERIFIED) | **ALLOW** | refused: UNVERIFIED |
| **Instance without the checkpoint changes a keyed store; file rolled back** | hold released, unnoticed | change refused |
| **Recorder writes history for an account the store is not bound to** | written | refused |
| `approve()` with any actor string | approval issued | unchanged (design item CL-01) |

### Root cause

`962d8d3` made recorded boundary history **replace** the trusted source's account
state whenever any history existed. Boundary rows come from
`record_rule_boundary`, whose caller supplies the snapshot and an unchecked
evidence hash, and the rebuilt state dropped the source's cashflow/reset flags.
So recorded rows could loosen what the trusted source reported: a regression
against CR-01 and CR-05. Separately, a keyed store accepted changes from an
instance opened without its checkpoint, so the checkpoint could lag the head and
a rollback past those changes passed.

### The change (`3bb9f8d`, `src/trading/review_store.py` only)

- `_tighten_with_history`: the source's state is the base. Today's recorded
  midnight balance must equal the source's exactly (same instant, same amount) or
  review is refused; the high-water is the higher of the two; equity, cashflows
  and resets are always the source's. Recorded history can tighten, never loosen.
- `_transaction`: a keyed store refuses any change without its `anchor_path`.
  `_verify_external_checkpoint` also requires the checkpoint to name the audit
  **head**, so any rollback, to any earlier point, is detected.
- `queue` refuses before opening a transaction when no evidence source is
  configured (keeps that refusal's message ahead of the checkpoint one).
- `record_rule_boundary`: a store bound to a source records only that account.

### Tests

New `tests/test_authority_followup.py`, 9 cases. On `268af1d`: 8 failed at the
attack step ("DID NOT RAISE"; each test's setup check of the source verdict
passed), 1 passed — `test_recorded_high_water_still_raises_a_lower_source_value`,
which pins behaviour that must survive (a lower transport high-water cannot
lower the recorded one). After the change: 9 passed.

Existing tests edited — **please scrutinise**; each still refuses the attack:

| Test | Before | Now | Why |
|---|---|---|---|
| `test_remediation_followup::test_store_history_overrides_transport_reconstructed_state` | refused, `EMERGENCY_HALT` | refused, "disagrees with the authoritative source" | its source (9,850) and record (10,450) disagree on today's midnight; that integrity failure is now reported first. Its intent is kept by the new sibling test above |
| `test_remediation_followup::test_reservation_requires_external_checkpoint` | keyed store without checkpoint queued and approved; reservation refused | refused at `queue`; nothing stored; reservation also refused | a keyed store now changes only with its checkpoint |
| `test_multiasset_review::test_atomic_single_use_and_restart_recovery` | restart reopened without the checkpoint | restart reopens with the same `anchor_path` | an unchecked restart is exactly the writer the rollback attack used |

Results: targeted review-store/risk/structure selection **319 passed**.
Full suite (scratch copy of the working tree): **3,647 passed, 21 skipped, 1 warning, exit 0** (243 s); the 21 skips are 11 git-only tests, which pass in the worktree, plus production-data and opt-in sweeps.

### Still open in P0 (not changed by Stage 1)

- **CL-01:** `approve()` takes the owner's identity from its caller. No endpoint
  or model path exposes it, but before any endpoint does, it should be bound to
  the owner's step-up session the way evidence is bound to `BoundRiskSource`.
  Owner/coordinator decision.
- `record_rule_boundary` still takes a caller snapshot; after Stage 1 those rows
  can only tighten or cause a refusal. Binding the recorder to the source's own
  boundary reader is the cleaner end state.
- `BoundRiskSource` readers are fixtures. No real provider reader, investor login
  or Windows host exists (OP-01, external).
- Submitted `inputs` must byte-match a fresh acquisition. That is safe, but with
  a real, moving source most calls would refuse; a later API could drop the
  caller's copy and assess the acquisition alone.
- The store does not count its own queues/reservations toward daily activity
  limits; it relies on the source (P2).

### What Codex should try to break

- Any remaining path where store-held state, rather than the acquired source, is
  what `assess` sees.
- Checkpoint handling: two anchored instances on one database; an interrupted
  commit; restoring database and checkpoint together (outside the database-only
  model, but state the boundary).
- Whether the edited tests above lost any coverage.

### Publication note (P3-PII)

Two collaboration files on this branch, `docs/collab/CODEX_HANDOVER.md` (since
2026-10-11) and `docs/collab/log-codex.md` (since 2026-10-16), contain local
Windows paths that include the owner's username. They are already public on
several remote branches; this branch did not introduce them. Stage 1 adds none.
Removing them from history needs a rewrite, which is the owner's decision.

### Reproduce

From the repository root, with the project venv:

```
python -m pytest -q -p no:cacheprovider tests/test_authority_followup.py
python docs/trading/claude_review_probes/p0_configured_store_probe.py .
```

To see the regression, run the same probe with a `git archive 268af1d` copy as
its argument.

## 5. Next stage (Stage 2, P1 backtesting) — Claude

Still reproducing at `268af1d` with the reviewer's own script:
- `REPLAY_CLOSE_BALANCE`: a day whose balance closes at 8,900, below both
  floors, replays as OPEN.
- `REPLAY_TARGET_OPEN_LOSS`: the target counts as reached at balance 11,000 while
  equity is 9,600.

Then re-verify the Codex implementation agent's P1 fixes (missing Open, missing
OHLC disclosure, hourly timestamps, equity accounting, 21-hour cadence) against
the reviewer's cases, adding failing-first tests where coverage is missing.
