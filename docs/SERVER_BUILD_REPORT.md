# SENTINEL + ARIA — private server build report

**Dates:** 2026-09-20 → 2026-09-21
**Brief:** the master execution prompt (secure → reachable → reliable → fast-feeling → intelligent → measurable; training paused)
**Built on:** the Windows dev machine. **The Mac mini was not reachable from this session** — everything for it is written, syntax-checked and (where Python) tested, but not yet run there. Phase 4 (remote access from real devices) is therefore not done; see §I.

Phase 1's audit, with the baseline test numbers, is [`ARCHITECTURE_AUDIT.md`](../ARCHITECTURE_AUDIT.md).

---

## A. Architecture

```
   phone / laptop / tablet / any browser
                  │ HTTPS (Cloudflare certificate)
                  ▼
        cloudflared tunnel — outbound only, no router port
                  │
   ┌──────────────┴────────────── Mac mini ──────────────────────────┐
   │  aria.<domain> → 127.0.0.1:8000        sentinel.<domain> → :8300 │
   │  ┌────────── ARIA ──────────┐      ┌──────── SENTINEL ────────┐  │
   │  │ owner session (ownerauth)│      │ owner session (ownerauth)│  │
   │  │ policy: roles + allowlist│      │ auth.decide: one gate    │  │
   │  │ step-up on money routes  │      │ step-up on widening      │  │
   │  │ research · markets · desk│◄────►│ kernel · routing · tools │  │
   │  │ execution (kill switch)  │ HTTP │ memory · capture · stream│  │
   │  └──────────────────────────┘scoped└──────────────────────────┘  │
   │   Ollama (local models) · launchd: aria, sentinel, watchdog      │
   └──────────────────────────────────────────────────────────────────┘
```

| | ARIA | SENTINEL |
|---|---|---|
| Role | finance / trading specialist | general intelligence, orchestration |
| Repo | `Documents/trading-intelligence-system` | `Documents/atlas` |
| Port | 8000 (UI at `/app`) | 8300 (UI at `/`, dashboard at `/dashboard`) |
| Auth core | `src/auth/ownerauth.py` (canonical) | `sentinel/ownerauth.py` (byte-identical, checksum-pinned) |
| Streaming | `POST /api/chat/stream` | `POST /api/chat/stream` |
| Models | Ollama via `src/inference/router.py` (vendor providers refused by policy) | Ollama via `mind/router.py`, tiered by `sentinel/reason.py` |
| Memory | ChromaDB over the Obsidian vault (owner-only) | ChromaDB memory palace + session context |

They stay two products: separate repos, venvs, processes, launchd jobs and hostnames. They share one file (the session core, duplicated on purpose so neither depends on the other being installed), one machine, one tunnel, one control script and one backup.

**Consultation, both directions:**

- **ARIA → SENTINEL**: `POST /api/consult` with `SENTINEL_CONSULT_TOKEN` (configured on both sides already).
- **SENTINEL → ARIA**: **new** `POST /api/specialist/analysis` with `ARIA_SERVICE_TOKEN`. The service role reaches five method-and-path pairs and nothing that moves money. **Not yet configured on this machine** — see §I.

---

## B. What changed

### Security findings fixed (all found in this work)

| # | Finding | Fix |
|---|---|---|
| 1 | ARIA's browser kept the raw owner token in `localStorage` and sent it on every request | Credential posted once → server-side session → HttpOnly cookie. Token never stored in the browser |
| 2 | SENTINEL's cookie was `HMAC(token, label)` — a constant; logout deleted only the browser copy; no expiry enforced | Cookie names a server record: 7-day absolute, 2-day idle, revocable, listable |
| 3 | SENTINEL's login had no throttle at all | Shared backoff after 4 failures, 15-min lockout at 12, credential not read while locked out |
| 4 | SENTINEL's cookie was never `Secure` | `Secure` tracks the real transport (`X-Forwarded-Proto` behind a declared proxy) |
| 5 | ARIA's built UI could not load its own login page — `/app/*` answered 401 to anonymous browsers (would have locked everyone out behind the tunnel) | The data-free SPA shell is public; every API stays guarded |
| 6 | **The watchdog would have restarted ARIA every 10 minutes**: it polled an owner-only route with no credential | Rewritten: restarts only on no-answer; notifies (once) for not-ready, tunnel, disk, memory |
| 7 | Credentials could reach logs: uvicorn writes `?token=` before the app refuses it; httpx logs URL objects carrying `?api_key=` | Redaction at log-record creation, in both apps, all handlers |
| 8 | SENTINEL sent no security headers; its `/openapi.json` and `/docs` were public | CSP, nosniff, frame-deny, referrer, permissions, COOP/CORP, HSTS over https; API description private |
| 9 | Nothing distinguished "authenticated" from "allowed to move money right now" | Step-up ("confirm it's you", 10 min): trade approval, kill-switch release, arming auto-execute, desk config; SENTINEL permissions, config, approving writes, applying retention. Risk-*reducing* actions never ask |
| 10 | `/api/auth/*` shared one 20-per-5-min bucket including `/me`, which every page load calls — and a 429 there reads as "signed out" | Strict bucket only on the two credential routes |

### Data-integrity findings fixed

| # | Finding | Fix |
|---|---|---|
| 11 | **SENTINEL's test suite has been writing into the owner's real conversation corpus** — 167 of 168 rows in `data/sentinel/conversations.jsonl` are test output | conftest now redirects it; `scripts/quarantine_test_turns.py` separates them (dry-run by default — **not applied**, your call) |
| 12 | SENTINEL tests could write `data/sentinel_config.json` (happened once, in this session — values unchanged, defaults now spelled out) | conftest redirects it |
| 13 | ARIA's conftest swept `backend/main.py` half-built and never again, so data paths defined further down escaped isolation | Modules still initialising are swept but not marked done |

### Deployment and reliability

`scripts/serverctl` (install / start / stop / restart / status / health / logs / backup / update), launchd jobs for both services (agent or `--boot` daemon, running as you), `/ready` on both, JSON rotating logs, `scripts/backup.py`, cloudflared config with two hostnames, preflight auth checks, and SENTINEL's first `requirements.txt` (it had none).

### Intelligence

Streaming in both apps; structured SENTINEL→ARIA consultation; two routing bugs the benchmark found; vault embedder warmed at startup.

### Files

**ARIA** — new: `src/auth/ownerauth.py`, `src/core/readiness.py`, `frontend/src/auth/StepUp.jsx`, `frontend/src/hooks/streamChat.js`, `scripts/{serverctl, backup.py, init_owner_auth.py, security_audit.py, sync_ownerauth.py}`, `docs/{AUTH_SETUP.md, MAC_MINI_SERVER.md, SERVER_BUILD_REPORT.md}`, `ARCHITECTURE_AUDIT.md`, tests `test_{ownerauth, step_up, readiness, backup, chat_stream, specialist}.py`.
Modified: `backend/main.py`, `src/auth/{policy, session, guard, hardening}.py`, `src/inference/{router.py, providers/ollama.py}`, frontend `Login, App, main, Chat, Desk, Settings, OwnerOnly`, `scripts/{serve.py, watchdog.sh, mini_preflight.py, mini_deploy.sh, mini_setup.sh, com.aria.*.plist, cloudflared-config.example.yml}`, `.env.example`, `SECURITY.md`, `docs/DEPLOY_PUBLIC.md`, `requirements-ci.txt`, tests `conftest, test_auth_policy, test_auth_session, test_security_execution_auth, test_service_role`.
Deleted: `src/auth/oauth.py`, `src/auth/users.py`, `frontend/src/auth/ownerToken.js`.

**SENTINEL** — new: `sentinel/{ownerauth, stream}.py`, `face/dashboard.html`, `deploy/launchd/com.sentinel.server.plist`, `requirements.txt`, `scripts/quarantine_test_turns.py`, `benchmarks/results/*.json`, tests `test_{ownerauth, auth_sessions, stream, stream_live, benchmark_live, aria_structured, quality_dashboard, routing_benchmark_fixes}.py`.
Modified: `sentinel/{auth, server, kernel, reason, capture, config, aria}.py`, `mind/router.py`, `face/sentinel.html`, `docs/AUTH_SETUP.md`, tests `conftest, test_auth_boundary`.

Nothing was committed — you did not ask for commits. A pre-change backup of every uncommitted file in both repos is in `Documents/_sentinel_aria_backups/20260920_210517/`.

---

## C. Authentication

```
credential ──► POST /api/auth/login ──► ownerauth.begin()
                                            │ throttled per client (backoff → lockout)
                                            │ constant-time compare (plaintext or scrypt hash)
                                            ▼
                         session record {issued, last_seen, confirmed_at, expires}
                                            │
                              cookie "<random id>.<HMAC(key, id)>"
                              HttpOnly · SameSite=Strict · Secure over https · no Max-Age
```

- **Setup:** `python scripts/init_owner_auth.py [--app sentinel] [--hash | --passphrase] [--out FILE]`. Refuses to overwrite a live credential without `--force`, refuses to write inside a repo. Nothing configured ⇒ **SERVER NOT INITIALIZED** (503, not a default).
- **Storage:** `ARIA_OWNER_TOKEN` / `SENTINEL_TOKEN`, or their `*_HASH` scrypt form (wins if both set). Session key persisted with 0600.
- **Logout** revokes server-side; **logout-everywhere** revokes all; **sessions** lists devices (no usable ids); **rotation** = re-run setup with `--force` (every session dies with the old credential).
- **Break-glass:** `Authorization: Bearer <credential>` still works for scripts, and counts as a fresh confirmation.
- **Google, GitHub and Apple sign-in are removed**, code and UI. The FREE role's policy remains (tested) but nothing mints a free session.

---

## D. Remote access

Once the Mac mini is set up (`docs/MAC_MINI_SERVER.md`):

| Device | How |
|---|---|
| Laptop (Windows / macOS / Linux) | `https://aria.<domain>/app/` and `https://sentinel.<domain>/` — sign in once per browser |
| Phone / tablet | Same URLs in Safari or Chrome; ARIA can be added to the home screen |
| Another computer | Same URLs. No client to install |
| Scripts | `Authorization: Bearer <credential>` |

Nothing listens on the LAN: both apps bind 127.0.0.1, only cloudflared reaches them, and the tunnel dials out. **Not yet verified from real devices** (§I).

---

## E. Security

| Control | ARIA | SENTINEL |
|---|---|---|
| Unconfigured | SERVER NOT INITIALIZED; loopback-only inference that never reaches a broker | 503 on everything private |
| Sessions | ownerauth: 7 d absolute, 2 d idle, revocable | same (same file) |
| Brute force | backoff after 4, lockout at 12 (+ 20/5 min per-IP bucket) | backoff after 4, lockout at 12 |
| Step-up | approve trade, kill-switch release, arm auto-execute, desk config | permissions, config, approve writes, apply retention |
| Token in URL | never accepted; scrubbed from logs | refused with 400; scrubbed from logs |
| CSRF | SameSite=Strict + Origin check on writes | SameSite=Strict + Origin required on cookie writes |
| CORS | exact origins, no wildcard | exact origins, no wildcard |
| Headers | CSP (`script-src 'self'`), HSTS over https, frame-deny… | CSP (`script-src 'self' 'unsafe-inline'` — inline face script), HSTS over https, frame-deny… |
| Authorization | roles + allowlist + `require_owner` (PROVEN basis) + step-up | one decision table + named identities + step-up |
| Peer credential | `ARIA_SERVICE_TOKEN`: 5 routes, none that move money | `SENTINEL_CONSULT_TOKEN`: 2 consult routes |
| HTTPS | Cloudflare terminates TLS; apps mark cookies Secure and send HSTS when `X-Forwarded-Proto: https` and `*_TRUST_PROXY=1` | same |

**Audit:** `scripts/security_audit.py` probes every route from the app's own schema *anonymously*, plus streams, URL tokens, hostile-origin CORS, headers, TLS/HSTS, cookie on a wrong credential, and (opt-in) lockout.

---

## F. Server

| | |
|---|---|
| Start at boot | `serverctl install` (LaunchAgents + auto-login) or `serverctl install --boot` (LaunchDaemons, runs as you) |
| Restart on crash | launchd `KeepAlive`, throttled to 15 s |
| Watchdog | every 10 min: restart on no-answer, restart ARIA on a stale desk loop; notify once per outage for not-ready, tunnel down, disk < 1 GB, memory < 10 % |
| Health | `/health` liveness (public) · `/ready` readiness, true/false only (public) · reasons at `/api/system/health` (ARIA) and `/api/health` (SENTINEL), private |
| Logs | JSON lines, rotating: ARIA `data/aria.log` 5 MB × 3, SENTINEL `data/logs/sentinel.log` 10 MB × 5; `serverctl logs aria -f` |
| Backups | `serverctl backup` → `~/ServerBackups`, SQLite via its backup API, SHA-256 manifest, no secrets, newest 14 kept; restore never touches live data |
| Update | `serverctl update`: backup → pull (refuses dirty tree) → deps → frontend → preflight → restart → verify → prints rollback if not ready |
| FileVault | a reboot waits at the unlock screen — your decision, documented |
| Resources | the 2014 mini has 8 GB: one local model at a time; watchdog memory alert |

**A real backup was taken** of both apps' data: 79.4 MB, 3,003 files, 16 SQLite snapshots, verified, no secret-named files — `~/ServerBackups/server-backup-20260921_095020.tar.gz`.

---

## G. Intelligence

**Routing (existing, kept):** SENTINEL decides deterministically first (rules settle ~83 % of requests without a model call), then a classifier; answers use tiers REFLEX → FAST → DEEP → FRONTIER, where "frontier" is the strongest *local* model — vendor models are off by the owner's standing decision. ARIA's router refuses vendor providers by policy and abstains rather than silently degrading.

**Routing fixes (from the benchmark):**
1. A classifier-proposed parallel part must share a meaningful word with the request — "list three colours" was split into red / green / blue workers (123 s, failed → 1.6 s, passed).
2. "Reply with only a code block" answers inline instead of going to the file-writing builder (failed → 4.1 s, passed).

**Streaming:** `ack` at once → `status` from real state (the purpose of the model call in flight, or a marker where the kernel actually consults ARIA / starts a mission / searches) → `token` from **only** the call that writes the final answer (no chain of thought; Ollama `thinking` deltas are never forwarded) → `done` carrying the *recorded* reply, which replaces the draft. Keep-alive every 15 s for the tunnel. Falls back to the blocking route if a stream cannot start.

**SENTINEL ↔ ARIA consultation:** `POST /api/specialist/analysis` returns `status, analysis, assumptions, risks` (stated by the model, labelled so), `data_timestamp, sources` (measured file ages), `signal` (the engine's own output for a named ticker), `confidence: null` unless measured — the engine's confidence is a label ("High") and is reported as one, never converted to a number — and `needs_verification` with reasons (stale source, no signal for the ticker, unstructured answer). SENTINEL puts the data age and ARIA's verification flag **into the text**, not just a JSON field, and never substitutes its own market view when ARIA cannot answer.

**Memory:** unchanged separation — conversation session, long-term memory palace, the owner-only vault, the capture corpus. Capture retention is now configurable (`capture_retention_days`, default 0 = keep everything); corrected turns are never pruned; applying retention archives first and needs step-up.

**Degraded mode:** model unreachable ⇒ SENTINEL says it could not answer; ARIA abstains with what she would say; specialist returns `status: unavailable`. Nothing is fabricated.

---

## H. Testing

```
                      before (baseline)            after
ARIA      pytest      1692 passed, 1 failed*       1818 passed, 1 failed* (same test)
SENTINEL  pytest       665 passed                  812 passed, 3 skipped (opt-in live-model tests)
          * pre-existing: signals.json fixture assumes Boeing > 200, file says 198.20
```

**New tests:** shared session core (58, run against both copies), step-up (27 + 11), sessions over HTTP (22), readiness (6 + 3), backup (9), streaming (17 SENTINEL + 12 ARIA), specialist contract (9 + 5), quality/retention/dashboard (13), routing fixes (6), security headers/schema (7), plus rewritten auth tests.

**Security audit** (`scripts/security_audit.py`, in-process, anonymous caller from a non-loopback address):

| | routes | anonymous access | result |
|---|---|---|---|
| ARIA | 191 | none (public: health, ready, auth status/me/logout, UI shell) | **PASS** |
| SENTINEL | 92 | none (public: health, ready, auth status/logout, UI shell, dashboard shell) | **PASS** (after fixing headers + schema) |

**Secrets:** 707 tracked/untracked files scanned — 0 real credentials (9 hits, all test literals or placeholders); git history (169 + 11 commits) — 0 provider-format keys.

**Benchmark** (`tests/test_benchmark_live.py`, real `qwen2.5:7b-instruct-q4_K_M`, isolated state, this Windows box):

| run | passed | not run |
|---|---|---|
| before routing fixes | 11 / 13 | kelly (needs ARIA) |
| after | **13 / 13** | kelly (needs ARIA) |

Categories: reasoning, math ×3, coding ×2, instruction following ×2, error correction, memory, long context, identity, research (live web). One criterion each, checked mechanically — a regression baseline for this system on this machine, **not** a comparison with any other model.

**Performance (measured, not estimated):**

| | first status | first word | total |
|---|---|---|---|
| SENTINEL, warm model | 0.00 s | 1.09 s | 2.41 s |
| SENTINEL, cold (model + memory load) | 0.03 s | 34.0 s | 35.1 s |
| benchmark, one-word answer | — | 1.03 s | 1.14 s |
| benchmark, long-context needle | — | 3.98 s | 4.42 s |
| benchmark, "hard" question (escalates to a larger model → model swap) | — | 16.2 s | 21.8 s |
| ARIA chat preparation, cold | | | 24–28 s (vault embedder) — now warmed at startup |
| ARIA chat preparation, warm | | | 1.2–3.1 s (of which ticker lookup 0.1–2.8 s) |

**Browser:** ARIA's login page verified in the browser pane (desktop + 375 px phone, wrong credential → "not accepted", every data route 401 from the page, CSP + frame-deny on the shell). The owner dashboard rendered at desktop and phone width. **Not browser-tested:** a signed-in session and the streaming chat UIs — that needs your credential typed into a browser, which I do not do. Covered at HTTP level and by the live-model test instead.

---

## I. Remaining work

### REQUIRED

1. **Commit both repositories.** SENTINEL's whole auth, capture and streaming layer is still untracked (audit R1). Nothing here was committed because you did not ask.
2. **SENTINEL → ARIA has no credential.** Generate one value and put it in ARIA's `.env` as `ARIA_SERVICE_TOKEN` and in SENTINEL's environment (or `data/sentinel/keys.json`) as `ARIA_SERVICE_TOKEN`. Until then every "ask ARIA" from SENTINEL gets 401.
3. **Remove `ARIA_OWNER_TOKEN` from `atlas/data/sentinel/keys.json`.** It is ARIA's broker-capable owner key sitting in SENTINEL's data; SENTINEL never sends it, but it should not be there.
4. **Mac mini** (`docs/MAC_MINI_SERVER.md`): credentials (`init_owner_auth.py`), Cloudflare tunnel + DNS for two hostnames, `*_TRUST_PROXY=1`, `SENTINEL_ALLOWED_ORIGINS`, `serverctl install`, FileVault decision.
5. **Phase 4 on real devices:** from a phone on mobile data, sign in to both, sign out, and run `python scripts/security_audit.py https://aria.<domain> --owner-env ARIA_OWNER_TOKEN` and the SENTINEL equivalent against the tunnel.
6. **`auto_execute` is TRUE** in ARIA's desk config — the preflight treats this as a blocker, so `serverctl update` will stop until you confirm it (paper-only by design).
7. **Decide on the corpus pollution:** `venv\Scripts\python.exe scripts\quarantine_test_turns.py` (dry run) then `--apply` in `Documents/atlas`. Keeps exactly one turn — the owner's only real conversation; originals are backed up.
8. **Delete six dead OAuth variables** from ARIA's `.env` (the preflight lists them).

### RECOMMENDED

- Move the face's inline `<script>` into a file so SENTINEL's CSP can drop `'unsafe-inline'`.
- Ticker detection makes up to five universe searches per chat even with no ticker (1–3 s).
- On the 8 GB mini: raise Ollama's `keep_alive` for the chat model (cold first word 34 s) and pin `strong_model` to the chat model or leave it empty — escalation currently swaps to `qwen3:8b`.
- Put Cloudflare Access in front of both hostnames as a second door.
- Stream the small `CoreTalk` widget too (it uses the blocking route).
- Sign in once in a real browser and try a streamed chat on each app.
- Fix the stale Boeing fixture test; delete the stray `sentinel/pixel_game.py` (untracked, opens a pygame window on import).
- ARIA's public `/health` still reports its version string.

### FUTURE

- The training pipeline, when unpaused, with explicit corpus approval — the capture exports only corrected turns and stamps `contains_personal_data`.
- Per-tool quality breakdown (capture records a tool *count*, not names).
- A shared rate-limit store if either app ever runs multiple processes.
- Passkeys (WebAuthn) as the owner credential.
