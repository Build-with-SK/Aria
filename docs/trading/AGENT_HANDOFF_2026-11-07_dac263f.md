> **Historical.** Claude's handover at `dac263f`, written before commits
> `0abe76b`..`268af1d` landed. Sections on uncommitted RQ work, the unpublished
> branch and next steps are superseded by [AGENT_HANDOFF.md](AGENT_HANDOFF.md).
> Two hosting details are generalised for the public repository.

# ARIA — agent handover report

Prepared by Claude Code on 2026-11-07 (19:00 UTC) for the move to two
engineering agents (Claude Code and Codex) coordinated by ChatGPT.

This report was written without changing anything else: no file was edited,
reset, discarded or committed while preparing it, and this file itself is
**uncommitted**. Live trading is disabled and stays disabled. The Oracle
deployment and its configuration were not touched.

Paths are given relative to the owner's home folder (`%USERPROFILE%`) because
the repository is public.

---

## 1. Repository, branch and latest commit

### Where the work is

| | |
|---|---|
| Worktree | `%USERPROFILE%\Documents\aria-multiasset-trading` |
| Branch | `feature/aria-multiasset-trading` |
| Latest commit | `dac263f` — "QA-01..QA-04: backtester time order, intrabar stops, calendar annualisation, path replay" (2026-11-06) |
| Upstream | none configured; **the branch is not on GitHub** |
| Remote | `origin` = `https://github.com/Build-with-SK/Aria.git` (**public**) |

### How it relates to other branches

| Ref | Commit | Relationship to `dac263f` |
|---|---|---|
| Reviewed commit | `7385130` | 12 remediation commits since the review (`105fbc6`..`dac263f`) |
| Local integration `aria/integrity-upgrade` | `58f5f25` | 6 Codex multiasset commits + 12 remediation commits ahead |
| Remote `aria/integrity-upgrade` (`git ls-remote`, 2026-11-07) | `c2ffa8b` | **21 commits ahead**: 3 local-only integration commits (`b77c523`, `3c88cf7`, `58f5f25`), 6 Codex multiasset commits (`5e8767c`..`7385130`), 12 remediation commits |
| Remote `main` | `0ec8c3f` (2026-09-11) | 129 commits ahead |

Pushing the feature branch therefore also publishes the 3 integration commits
and the 6 Codex commits for the first time.

### Main checkout (not used by this task)

`%USERPROFILE%\Documents\trading-intelligence-system`, branch
`aria/integrity-upgrade` at `58f5f25`. It holds live data files and the owner's
uncommitted state; this task never edited it (see section 2).

### Worktree hazard

`frontend/node_modules` in the worktree is a **junction** into the main
checkout's `frontend/node_modules`. Never remove this worktree with
`git worktree remove` or a recursive delete: both follow junctions and would
delete the main checkout's packages.

---

## 2. Modified, untracked and uncommitted files

### Worktree `aria-multiasset-trading`

| Status | File | What it is |
|---|---|---|
| modified | `src/research/multiasset.py` | RQ-01/RQ-02 work in progress (+79/−23 lines together with contracts.py) |
| modified | `src/trading/contracts.py` | `TradeHypothesis.client_evidence` field (+4 lines) |
| untracked | `src/research/sources.py` | New `SourceResolver` (RQ-01) |
| untracked | `docs/trading/AGENT_HANDOFF.md` | This report |

Everything else on the branch is committed.

### Main checkout `trading-intelligence-system` (pre-existing; not touched)

| Status | File |
|---|---|
| modified | `data/alerts.json`, `data/macro_data.json`, `data/ml_predictions.json`, `data/signals.json`, `data/trade_candidates.json` |
| modified | `data/political/political_signals.json`, `data/political/raw/auto_fetch_state.json`, `data/political/watchlist.json` |
| modified | `docs/collab/log-claude.md`, `docs/collab/log-codex.md` |
| untracked | `data/chat_stream_metrics.jsonl`, `data/pipeline_run.json` |
| untracked | a local cloud-migration handover note (infrastructure details; never committed) |
| untracked | `docs/trading/` — the original review report and probes. The branch carries a copy with hosting details generalised. |

The `data/` files are runtime outputs. Leave them alone unless the owner says
otherwise.

### Outside the repository

The before/after evidence runs and full-suite logs live in the Claude
session's temporary scratchpad and are not part of the repository. The
reproducible scripts are committed under `docs/trading/claude_review_probes/`.

---

## 3. Work completed since the independent review

1. **Independent review.** Claude reviewed Codex's multiasset extension at
   `7385130`, read-only. Verdict: **CONDITIONAL_REVIEW**, with 23 findings.
   Report: `docs/trading/CLAUDE_INDEPENDENT_REVIEW.md`.
2. **Sole-ownership remediation.** On 2026-11-06 the owner made Claude the sole
   implementation agent (`docs/collab/WORK_PLAN.md` §8). It worked in small
   stages; each was committed only after:
   - the reviewed failure was reproduced against `7385130`;
   - a regression test failed before the fix and passed after it;
   - the targeted suites passed;
   - the full suite later passed on a `git archive` copy of the commit.
3. **Delivery tracker.** `docs/trading/ARIA_DELIVERY_TRACKER.md` records status,
   evidence, decisions and the next action.

| Stage | Commit | Content |
|---|---|---|
| 0 | `105fbc6` | Review, probes and delivery tracker imported into the branch |
| 1 | `3f6ec66` | CR-02 |
| 2 | `e0d9c89` | CR-05, CR-10 |
| 3 | `4027f07` | CR-03 |
| 4 | `ff1d23a` | CR-04, CR-13 |
| 5 | `4e1cfde` | CR-01 |
| 6 | `f7bf452` | CR-07, CR-12 |
| 7 | `2fe1d0b` | CR-08, CR-09 |
| 8 | `f103ace` | CR-11 |
| 9 | `7bfe3bd` | CR-06, CR-14, SEC-01 |
| 10 | `e717ea6` | UI-01, OP-01 (documentation, plus a clear refusal on a non-Windows host) |
| 11 | `dac263f` | QA-01, QA-02, QA-03, QA-04 |
| 12 | uncommitted | RQ-01, RQ-02 (in progress) |

Docs updated along the way:

- `FTMO_RULE_PROFILES.md`, `RISK_POLICY.md`, `SECURITY_MODEL.md`
- `KNOWN_LIMITATIONS.md`, `MT5_SETUP.md`, `BROKER_ADAPTERS.md`
- `OPERATIONS.md`, `IMPLEMENTATION_MATRIX.md`, `BACKTEST_VALIDATION.md`

---

## 4. Findings fixed

"Before" means the failure was reproduced at `7385130` (scripts in
`docs/trading/claude_review_probes/`). All tests named below are in
`tests/test_review_findings.py` unless another file is given.

| ID | Sev | Finding | Commit | Regression tests | Before (at `7385130`) |
|---|---|---|---|---|---|
| CR-02 | HIGH | Approval store trusted a caller-built ALLOW | `3f6ec66` | `test_cr02_*` (7) | Probe R1: a forged ALLOW reserved an intent |
| CR-05 | HIGH | Daily stop ignored balance vs equity | `e0d9c89` | `test_cr05_*` (2); `test_multiasset_risk.py::test_internal_daily_stop_*` (4) | E8 ALLOW |
| CR-10 | MED | A trade could land exactly on the floor | `e0d9c89` | `test_cr10_*` (4) | E7 ALLOW; the equality case was allowed |
| CR-03 | HIGH | Contract-size errors went undetected | `4027f07` | `test_cr03_*` (7) | Gold, 10 lots, wrong contract size: ALLOW |
| CR-04 | HIGH | FX conversion checked by label only | `ff1d23a` | `test_cr04_*` (3); `test_multiasset_risk.py::test_cross_currency_requires_fresh_pair_quote` | Inverted rate: ALLOW at $39.37 risk; true risk $63.50 |
| CR-13 | LOW | One FX rate, taken at today's price | `ff1d23a` | `test_cr13_*` (2) | Converted at today's rate |
| CR-01 | HIGH | Trailing high-water lowered by re-versioning the profile | `4e1cfde` | `test_cr01_*` (4) | High-water 10,800 → 10,000 |
| CR-07 | HIGH | "DEMO" was treated as the safety boundary (every FTMO account is a demo) | `f7bf452` | `test_cr07_*` (13) | Paid FTMO Challenge on a DEMO server: ALLOW and queued |
| CR-12 | LOW | FTMO detected by exact strings | `f7bf452` | `test_cr12_*` (15) | "FTMO s.r.o." with London validated; wrong 1-Step formula gave PASS |
| CR-08 | HIGH | News, gap and weekend rules not enforced | `2fe1d0b` | `test_cr08_*` (33) | No calendar or session inputs; NFP 90 s away on a Standard account: ALLOW |
| CR-09 | MED | No per-day server-request, position or order limits | `2fe1d0b` | `test_cr09_*` (13) | Only a per-minute limit existed |
| CR-11 | MED | Book and operational inputs were self-asserted | `f103ace` | `test_cr11_*` (19), plus the reservation re-check | `ARIA_KILL_SWITCH=1` engaged and `assess` still gave ALLOW; 13 result fields were caller-stated |
| CR-06 | HIGH | MT5 adapter not genuinely read-only | `7bfe3bd` | `test_cr06_*` (7); `test_trading_structure.py` SDK-import and adapter-construction tests (2) | Trade-enabled login and algo-trading terminal connected; raw `adapter._sdk.order_send` reached the SDK |
| CR-14 | LOW | Backup left a connection open | `7bfe3bd` | `test_cr14_*` (1) | Backup target connection never closed |
| SEC-01 | LOW | Audit chain was unkeyed | `7bfe3bd` | `test_sec01_*` (6) | SHA-256-rebuilt chain accepted and still reserved an intent; truncation accepted |
| UI-01 | LOW | "EXECUTION DISABLED" banner could mislead | `e717ea6` | `frontend/scripts/verify-multiasset.mjs` banner assertions | Old component fails "banner must say the extension has no order sender" |
| QA-01 | HIGH | Backtester breaker look-ahead | `dac263f` | `test_qa01_*` (2), incl. a change-only-this-bar's-close test; `test_no_lookahead.py` passes | Breaker exit at 100 after a close of 70; final equity 100,000 instead of 70,000 |
| QA-02 | MED | Fixed 252-period annualisation | `dac263f` | `test_qa02_*` (15) | A one-year doubling (365 daily returns) gave CAGR 0.6116, assuming 1.45 years |
| QA-03 | MED | Close-only stops | `dac263f` | `test_qa03_*` (4) | Low −10%, close flat, 5% stop: no stop exit |
| QA-04 | MED | No path-level FTMO breach simulation | `dac263f` | `test_qa04_*` (7) | No path replay existed; the only resampler saw a −5.1%-intraday day as its −3% close |

**20 of 23 findings are fixed and tested.**

Bookkeeping gap: the tracker still lists QA-01..QA-04 against "stage 11" (the
commit is `dac263f`), and it does not yet record the `dac263f` full-suite
result.

---

## 5. Findings still open, blocked or unverified

### Open: in progress, uncommitted

**RQ-01 (MED) — caller-asserted "verified" evidence.**

- Code written:
  - `src/research/sources.py` adds a `SourceResolver`. It fetches the source
    itself: https only, an explicit owner-chosen host allowlist, bounded size
    and time, no redirect to another host. It records the document's SHA-256
    and fetch time, and accepts a claim only if the claim's text appears in
    the fetched document.
  - `analyse()` never counts a caller's OBSERVED or VERIFIED_EXTERNAL_FACT
    claim as support unless that resolver verified it. Such claims are
    returned in the new `TradeHypothesis.client_evidence` field, labelled
    UNVERIFIED.
- Not yet done: regression tests and the tracker entry.
- Before-evidence exists in `probes_out.json` (`S_*`): a fabricated "ECB will
  cut 200bp" claim appeared as verified support.
- Design decision: no HTTP route builds a resolver. Fetching URLs named in
  client input is a server-side request (SSRF) risk that needs its own review
  and an owner decision on the allowlist. Over the API, every client
  "verified" claim is therefore downgraded.

**RQ-02 (MED) — contradictory hypotheses and the SR logic defect.**

- Code written:
  - The support/resistance rule now requires the close at or above support
    (or at or below resistance).
  - Every trigger family is evaluated on the same bar. Any family pointing
    the other way makes the result ABSTAIN and is listed as contradicting
    evidence.
  - `triggers_on_latest_bar()` exposes all the triggers.
- **One existing test fails, as the review's rule predicts:**
  `tests/test_multiasset_research.py::test_strategies_abstain_for_missing_data_and_unknown_regime`.
  Its steadily rising fixture has RSI near 100, so MeanReversion and
  SupportResistance say SELL against TrendFollowing's BUY.
- A replacement fixture has been identified but not yet written into the
  test: an uptrend with pullbacks (+3, +3, −2.5, −2.5 pips, repeated) whose
  last bar breaks out. It has RSI 59, every triggering family says BUY, and
  the result is APPROVE_FOR_HUMAN_REVIEW.
- Not yet done: that fixture change, RQ-02 regression tests (the review's
  probe bar must ABSTAIN for every family) and the tracker entry.

### Blocked

- **OP-01 (MED).** An MT5 worker needs a separate, always-on Windows host.
  The cloud runtime is Linux and cannot run the terminal. Documented in
  `OPERATIONS.md`; the worker refuses clearly elsewhere. This needs an owner
  decision.
- **External.** Written FTMO clarification on the AI/software and third-party
  clauses. Needed before any order stage, not for this delivery.

### Unverified (no evidence either way)

- No genuine MT5 terminal, investor login or Windows worker host has been
  exercised. Tests use a fake SDK inside a real spawned process.
- `SourceResolver`'s default network fetcher has not been run against a real
  site; tests inject a fetcher.
- No multiasset strategy has been backtested, validated, paper-traded or
  demo-traded. `breach_probabilities` intervals cover simulation error only.
- The deployed runtime is a snapshot of `58f5f25`, without this branch.
  Whether the cloud configuration enables the legacy backtester is unverified.

### Release work not yet done

- Commit RQ-01/RQ-02 with tests. Then run a full suite on that commit.
- Write an "after" probe script that mirrors `probes.py` on the new API, and
  re-run every probe.
- `npm run build` for the frontend. Only the offline render verifier has run
  since the UI-01 change.
- `npm audit` and `pip-audit`.
- Secret, credential, `.env`, personal-data and absolute-path scan of the full
  diff. The repository is public.
- Reconcile Codex's earlier docs (`REVIEW_PACKET.md`, `TEST_EVIDENCE.md`,
  `PR_SUMMARY.md`, `CHANGED_FILES.md`, parts of `IMPLEMENTATION_MATRIX.md`).
  They still state guarantees the review refuted.
- Add this task's entries to `docs/collab/log-claude.md`. Update the tracker
  (QA commit id, `dac263f` suite result, RQ rows).
- Push the branch without force, verify the remote branch and commit, then
  open a pull request. Do not merge into `aria/integrity-upgrade` or `main`
  directly.

### Observed but deliberately not changed

- The root `main.py` backtest hook imports `Backtester` and `compute_metrics`,
  which `src/backtesting` does not define. The hook is off by default; if
  enabled, it logs a warning and returns nothing.
- Running the `tests/test_trading_*.py` subset on its own hits a blocked write
  to `data/execution/approval_queue.json`. Nothing is written, it is the same
  at `7385130`, and the full suite isolates it.

---

## 6. Latest full test-suite results

Command, run on a `git archive` copy of each commit with the shared venv:
`python -m pytest -q -p no:cacheprovider --no-header -rs`.

| Commit | Result | Time |
|---|---|---|
| `7385130` (review baseline) | 3,432 passed, 10 skipped, exit 0 | 392 s |
| `4e1cfde` | 3,454 passed, 21 skipped, exit 0 | 352 s |
| `f7bf452` | 3,482 passed, 21 skipped, exit 0 | 364 s |
| `2fe1d0b` | 3,528 passed, 21 skipped, exit 0 | 366 s |
| `f103ace` | 3,546 passed, 21 skipped, exit 0 | 375 s |
| `7bfe3bd` | 3,563 passed, 21 skipped, exit 0 | 388 s |
| **`dac263f` (branch head)** | **3,594 passed, 21 skipped, exit 0** | 376 s |

- **Extra skips.** The 11 extra skips are git-only tests (`test_protected_paths`,
  `test_selfmod_sandbox`) that need a git checkout. They pass when run in the
  worktree.
- **Uncommitted state** (2026-11-07, targeted run): 329 passed, **1 failed**
  (the RQ-02 fixture test above). Files: multiasset, structure, review
  findings and look-ahead tests. No full suite has been run on the
  uncommitted state.
- **Frontend.** `node scripts/verify-multiasset.mjs` passes at `e717ea6`.
  `npm run build` has not been run on this branch since then.

---

## 7. Current ARIA architecture and outstanding issues

### Runtime (from Codex's cloud-migration handover; not verified on the server by Claude)

- Since 2026-11-06 the live ARIA and Sentinel host is a cloud Linux host
  [detail generalised for the public repository], running a snapshot of `58f5f25`. It does **not** contain this
  branch.
- Windows writer tasks are deliberately disabled.
- At handover: broker disconnected, PAPER mode, live gate closed, auto-execute
  and reflex off.

### Legacy ARIA (unchanged by this work)

- FastAPI backend: `backend/main.py`, owner-token auth, step-up for sensitive
  routes.
- React/Vite frontend.
- Signal pipeline, ML and the "brain" (`src/brain`, `src/intelligence`).
- The legacy desk and execution: `src/desk`, `src/execution` with Alpaca and
  IBKR adapters, the kill switch, risk profiles and pre-trade gate, and
  `approve_and_execute`, which is owner plus step-up and never autonomous.

### Multiasset extension (this branch): three separated layers

**Intelligence — research only, no LLM, no order.**

- `src/research/multiasset.py`: ten strategy families with deterministic
  triggers on completed, validated candles. It abstains on missing data, an
  unknown regime, staleness or (after RQ-02) a cross-family conflict.
- `src/research/sources.py` (uncommitted): server-side source checks.
- Routes:
  - `POST /api/trading/research` (owner-only) returns a hypothesis and
    `order_created: False`.
  - The workspace status route reports `execution_mode: DISABLED`.

**Deterministic risk.**

- `src/trading/contracts.py`: frozen, validated contracts. They are
  revalidated at every trust boundary.
- `src/trading/rule_profiles.py`: FTMO and other rule profiles, built from
  enums.
  - FTMO stage fixes the account class (PRACTICE, PAID_EVALUATION, FUNDED or
    LIVE).
  - Prague-day reset.
  - Published daily limits.
- `src/trading/operational_rules.py`: news, session, break, daily-activity and
  instrument checks against evidence.
- `src/trading/multiasset_risk.py` (`assess`): takes evidence only and derives
  the book, costs, session and cooldown itself.
  - It reads the legacy kill switch and fails closed.
  - LIVE account classes are disabled.
  - Buffered floors.

**Review — no execution.**

- `src/trading/review_store.py`: SQLite with a keyed, anchorable audit chain.
  - It runs `assess` itself under its own policy.
  - PRACTICE accounts only.
  - Single-use approval token, intent reservation, recovery.
- **There is no order sender in the extension.**

**Broker (read-only).**

- `src/trading/mt5_readonly.py` and `src/trading/mt5_worker.py`.
- The SDK runs only in a separate worker process, with an investor login,
  allowlisted reads and plain data back.

**Backtesting.**

- `src/backtesting/backtester.py`: legacy, now time-ordered, with intrabar
  stops.
- `src/backtesting/metrics.py`: calendar-aware annualisation.
- `src/backtesting/experiments.py`.
- `src/backtesting/challenge_replay.py`: path-level rule replay.

**Frontend.**

- `frontend/src/components/MultiassetWorkspace.jsx`: banner
  "EXTENSION: NO ORDER SENDER", no order controls.

### Enforced boundaries

- The LLM cannot reach `assess`, the review store or any sender.
- Structure tests check that no LLM module imports a broker, and that only
  `mt5_worker.py` imports `MetaTrader5`.
- Plugins and MCP are declaration-only: third-party loading is disabled and
  MCP execution is disabled.

### Invariants (`docs/collab/WORK_PLAN.md` §4)

- Nothing calls `approve_and_execute` autonomously.
- Kill switch, risk profiles, the pre-trade gate and reduce-only stay on every
  order path.
- An UNKNOWN session is never OPEN.
- The vault, `.env` and credentials are never copied or committed.
- No claim of improvement without measured before/after evidence.

### Outstanding architectural issues

1. RQ-01/RQ-02 are uncommitted (section 5).
2. The source resolver is not wired to any route. Doing so needs an SSRF and
   allowlist review.
3. Correlated risk is approximated as "shares a currency with the
   instrument", because no correlation data exists yet.
4. Audit key and anchor storage are the operator's: no key manager or anchor
   store ships.
5. The MT5 worker host (OP-01). Any future bridge from a Windows host to the
   cloud runtime is a new trust boundary.
6. The legacy backtester still uses equity-style percentage costs, with no
   per-instrument spread, swaps or lot model.
7. SQLite stores are local and unencrypted.
8. The cloud runtime's local model is slow (2-core host). No time-sensitive
   path may depend on it; news and break checks are deterministic.
9. The deployed runtime predates this branch. Nothing here is deployed, and
   deployment is out of scope.

---

## 8. Recommended next task

### Next: finish RQ-01 and RQ-02 and commit them (Claude, same worktree)

This is the smallest step and it unblocks everything after it. Two agents
should not edit `src/research/multiasset.py` at the same time.

1. Replace the rising fixture in
   `test_strategies_abstain_for_missing_data_and_unknown_regime` with the
   agreeing-families fixture from section 5. This is not a weaker test: it
   still requires a reviewable case to reach APPROVE_FOR_HUMAN_REVIEW.
2. Add regression tests:
   - RQ-01:
     - a fabricated VERIFIED claim comes back in `client_evidence` as
       UNVERIFIED, never in `supporting_evidence`;
     - client OBSERVED is downgraded;
     - the resolver checks: allowed host plus claim present → verified;
       claim absent, http, another host or a redirect away → unverified;
       a verified claim edited afterwards → not verified.
   - RQ-02:
     - on the review's probe bar every family abstains;
     - the SR trigger is None when the close is below support.
3. Record before/after evidence, update the tracker, commit, and run a full
   suite.

### Then: release, under the coordinator's sign-off

- Run the release checks listed in section 5.
- Push `feature/aria-multiasset-trading` to `origin` without force, verify
  the remote branch and commit, and open a pull request.
- The push also publishes the 3 integration commits and the 6 Codex commits
  (section 1). Confirm that is wanted.

### Good first task for Codex (no file conflicts)

An independent, read-only cross-review of the 12 remediation commits
(`105fbc6`..`dac263f`), and then of the RQ commit. Work in Codex's own
worktree and report only. While Claude worked alone, the cross-review rule
(WORK_PLAN rule 6) could not be met. This review closes that gap before the
pull request is merged.

### Coordination note

`docs/collab/WORK_PLAN.md` §8 still says Claude is the sole implementation
agent. The owner or coordinator should record the new two-agent arrangement
there. Two options:

- restore the §3 ownership split;
- or assign each area per task.

Either way, the `aria-multiasset-trading` worktree should stay with one agent
until its pull request is open.
