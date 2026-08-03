# ARIA — session handoff

**Updated 2026-08-03, end of session. Paste this whole file as the first message
in the new session.**

---

## Where things stand

The repo is **public** at `github.com/Build-with-SK/Aria` (the account was
renamed from ThatFinanceGuy — the git remote already points at the new name).
88 commits, branch `main`, working tree clean, in sync with origin.
**327 tests pass.** Frontend builds clean.

Sign-in works: Google and GitHub are configured and live. Apple is deliberately
greyed out (needs a paid developer account and the `cryptography` package).

Read `SECURITY.md` before touching anything security-adjacent. It is honest
about what is NOT defended, and that list is the useful part.

---

## 🚨 START HERE — two CRITICAL findings, both unfixed

An Opus 5 red-team audit found these. Neither is theoretical; both are reachable
in the default configuration. Fix in this order.

### C1 — the risk officer fails OPEN (one word to fix)

`src/desk/reflex.py:400-405`

```python
def _risk_check(self, entry, account) -> bool:
    try:
        from src.desk.analysts import macro_agent
        conditioner = macro_agent.condition()
    except Exception:
        return True     # ← "approved". Skips EVERY cap.
```

`return True` means approved, so any exception before `RiskOfficer` is even
constructed bypasses name %, sector %, portfolio heat, correlation stack,
position cap, regime conviction bar and the drawdown circuit-breaker.

No attacker needed. `macro_agent.py:40-42` does `vix > 25` and formats
`{macro.get('dxy_trend', 0):+.2%}` — a string in `data/macro_data.json` where a
number belongs raises `TypeError`.

The reflex lane runs by default (`reflex_enabled: True`), every 3 seconds, from
server startup. The comment claims the gates re-run at execution; **they do
not** — `auto_executor.gates()` checks paper/armed/connected/budget only, never
a risk cap.

The tell that this is a bug and not a decision: the deliberate lane calls the
same `condition()` **outside** any try (`desk_daemon.py:285`), so the identical
fault fails that lane *closed*. Two lanes, one fault, opposite outcomes.

**Fix:** `return False`. Then add a test that a raising conditioner blocks.

### C2 — the exit engine bypasses the approval queue on a LIVE account

`src/desk/position_manager.py:616-648`, `650-665`, and `:339`

`_close()` is correctly welded shut at `position_manager.py:443`
(`paper_mode_confirmed() and broker.paper`, else queue for manual approval) —
and it is the **only** path that is. Three siblings in the same `tick()` have no
paper gate at all:

1. `_heal_brackets` — cancels every existing stop/limit at the broker, then
   re-places. **If the re-place fails the position is left naked**, with its
   original protection already cancelled.
2. `_replace_stop_order` — trailing ratchet, same cancel-then-replace.
3. `mgr.cancel_stale_orders()` — cancels any unfilled order over a day old on a
   ticker with no position. On a live account that silently kills the human's
   own working limit orders.

Path with zero human involvement: server startup → `_start_desk`
(`backend/main.py:144`) → `DeskDaemon.start` → `_management_tick` every 5 min,
24/7, not gated by `auto_execute`, `ALPACA_PAPER` or `broker.paper`.

**Fix:** hoist the paper check from `_close` to the top of `tick()`, or gate all
three individually. Then update the README claim — "enforced in code with no
override" is currently true for exits and false for bracket management.

---

## Then these — HIGH, and two are mine

### H1 — `X-Forwarded-For` grants remote OWNER

`src/auth/hardening.py:149-156` takes the **leftmost** XFF hop, which is
attacker-controlled (nginx and Cloudflare both *append*). With
`ARIA_TRUST_PROXY=1` and `ARIA_OWNER_TOKEN` unset, the single header
`X-Forwarded-For: 127.0.0.1` yields role OWNER via the loopback rule in
`policy.py:130-132` — vault, portfolio, desk, `/api/execute/approve/{id}`. It
also makes every rate limit free to bypass with a fresh fake IP per request.

That is exactly the configuration `docs/DEPLOY_PUBLIC.md` tells you to set.
Saved today only because `cf-connecting-ip` is preferred and Cloudflare always
sets it.

**Fix:** take the rightmost untrusted hop, or refuse to grant OWNER by loopback
whenever `ARIA_TRUST_PROXY` is set. Then correct `SECURITY.md`, which documents
only the rate-limit consequence and not the owner-escalation one.

### H2 — one NaN silently disables every risk cap

`src/desk/config.py:67-84` never validates values or types, and `json.loads`
accepts the bare `NaN` literal. `PATCH /api/desk/config` with
`{"drawdown_halt_pct": NaN}` persists, and every gate is a `>` comparison:
`NaN > x` is False, so the circuit breaker never fires, heat never rejects, and
name caps never trim. The checks still "run", still report `False`, and the UI
still shows green.

Related: `risk_officer.py:84` skips the drawdown check entirely when
`start_equity` is 0, and `day_state.py:43` writes `current_equity or 0.0` —
which is 0 whenever the broker is disconnected at the day roll.

**Fix:** validate types and reject non-finite floats in both `save_config` and
`load_config`.

### M6 — the public deployment cannot work yet (both fail closed)

1. `backend/main.py:2455` mounts the SPA at `/app`, which is neither public nor
   free, so anonymous users get **401 JSON at the login page itself**. Nobody
   can ever sign in through the served frontend.
2. `_mutation_guard` (`main.py:254-259`) rejects any POST whose `Origin` is not
   in `_ALLOWED_ORIGINS` — localhost only. Behind the tunnel, **every** POST
   from the deployed UI is 403, including trade approval.

Fix both before the Cloudflare Tunnel goes up, or nothing will work and it will
look like a network problem.

---

## Lower, but worth doing

- **`policy.py:150-155` matches on raw `startswith` with no segment boundary.**
  `is_allowed("/api/brain/pulsex", "free")` is True. No current route exploits
  it — all 112 were enumerated against the live policy and the free/anon sets
  are exactly the intended ones — but it contradicts the file's own promise
  that a new endpoint is invisible until named. `/api/signals-private` would be
  public the moment someone writes it.
- **Dot-segment paths pass the guard.** `/api/lse/../execute/queue` → True.
  Verified empirically as **not exploitable today**: Starlette does no
  normalisation, so the middleware and the router see the identical string and
  the router 404s. It goes live the moment anyone adds a `{path:path}` route or
  a normalising proxy. Reject `..`, `//` and `%` before matching.
- **OAuth `state` is not bound to the browser (login-CSRF).** `oauth.py:131-142`
  signs a random `r` that is never stored or compared, so an attacker can force
  a victim into the *attacker's* session. Bounded — it cannot mint an owner
  session, since ownership comes from the email — but the docstring's claim that
  "a forged redirect cannot start a session" is wrong as written. Fix: put `r`
  in a short-lived cookie and compare on callback.
- **Owner identity is email-only.** `session.py:74-80` ignores provider and
  `sub`, and the Apple path takes `email` from the id_token with no
  `email_verified` check while GitHub correctly requires `primary and verified`.
  Not reachable now (Apple is unconfigured) but it is the weakest link if Apple
  is ever enabled. Bind owner to `(provider, sub)` after first sign-in.
- **Free users can mutate:** `POST /api/v5/learning/resolve`,
  `/api/v5/learning/revert/{version}` (rewrites module weights),
  `/api/universe/refresh`, `/api/quant/run-now`, `/api/technical/snapshot`. All
  research-scoped, but granted by prefixes documented as read-only.
- **NaN ATR on any existing holding vetoes every new trade** with a nonsense
  `nan%` figure (`risk_gate.py:128-136`). `_atr` returns NaN rather than None
  and `if (atr and px)` treats NaN as truthy.

---

## The debate desk — deliberately parked, ready to go

A separate audit of all 474 transcripts. Most of it is plumbing, not model
failure, which is why it is worth doing.

- **The judge prompt hands the model the verdict and forbids changing it**
  (`debate.py:213-215`), which is a motivated-reasoning generator by
  construction. 129 of 474 judge paragraphs describe macro as bearish when it
  was `Expansion (Goldilocks), +10.8, bull` in all 474.
- **The contradiction guard has never fired in 474 debates.** `_contradiction_guard`
  compares `bull_ev` against `bear_ev`, which `opinion.py:33-37` partitions on
  `lean` — disjoint by construction. `CONTRADICTION_PENALTY` has never applied.
  A dormant control reads as a real one; fix it or delete it.
- **35% of invalidation levels sit on the wrong side of the trade** (158 of 456).
  The level is copied from the signal engine without reference to the judge's
  view. All 18 BONK-USD debates carry `invalidation_level: 0.0` from an `or 0.0`
  fallback. None of the 30 actionable verdicts were affected — luck, not design.
- **`key_risk` is decorative.** 20 distinct values across 474; 307 are just a
  restatement of the composite score, which is the *reason for the view*, not a
  risk to it.
- **Conviction is the technical analyst laundered.** 143 of 474 debates had
  exactly one voting analyst; 19 of 30 actionable verdicts match technical's own
  number within 2 points.
- Fabrication is **rare** — ~8 genuine cases in 474, all in researcher rounds,
  zero in judge prose. The "cite the numbers, never invent data" instruction
  works.

The full fix list with line numbers is in that audit; ask for it and I will
reproduce it.

---

## What was done this session

- **History rewritten twice** (both verified, both backed up): six third-party
  academic PDFs, and a committed session signing key. Neither is recoverable
  from history; the key was also rotated so the committed value is dead.
- **Auth**: single-owner access control, default-deny allowlist, OAuth sign-in
  with Google/GitHub/Apple, signed sessions, ownership re-derived from
  `ARIA_OWNER_EMAIL` on every cookie read.
- **Hardening**: CSP and security headers, rate limiting applied before
  authentication, audit logging to `data/aria.log` (there was none — every
  `logger.info` in the codebase was being discarded).
- **PWA**: manifest, generated icons, service worker that caches the shell and
  never the API.
- **V5 audit fixes**: neutral mass no longer discarded by the ensemble,
  overlapping windows discounted before Wilson, undefined ratios excluded,
  outcomes graded at the horizon date. Scores are lower and intervals wider
  across the board — that is the correction, not a regression.
- **Ops**: `scripts/mini_preflight.py`, `mini_deploy.sh`,
  `backfill_outcomes.py`, `docs/DEPLOY_PUBLIC.md`, `SECURITY.md`.
- **UI**: command palette (Ctrl/⌘+K), glossary, mobile fixes, rotating galaxy,
  role-aware navigation, public brain view.
- Personal identifiers removed from every tracked file; launcher scripts made
  portable via `%~dp0`.

**Still unrun:** the adversarial-market-input agent (hit a session limit after
confirming 15/15 symbol abuses abstain cleanly). Worth resuming — it was about
to test the unit-confusion path and whether an unadjusted split can cross the
confidence floor.

---

## Things that will bite you

| Gotcha | Why |
|---|---|
| `.gitignore` has **no trailing comments** | `data/v5/  # note` makes the whole line literal and ignores nothing |
| Backend runs **without `--reload`** | It needs a restart to pick up changes, and `--reload` spawns orphans that survive the parent and serve stale code |
| Preview pane hidden ⇒ **rAF is suspended** | Canvas never paints and screenshots fail outright. Verify with DOM reads, not pixels |
| `min-width: auto` on flex/grid children | The single most common cause of mobile overflow here; it silently defeats `overflow-x: auto` |
| London quotes in **pence** (`GBp`) | Treating it as GBP is a 100× error. Always go through `src/data/currency.py` |
| `^TNX` is already a percentage | Do not divide by 10 |
| `max(lo, min(hi, NaN))` returns `hi` | A division by zero became maximally bullish once. `_util.clamp` propagates NaN now |
| Only `backend/main.py` calls `load_dotenv` | cron, `main.py` and any standalone script see **no** API key and fail as "not set" |
| `data/desk_config.json` has `auto_execute: true` locally | Gitignored, so clones are safe. The mini preflight fails the run if it is set |

---

## How to run

```bash
START_ARIA.bat
```

Backend on :8000, frontend on :3000. Tests: `venv\Scripts\python.exe -m pytest tests/ -q`.
Frontend build: `cd frontend && node node_modules/vite/bin/vite.js build`.

`gh` CLI is installed but **not authenticated** — run `gh auth login` once and
the repo description and topics can be set from the command line (they are still
empty).

---

## Suggested first message for the new session

> Read HANDOFF_TOMORROW.md. Start with C1 (`reflex.py:405` returns True on a
> failed risk check — make it fail closed) and C2 (the exit engine's
> `_heal_brackets`, `_replace_stop_order` and `cancel_stale_orders` submit real
> orders on a live account with no paper gate). Add a regression test for each,
> run the full suite, and push. Then H1 and M6 before we put it behind the
> Cloudflare Tunnel.
