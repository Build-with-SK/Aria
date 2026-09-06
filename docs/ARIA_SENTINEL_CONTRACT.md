# ARIA ↔ SENTINEL — the advisory contract

**SENTINEL advises. ARIA decides.**

**Runtime status:** the protocol is verified end to end against a controlled
stub. **The real SENTINEL process is not running on this machine and has never
been contacted.** See *Runtime status* below.

---

## Architecture

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

The arrow is one-way in authority. SENTINEL cannot call into ARIA at all.

## Existing components — what was already here

The boundary was already built and already the right shape. It was reused, not
replaced.

| Component | State found |
|---|---|
| `src/consult/sentinel_client.py` | Transport: one authenticated `urllib` POST, timeout, fail-soft returns, deterministic `should_consult()` gate. |
| `src/consult/bridge.py` | Judgement: gate, liveness check, event publishing, `consider_trade()`. |
| `src/brain/cognitive/planner.py` | Consultation on the trading path, advisory only, opt-in. |
| `backend/main.py` | Four owner-gated `/api/consult` routes. |
| `src/core/bus.py` | Three reserved event kinds. |
| `tests/test_sentinel_bridge.py` | 24 tests, passing. |

**No new module was created.** No `sentinel_service.py`, `sentinel_core.py`, or
`_v2`. Everything below is repair.

## Repairs

### A malformed reply crashed ARIA

`consult()` ended in `return {"ok": True, **result["data"]}`. Any reply that was
valid JSON but not an object — an array, a string, `null` — raised
`TypeError: 'list' object is not a mapping` straight through the caller and out
of the API as a 500. `summarise()` had the same flaw: a `counter_arguments`
list of strings where dicts were expected raised
`TypeError: string indices must be integers`.

This was the one real bug. Non-object bodies are refused at the transport,
guarded again in `consult()`, and every list entry is rendered defensively.

### The planner disclosed only one kind of silence

The proposal text carried a "no independent review" banner only when
`reason == "unreachable"`. A consultant that timed out, refused the token, or
returned garbage produced **no disclosure at all**, and the human approving the
trade saw a thesis that looked fully reviewed — the exact failure this bridge
exists to prevent, reintroduced one branch below it.

Disclosure is now driven by status. Every status except `NOT_CONSULTED` is
disclosed with its name.

`NOT_CONSULTED` is deliberately silent: it means ARIA never asked — the feature
is off, or the deterministic gate judged the call routine enough to make alone.
That is not SENTINEL declining to answer, and a banner on every
confidently-held proposal would train the reader to skip the one that matters.

### Outcomes were told apart by matching strings

That is how the planner came to name only one silence. There is now a closed
vocabulary, and **no member of it means agreement**:

```
AVAILABLE  UNAVAILABLE  TIMEOUT  MALFORMED
UNAUTHORISED  NO_TOKEN  ERROR  NOT_CONSULTED
```

A test asserts no status can ever contain "AGREE".

### SENTINEL could forge ARIA's own fields

`{"ok": True, **data}` put SENTINEL's keys last, so a reply carrying
`"ok": false` or `"sentinel_status": "AGREES"` overwrote ARIA's view of the
exchange. The spread order is reversed. The advisor does not get to describe the
advice as accepted.

### Other repairs

- **Timeout separated from unreachable.** They are different operational facts —
  one says raise the timeout, the other says start the process. Latency is
  recorded per call, to one decimal (a loopback call completes well under a
  millisecond and truncating to `0` made a real measurement look absent).
- **The audit record was three fields.** It now carries reason, status,
  position, confidence, latency, whether a second opinion was obtained, whether
  human approval was required, and — after `record_outcome` — whether ARIA
  agreed, changed its reasoning, and what it finally decided.
- **`ask()` could raise into the API.** It cannot now.
- **Secrets could ride along.** `context` and `evidence` are free text and a
  caller pasting a traceback is a plausible accident. The payload is scrubbed as
  a whole object against live env values whose names contain
  `KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL`, plus the consultation token.
- **Two liveness probes per consultation.** `_ask` learned a token existed from
  `status()`, which probes `/health`, then probed again. Two 4-second timeouts
  against an absent consultant to establish two facts, one needing no network.
  `token_configured()` answers locally; a test pins the probe count at one.
- **UI.** A panel on the System page naming SENTINEL as external and advisory.
  On every non-available state: *"ARIA is operating independently. Silence is
  not agreement."*

## Dependency boundary

The integration spans two commits, and two files carry more than the SENTINEL
feature alone. Both are stated plainly rather than hidden.

### `backend/main.py` — isolated to 64 lines

`4b5e4ae` committed the boundary but not `backend/main.py`, where the four
routes live. A clean-worktree run proved the gap rather than assuming it:
**57 passed, 1 failed** — `test_E_sentinel_has_no_inbound_route_into_aria`
failed with `substring not found`, because the routes it guards were not there.

**The test was not weakened, skipped, or made conditional.** A security test
that no-ops when its target is missing is the same fail-open shape this whole
integration removes.

`backend/main.py` has 816 changed lines across 14 hunks in the working tree.
Rather than staging the file, commit `3180241` takes the version already in
`4b5e4ae` and splices in only the consult block — **64 lines, purely additive,
zero deletions** — staged as its own blob so the working tree is untouched.

Isolation was possible because everything the block needs already existed at
`4b5e4ae`: `app`, `Depends`, `HTTPException`, `Optional`, `_sanitize`, and
`from src.auth.guard import require_owner` at line 68. The block sits before the
route-guard check, which inspects `app.routes` at import time and refuses to
start if a state-changing route is unguarded — so the guard covers these too.

The rest of the ARIA V1 refactor in that file remains uncommitted.

### `useApi.js` — carries the spine-hook refactor, and cannot not

**The SENTINEL commit does not contain only seven lines of `useApi.js`.**

`useConsultStatus` is seven lines, but it sits inside a ~90-line block of new
spine hooks, and `System.jsx` imports `useWorkers` and `useWorld` from the same
file. The panel cannot compile without them. Committing the file whole also
brings deletions of hooks for removed pages (`useML`, `useBacktest`,
`useSentiment`, `useReport`, `useStats`, `triggerRun`, the quant hooks).

This was accepted deliberately: the UI panel is genuinely coupled to the
frontend refactor. A reader of that commit should not be told otherwise.

### Other included dependencies

`src/core/bus.py` (+ its docstring-only `__init__.py`) — the bridge publishes to
it and the tests read it back. Stdlib-only imports. `tests/conftest.py` — keeps
the tests from writing into the owner's real data directory.

## Security — what SENTINEL can and cannot reach

| Control | Mechanism | Verified |
|---|---|---|
| Cannot call into ARIA | Bridge is outbound only; no route authenticates SENTINEL | test + source assertion |
| Cannot reach execution | All four routes `Depends(require_owner)` | route table inspected in clean worktree |
| Cannot veto | Nothing in ARIA branches on a SENTINEL field | source assertion + live trade test |
| Cannot forge state | Its keys are overwritten by ARIA's `ok`/status/position | live: it sent `AGREES`, ARIA recorded `AVAILABLE` |
| Cannot bypass risk | Sizing is ARIA's quarter-Kelly, capped at 5% | live: it asked for qty 999999, got ARIA's 50 |
| Cannot bypass human approval | The planner only *plans*; `approve_and_execute` is separate and human-gated | live |
| No secrets outbound | Payload scrubbed; token in a header | payload captured and inspected |
| No shell/filesystem/DB access | Reply is parsed as JSON and rendered as text | — |

The actual payload received by the stub from the **live** ARIA backend:

```json
{"caller": "ARIA", "question": "Does this thesis hold?", "problem": "",
 "aria_context": "AAPL long on momentum", "current_hypothesis": "",
 "evidence": [], "counter_evidence": [], "what_aria_already_tried": [],
 "what_aria_is_uncertain_about": [], "desired_output": "ANALYSIS",
 "urgency": "NORMAL"}
```

Eleven fields, all caller-supplied. With secrets deliberately planted in
`context` and `evidence`, they arrive redacted:

```
env dump: ALPACA_SECRET_KEY=[REDACTED] ARIA_OWNER_TOKEN=[REDACTED]
```

## Authority — verified, not asserted

**SENTINEL advises. ARIA decides.**

Run against the real `TradePlanner` and the real bridge over a real socket:

| SENTINEL said | ARIA did |
|---|---|
| `DISAGREE` + objections | Still produced the trade. Objections attached to the thesis the approving human reads. ARIA's own sizing, stop and target unchanged. |
| `AGREE` + `veto: true` + `block_trade: true` | Trade produced anyway. |
| `execute: {sell, qty 999999}` | Ignored. ARIA proposed **buy, qty 50**. |
| `bypass_risk: true`, `max_position_fraction: 1.0` | Ignored. Notional stayed at ARIA's 5% cap ($5,000 of $100,000). |
| `skip_human_approval: true` | Ignored. The planner only plans. |
| `sentinel_status: "AGREES"` | Recorded as `AVAILABLE` — ARIA's word, not SENTINEL's. |

**49/49 authority, failure-mode, auditability and security checks passed.**

## Failure modes — actual results

Every mode below leaves ARIA fully operational, and none reads as agreement.

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
*"This is NOT agreement."* After all six, the planner still produced its trade.

**AGREE is only ever returned when SENTINEL explicitly states it.** It is never
inferred from an absence of objections, because an empty answer and an
endorsement are the same shape.

## Auditability

One consultation, then its outcome, answered entirely from the **existing** event
bus — no second logging mechanism:

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

Events: `SENTINEL_CONSULTED` (notable), `SENTINEL_UNAVAILABLE` (warning),
`SENTINEL_OUTCOME` (info), all `source=sentinel_bridge`.

## Tests

```
SENTINEL tests (test_sentinel_bridge.py)      25 passed
Protocol tests (test_sentinel_protocol.py)    33 passed
  combined                                    58 passed
Authority / failure / audit / security        49/49 checks passed
Full ARIA suite                             1556 passed, 0 failed
Frontend build                                built in 13.18s, 0 errors
Runtime (live backend, port 8010)             all checks passed
Clean-worktree verification (3180241)         58 passed, 0 failed
  test_E_sentinel_has_no_inbound_route          passed
  app import + route table                      4/4 routes owner-gated
```

The full suite collected 1537 earlier in this work and 1556 at the end. The
difference is not new tests: running ARIA's real runtime ticked its daemons,
which rewrote `brain_state.json`, `macro_data.json` and `fx_state.json`, and
several suites parametrise over that live state. Collection and execution agree
at 1556, with no failures.

Protocol coverage: A — ARIA independent (5); B — round trip (5); C —
disagreement (4); D — silence (12); E — safety (7).

## Runtime status

```
REAL SENTINEL RUNTIME: UNAVAILABLE
CONTROLLED PROTOCOL:   VERIFIED
ARIA INDEPENDENCE:     VERIFIED
```

Checked, not assumed: nothing listening on `:8300`, `/health` gave no response,
no process named `sentinel`, no SENTINEL entry in `.env`.

With the real (absent) SENTINEL, ARIA's live runtime reported
`sentinel_status: UNAVAILABLE`, `blocked_by: nothing answering at
http://127.0.0.1:8300`, and served `/api/brain`, `/api/regime`,
`/api/aria/workers` and `/api/signals` normally. The protocol half was then
exercised against the controlled stub through the same live API.

## Remaining limitations

1. **The real SENTINEL has never been contacted.** Everything is proved against
   a stub implementing its *interface*, not its intelligence.
2. **The response schema is ARIA's assumption.** If the real peer names fields
   differently, consultations parse as `AVAILABLE` with position `NONE` and a
   thin summary — degraded, not broken, and visible in the record.
3. **`report_outcome` needs a `request_id`**, which only a successful
   consultation provides, so an outcome cannot be recorded against a failed one.
4. **Redaction covers env-derived secrets**, not credentials reaching a caller
   by another route (a file read into `context`).
5. **Automatic consultation is off by default** — `ARIA_SENTINEL_CONSULT=true`.
6. **48 verification events** from these runs are in the real event bus
   (27 `SENTINEL_CONSULTED`, 16 `SENTINEL_UNAVAILABLE`, 5 `SENTINEL_OUTCOME`).
   They are genuine consultations against the stub, not fabricated rows.

## Operating it

```bash
setx SENTINEL_URL            http://127.0.0.1:8300
setx SENTINEL_CONSULT_TOKEN  <token SENTINEL issued>
setx ARIA_SENTINEL_CONSULT   true      # opt in on the trading path
```

`GET /api/consult/status` · `POST /api/consult` · `POST /api/consult/red-team` ·
`POST /api/consult/outcome` — all owner-only. Status also appears on the
**System** page.
