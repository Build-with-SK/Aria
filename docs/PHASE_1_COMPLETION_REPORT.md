# Phase 1 — Completion report

**Date:** 2026-09-14
**Scope:** establish a trustworthy baseline; close the confirmed
authentication and access-control gaps.
**Explicitly out of scope and not done:** deployment, publishing, external
messages, trades, autonomous execution, credential rotation, service restarts,
new capabilities.

Sanitised: no credentials, no vault content, no private conversation text, no
runtime databases. Supersedes `PHASE1_ACCESS_CONTROL_REPORT.md` (deleted; this
document contains its findings plus the outage evidence that corrects it).

---

## 1. Starting snapshot

| | ARIA | SENTINEL |
|---|---|---|
| Path | `trading-intelligence-system` | `atlas` (the `sentinel/` package) |
| Branch | `fix/signal-breadth-discount` | **`sentinel`** |
| HEAD | `0ec8c3f` (2026-09-11) | `20bd410` (2026-09-11) |
| CI | `.github/workflows/security.yml` — `protected-paths`, `security-regression`, **`full-suite`**; plus `site.yml` | **none — no `.github` directory** |
| Repo instructions | `CLAUDE.md` present | none present |

Environment: Windows 11 (build 26200). ARIA venv Python 3.12; SENTINEL venv
Python 3.11; pytest 9.1.1. Ollama serving `qwen2.5:7b-instruct-q4_K_M`,
`gemma3:4b`, `qwen2.5-coder:7b`.

### Pre-existing uncommitted work — preserved

SENTINEL's worktree carries an in-flight body of work: the **daily brief**, the
**interface redesign**, and **device/environment control**. It was snapshotted
(patch + untracked copies + per-file hashes) before any edit, and built upon
rather than replaced.

| File | State at start | Belongs to |
|---|---|---|
| `sentinel/brief.py` (506 ln) | untracked | daily brief |
| `tests/test_brief.py`, `test_reflex.py`, `test_schedule.py` | untracked | its tests |
| `face/sentinel-face.js` (483 ln) | untracked | interface redesign |
| `README.md` (295 ln) | untracked | rewritten |
| `face/sentinel.html` | modified | interface redesign |
| `face/head.js` | **staged deletion** (pre-existing) | superseded |
| `sentinel/{config,environment,kernel,watch,world}.py` | modified | brief + devices |
| `tests/test_environment.py` | modified | devices |
| `data/mind/alerts.jsonl` | modified | runtime data |

No reset, no stash, no checkout, no deletion of untracked source. The staged
deletion of `face/head.js` was already staged before this session and was left
exactly as found.

### Handover claims corrected by inspection

| Claim | Reality |
|---|---|
| SENTINEL branch unrecorded/incorrect | it is `sentinel` |
| ARIA lacks a full-suite CI job | `full-suite` runs `pytest tests/ -q` on every push and PR |
| 7 ARIA + 2 SENTINEL tests failing | both suites green; those were stale `.pytest_cache` entries |
| SENTINEL has 11 modified files | also **6 untracked new source files**, the larger part of the work |
| ARIA down since 2026-09-13 13:08 | last served **2026-09-12 16:00:00** — 21 hours earlier (§6) |

---

## 2. Baseline

Taken before any change, against temporary stores, no broker, no production
service touched. SENTINEL's baseline used an out-of-tree pytest plugin to
redirect the un-isolated stores (§5) so the baseline itself could not pollute.

| Project | Command | Revision | Result |
|---|---|---|---|
| ARIA | `venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider` | `0ec8c3f` + worktree | **1646 passed**, 0 failed, 201 s |
| SENTINEL | same, `-p isolate_extra` | `20bd410` + worktree | **473 passed**, 0 failed, 137 s |

Historical (`.pytest_cache`, 2026-09-10 / 09-11): 7 and 2 failures respectively.
Classified as **stale** — the SENTINEL routing failures were fixed by the
uncommitted work; the ARIA cache predates two later commits. No test was
deleted, skipped or weakened.

---

## 3. Authentication changes (SENTINEL)

### Verified defect

`SENTINEL_TOKEN` is unset on this machine (`atlas/.env` does not exist). The
gate read `if TOKEN and path.startswith("/api")` — **failing open**. Confirmed
against the running instance by unauthenticated `GET`: `/api/permissions` and
`/api/capabilities` both returned full data.

Also: `?token=` accepted; `allow_origins=["*"]` with `allow_credentials=True`.

And a latent defect that would have appeared on the first attempt to fix it:
**enabling the token would have severed the ARIA bridge**, because ARIA sends
only `X-Sentinel-Consult-Token` and the owner gate rejected `/api/consult`
before `consult.authorise()` ran. The bridge worked *only because
authentication was off*.

### Architecture

One boundary — `sentinel/auth.py` — consulted by one middleware. The middleware
knows nothing about which routes are sensitive; it asks. A route added later is
private by default.

`decide()` returns `(decision, reason, identity)`:

| Condition | Response | Identity |
|---|---|---|
| `?token=` present | **400** `token_in_url` | — |
| Private route, no `SENTINEL_TOKEN` configured | **503** `authentication_not_configured` | — |
| Private route, missing/invalid/malformed credential | **401** | — |
| Cookie-authenticated state change, untrusted or absent `Origin` | **403** `forbidden_origin` | — |
| Valid bearer token or session cookie | allow | `OWNER` |
| Consult route with valid consultation token | allow | `SENTINEL_CONSULT` |

**Public allowlist — exactly four paths:** `/health`, `/api/auth/login`,
`/api/auth/logout`, `/api/auth/status`.

**Liveness separated from operational status.** `/health` is public and now
returns only `{status, at}` — no version, no system name, no subsystem detail.
ARIA probes it for liveness, so it must stay open. `/api/health`,
`/api/capabilities` and everything else are private.

**Sign-in preserved.** `POST /api/auth/login` exchanges the token for an
HttpOnly, `SameSite=Strict` cookie whose value is `HMAC(token, label)` — not the
token. The page cannot read it; rotating the token invalidates all sessions with
no revocation store.

### Consultation is authenticated, not exempted

The first implementation of `decide()` returned `ALLOW` for `/api/consult` and
left the check to the route. That made it the only path in the application whose
privacy depended on a handler remembering — add a third consultation route and
forget one line, and it is anonymous with no failing test.

Corrected: the consultation token is now verified **centrally** and the caller
is given the named identity `SENTINEL_CONSULT`. `consult.authorise()` still runs
in the route as defence in depth (it is what refuses callers when no
consultation token is configured at all).

`SENTINEL_CONSULT` reaches exactly the two consultation routes. It is not a
weaker grade of owner: `is_private()` covers every other `/api` path and the
consult branch is never taken for them. Verified by test for
`/api/permissions`, `/api/missions`, `/api/memory/search`, `/api/tools`,
`/api/capabilities`, `/api/world`, `/api/approvals`.

### Query-string tokens removed

Repo-wide survey of `?token` / `token=` / `query_params`. Three callers found
and updated; the only remaining matches are the refusal logic itself.

| Caller | Change |
|---|---|
| `face/sentinel.html` | no token in page or URL; `credentials: "same-origin"`; sign-in panel on 401/503 |
| `face/index.html` (legacy ATLAS interface) | stopped reading `?token=` and mirroring it into `localStorage`; rides the same cookie |
| `tests/acceptance.py` | token moved to the `Authorization` header |

### CORS and origin handling

Allowlist derived from **actual** interface origins, not guessed:
`face/manifest.json` has `start_url: "/"` (same-origin), `START_SENTINEL.bat`
binds `127.0.0.1:8300`, and there is no tunnel or reverse-proxy configuration
anywhere in the repository. Allowlist is therefore `http://127.0.0.1:8300` and
`http://localhost:8300`, overridable via `SENTINEL_ALLOWED_ORIGINS`; `*` is not
honoured.

CORS is used **only** as CSRF defence for cookie-authenticated state changes.
Authentication does not depend on it — a non-browser client ignores CORS
entirely, which is why the token check stands alone and is tested alone.

**Host/loopback:** SENTINEL never trusts loopback, `Host`, or bind address for
authentication — a credential is required on every private route regardless of
origin. This is structurally stronger than ARIA's model, which retains a
documented loopback fallback that retires itself on the first proxied request.
No Host-validation code was added because no privilege depends on Host; adding
it would be scope expansion without a corresponding risk.

---

## 4. ARIA credential scoping

### Inventory — every ARIA route SENTINEL calls

Read from `sentinel/aria.py`, not from the handover.

| Method | Route | Side effects | Granted |
|---|---|---|---|
| `GET` | `/health` | none | yes |
| `GET` | `/api/summary` | none | yes |
| `GET` | `/api/brain/status` | none | yes |
| `POST` | `/api/chat/local` | none on state; fetches market data, spends local model time | yes |
| `GET` | `/api/execute/queue` | none (read) | **no** |

`/api/chat/local` was **read, not assumed**. `intents.parse` / `intents.record`
— which create a held trade intent — are called by `/api/chat` and
`/api/desk/intents`, never by `/api/chat/local`. The cloud route is therefore
deliberately **not** granted: a consultation peer must not be able to put an
intent in the owner's book by phrasing a question as *"should I buy 20 AAPL"*.

### Implemented

A `service` role in ARIA's `src/auth/policy.py`:

- authenticated by **`ARIA_SERVICE_TOKEN`**, distinct from the owner token;
  ARIA disables the role and logs an error if the two are equal;
- granted a frozen set of four **(METHOD, path)** pairs — no prefix matching, so
  `/api/execute/queue` can never widen into `/api/execute/approve/{id}`;
- its basis `BASIS_SERVICE` is **not** in `PROVEN_BASES`, so `require_owner`
  refuses it on two independent grounds (role, and basis);
- it is not "free plus extras" — the free research surface is not granted, so
  reading what SENTINEL can do means reading one list.

On SENTINEL's side, `sentinel/aria.py` reads `ARIA_SERVICE_TOKEN` with **no
fallback to the owner token**: a missing scoped credential means
unauthenticated, because "not configured" must never silently restore full
privilege. `holds_owner_token()` reports whether a stale owner credential is
still on disk, so the migration is visible and finishable.

### Capability given up, stated not hidden

`/api/execute/queue` carries `require_owner`. Keeping the probe would have meant
admitting a non-owner role past the guard standing on every order-placing route.
The probe was removed instead.

SENTINEL's morning brief loses *"ARIA has trades waiting for your decision"*.
Because both consumers would otherwise degrade to a silent `False` —
indistinguishable from "looked, found none" — `aria.state()` now returns an
explicit `pending_trades: {"out_of_scope": True, …}` and the watcher logs why it
cannot observe its condition. **Unknown is reported as unknown.**

Restoring the signal wants a count-only endpoint on ARIA, not a wider credential.

### Protected path — blocked, owner action required

`src/auth/policy.py` is behind ARIA's self-modification fence.

- `scripts/check_protected_paths.py --require-git`: **clean** (exit 0).
- `--working-tree --assume-aria`: **FENCE VIOLATION** on `src/auth/policy.py`
  (exit 1), as designed.

The fence targets ARIA-authored commits and this change is not one, so it would
not trip CI. That is not a licence to route around it: the fence exists so
ownership-layer changes get deliberate human review.

**The change is left uncommitted. Authorship was not disguised, no fence file
was modified, and no control was weakened.** The owner must review and commit
`src/auth/policy.py` and `backend/main.py` personally.

### Still requires owner credential migration

Not done here, by instruction: creating `ARIA_SERVICE_TOKEN`, installing it on
both sides, removing the stale `ARIA_OWNER_TOKEN` from `atlas/.env` and
`atlas/data/sentinel/keys.json`, and rotating the owner token afterwards.
Instructions: `atlas/docs/AUTH_SETUP.md`.

---

## 5. Test isolation

`atlas/tests/conftest.py` redirected six writable stores and missed six others.

**Evidence it mattered:** production `data/sentinel/reasoning_usage.jsonl`
contains rows with `"brain": "stub"` and purposes `research.probe` /
`research.fail` — written by `tests/test_boundary.py`, which monkeypatches the
router. `reason.usage()`, shown in the interface as "what thinking has cost
today", has been counting test rows.

Fixed by redirecting `reason.USAGE_LOG`, `watch.STATE_FILE`,
`environment.REGISTRY_FILE`, `code_index.DB_FILE`, `workers.HEALTH_FILE`,
`brief.LATEST_FILE`. No test's writes were deleted; the destinations moved.

**Evidence production state is now untouched by tests.** Every file in
`data/sentinel/` hashed before and after a full run. The only file that differs
is `world.db`. A control experiment attributes it to the live server, not the
suite: `world.db` changed (`01a9a423…` → `4b538a1f…`) during a window in which
**only ARIA's suite ran and no atlas test executed at all**.

A comparable, smaller leak remains in **ARIA**: `_configure_logging()` in
`backend/main.py` runs at import and writes to the real `data/aria.log`, outside
the conftest data-root redirection. Test runs append to the production log. Not
fixed — log noise only, no state corruption. Listed in §10.

---

## 6. ARIA outage investigation (read-only; ARIA was not started)

### Evidence

| Source | Finding |
|---|---|
| Port 8000 | nothing listening |
| Scheduled task `ARIA Backend` | State `Ready`; **LastRunTime 2026-09-12 15:56:28**; **LastTaskResult 1**; trigger is `MSFT_TaskLogonTrigger` (logon only); `NextRunTime` empty; missed runs 0 |
| Task action | `venv\Scripts\pythonw.exe scripts\serve.py` |
| `data/logs/backend.log` | serving normally (HTTP 200s) until it **stops mid-stream at 2026-09-12 16:00:00**. No traceback, no uvicorn shutdown line, no exception — and `serve.py` redirects `stderr` into this file, so an unhandled Python exception would have appeared here |
| Windows Application log | no `python`/`pythonw` fault events in the window. The only `Application Error` entries are `TOD.exe`, an unrelated third-party process |
| Windows System log | **no shutdown, restart or sleep event at that time** (nearest sleep is 2026-09-13 18:39) — the machine stayed up |
| `data/aria.log` and 3 rotated predecessors | contain **only pytest-generated entries** (repeated `"no network in tests"`); the last `daemon started` lines are five identical starts in one second, which is a test |

### Conclusion

**CAUSE UNKNOWN.** What is established: ARIA started at 15:56:28, served for
about 3.5 minutes, and its process ended at **2026-09-12 16:00:00** with exit
code 1, while the machine remained running, leaving no traceback and no crash
event. What is *not* established is why. The absence of a traceback, with stderr
captured, rules out an unhandled application-level exception; it is consistent
with external termination (a closed session, a manual kill) or an abrupt
native-level exit, and the available evidence cannot discriminate between them.

**Why it stayed down is established, and is architectural:** the task's only
trigger is user logon and it has no restart-on-failure. Nothing supervises it.

**Correction to the earlier handover and to this project's own prior report:**
"down since 2026-09-13 13:08" is wrong. That is when SENTINEL's watcher first
recorded a failed probe. The last served request was **2026-09-12 16:00:00**,
21 hours earlier. An unanswered health endpoint dates an observation, not an
outage.

### Startup side effects — verified from current code

Starting ARIA spawns ten daemon threads (`backend/main.py` startup), including
the trading desk, the cognitive brain, the quant lab, the research eye, the
macro loop, the ledger loop and the daily report loop.

`data/desk_config.json` currently has **`auto_execute: true`** and
**`reflex_enabled: true`**; `data/kill_switch.json` reads **`engaged: false`**.

**A restart would therefore immediately resume the autonomous desk with
auto-execution armed** — 30-minute cycle, 5-minute management tick, 3-second
reflex polling — plus external market-data and news retrieval. It is paper-only
(`live_guard` enforces that independently of the desk), but it places orders.

**ARIA was not started, and nothing was restarted.**

---

## 7. Mission count discrepancy

### Evidence

Read with a read-only SQLite connection; neither store modified.

- `missions.db`: **7** rows, earliest 2026-08-24.
- `watch_state.json` `mission_seen`: **14** markers.
- Markers with no DB row: **7**, all with status `FAILED`.
- DB rows never watched: **0**.
- `sentinel/watch.py` `_watch_missions()`: `mission_seen` is an **append-only
  dedupe ledger**, capped at the last 200 markers, that never prunes.

### Conclusion

**Not corruption, and not two different mission concepts.** The ledger is
monotonic by design and outlives the rows it refers to. The seven orphans are
consistent with test-created missions that were cleaned out of the database
while `watch_state.json` — which conftest was **not** isolating until this phase
— kept their markers.

**Residual unknown:** the exact deletion event was not recovered; no log records
it. The explanation is inferred from the mechanism and the orphans' uniform
`FAILED` status, not directly observed. The isolation fix in §5 prevents
recurrence.

---

## 8. Final tests

Run against the final worktree, immediately before writing this report.

| Project | Command | Baseline | Final | Delta |
|---|---|---|---|---|
| ARIA | `venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider` | 1646 passed | **1693 passed**, 0 failed, 173 s | +47 |
| SENTINEL | same | 473 passed | **550 passed**, 0 failed, 120 s | +77 |

Zero failures, zero skips, zero xfails in both. No unavailable integrations were
marked as passing. One test defect of mine was found and fixed during this work
(a test requesting two fixtures that each set `SENTINEL_TOKEN`, so the second
overrode the first) — split into two tests rather than weakened.

Not run: `tests/acceptance.py`. It requires a live server, and it now **refuses
to run against production state** unless `SENTINEL_DATA_DIR` points elsewhere.
**UNVERIFIED** — no acceptance result exists.

---

## 9. Files changed

### SENTINEL (`atlas`)

| Path | Reason |
|---|---|
| `sentinel/auth.py` *(new)* | the single authentication boundary: public allowlist, fail-closed, identities, query-token refusal, origin policy |
| `sentinel/server.py` | wire the boundary into one middleware; `/health` reduced to liveness; add `/api/auth/{login,logout,status}`; restrict CORS; record caller identity |
| `sentinel/aria.py` | use the scoped `ARIA_SERVICE_TOKEN` with no owner fallback; report a stale owner token; drop the out-of-scope queue probe; report `pending_trades` as out-of-scope rather than absent |
| `sentinel/watch.py` | pending-trade rule states that it cannot observe its condition instead of silently returning `False` |
| `face/sentinel.html` | remove URL tokens; same-origin credentials; sign-in panel |
| `face/index.html` | legacy interface: stop putting a token in the URL and in `localStorage` |
| `tests/conftest.py` | redirect the six un-isolated production stores |
| `tests/acceptance.py` | header auth instead of query string; refuse production state without explicit override |
| `tests/test_auth_boundary.py` *(new)* | 77 regression tests for the boundary |
| `docs/AUTH_SETUP.md` *(new)* | local credential setup and migration, no secret values |
| `docs/SENTINEL.md`, `README.md` | document the new scheme and the scoped ARIA credential |

### ARIA (`trading-intelligence-system`)

| Path | Reason |
|---|---|
| `src/auth/policy.py` | **protected path** — add the `service` role, its method-qualified allowlist, token-distinctness check, and `BASIS_SERVICE` excluded from `PROVEN_BASES` |
| `backend/main.py` | pass the request method to `policy.is_allowed` so the service scope can be method-qualified |
| `tests/test_service_role.py` *(new)* | 47 regression tests incl. HTTP-level proof against brokers, kill switch and vault |
| `docs/PHASE_1_COMPLETION_REPORT.md` *(new)* | this document |
| `docs/PHASE1_ACCESS_CONTROL_REPORT.md` *(deleted)* | superseded by this document |

`data/macro_data.json` was already modified before this session and was not
touched.

---

## 10. Remaining risks, prioritised

| # | Risk | Severity | Note |
|---|---|---|---|
| 1 | `SENTINEL_TOKEN` still unset; the running process is pre-change code | **high** | the fix takes effect only on restart. Until then the live instance remains open on loopback |
| 2 | Stale `ARIA_OWNER_TOKEN` readable by SENTINEL | **high** | SENTINEL no longer sends it; removal and rotation are owner actions |
| 3 | `src/auth/policy.py` change uncommitted behind the fence | medium | needs an owner commit or it is lost on a careless checkout |
| 4 | `atlas` has no CI | medium | 550 tests run only when someone remembers |
| 5 | ARIA restart resumes auto-execution | medium | `auto_execute: true`, kill switch released |
| 6 | Nothing supervises either service | medium | logon-trigger only, no restart-on-failure; explains a 21-hour silent outage |
| 7 | Acceptance suite never run | medium | **UNVERIFIED**; needs an isolated server |
| 8 | ARIA tests append to production `data/aria.log` | low | log noise, no state corruption |
| 9 | ARIA outage cause unknown | low | no recurrence mechanism identified; may repeat |

---

## 11. Phase 2 recommendation — not implemented

Chosen from what this phase actually found, in dependency order.

1. **CI for `atlas`.** 550 tests exist and nothing runs them. This phase's
   boundary is exactly the kind of thing that regresses silently. Mirror ARIA's
   `full-suite` job. Cheapest item with the highest protective value.
2. **Process supervision for both services.** Evidence: a 21-hour outage nobody
   noticed, a logon-only trigger, no restart-on-failure, and a task result of 1
   with no alerting. This is also what would have dated the outage correctly.
3. **Run the acceptance suite against an isolated server** to get the first
   recorded end-to-end result. It is written, guarded, and has never produced a
   number.
4. **Native structured tool calls in SENTINEL's worker loop.** The loop depends
   on a 7B emitting one parseable JSON object extracted by regex
   (`workers._parse_call`); the repeat-call suppressor and remaining-steps hint
   exist to compensate, and 3 of 7 recorded missions failed. Measure
   `avg_steps` in `worker_health.json` before and after.
5. **A count-only pending-trades endpoint on ARIA**, restoring the brief signal
   given up in §4 without widening any credential.

**Not recommended:** more agents, larger models, browser control, or a rewrite.
Nothing found in this phase points at model capacity as the constraint.
