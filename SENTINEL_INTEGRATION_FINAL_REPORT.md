# SENTINEL INTEGRATION — FINAL REPORT

**Status: CLOSED — PROTOCOL VERIFIED, REAL SENTINEL RUNTIME NOT PRESENT**

**SENTINEL advises. ARIA decides.**

Commits reviewed: `4b5e4ae`, `3180241`, `246394d` (branch
`fix/signal-breadth-discount`, on top of `215c1a2`).

---

## A. Executive result

The ARIA ↔ SENTINEL advisory boundary is implemented, repaired, and verified end
to end against a controlled HTTP stub. The boundary already existed and was
already the right shape; this work was repair, not construction. No new module,
service, worker, database, protocol layer, or UI subsystem was created.

One genuine bug was found and fixed (a malformed reply crashed ARIA through the
API), one silent safety hole was closed (only one kind of consultant silence was
disclosed to the approving human), and one privilege-escalation shape was
removed (SENTINEL could overwrite ARIA's own result fields).

**The real SENTINEL process was never running on this machine and has never been
contacted.** This is a verified protocol implementation, not a live SENTINEL
integration.

## B. Final architecture boundary

```
┌─────────────────────────────────────────┐
│               SENTINEL                  │
│   general intelligence · own process    │
│   own brain, memory, tools, workers, UI │
└────────────────────┬────────────────────┘
                     │
              advisory bridge
        one authenticated POST, outbound only
                     │
                     ▼
┌─────────────────────────────────────────┐
│                 ARIA                    │
│   trading intelligence                  │
│   research · strategy · risk · ledger   │
└────────────────────┬────────────────────┘
                     │
                     ▼
              FINAL DECISION
        ARIA reasoning + ARIA evidence
        + ARIA risk controls + human approval
```

The arrow is one-way in authority. SENTINEL has **no inbound route into ARIA**.
ARIA imports no SENTINEL code and reads no SENTINEL state; a test walks the AST
of every module under `src/` and fails if anything outside `src/consult/`
imports a module with "sentinel" in its name.

## C. Commit-by-commit changes

Thirteen files across three commits. Nothing else.

### `4b5e4ae` — the boundary (12 files, +3092 / −20)

| File | Change |
|---|---|
| `src/consult/sentinel_client.py` | +525 · transport; status/position vocabulary, malformed + timeout handling, latency, payload redaction |
| `src/consult/bridge.py` | +348 · judgement; audit record, non-raising `ask()`, single liveness probe |
| `src/consult/__init__.py` | +1 · package docstring |
| `src/core/bus.py` | +336 · event bus (**dependency**: the bridge publishes to it) |
| `src/core/__init__.py` | +24 · docstring only |
| `tests/test_sentinel_protocol.py` | +551 · 33 tests, real stub over a real socket |
| `tests/test_sentinel_bridge.py` | +268 · 25 tests (24 pre-existing + 1 probe-count) |
| `tests/conftest.py` | +322 · test data isolation (**dependency**) |
| `docs/ARIA_SENTINEL_CONTRACT.md` | +319 · contract |
| `frontend/src/pages/System.jsx` | +263 · System page incl. the SENTINEL panel |
| `frontend/src/hooks/useApi.js` | +77 / −19 · see §M |
| `src/brain/cognitive/planner.py` | +46 / −1 · consultation on the trading path; **entire diff is SENTINEL** |

### `3180241` — the endpoints (1 file, +64 / −0)

`backend/main.py` only. Purely additive. Four owner-gated routes:

```
GET  /api/consult/status
POST /api/consult
POST /api/consult/red-team
POST /api/consult/outcome
```

### `246394d` — the contract (1 file, +253 / −241)

`docs/ARIA_SENTINEL_CONTRACT.md` only. Rewritten against measured behaviour.

### Confirmation: no unrelated files

Union of files changed across `215c1a2..246394d`:

```
backend/main.py                    src/consult/__init__.py
docs/ARIA_SENTINEL_CONTRACT.md     src/consult/bridge.py
frontend/src/hooks/useApi.js       src/consult/sentinel_client.py
frontend/src/pages/System.jsx      src/core/__init__.py
src/brain/cognitive/planner.py     src/core/bus.py
tests/conftest.py                  tests/test_sentinel_bridge.py
tests/test_sentinel_protocol.py
```

Checked and **absent**: `BrainHub.jsx`, `__pycache__`, `*.pyc`, `frontend/dist/`,
`node_modules`, `*.log`, `*.db`, `*.sqlite`.

### Confirmation: the 64-line backend dependency is genuinely isolated

Measured line counts of `backend/main.py`:

```
215c1a2 (pre-SENTINEL)   3478
4b5e4ae                  3478   (untouched by that commit)
3180241 (committed)      3542   = 3478 + 64
working tree (ARIA V1)   4268   (726 lines still uncommitted)
```

Deletions in `3180241`: **0**. Every added line belongs to the consult block.

ARIA V1 markers confirmed **absent** from the committed file and present only in
the uncommitted working tree:

| Marker | committed `3180241` | working tree |
|---|---|---|
| `api/aria/world` | 0 | 3 |
| `api/ledger/investigate` | 0 | 1 |

`git diff 3180241 -- backend/main.py` still reports **739 insertions, 13
deletions** outstanding — the ARIA V1 refactor, deliberately left uncommitted.

Isolation was possible because everything the block needs already existed at
`4b5e4ae`: `app`, `Depends`, `HTTPException`, `Optional`, `_sanitize`, and
`from src.auth.guard import require_owner` (line 68). The block sits before the
route-guard check, which inspects `app.routes` at import time and refuses to
start if a state-changing route is unguarded.

## D. Security / authority verification

| Control | Mechanism | Measured result |
|---|---|---|
| Cannot call into ARIA | Outbound only; no route authenticates SENTINEL | test passes; no `SENTINEL_CONSULT_TOKEN` in any route |
| Cannot reach execution | All routes `Depends(require_owner)` | 4/4 owner-gated, verified from the live route table in a clean worktree |
| Cannot veto | Nothing branches on a SENTINEL field | trade produced despite `veto: true`, `block_trade: true` |
| Cannot forge state | ARIA's fields overwrite SENTINEL's | it sent `sentinel_status: "AGREES"`; ARIA recorded `AVAILABLE` |
| Cannot bypass risk | ARIA's quarter-Kelly, 5% cap | it asked for `sell qty 999999`; ARIA proposed `buy qty 50`, $5,000 of $100,000 |
| Cannot bypass human approval | Planner only *plans* | `skip_human_approval: true` ignored |
| No secrets outbound | Payload scrubbed; token in header | planted secrets arrived `[REDACTED]` |

Outgoing payload as the stub actually received it from the **live** backend —
eleven fields, all caller-supplied, no credentials:

```json
{"caller": "ARIA", "question": "Does this thesis hold?", "problem": "",
 "aria_context": "AAPL long on momentum", "current_hypothesis": "",
 "evidence": [], "counter_evidence": [], "what_aria_already_tried": [],
 "what_aria_is_uncertain_about": [], "desired_output": "ANALYSIS",
 "urgency": "NORMAL"}
```

Adversarial authority test, run against the real `TradePlanner` and the real
bridge over a real socket: **49/49 checks passed.**

## E. Failure-mode verification

```
MODE               STATUS         POSITION   ok      READS AS AGREEMENT?
--------------------------------------------------------------------------
AVAILABLE          AVAILABLE      DISAGREE   True    no
UNCERTAINTY        AVAILABLE      UNCERTAIN  True    no
MALFORMED          MALFORMED      NONE       False   no
MALFORMED(array)   MALFORMED      NONE       False   no
TIMEOUT            TIMEOUT        NONE       False   no
UNAVAILABLE        UNAVAILABLE    NONE       False   no
```

Every non-available status returns guidance containing the literal words
*"This is NOT agreement."* After all six modes, the planner still produced its
trade. `AGREE` is returned only when SENTINEL explicitly states it — never
inferred from an absence of objections.

## F. Audit / event-bus verification

One consultation and its outcome, answered entirely from the **existing** event
bus. No second logging mechanism was created.

| Question | Answer from the bus |
|---|---|
| Why did ARIA consult? | `an anomaly may indicate a broken assumption` |
| What did ARIA ask? | `Does the AAPL momentum thesis hold?` |
| Was SENTINEL available? | `AVAILABLE` |
| What did SENTINEL return? | `position=DISAGREE, confidence=0.62, latency=16.0ms` |
| Agree or disagree? | `DISAGREE` |
| Did ARIA change its reasoning? | `True` |
| Did ARIA agree? | `False` |
| Final ARIA decision? | `PROPOSE_BUY AAPL 25 shares, human approval pending` |
| Human approval required? | `True` |

Kinds: `SENTINEL_CONSULTED` (notable), `SENTINEL_UNAVAILABLE` (warning),
`SENTINEL_OUTCOME` (info) — all `source=sentinel_bridge`.

## G. Deterministic consultation behaviour

`should_consult()` is deterministic and is the gate. It returns `(bool, why)`,
and the `why` is carried into the audit record.

| Condition | Consult? |
|---|---|
| outside ARIA's trading domain | yes |
| conflicting evidence | yes |
| anomaly | yes |
| high impact **and** confidence < 0.75 | yes |
| unfamiliar problem | yes |
| own confidence < 0.35 | yes |
| otherwise | no — `NOT_CONSULTED` |

`NOT_CONSULTED` is deliberately **not** disclosed on proposals: it means ARIA
never asked, which is not SENTINEL declining to answer. Every other status is
disclosed by name on the thesis the approving human reads.

Automatic consultation on the trading path is **off** unless
`ARIA_SENTINEL_CONSULT=true`.

## H. UI / build verification

The System page panel names SENTINEL as *external advisory intelligence ·
separate system*, shows the status, and on every non-available state renders:

> ARIA is operating independently. **Silence is not agreement.**

Rendered and confirmed across all four states (`UNAVAILABLE`, `AVAILABLE`,
`NO_TOKEN`, `UNAUTHORISED`).

```
Frontend production build: built in 13.18s, 0 errors
```

## I. Test results

```
SENTINEL tests (test_sentinel_bridge.py)      25 passed, 0 failed
Protocol tests (test_sentinel_protocol.py)    33 passed, 0 failed
  combined                                    58 passed, 0 failed
Authority / failure / audit / security        49/49 checks passed
Full ARIA suite                             1556 passed, 0 failed
Frontend build                                0 errors
Clean-worktree verification (246394d)         58 passed, 0 failed
  test_E_sentinel_has_no_inbound_route          PASSED
  app import + route table                      4/4 routes owner-gated
```

Protocol coverage: A — ARIA independent (5); B — round trip (5); C —
disagreement (4); D — silence (12); E — safety (7).

The full suite collected 1537 earlier and 1556 at the end. Not new tests:
running ARIA's real runtime ticked its daemons, rewriting `brain_state.json`,
`macro_data.json` and `fx_state.json`, and several suites parametrise over that
live state. Collection and execution agree at 1556.

**No test or assertion was weakened.** `test_E_sentinel_has_no_inbound_route_into_aria`
retains three hard assertions, contains no `skip` or `xfail`, and both test files
are byte-identical between `246394d` and the working tree.

## J. Runtime status

```
REAL SENTINEL RUNTIME: UNAVAILABLE
CONTROLLED PROTOCOL:   VERIFIED
ARIA INDEPENDENCE:     VERIFIED
```

Checked, not assumed — twice, including at closure: nothing listening on
`:8300`, `/health` gave no response, no process named `sentinel`, no SENTINEL
entry in `.env`.

With the real (absent) SENTINEL, ARIA's live runtime reported
`sentinel_status: UNAVAILABLE`, `blocked_by: nothing answering at
http://127.0.0.1:8300`, and served `/api/brain`, `/api/regime`,
`/api/aria/workers` and `/api/signals` normally. The protocol half was then
exercised against the controlled stub through the same live API.

**This must not be described as a live SENTINEL integration.** The peer that
answered was a stub implementing SENTINEL's interface, not its intelligence.

## K. Known limitations

1. **The real SENTINEL has never been contacted.** Protocol verified against a
   stub only.
2. **The response schema is ARIA's assumption.** If the real peer names fields
   differently, consultations parse as `AVAILABLE` with position `NONE` and a
   thin summary — degraded, not broken, and visible in the record.
3. **`report_outcome` needs a `request_id`**, which only a successful
   consultation provides; an outcome cannot be recorded against a failed one.
4. **Redaction covers env-derived secrets**, not credentials reaching a caller
   by another route (e.g. a file read into `context`).
5. **Automatic consultation is off by default.**
6. **`useApi.js` and `System.jsx` are coupled to the uncommitted frontend
   refactor** (§M).
7. **`backend/main.py` still carries 739 uncommitted insertions** of ARIA V1
   work, outside these commits by design.

## L. Verification-generated audit records

**48 events in the live event bus were generated by this verification work.**

```
SENTINEL_CONSULTED     27
SENTINEL_UNAVAILABLE   16
SENTINEL_OUTCOME        5
TOTAL                  48
```

These are **genuine test/audit records**: real consultations that really
happened, against the controlled stub, published by the real bridge with
`source=sentinel_bridge`. They are **not** fabricated rows and **not** evidence
of contact with a real SENTINEL.

They have been **deliberately retained**. They were not deleted to tidy the
event log, and no production architecture was modified to segregate them —
either action would have meant changing the system to flatter its own record.
Anyone auditing the bus should read these 48 rows as verification traffic dated
to this work.

## M. Exact files intentionally outside the commits

| Excluded | Why |
|---|---|
| `frontend/src/pages/BrainHub.jsx` (deletion) | Was staged before this work began. Not SENTINEL. Returned to unstaged (` D`); re-`git add` it when committing the refactor. |
| `backend/main.py` — remaining 739 insertions / 13 deletions | ARIA V1 refactor. Only the 64-line consult block was committed. |
| 32 deleted frontend pages, `App.jsx`, `Sidebar.jsx`, `main.jsx`, `src/political/*`, and the other ~107 uncommitted paths | Unrelated ARIA V1 work. |
| `frontend/dist/` | Generated; gitignored. Rebuilt so the panel reaches the running app. |
| `__pycache__`, `*.pyc` | Generated; gitignored. |

### The `useApi.js` / `System.jsx` dependency — stated accurately

**The SENTINEL commit does not contain a tiny isolated change to `useApi.js`.**

Measured: `useApi.js` in `4b5e4ae` is **+77 / −19**. Of the 77 added lines,
**7** are `useConsultStatus`. The remaining **70 added and 19 removed (89 lines)
are the spine-hook refactor**, including deletions of `useML`, `useBacktest`,
`useSentiment`, `useReport`, `useStats`, `triggerRun` and the quant hooks.

It could not be separated: `System.jsx` imports `useWorkers` and `useWorld` from
the same file, and those hooks are themselves new and uncommitted, so the panel
cannot compile without them. `System.jsx` (+263) is likewise a whole new page of
which the SENTINEL panel is one section.

This was accepted deliberately and is recorded here and in
`docs/ARIA_SENTINEL_CONTRACT.md` so no reader of the commit is misled.

## N. Definition-of-done checklist

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Malformed responses cannot crash ARIA | ✓ | 5 malformed bodies → `MALFORMED`; previously `TypeError`/500 |
| 2 | Timeout distinguishable from unreachable | ✓ | `TIMEOUT` vs `UNAVAILABLE`, separate codepaths |
| 3 | Unavailable distinguishable from agreement | ✓ | 6/6 modes, none reads as agreement |
| 4 | Disagreement handled | ✓ | `DISAGREE` recorded, attached, trade unblocked |
| 5 | Uncertainty handled | ✓ | `UNCERTAIN` position named |
| 6 | Consultation auditable | ✓ | 9/9 questions answered from the existing bus |
| 7 | Secrets excluded | ✓ | payload inspected; planted secrets `[REDACTED]` |
| 8 | SENTINEL cannot veto ARIA | ✓ | `veto`/`block_trade` ignored |
| 9 | SENTINEL cannot execute trades | ✓ | `execute` block ignored |
| 10 | SENTINEL cannot bypass risk controls | ✓ | qty 999999 → ARIA's 50, 5% cap held |
| 11 | ARIA works without SENTINEL | ✓ | live runtime: brain/regime/workers/signals all 200 |
| 12 | Protocol works with SENTINEL | ✓ | full round trip via live API |
| 13 | Strict security test passes | ✓ | unchanged, 3 hard assertions, passes in clean worktree |
| 14 | Full SENTINEL suite passes | ✓ | 58 passed, 0 failed |
| 15 | Full ARIA suite passes | ✓ | 1556 passed, 0 failed |
| 16 | Build passes | ✓ | 13.18s, 0 errors |
| 17 | Runtime verified | ✓ | live backend, port 8010 |
| 18 | Clean-worktree verification passes | ✓ | 58 passed at `246394d` |
| 19 | Documentation reflects reality | ✓ | this report + the contract |

## O. Authority — the closing statement

**SENTINEL advises. ARIA decides.**

SENTINEL is a separate general intelligence in its own process with its own
brain, memory, tools, workers and UI. ARIA is the trading specialist. ARIA may
ask SENTINEL for a second opinion; SENTINEL may agree, disagree, warn, offer an
alternative, or say it does not know.

SENTINEL cannot execute a trade, modify the portfolio, bypass a risk control,
bypass human approval, veto a decision, or mutate ARIA's decision ledger. Final
authority is ARIA's reasoning, ARIA's evidence, ARIA's risk controls, ARIA's
decision policy, and human approval where required.

If SENTINEL is silent, it has not agreed.

---

**CLOSED — PROTOCOL VERIFIED, REAL SENTINEL RUNTIME NOT PRESENT**
