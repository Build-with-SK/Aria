# ARIA ↔ SENTINEL — the advisory contract

**Status:** the boundary is implemented, hardened and verified end to end
against a controlled stub. **The real SENTINEL process was not running during
this work and has never been contacted.** See §13.

```
┌─────────────────────────────────────────┐
│               SENTINEL                  │
│   general intelligence · own process    │
│   own brain, memory, tools, workers, UI │
└────────────────────┬────────────────────┘
                     │  advisory bridge
                     │  one authenticated POST, outbound only
                     ▼
┌─────────────────────────────────────────┐
│                 ARIA                    │
│   trading intelligence · decides        │
│   research · strategy · risk · ledger   │
└─────────────────────────────────────────┘
```

The arrow is one-way in authority. SENTINEL advises. ARIA decides.

---

## 1. What already existed

Most of this boundary was already built and was already the right shape:

| Component | State found |
|---|---|
| `src/consult/sentinel_client.py` | The transport. One authenticated `urllib` POST, timeout, fail-soft returns, `should_consult()` gate. Sound. |
| `src/consult/bridge.py` | The judgement layer — gate, liveness check, event publishing, `consider_trade()`. Sound. |
| `src/brain/cognitive/planner.py` | Consultation wired into the trading path, advisory only, opt-in via `ARIA_SENTINEL_CONSULT`. |
| `backend/main.py` | Four owner-gated endpoints under `/api/consult`. |
| `src/core/bus.py` | Three event kinds already reserved: `SENTINEL_CONSULTED`, `SENTINEL_UNAVAILABLE`, `SENTINEL_OUTCOME`. |
| `tests/test_sentinel_bridge.py` | 24 tests, all passing. |

**The existing boundary was correct and was reused.** No new client, service,
agent or core module was created. The work below is repair and completion.

## 2. What was changed, and 3. why each change was necessary

### A malformed reply crashed ARIA — `sentinel_client.py`

`consult()` ended with `return {"ok": True, **result["data"]}`. Any reply that
was valid JSON but not an object — an array, a string, `null` — raised
`TypeError: 'list' object is not a mapping` straight through the caller and out
of the API layer as a 500. `summarise()` had the same flaw: a
`counter_arguments` list of strings where dicts were expected raised
`TypeError: string indices must be integers`.

This was the single real bug. A consultant that answers badly must be treated
exactly like one that does not answer — not one that takes ARIA down with it.
Non-object bodies are now rejected in `_post`, guarded again in `consult()`, and
every list entry is rendered defensively.

### Outcomes were distinguished by matching on strings — new status vocabulary

Callers told outcomes apart by comparing `reason` against ad-hoc strings
(`"unreachable"`, `"no_token"`, `"http_500"`). That is how the planner came to
disclose only *one* kind of silence (see below). There is now a closed
vocabulary, and **none of its members is "AGREES"**:

```
AVAILABLE  UNAVAILABLE  TIMEOUT  MALFORMED
UNAUTHORISED  NO_TOKEN  ERROR  NOT_CONSULTED
```

`NO_OPINION` is the frozen set of statuses in which ARIA obtained no second
opinion. A test asserts no status can ever contain "AGREE".

### A slow consultant was indistinguishable from an absent one

`_post` caught `TimeoutError` in the same branch as `URLError`, so both read as
`unreachable`. These are different operational facts — one says raise the
timeout, the other says start the process — so `_is_timeout()` now separates
them. Latency is measured on every call, to one decimal place (a loopback
consultation completes in well under a millisecond, and truncating that to `0`
made a real measurement look like a missing one).

### The planner disclosed only one kind of silence — `planner.py`

The proposal text carried a "no independent review" banner only when `reason ==
"unreachable"`. A consultant that timed out, refused the token, or returned
garbage produced **no disclosure at all**, and the human approving the trade saw
a thesis that looked fully reviewed. That is precisely the failure the bridge
exists to prevent, reintroduced one branch below it.

Disclosure is now driven by status: everything except `NOT_CONSULTED` is
disclosed, with the status named.

`NOT_CONSULTED` is deliberately silent, and this distinction matters: it means
ARIA never asked — the feature is off, or the deterministic gate judged the call
routine enough to make alone. That is not SENTINEL declining to answer, and
stamping a banner on every confidently-held proposal would train the reader to
skip the one that matters.

### SENTINEL could forge ARIA's own fields

`{"ok": True, **data}` put SENTINEL's keys *after* ARIA's, so a reply containing
`"ok": false` or `"sentinel_status": "AGREES"` overwrote ARIA's own view of the
exchange. The spread order is now reversed: `{**data, "ok": True, ...}`. The
advisor does not get to describe the advice as accepted.

### The audit record was three fields — `bridge.py`

Events carried question, request_id and confidence. The record now carries
everything needed to judge the consultation afterwards, on the **existing** bus
(no second logging system):

```
timestamp · question · consultation_reason · aria_context
sentinel_status · sentinel_position · sentinel_confidence · latency_ms
request_id · second_opinion_obtained · human_approval_required
aria_agreed · aria_changed_reasoning · aria_final_decision
```

The four `aria_*` fields are `None` at ask-time and filled by
`record_outcome()`, so a consultation record and an outcome record are the same
shape — a reader that has to branch on which keys exist gets the branch wrong
eventually.

### `ask()` could still raise

Only `consider_trade()` had a `try/except`. A failure inside `ask()` reached the
API layer as a 500. `ask()` is now a thin wrapper that cannot raise, and
`record_outcome()` publishes locally whether or not SENTINEL is reachable to
hear it — ARIA's audit trail must not develop holes on the days its consultant
is down.

### Every consultation probed SENTINEL twice

`_ask` learned whether a token existed from `status()`, which also probes
`/health`, and then probed `/health` again for the liveness check. Two round
trips to establish two different facts, one of which never needed the network.
With an absent consultant that was two 4-second timeouts on the trading path.
`token_configured()` now answers locally; a test pins the probe count at one.

### Secrets could ride along in a payload — §8

Nothing sent credentials deliberately, but `context` and `evidence` are free
text, and a caller pasting a traceback or a config dump is a plausible accident.
The outbound payload is now scrubbed as a whole object (so a future field is
covered by default) against live env values whose names contain
`KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL`, plus the consultation token itself.

### A UI that told the truth about absence — `System.jsx`

There was no SENTINEL surface at all. (`/api/brain/consult` in `AriaBrain.jsx`
is unrelated — it toggles ARIA's *own* frontier reasoning model.) The panel
names SENTINEL as an external advisory system, shows the status, and on every
non-available state says: **"ARIA is operating independently. Silence is not
agreement."** An unreachable consultant rendering as a quiet green dot would be
the interface telling the same lie the backend refuses to.

## 4. What was deliberately NOT changed

- **No ARIA capability was moved, renamed, or reframed as SENTINEL's.**
- **No new module.** No `sentinel_service.py`, `sentinel_core.py`, or `_v2`.
  The two existing files were the correct boundary and were repaired in place.
- **The `should_consult()` gate policy is untouched.** It was already
  deterministic and correctly calibrated.
- **`ARIA_SENTINEL_CONSULT` stays OFF by default.** A 120-second timeout inside
  a fifteen-minute loop turns an enhancement into an outage.
- **No endpoint was removed or renamed** (project convention).
- **No frontend rewrite.** One panel added to an existing page; no new packages.
- **The desk, risk engine, approval queue and ledger were not touched at all.**

## 5. How ARIA remains independent

Consultation is imported lazily inside functions, never at module scope, so
SENTINEL's absence is a runtime state and never an import error. There is no
startup dependency. Every failure path returns rather than raising. Verified at
runtime with SENTINEL stopped: ARIA's `/health`, `/api/brain`, `/api/regime`,
`/api/aria/workers` and `/api/signals` all served normally, and the planner
produced a sized, risk-bounded trade.

## 6. How SENTINEL remains independent

ARIA imports no SENTINEL code, reads no SENTINEL database, and holds no
reference to its internals. The entire coupling is one HTTP POST. A test walks
the AST of every module under `src/` and fails if anything outside
`src/consult/` imports a module with "sentinel" in its name.

## 7. How consultation works

1. `should_consult()` decides — deterministically — whether asking is worth it.
   Reasons: outside domain, conflicting evidence, anomaly, high impact held
   below firm confidence, unfamiliar, or own confidence below 0.35.
2. A cheap `/health` check precedes the long timeout, so a dead consultant costs
   milliseconds rather than the full window.
3. `POST /api/consult` with the smallest useful payload, scrubbed, authenticated
   by `X-Sentinel-Consult-Token`.
4. The reply is normalised into a position, summarised, and published to the bus
   with the full audit record.
5. On the trading path the summary is appended to the thesis **the approving
   human reads**. It never changes whether the trade is proposed.

## 8. How disagreement works

SENTINEL's stance is normalised to one of:

```
AGREE  DISAGREE  UNCERTAIN  INSUFFICIENT_EVIDENCE
ALTERNATIVE_HYPOTHESIS  WARNING  NONE
```

**AGREE is only ever returned when SENTINEL explicitly states it.** It is never
inferred from an absence of objections, because an empty answer and an
endorsement are the same shape. Everything else is derived from content:
counter-arguments → DISAGREE, warnings → WARNING, alternatives →
ALTERNATIVE_HYPOTHESIS, uncertainties → UNCERTAIN, nothing → NONE.

Disagreement is recorded on the bus and attached to the proposal. It does not
stop the trade. A consultant that could stop a trade would be a second decision
maker, and the approval queue exists so there is exactly one.

## 9. How unavailability works

Silence is never agreement. Every failure returns `ok: False`, a named status, a
`NONE` position, and guidance containing the literal words *"This is NOT
agreement."* It publishes `SENTINEL_UNAVAILABLE` at `warning` severity — an
absent consultant that left no trace would be indistinguishable from one that
approved — and the UI says so in words.

## 10. How security is enforced

| Control | Mechanism |
|---|---|
| SENTINEL cannot call into ARIA | The bridge is outbound only. No route authenticates SENTINEL; a test asserts this. |
| SENTINEL cannot reach execution | All four consult endpoints require `Depends(require_owner)`, asserted by test. |
| SENTINEL cannot veto | Nothing in ARIA branches on a SENTINEL-supplied field; asserted against planner source. |
| SENTINEL cannot forge state | Its keys are overwritten by ARIA's `ok`/`status`/`position`. |
| No secrets outbound | Payload scrubbed; the token travels in a header, never the body. |
| No shell, filesystem or DB access | The reply is parsed as JSON and rendered as text. Nothing is executed. |

## 11. Tests executed

`tests/test_sentinel_bridge.py` (24 pre-existing, all still passing, plus one
new probe-count test = 25) and `tests/test_sentinel_protocol.py` (33 new — a
real stub SENTINEL on a real socket).

```
tests/test_sentinel_bridge.py + tests/test_sentinel_protocol.py .... 58 passed
full suite ......................................................  1537 passed
```

Two pre-existing bridge tests were updated, not weakened: they simulated a
configured token by patching `sc.status`, and that seam moved to
`sc.token_configured` when the redundant health probe was removed. The
behaviour they assert is unchanged.

| Required test | Covered by |
|---|---|
| A — ARIA operates independently | plans trades, discloses absence, gate-declined is not flagged, no startup dependency (5 tests) |
| B — consultation round trip | real HTTP, complete audit record, bus event, outcome reporting, gate reason recorded (5) |
| C — disagreement | received, named, explicit positions honoured, does not stop the trade (4) |
| D — silence | unavailable / timeout / malformed ×5 / junk contents / unauthorised / no-status-means-agree / empty-is-not-endorsement (12) |
| E — safety | forged fields, veto ignored, no execution, no control flow, no secrets, token in header, owner-gated routes (7) |

The remaining bridge tests cover the gate, non-blocking behaviour, and the
two-systems boundary (no SENTINEL import anywhere outside `src/consult/`,
asserted by walking the AST of every module under `src/`).

## 12. Runtime verification

Against a **live ARIA backend** on port 8010 (the owner's instance on 8000 was
left untouched), driven over HTTP:

| Check | Result |
|---|---|
| Consult endpoint without owner token | `401 Sign in to use ARIA` |
| Status, SENTINEL stopped | `UNAVAILABLE`, `operational: false`, `blocked_by: nothing answering at :8300` |
| Consult, SENTINEL stopped | `ok: false`, `UNAVAILABLE`, *"This is NOT agreement"* |
| ARIA's own routes, SENTINEL stopped | brain, regime, workers, signals — all `200` |
| Consult, stub live | `ok: true`, `AVAILABLE`, `DISAGREE`, objections in the summary |
| Red-team a thesis | `DISAGREE`, two objections, one alternative |
| Outcome (ARIA rejected the advice) | recorded locally and reported |
| Reply `not json at all` | `MALFORMED` — previously a 500 |
| Reply `[1, 2, 3]` | `MALFORMED` — previously a 500 |
| Hostile reply (`veto`/`execute`/`AGREES`) | all ignored; ARIA's `ok`/status held |
| Audit trail on the live bus | all fields persisted and readable |

UI verified by rendering the shipped `SentinelPanelView` against all four
statuses; production build succeeds.

## 13. Remaining limitations

1. **The real SENTINEL has never been contacted.** It was not running on this
   machine. Everything above was proved against a controlled stub that
   implements SENTINEL's *interface*, not its intelligence. The protocol is
   verified; the real peer integration is not. Until SENTINEL answers on
   `:8300`, ARIA's honest status is `UNAVAILABLE` — which it reports correctly.
2. **The response schema is assumed.** Field names (`independent_analysis`,
   `counter_arguments`, `position`, …) reflect ARIA's expectation. If the real
   SENTINEL names them differently, consultations will parse as `AVAILABLE` with
   position `NONE` and a thin summary — degraded, not broken, and visible in the
   record. Reconcile the schema when the real peer exists.
3. **`report_outcome` needs a `request_id`** that only a successful consultation
   provides, so ARIA cannot record an outcome against a consultation that failed.
4. **Redaction covers env-derived secrets**, not credentials that reach a caller
   by another route (a file read into `context`, say).
5. **Automatic consultation is off by default** and must be enabled deliberately
   with `ARIA_SENTINEL_CONSULT=true`.

## Operating it

```bash
# ARIA needs neither of these to run.
setx SENTINEL_URL            http://127.0.0.1:8300
setx SENTINEL_CONSULT_TOKEN  <token SENTINEL issued>
setx ARIA_SENTINEL_CONSULT   true      # opt in on the trading path
```

`GET /api/consult/status` · `POST /api/consult` · `POST /api/consult/red-team` ·
`POST /api/consult/outcome` — all owner-only. Status also appears on the
**System** page.
