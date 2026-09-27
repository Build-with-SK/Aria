# Owner-token login + autonomous trading architecture — build report

*2026-09-23. Companion to `docs/SERVER_BUILD_REPORT.md` (the server/auth build)
and `docs/TRADING_ARCHITECTURE.md` (the full description of `src/trading`).*

**Nothing here has traded real money.** Live autonomous execution is disabled by
an out-of-process switch that is not set, and no strategy has reached the
evidence gates that would allow it. No claim of profitability is made anywhere
in this work, and no training pipeline was started or touched.

---

## 1. Login pages

Both applications present the same minimal page — no animation, no registration,
no password reset, no social buttons, no email field.

```
        ARIA                          SENTINEL
     Owner Access                   Owner Access
 [ Owner Token            ]     [ Owner Token            ]
 [        LOGIN           ]     [        LOGIN           ]
```

- ARIA: `frontend/src/pages/Login.jsx`, rebuilt into `frontend/dist`. Verified in
  a browser against the built bundle: `form → "ARIA" → "Owner Access" →
  input[type=password, placeholder/aria-label "Owner Token"] → button "LOGIN"`.
- SENTINEL: `face/sentinel.html` sign-in panel.
- Errors, verbatim and identical on both: **"Invalid owner token."** for any wrong
  value (never "wrong length", never "close"), a throttle message with the
  remaining seconds on 429, and "Server not initialized: no owner token is
  configured." when no credential exists at all.
- A single `<input type="password">`; the credential is never placed in a URL,
  never logged, never stored in `localStorage`, and never returned by an API.

## 2. Authentication architecture

Token → server validates → **HttpOnly, Secure, SameSite session cookie**. The
browser sends the raw credential exactly once, at `POST /api/auth/login`.

- Shared core: `src/auth/ownerauth.py` (ARIA, canonical) and
  `sentinel/ownerauth.py` (byte-identical copy, pinned by sha256 in both test
  suites via `scripts/sync_ownerauth.py`).
- Server-side session records: 7-day absolute expiry, 2-day idle expiry, single
  logout, revoke-all, rotation on privilege change.
- Cookie value is `<session id>.<HMAC>`; a forged or edited id fails the HMAC and
  an unknown id fails the store lookup.
- Brute force: 4 free attempts, then exponential backoff, then a 15-minute
  lockout at 12 — per identity and per IP (the per-IP limiter believes
  `X-Forwarded-For` only when `ARIA_TRUST_PROXY=1`).
- Step-up ("confirm it's you", 10-minute window) on actions that *raise* risk.
  Actions that *reduce* risk never ask.
- `install_log_redaction()` scrubs credential-shaped strings from every log
  record in both apps, including non-string arguments and httpx URL objects.

## 3. OAuth removal

Searched the whole codebase before removing anything. Removed: `src/auth/oauth.py`,
`src/auth/users.py`, `frontend/src/auth/ownerToken.js`, every Google/GitHub/Apple
button and route, and the `GOOGLE_*`, `GITHUB_*`, `APPLE_*`, `ARIA_OWNER_EMAIL`,
`ARIA_FRONTEND_URL` variables (`scripts/init_service_tokens.py` deletes them from
`.env`; preflight warns if they reappear). The `FREE` role went with them — roles
are now `OWNER`, `SERVICE`, `ANON` (`policy.ROLES`).

Unrelated provider integrations were **not** touched: Alpaca, IBKR, Anthropic,
Ollama, NewsAPI, FRED all still work exactly as before.

## 4. `.env`

| variable | role |
|---|---|
| `ARIA_OWNER_TOKEN` / `ARIA_OWNER_TOKEN_HASH` | the owner credential (hash wins if both set) |
| `ARIA_SESSION_SECRET` | signs session ids |
| `ARIA_SERVICE_TOKEN` | SENTINEL → ARIA |
| `SENTINEL_SERVICE_TOKEN` | ARIA → SENTINEL |
| `ARIA_LIVE_TRADING_ENABLED` | **unset** — the out-of-process live switch |
| `ARIA_KILL_SWITCH` | unset — the emergency stop |

`.env.example` documents all of them; `.env` is gitignored and no value appears in
source, in the frontend, or in any log.

## 5. SENTINEL owner auth

`SENTINEL_TOKEN` / `SENTINEL_TOKEN_HASH`, the shared session core, the same
throttle, the same step-up (permissions, config, approvals, retention), the same
`"Invalid owner token."`. `/ready` and liveness stay public; everything under
`/api` is owner-only or consult-scoped.

## 6. ARIA owner auth

`require_owner` on every route that can reach a broker, plus a startup assertion
that **every** `POST/PATCH/PUT/DELETE` route in the app is either owner-guarded or
named in `policy.PUBLIC_WRITE_ROUTES` — the app refuses to start otherwise. 83
state-changing routes are guarded today. Ownership must be *proven* by token or
session: the "requests from 127.0.0.1 are the owner" fallback is retired whenever
a proxy is in front.

## 7. Sentinel ↔ ARIA service auth

Three secrets, three jobs, three values — never reused:

| credential | direction | scope |
|---|---|---|
| `ARIA_OWNER_TOKEN` | human → ARIA | everything |
| `ARIA_SERVICE_TOKEN` | SENTINEL → ARIA | a method-and-path allowlist, nothing else |
| `SENTINEL_SERVICE_TOKEN` | ARIA → SENTINEL | the consultation endpoint |

Each side refuses a peer credential equal to an owner credential or to the other
direction's peer credential, and logs why. `scripts/init_service_tokens.py`
provisions both peer credentials into both `.env` files without printing them;
`scripts/mini_preflight.py` confirms all four are present and pairwise distinct
(it now also reads the key files, not just `.env`).

**SENTINEL has no execution authority.** `/api/trading/*` is in
`policy.EXECUTION_PREFIXES` and `policy.NEVER_SHARED`; a test drives every one of
those routes with the service token and asserts 401/403.

## 8. Autonomous trading architecture

`src/trading/` — 20 modules, 3,888 lines, deterministic, no model imports:

```
Signal → RISK ENGINE → order generation → VALIDATOR → ORDER ENGINE → BROKER ADAPTER
```

A `Signal` carries side, gross expected edge, a stop and the time of the data it
was computed from — and no size. Sizing belongs to the risk engine, so confidence
buys nothing. The structural test fails the build if `src/trading` ever imports
`src.brain`, `src.inference`, `src.consult`, `ollama`, `openai` or `anthropic`.

## 9. Arbitrage

`net edge = gross spread − fees − slippage − transfer − borrow − funding`, with
the spread already paid in the leg prices. Then liquidity (participation cap on
displayed depth) and execution probability (quote age, latency still to spend,
share of depth; halved when legs cannot execute simultaneously, and charged the
cost of unwinding a half-filled pair).

Verdicts: `CANDIDATE`, `NOT_ARBITRAGE`, `STALE`, `ILLIQUID`, `FALSE_ARBITRAGE`
(>300 bps on a supposedly riskless trade is a data problem), `EXECUTION_RISK`,
`STATISTICAL_SPREAD` (mean reversion — never called arbitrage).

## 10. Risk engine

Rejects on: kill switch, unknown or stale account state, strategy not permitted at
its lifecycle stage, stale market data, stale signal data, short selling,
drawdown, daily loss, orders per minute, orders per day, open positions, no stop,
an insane stop, size below the minimum notional, **economic non-viability**, and
leverage. Sizing is the minimum of risk-per-trade against the stop, position cap,
order-value cap, available cash, instrument/strategy/sector exposure room, and a
share of displayed depth. Closing sells are reduce-only and are never blocked by
the checks that exist to stop new risk.

## 11. Execution engine

Idempotent `client_order_id = "aria-" + sha256(strategy|signal|leg)[:24]`, an
append-only journal, duplicate refusal before the broker is asked, and **fail
closed on unknown state**: a timeout halts the engine until `reconcile()` has
asked the broker by client order id. Position reconciliation halts on any
mismatch between ARIA's belief and the broker's book.

Fixed along the way: `AlpacaBroker.submit_order` used to turn a network failure
into `REJECTED` — a duplicate-order hazard. It now returns `UNKNOWN` whenever a
client order id was supplied, and `get_order_by_client_id()` was added so the
unknown can be resolved.

## 12. Paper / live separation

Three modes — `RESEARCH` (default), `PAPER`, `LIVE_AUTONOMOUS`. Up: one step at a
time, an exact typed phrase, a clear kill switch, a recent step-up, and for live
an out-of-process environment switch plus a `LIVE_ELIGIBLE` strategy. Down:
instant. A stored live mode with the switch absent behaves as paper.

`ModeGatedBroker` re-reads the mode on **every call**, so a cached adapter stops
working the moment the mode drops. The legacy desk's ceiling is `PAPER` in every
mode; the human-approval path may reach live only in `LIVE_AUTONOMOUS`, and even
then passes the order-value and position checks. **This closed a real hole:**
before it, the approval path held an unwrapped broker, so an approved trade on a
live account executed in any configuration.

## 13. Kill switch

`POST /api/trading/halt` → engage the kill switch first (a file plus an env var,
read by the adapters themselves), then force the mode to `RESEARCH`, disable the
desk's auto-execute, then cancel resting orders. Positions are **preserved** by
default; `FLATTEN` is opt-in and reduce-only. A resting protective sell is left in
place unless the policy says otherwise, so halting never strips a position of its
stop. The halt never raises, needs no model, and needs no step-up.

## 14. Broker abstraction

`BrokerAdapter`: `get_account`, `get_positions`, `get_balances`, `get_quote`,
`submit_order`, `cancel_order`, `get_order`, `get_open_orders` — addressed by
client order id. `SimulatedBroker` is a deliberately unkind paper venue (fees,
slippage past the touch, cash enforced, no shorting) with fault injection for
timeouts, lost acknowledgements and lookup failures. `LegacyBrokerAdapter` puts
the existing Alpaca/IBKR adapters behind the same interface, always through the
mode gate.

## 15. Tests

**ARIA: 2,077 passed, 0 failed** (245 of them new, across 10 new files).
**SENTINEL: 815 passed, 3 skipped.**

Covered as the brief asked: login, invalid token, rate limiting, logout,
expiration, credential never logged or placed in a URL; insufficient capital,
stale data, excessive position, drawdown breach, duplicate order, broker timeout,
unknown order state, kill switch, strategy suspension; gross spread, fees,
slippage, net edge, liquidity, latency, false arbitrage, stale quotes,
simultaneous-execution failure; and the four mode claims — RESEARCH places no
orders, PAPER cannot reach a live account, only `LIVE_AUTONOMOUS` obtains a live
adapter, and the hard limits still apply inside `LIVE_AUTONOMOUS`.

## 16. What remains disabled

- live autonomous execution (`ARIA_LIVE_TRADING_ENABLED` unset; no strategy at
  `LIVE_ELIGIBLE`)
- real-money orders from any autonomous path
- self-modifying production strategies
- model-driven orders — the pipeline takes `Signal` objects, not prose
- training — untouched and paused; every research memory row is written
  `training_eligible: false` and no pipeline reads that directory

Current state: mode **PAPER**, kill switch clear, engine not halted, limits at
their tight defaults (max order value 100, 1% risk per trade, no leverage).

## 17. Next steps

1. **Restart the running ARIA** (port 8000 is still on pre-change code, so
   `/api/trading/*` is not yet served there).
2. Decide about `auto_execute`, which is `true` in the desk config. It is now
   paper-ceilinged in every mode, but preflight flags it on an unattended box.
3. Register a first strategy and file a hypothesis and data sources.
4. Backtest with costs modelled; record the evidence; promote.
5. Purged walk-forward, out-of-sample trades, deflated Sharpe.
6. Paper-trade ≥20 trades over ≥20 days, then compare realised slippage with the
   model — that number decides whether the cost model is honest.
7. Verify the fee schedules in `src/trading/costs.py` against the brokers'
   current published rates before any capital is at stake.
8. Measure a real broker acknowledgement and fill latency when the first paper
   orders go out (both are currently unmeasured, and reported as such).
9. Review, record the review, and only then consider the live switch.
