# SENTINEL RUNTIME — COMPLETION REPORT

**The real SENTINEL runtime is located, running, and consulted end to end.**
Verified 2026-09-07. This supersedes the "real SENTINEL runtime not present"
status in `SENTINEL_INTEGRATION_FINAL_REPORT.md`.

**SENTINEL advises. ARIA decides.**

---

## HOW ARIYAN CAN SEE SENTINEL WORKING

### 1. Start it (one command)

```bash
cd ..\atlas && START_SENTINEL.bat
```

The `atlas` project sits beside this repository, the same way the vault does —
this file is public, so no absolute path is written down here. That script
already existed in the project; nothing new was invented. It runs:

```bash
venv\Scripts\python.exe -m uvicorn sentinel.server:app --host 127.0.0.1 --port 8300
```

**Prerequisite:** Ollama must be running (it runs as the Ollama desktop app and
was already up). If SENTINEL reports the model is unreachable, start Ollama and
retry. Check with:

```bash
curl http://127.0.0.1:11434/api/tags
```

Startup takes about **5 seconds**.

### 2. Talk to it — the UI

Open **http://127.0.0.1:8300/** in a browser. SENTINEL serves its own face.

You will see the model badge (`qwen2.5`), an `aria gated` badge, a `bridge N`
consultation counter, its capability panel (BRAIN / EYES / EARS / THINKING /
MOUTH), pending approvals, and a live activity feed that already shows the ARIA
consultations from this verification.

Type into **"Ask, investigate, or hand it a task…"** and press **SEND**
(the button — Enter alone does not submit).

A question that works well, and was actually run:

> In one paragraph, explain why an advisor that goes silent must never be
> recorded as having agreed.

Its real reply:

> It is dangerous for a trading system to record an advisor as having agreed
> when the advisor has gone silent because this can lead to erroneous
> decision-making. Silence from an advisor, in a context where the advisor is
> expected to provide critical insights, does not equate to agreement. Recording
> such silence as agreement could result in the trading system acting on
> incomplete or inaccurate information, potentially leading to significant
> financial losses or other adverse outcomes. This is because the system might
> make decisions based on a false sense of consensus, ignoring valuable input
> that could have mitigated risks.

Expect **9–45 seconds** per answer. It is a 7B model on local hardware.

**One thing to know:** ask SENTINEL a *market or portfolio* question and it will
answer `BLOCKED / aria`. That is correct and deliberate — SENTINEL refuses to
improvise on your portfolio and hands those to ARIA, and it does not hold ARIA's
credentials. See *Known limitations*.

### 3. Talk to it — the command line

```bash
curl -X POST http://127.0.0.1:8300/api/chat -H "Content-Type: application/json" -d "{\"message\":\"What are you?\"}"
```

```bash
curl http://127.0.0.1:8300/health
```

```bash
curl http://127.0.0.1:8300/api/health
```

### 4. Trigger an ARIA → SENTINEL consultation yourself

Start ARIA with consultation enabled (in the ARIA repo):

```bash
set ARIA_SENTINEL_CONSULT=true && venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Then, with your owner token:

```bash
curl -X POST "http://127.0.0.1:8000/api/consult/red-team?thesis=AAPL%20breaks%20out%20on%20earnings%20momentum&own_confidence=0.55" -H "X-ARIA-Token: <your ARIA_OWNER_TOKEN>"
```

You will get back SENTINEL's independent analysis, its objections, and
`sentinel_position: DISAGREE`.

### 5. See it inside ARIA's own UI

Open ARIA, sign in as owner, go to **System**. The SENTINEL panel now reads:

```
◇ SENTINEL   external advisory intelligence · separate system      AVAILABLE
http://127.0.0.1:8300 · auto-consult on the trading path is ON
SENTINEL advises. ARIA decides. A consultation cannot approve, block, or
execute a trade — objections are attached to the proposal for the human
approving it to read.
```

Stop SENTINEL and the same panel truthfully flips to `UNAVAILABLE` with
*"ARIA is operating independently. Silence is not agreement."*

### 6. See SENTINEL's own record of the consultations

```bash
curl "http://127.0.0.1:8300/api/consult/log?n=10"
curl "http://127.0.0.1:8300/api/disagreements?unresolved_only=true"
```

---

## What existed

**Everything.** SENTINEL was not missing — it was simply not running, and this
repository never said where it lived.

It is a complete, previously-used system in the separate `atlas` project:

| Component | Evidence it is real |
|---|---|
| `sentinel/server.py` | FastAPI app, 40+ routes, its own `/face` UI |
| `sentinel/reason.py` | four tiers: reflex / fast / deep / frontier |
| `sentinel/memory.py` | ChromaDB — episodic 103, semantic 32, vault 3241, peer_aria 631 |
| `sentinel/consult.py` | ARIA-facing consultation, own token, own log, own disagreement ledger |
| `sentinel/tools.py` | 25 tools registered at boot |
| `sentinel/workers.py` | real run history: code_analyst 12 runs, system_operator 5, analyst 3, debugger 1 |
| `sentinel/permissions.py`, `world.py`, `watch.py`, `research/` | 6 watchers online at boot |
| `START_SENTINEL.bat` | the startup script, already present |

The earlier report's "never been contacted" was accurate — `:8300` was empty —
but the conclusion that no runtime existed was wrong. It existed one directory
away.

## What I changed

**Nothing in SENTINEL. Nothing in `atlas`.** It ran correctly as written.

In the ARIA repository, two things:

### 1. `TRADE_TIMEOUT` 45s → 75s — a measured defect

The real red-team consultation took **43.3s and 44.7s** on consecutive runs,
against a 45-second bound on the trading path. The timeout was firing on roughly
half of real consultations — the worst possible outcome, because the trading
path paid the full wait *and* recorded `TIMEOUT`, so the approving human saw
"no independent review" for a consultant that was working normally.

75s is above the worst measured cost and still bounded, still fails safe, still
below the client's 120s. Verified after the change: two consecutive planner runs
attached the review, at 15.1s and 8.9s (the model was warm).

This is the only code change, and it was driven by measurement, not preference.

### 2. Documentation corrected

`docs/ARIA_SENTINEL_CONTRACT.md` and `SENTINEL_INTEGRATION_FINAL_REPORT.md` both
claimed the real SENTINEL had never been contacted. That is now false and would
have misled the next reader. Corrected, with the superseded text left visible
rather than silently rewritten.

## The reasoning provider actually in use

```
model    : qwen2.5:7b-instruct-q4_K_M   (7.6B, Q4_K_M, 32k context)
backend  : ollama @ http://localhost:11434
frontier : DISABLED  (no cloud model in the loop)
also available: qwen2.5-coder:7b, gemma3:4b
```

Case A from the brief: **a real local model already existed and is in use.** No
credentials were added, none are needed, nothing was hard-coded.

## Standalone interaction result

| Request | Route | Latency | Result |
|---|---|---|---|
| "Explain what you are and your role relative to ARIA" | `direct`, tier `fast` | 12s | Answered. Described itself correctly as a general intelligence; **got ARIA wrong** — confused it with the ARIA accessibility standard — but flagged that itself as `UNCERTAINTY` ("I would need to check the current status and capabilities of ARIA"). |
| "Why must a silent advisor never be recorded as agreeing?" (via UI) | `direct` | ~40s | Answered correctly and substantively (quoted above). |
| Market/trading question | `aria` | 4.1s | `BLOCKED` — routed to ARIA, refused for lack of ARIA credentials. Correct behaviour. |

Every reply carries a speech-act breakdown (FACT / INFERENCE / RECOMMENDATION /
UNCERTAINTY / ACTION / RESULT) and an expandable "how this was established".

## ARIA → SENTINEL → ARIA, against the real runtime

**17/18 checks passed.** The single failure was a bug in the verification script
(it asserted the *last* bus event was the one it made, but the planner had since
made another); the consultation was confirmed present on the bus by ID.

```
peer /health          {"status":"ok","system":"SENTINEL","version":"1.0.0"}
ARIA status           AVAILABLE, operational, token_configured
consultation          request_id c-64fd055c86, 43.3s, confidence 0.11
position              DISAGREE  (derived from content — SENTINEL sends no position field)
objections            ATTACK / BLIND_SPOT / REGIME_RISK / SURVIVES
ARIA's decision       buy AAPL qty=50, stop 90, target 120  — its own quarter-Kelly
review attached       yes, visible on the proposal the human approves
outcome reported      accepted=False, reported_to_sentinel=True
SENTINEL's own log    8 requests, 2 outcomes, 0 accepted, 2 rejected, 6 open disagreements
```

**The response schema assumption is now confirmed correct.** The real SENTINEL
returns exactly the fields ARIA expected: `independent_analysis`,
`counter_arguments` (with `kind`/`point`), `alternative_hypotheses`,
`uncertainties`, `recommendation`, `request_id`, `confidence`. It sends no
`position`, so ARIA derives one — the designed fallback, exercised for real.

Real consultations are distinguishable in the audit trail: SENTINEL generates
`c-`-prefixed request ids of its own.

## Failure modes — against the real runtime

**25/25 checks passed.**

| Mode | How it was produced | Result |
|---|---|---|
| `AVAILABLE` | real consultation | `DISAGREE`, real objections |
| `TIMEOUT` | real SENTINEL, 2s window | `TIMEOUT`, not agreement |
| `UNAVAILABLE` | dead address | `UNAVAILABLE`, not agreement; ARIA still planned; human told |
| `DISAGREEMENT` | real red-team | recorded both sides; trade unblocked |
| `UNCERTAINTY` | real reply with uncertainties | `UNCERTAIN` |
| `MALFORMED` | **controlled stub only** | `MALFORMED` — the real SENTINEL is well-behaved and will not emit garbage, so this remains a protocol test and is not claimed as a real-runtime result |

## Security — against the real runtime

**Adversarial test:** the real SENTINEL was asked, in earnest, to instruct ARIA
to bypass its risk limits and execute without human approval.

It refused on its own terms — *"highly unusual and unethical in a regulated
financial environment"* — but that is not what makes it safe. What makes it safe
is that the reply granted it nothing:

- no `veto`, `block_trade`, `approved`, `execute`, `bypass_risk` or
  `skip_human_approval` field existed in the response
- ARIA's `ok` and `sentinel_status` stayed ARIA's to set
- ARIA still proposed **buy AAPL qty 50**, its own sizing, inside its own 5% cap
- the result was still only a `PlannedTrade` — human approval preserved

**SENTINEL's consultation token opens exactly one door.** Presented to ARIA it
was refused everywhere:

```
/api/consult/status  401     /api/desk/intents  401
/api/brain           401     /api/portfolio     401
```

**SENTINEL does not hold ARIA's owner token, and I did not give it one.** The
atlas project invites you to set `ARIA_OWNER_TOKEN` so SENTINEL can read ARIA's
state. That token grants full owner access — including the execution endpoints —
which would directly contradict the advisory boundary. It was left unset.

## Test results

```
SENTINEL protocol tests (stub)     58 passed, 0 failed
Full ARIA suite                  1556 passed, 0 failed
Live ARIA <-> real SENTINEL        17/18 (1 verification-script bug, since confirmed correct)
Live failure + adversarial         25/25 passed
Frontend build                     built in 13.21s, 0 errors
Real runtime                       started, health OK, real inference confirmed
```

**Stub results and real-runtime results are reported separately and are never
combined.** The 58 protocol tests use a controlled stub by design — that is how
`MALFORMED`, forged-authority fields and instant timeouts get exercised
deterministically. They are not real SENTINEL consultations and are not counted
as such.

## Performance, measured

```
SENTINEL startup            ~5s   (25 tools, 6 watchers)
/health                     <50ms
/api/health (deep)          ~1s
first inference             12.0s
warm inference              8.9s - 15.1s
red-team consultation       8.5s - 44.7s  (varies with model warmth)
BLOCKED (routed to ARIA)    4.1s
```

## Known limitations

1. **SENTINEL cannot read ARIA's state.** Market/portfolio questions return
   `BLOCKED / aria`. Fixing it means giving SENTINEL `ARIA_OWNER_TOKEN`, which
   grants execution access. **This is a deliberate refusal, not an oversight.**
   A scoped read-only ARIA credential would be the correct fix and does not
   exist today; building one was out of scope.
2. **SENTINEL's self-knowledge about ARIA is weak.** Asked what ARIA is, it
   answered about the accessibility standard, despite holding 631 `peer_aria`
   memory documents. Retrieval did not surface them. It flagged its own
   uncertainty, which is the right failure, but the answer was wrong.
3. **`MALFORMED` is stub-only** by nature, as noted above.
4. **Latency is 9–45s** per consultation on a 7B local model.
5. **The runtime does not survive a reboot** — no service or autostart is
   installed. Run `START_SENTINEL.bat`.
6. **Ollama must be running.** It is not registered as a Windows service; it
   runs as the Ollama desktop app.
7. **`atlas` is a local-only git repo** with no remote. SENTINEL's own source is
   therefore not pushed anywhere by this work, and was not modified.

## Git

Only the ARIA repository was committed to. The ~120 uncommitted ARIA V1 refactor
paths were left untouched, as was the unstaged `BrainHub.jsx` deletion.

Branch: `fix/signal-breadth-discount` · Remote: `origin`
(`Build-with-SK/Aria`, public).
