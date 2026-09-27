# ARCHITECTURE AUDIT — SENTINEL + ARIA

**Date:** 2026-09-20
**Scope:** Phase 1 of the master execution prompt. Read-only. No functionality was
changed to produce this document.
**Auditor's machine:** Windows 11. The Mac mini is a *different machine* and was
not reachable from this session — every claim below about it is derived from the
deployment scripts in the repositories, not from running anything there.

---

## 0. The single most important finding

**SENTINEL's current auth and capture layer is not committed to git.**

`sentinel/auth.py`, `sentinel/capture.py`, `sentinel/brief.py`, `sentinel/speech.py`
and eight test files are **untracked**, and sixteen more files including
`sentinel/server.py`, `sentinel/kernel.py` and `mind/router.py` are **modified but
uncommitted**. The entire authentication boundary this audit describes exists only
as working-tree files on one Windows machine.

Nothing else in this document matters until that is backed up. This is Phase 2's
first action, before a single line is edited.

---

## 1. Current architecture

### 1.1 Two repositories, two products

| | ARIA | SENTINEL |
|---|---|---|
| Path | `Documents/trading-intelligence-system` | `Documents/atlas` |
| Git branch | `fix/signal-breadth-discount` | `sentinel` |
| Role | finance / trading specialist | general intelligence, orchestration |
| Backend | `backend/main.py` (4,400 lines, **188 routes**) | `sentinel/server.py` (**81 routes**) |
| Port | 8000 | 8300 |
| Interface | React 18 + Vite (`frontend/`), served at `/app` | one static page (`face/sentinel.html`) |
| Python modules | 261 under `src/` | 30 under `sentinel/` |
| Test files | 74 | 23 |
| Secrets | `.env`, 21 keys, gitignored | `.env`, 1 key, gitignored |

`Documents/atlas` is the SENTINEL repository. It grew out of ATLAS: the old ATLAS
backends on **:8100** and **:8200** are superseded by `sentinel.server` on **:8300**,
and `mind/` plus `backend/` are ATLAS-era code still present in the tree.

**They must stay separate.** `sentinel/aria.py` states the boundary explicitly —
SENTINEL "does not reimplement any of that, does not read its database, and does
not import its Python." The coupling in both directions is HTTP with a timeout and
a scoped credential. This audit proposes nothing that merges them.

### 1.2 Process topology as it stands today

```
    Windows dev box (current)                 Mac mini (intended, not yet built)
    -------------------------                 ---------------------------------
    uvicorn backend.main:app                  launchd -> com.aria.backend.plist
      127.0.0.1:8000        ARIA                127.0.0.1:8000       ARIA
    uvicorn sentinel.server:app               (no launchd job exists for SENTINEL)
      127.0.0.1:8300        SENTINEL
    vite dev 3000 / 5173    ARIA UI           cloudflared -> aria.<domain> -> :8000
    ollama                  local models      (no tunnel ingress rule for :8300)
```

Both servers bind **127.0.0.1**, never `0.0.0.0`. That is already correct and the
brief's §3 requirement is half-satisfied: nothing is directly exposed. What is
missing is the reverse proxy / tunnel in front, and a SENTINEL ingress rule.

### 1.3 How the two systems talk

Two independent, separately-credentialed channels. Neither can be used for the
other's purpose.

```
ARIA --> SENTINEL      src/consult/sentinel_client.py  -->  POST /api/consult
  credential: X-Sentinel-Consult-Token (SENTINEL_CONSULT_TOKEN)
  reaches:    /api/consult, /api/consult/outcome — and nothing else
  failure:    returns ok=False. Never raises, never retries, never blocks a cycle.

SENTINEL --> ARIA      sentinel/aria.py  -->  GET /api/summary, /api/brain/status
  credential: ARIA_SERVICE_TOKEN (role SERVICE in src/auth/policy.py)
  reaches:    exactly 4 (METHOD, path) pairs, method-qualified
  excluded:   GET /api/execute/queue — deliberately, because that route carries
              require_owner and admitting SERVICE would weaken every broker route
```

The consultation response is already structured and already carries confidence,
position, assumptions and provenance (`src/consult/sentinel_client.py`,
`docs/ARIA_SENTINEL_CONTRACT.md`). The brief's §17 schema is substantially
implemented; the gap is naming `data_timestamp` and `needs_verification`
explicitly.

---

## 2. Authentication architecture

### 2.1 ARIA

Six modules under `src/auth/`:

| Module | Job |
|---|---|
| `policy.py` (456 ln) | roles, path allowlist, role resolution, basis |
| `session.py` (114 ln) | signed session cookie (`itsdangerous`), owner-by-email |
| `guard.py` (99 ln) | `require_owner` FastAPI dependency for broker routes |
| `hardening.py` (175 ln) | security headers, rate limiter, proxy-aware client IP |
| `oauth.py` (278 ln) | Google / GitHub / Apple authorization-code flow |
| `users.py` (144 ln) | who has signed in — JSONL, GDPR-shaped, deletable |

**Roles:** `OWNER`, `FREE`, `ANON`, `SERVICE`.

**Design:** allowlist — a path is owner-only unless named. Adding a route fails
closed at 403, not open.

**Basis:** every role resolution also returns *how* it was established —
`token`, `session`, `loopback`, `service_token`, `none`. `PROVEN_BASES = {token,
session}`. `require_owner` refuses `loopback` outright, because behind a tunnel
every caller arrives on 127.0.0.1.

**Proxy detection:** `note_proxy_evidence()` latches the moment any request
carries `X-Forwarded-For` / `CF-Connecting-IP`, permanently retiring the loopback
fallback for that process — it does not wait to be told via `ARIA_TRUST_PROXY`.

This is a genuinely good design and **nothing in Phase 2 should rewrite it.**

**How the browser authenticates today — and the defect:**

`frontend/src/auth/ownerToken.js` stores the raw `ARIA_OWNER_TOKEN` in
`localStorage` and monkey-patches `window.fetch` plus the axios defaults to attach
it as `X-ARIA-Token` on **every** same-origin API request. The file names the
accepted risk itself:

> "If ARIA is ever exposed to other people, this should become a server-issued
> httpOnly session cookie instead"

That condition is now true. This is exactly what the brief's §4 forbids.

### 2.2 SENTINEL

One module, `sentinel/auth.py`, one middleware in `server.py`. It is the newer and
in several respects the better of the two designs:

* **Fails closed.** No `SENTINEL_TOKEN` configured means the private API answers
  **503**, not 200. The predecessor's `if TOKEN and path.startswith("/api")`
  meant an unconfigured install served memory, missions and the permission policy
  to anyone who asked.
* **Refuses tokens in the query string** with an explicit 400 rather than
  silently ignoring them.
* **HttpOnly cookie that is not the token.** The cookie carries
  `HMAC(token, "sentinel-ui-session-v1")` — verifiable with no session store,
  useless if captured *as a token*, invalidated when the token rotates.
* **Origin required on cookie-authenticated writes.** CSRF defence, correctly
  separated from CORS ("CORS is not authentication").
* **Named identities:** `OWNER`, `SENTINEL_CONSULT`, `NOBODY`. The consultation
  bridge is authenticated *centrally*, not exempted centrally and checked in the
  handler.

### 2.3 Side by side

| | ARIA | SENTINEL |
|---|---|---|
| Unconfigured behaviour | loopback implies owner (laptop only) | **503, closed** |
| Browser credential | **raw token in localStorage** | HMAC cookie, HttpOnly |
| Cookie `Secure` flag | only if `ARIA_BASE_URL` is https | **never set** |
| Cookie `SameSite` | `lax` | `strict` |
| Session lifetime | 14 days, signed, re-derived on read | **unbounded server-side** |
| Revocation | rotate `ARIA_SESSION_SECRET` | rotate `SENTINEL_TOKEN` |
| Login rate limit | 20 / 5 min per IP | **none** |
| Lockout / backoff | none | none |
| Token in URL | not accepted | **explicitly refused, 400** |
| Third-party login | Google, GitHub, Apple | none |

---

## 3. Server architecture

### 3.1 ARIA

`backend/main.py`, FastAPI, three HTTP middlewares registered in this order
(so `_role_guard` is outermost and runs first):

1. `_role_guard` — rate limit, then proxy evidence, then role+basis, then the
   allowlist, then security headers on every response
2. `_mutation_guard` — Origin check and optional `x-api-key` on writes
3. `CORSMiddleware` — exact origins, `allow_credentials=True`

Heavy modules are lazy-imported inside handlers (a project convention). A startup
assertion walks every `POST/PATCH/PUT/DELETE` route and fails unless it is either
owner-guarded or named in `policy.PUBLIC_WRITE_ROUTES` — a new write endpoint
cannot be added without somebody answering the question.

**Deployment assets that already exist:** `scripts/serve.py` (redirects
pythonw's null streams to a rotating log), `scripts/com.aria.backend.plist` and
`com.aria.watchdog.plist` (launchd, `KeepAlive`, `RunAtLoad`),
`scripts/watchdog.sh`, `scripts/mini_deploy.sh`, `scripts/mini_setup.sh`,
`scripts/mini_preflight.py`, `scripts/cloudflared-config.example.yml`
(ingress to `127.0.0.1:8000`, with an explicit "never 0.0.0.0" rationale).

### 3.2 SENTINEL

`sentinel/server.py`, FastAPI, one `authenticate` middleware, and a `lifespan`
startup that registers tools (fatal if it fails — "a SENTINEL with no hands should
refuse to pretend it has them"), reconciles interrupted missions, starts watchers
and warms the voice model off the startup path.

**Deployment assets:** `START_SENTINEL.bat` (Windows, interactive),
`SENTINEL.bat`, and an ATLAS-era `deploy/` with a Dockerfile and docker-compose.
**There is no launchd job, no watchdog, and no tunnel ingress rule for :8300.**
Phase 3 for SENTINEL is greenfield; for ARIA it is mostly verification.

---

## 4. SENTINEL architecture

```
face/sentinel.html  --cookie-->  server.py --> kernel.handle(message)
                                                   |
      +--------------+------------+---------+------+
      v              v            v         v      v
   _social       commanding    _reflex  classify  aria.ask()
   (atoms)       (devices)   (no model)    |      (finance -> ARIA)
                                           v
                              missions / research / workers
                                           |
                                    reason.think(tier=...)
                                           |
                              REFLEX -> FAST -> DEEP -> FRONTIER
```

**Routing already exists and is tiered.** `sentinel/reason.py` defines
`REFLEX / FAST / DEEP / FRONTIER`, picks a tier per task (`pick_tier`,
`hardness`), logs every call with a purpose to `reasoning_usage.jsonl`, and
tracks in-flight calls. `sentinel/capabilities.py` adds capability-based routing
(`tests/test_capability_routing.py` covers it). `config.frontier_enabled` is
**False** by design — reasoning stays on the machine, matching ARIA's
`src/inference/policy.py`.

**Capture and rating is complete and working** (`sentinel/capture.py`):
append-only JSONL, `TURN` and `RATING` records, latest-rating-wins, with route,
tier, model, seconds and tools captured per turn, `worst_routes` in `stats()`,
and `export_corpus()` stamping `contains_personal_data: True` so ARIA's training
governance refuses to render an off-machine job for it. **Keep every line of this.**

**`reason.recent_purposes()` is the honest source for the brief's §11 status
line.** It returns `{purpose: timestamp}` for calls happening now or just
finished, derived from the usage log plus live in-flight counters. Status text can
be generated from real system state without inventing anything and without
exposing chain-of-thought.

---

## 5. ARIA architecture

188 routes over a research engine (`/api/v5/`, 41 modules), market data, signals,
macro, options and derivatives, quant, backtesting, the cognitive brain
(`src/brain/`, ChromaDB over the owner's Obsidian vault), the autonomous desk
(`src/desk/`), execution (`src/execution/`, Alpaca/IBKR, kill switch), the
evolution lab, and the inference router (`src/inference/`).

Three things are structurally never a tier — the vault, the portfolio and
execution. `policy.NEVER_SHARED` names each with its reason.

---

## 6. Risks

Ordered by severity. "Brief §n" refers to the master execution prompt.

| # | Severity | Risk |
|---|---|---|
| **R1** | **Critical** | **SENTINEL's auth layer is untracked in git.** One bad edit or disk failure loses `auth.py`, `capture.py`, `brief.py`, `speech.py` and 8 test files outright. |
| **R2** | **High** | **ARIA's browser holds the raw owner token** in localStorage and sends it on every request (`ownerToken.js`). Any XSS on the origin yields a permanent credential that opens execution routes. Directly contradicts brief §4. |
| **R3** | **High** | **SENTINEL sessions never expire server-side.** The cookie is `HMAC(token, label)` — a constant. `max_age` is a client-side hint; the server accepts a captured cookie until `SENTINEL_TOKEN` is rotated. Logout deletes the client's copy only. No revocation, no rotation, no expiry. Brief §4. |
| **R4** | **High** | **No login rate limiting or lockout on SENTINEL.** `/api/auth/login` is public and unthrottled — unlimited guessing against the owner token at network speed. ARIA has 20/5min per IP but no backoff or lockout. Brief §4. |
| **R5** | **High** | **No streaming anywhere.** Searching both codebases for `StreamingResponse`, `text/event-stream`, `EventSource` and `websocket` returns **zero** hits. `/api/chat` is a blocking POST in both. `kernel.py` records a measured 231 s worst case on one worker path. This is the largest UX defect. Brief §10, §25. |
| **R6** | **Medium-High** | **CORS will block remote access on SENTINEL.** `DEFAULT_ORIGINS` is `http://127.0.0.1:8300` / `localhost:8300`. Behind a tunnel the Origin becomes `https://<host>`, so every cookie-authenticated POST returns `FORBIDDEN_ORIGIN`. `SENTINEL_ALLOWED_ORIGINS` must be set at deploy or remote access silently half-works: reads succeed, writes fail. |
| **R7** | **Medium-High** | **SENTINEL's session cookie is not marked `Secure`** (`server.py` `set_cookie` passes `httponly`, `samesite`, `max_age`, `path` — no `secure`). Over a tunnel the transport is HTTPS, but the cookie would still be sent on a plaintext downgrade. |
| **R8** | **Medium** | **Two independent auth implementations.** Divergent defaults (`lax` vs `strict`, 14 days vs unbounded, rate-limited vs not). A fix applied to one does not reach the other. Brief §7. |
| **R9** | **Medium** | **No production process management for SENTINEL.** Windows `.bat` only. No launchd job, no watchdog, no health/readiness contract, no log rotation, no restart-on-crash. ARIA has all of this; SENTINEL has none. Brief §21. |
| **R10** | **Medium** | **Google / GitHub / Apple OAuth is still wired in** — 6 env secrets, 3 routes, a `users.jsonl` personal-data file, and the login buttons. Brief §6 asks for its removal. |
| **R11** | **Medium** | **No high-risk action tier in SENTINEL.** `POST /api/permissions` *loosens the tool policy* and `POST /api/environment/command` drives real hardware; both are ordinary owner routes. ARIA separates this properly (`require_owner` plus `PROVEN_BASES` plus the kill switch). Brief §8. |
| **R12** | **Low-Medium** | **ARIA's loopback-owner inference is still live** when no token is configured. Mitigated three ways (proxy latching, `PROVEN_BASES` on execution, a startup warning) and documented as a deliberate single-user compromise. Setting `ARIA_OWNER_TOKEN` retires it. |
| **R13** | **Low** | **In-memory rate limiter** — resets on restart, does not span processes. Honest for a single-process deployment; named as a limitation in `hardening.py`. |
| **R14** | **Low** | **No backup routine.** Nothing scripted backs up `data/sentinel/conversations.jsonl`, ARIA's `data/`, or the auth configuration. Brief §22. |

### Verified as *not* risks

* No secrets are committed. `.env` and key files are gitignored in both repos;
  `git ls-files` matches nothing secret-shaped in either.
* Neither server binds `0.0.0.0`.
* Neither accepts a token in a URL (SENTINEL refuses it loudly).
* Security headers (CSP with `script-src 'self'`, HSTS under HTTPS,
  `frame-ancestors 'none'`) are applied to every ARIA response.
* CORS wildcards with credentials were already removed from both.

---

## 7. Baseline test results

Run before any change, per brief §33. These are the numbers every later run is
compared against.

```
SENTINEL   venv\Scripts\python.exe -m pytest tests/ -q
           665 passed, 1 warning in 97.78s                    GREEN

ARIA       venv\Scripts\python.exe -m pytest tests/ -q
           1 failed, 1692 passed, 1 warning in 142.06s
```

**The one ARIA failure is pre-existing and unrelated to this work.**

`tests/test_price_unit_audit.py::test_a_signals_price_survives_conversion_at_its_true_scale`
asserts that Boeing's price in `data/signals.json` is `> 200` as a fixture
assumption. The pipeline last refreshed that file with a price of `198.20`, so the
assumption — not the code under test — is what broke. `data/signals.json` is
listed as modified in the working tree, which is consistent.

It is recorded here rather than fixed: it is outside this brief's scope, and
changing it now would blur the baseline.

---

## 8. Proposed changes

Deliberately conservative. The existing security designs are good; the work is to
**close named gaps** and **converge the two implementations**, not to rewrite.

### Phase 2 — Authentication

* **P2.0** Commit SENTINEL's working tree first. Nothing else starts until R1 is closed.
* **P2.1** Extract a shared owner-auth core. Both apps get: credential, then
  `POST /api/auth/login`, then a **short-lived, server-recorded session**, then an
  HttpOnly, `Secure`, `SameSite=Strict` cookie. Sessions get an id, an issued-at,
  an expiry and a revocation list, so logout actually revokes and rotation is
  possible. SENTINEL's decision-table shape is the better starting point; ARIA's
  role/basis model is the better authorization model. Take both.
* **P2.2** Failed-login throttling with exponential backoff and temporary lockout,
  shared by both login endpoints. Never log the presented credential.
* **P2.3** ARIA frontend: delete the localStorage token and the `fetch` patch. The
  owner-token field posts once to the session endpoint and receives a cookie. The
  `X-ARIA-Token` *header* path stays server-side as the documented break-glass
  credential for scripts and the deploy check — it is removed from the *browser*,
  not from the API.
* **P2.4** Remove Google / GitHub / Apple: routes, `src/auth/oauth.py`, the login
  buttons, the `GOOGLE_*` / `GITHUB_*` / `APPLE_*` env vars, and the provider
  plumbing in `session.py`. Decide `users.jsonl`'s fate explicitly rather than
  leaving an orphan personal-data file.
* **P2.5** First-run setup: refuse to start unconfigured, with a clear
  `SERVER NOT INITIALIZED`. No default credential of any kind.

### Phase 3 — Mac mini production server

* launchd jobs for SENTINEL matching ARIA's, with `KeepAlive` and `ThrottleInterval`
* one `cloudflared` tunnel, two ingress hostnames (ARIA :8000, SENTINEL :8300)
* HTTPS end to end; set `SENTINEL_ALLOWED_ORIGINS` and `ARIA_BASE_URL` to the
  tunnel hostnames (closes R6, and makes ARIA's cookie `Secure`)
* `/health` (liveness) and `/ready` (readiness) on both; structured rotating logs
* one `ariactl` command surface: `start stop restart status health logs backup update`
* scripted backup of conversations, memory, ratings, ARIA `data/` and the auth
  configuration — secrets excluded

### Phase 4 — Remote access verification

Real tests from laptop, phone on mobile data, and a non-browser client. Every
protected route, SSE stream and static asset probed unauthenticated.

### Phase 5 — Streaming

SSE (`text/event-stream`) — it is one-way, survives proxies and tunnels cleanly,
and needs no new dependency. `kernel.handle` becomes a generator yielding
`status` events sourced from `reason.recent_purposes()` and `content` deltas, with
time-to-first-token measured. The blocking `POST /api/chat` stays for non-browser
callers.

### Phases 6 to 8

Routing, memory and consultation improvements on top of the tiering that already
exists; a quality dashboard on top of `capture.stats()`; then the full audit,
benchmark and end-to-end pass.

---

## 9. What requires the owner's physical action

Per brief §35 — everything else proceeds autonomously.

1. **Generate and store the new owner credentials** on the Mac mini
   (`OWNER_SECRET` / `SESSION_SECRET` equivalents). No real secret will ever be
   written into either repository.
2. **Cloudflare**: create the tunnel, authorise it, and add the DNS records for
   the two hostnames.
3. **Run the deploy on the Mac mini.** This session is on Windows and cannot
   reach that machine. Every script can be written and tested here; none can be
   executed there, and launchd behaviour cannot be verified from here.
4. **External-network tests** (brief §9, phone on mobile data) need a device this
   session does not have.

Nothing in Phases 2, 5, 6 or 7 is blocked by any of these.
