Closes the three findings from the external audit, then measures the research
engine properly and reports what it found — including where the measurement
itself was wrong.

**493 tests pass**, in the project venv and in a clean environment built only
from `requirements-ci.txt`.

---

## Finding 1 — unauthenticated execution endpoints

A default-deny middleware already existed. Three things got past it:

- **Ownership could be inferred from a loopback address.** `cloudflared`
  connects to ARIA from `127.0.0.1`, so behind the tunnel every caller on earth
  arrives looking local. Ownership is now established **per request**
  (`resolve_role_with_basis` → `token` / `session` / `loopback`), and
  `require_owner` accepts only proof.
  A first attempt asked *"is an owner configured"* — a question about the
  environment, not the request. `ARIA_OWNER_EMAIL` satisfied it while leaving
  the fallback fully active, and **the test I wrote alongside it asserted that
  combination returns 200**, pinning the hole open. Both are gone; the
  replacement test is parametrised over both environments.
- **X-Forwarded-For could forge loopback.** Ownership now uses the socket peer
  (`hardening.peer_ip`); the rate limiter keeps the forwarded address.
- **`"testclient"` was in the loopback allowlist**, so every TestClient request
  authenticated as owner — which is why no test could previously prove an
  endpoint refuses an anonymous caller.

Enforcement is structural: all order-touching routes carry
`Depends(require_owner)`, and **the app refuses to start** if a state-changing
or broker-touching route lacks one (verified with a negative control).
`note_proxy_evidence` additionally retires the loopback fallback the moment a
request carries a forwarding header — so a proxied deployment closes itself
instead of waiting for someone to remember `ARIA_TRUST_PROXY`.

## Finding 2 — IBKR order-status misreporting

- Partial fills reported as `SUBMITTED` — IBKR labels a half-filled order
  identically to an untouched one, so the fill counters now decide.
- A placement whose *status read* threw reported `REJECTED` for an order that
  was **live at IBKR** — nobody polls it, nobody brackets it.
- Disconnection and read errors masqueraded as rejections.
- Rejections now carry IBKR's reason from `trade.log`; `cancel_order`
  distinguishes already-cancelled from already-filled.

Status is **reconciled against the order size** (`filled + remaining ==
totalQuantity`) rather than picking a winner between two fill counters. Three
successive tiebreak rules (min → executions-only → max) each had a counter-case;
the invariant was sitting unused three lines away.

Found by these tests: `ApprovalQueue.reject()` only accepted `pending`, but
`execute()` marks a trade `approved` *before* calling the broker — so every
broker rejection left the audit trail claiming an approved trade that never
existed.

## Finding 3 — live-account auto-execute bypass

The entry path was gated. **Bracket healing, trailing-stop replacement and
stale-order cleanup were not** — all three place or cancel real orders from the
5-minute desk tick.

The desk no longer receives a broker; it receives `PaperOnlyBroker`, so the
refusal sits at the boundary every order must cross, including methods nobody
has written yet. Blocked orders are **queued for a human**, not swallowed.
Also: the "checked in two places" claim was false (both read one env var), so
adapters now declare `PAPER_ENV_VAR` and their own `endpoint_is_paper()`, and
one that declares nothing fails closed.

---

## Data reliability

Two more vendors behind yfinance (Alpha Vantage, and keyless Stooq so failover
works on a fresh checkout). The **silent stale-cache fallback** now propagates an
explicit staleness flag into the confidence calculation *and* the headline, so a
signal built on nine-day-old data cannot read as a fresh one. A total vendor
outage is reported as an outage, not as an unknown ticker.

## Statistical rigor — and what it found

All 41 modules walk-forward validated (point-in-time replay via
`marketdata.as_of`). **Thirteen had never expressed a single opinion**, and four
of those were silenced by arithmetic: a 5y tercile yields *exactly* the
20-observation minimum, while the ten years they needed was already cached.
They now report — which buys **coverage, not skill**; an A/B under identical
harness code found nothing significant in either arm.

**Two bugs in the measuring instrument, both written in this branch:**

1. No cross-sectional discount — forty tickers on one date are forty
   observations of *one market day*.
2. A raw success count against a discounted sample, which read 624/1039 as a 76%
   hit rate and returned `p = 0.0` for **every** module, including one whose edge
   was negative.

After both fixes, `clustering`'s 85% hit rate and "+17.6% edge" — which would
have looked like the best module in the system — turns out to be 14 effective
observations and collapses to +3.3%.

### The full-registry result

33 of 41 measured; **12 significant, every one negative.** No module has a
significant positive edge. Splitting on the information coefficient separates
two groups that deserve opposite conclusions:

| | | |
|---|---|---|
| **Genuinely anti-predictive** | `carry` (IC −0.215, wrong on *both* bull and bear calls), `value` (−0.254), `breadth` (−0.177) | signal points the wrong way |
| **Real information, miscalibrated threshold** | `quality` (IC **+0.192**), `growth` (**+0.150**), `currency_strength` (**+0.095**) | bull calls beat base; bear calls near chance drag the aggregate down |

**Nothing was changed on the strength of this.** Flipping a sign because of one
decade of forty US large caps is the curve-fitting the weight-fitting harness
exists to refuse. Recorded as a finding to investigate.

`walk_forward_validated` now means *measured*;
`walk_forward_skill_demonstrated` is a separate field, separate because the
answer is usually no.

## Track record infrastructure

The loop that turns predictions into a track record **did not exist** —
`resolve_pending()` had no caller but a manual endpoint, so predictions could
never accumulate unattended. `src/v5/loop.py` is that loop, scheduled with a
watchdog and a heartbeat; APScheduler's 1-second misfire default (which silently
drops late jobs) is fixed.

`/api/v5/track-record` verified to flip at exactly 20 resolved calls, plus a
static public page generated from the API.

---

## Reviewer notes

- **Four adversarial review passes** were run against this branch. Each found
  the previous pass's fix incomplete — including the one where I encoded the
  vulnerability into a passing test. The trend is good; **there is no basis for
  assuming a fifth pass would come back clean.** `docs/audit/REVIEWER_BRIEF.md`
  is set up for a genuinely independent reviewer.
- `data/v5/` is gitignored, so the walk-forward results and the prediction log
  are **not** in this PR. Regenerate with `python scripts/run_walkforward.py`;
  compare two runs with `scripts/compare_walkforward.py`. The generated
  `docs/public/track-record.html` is committed.
- `data/alerts.json` / `data/signals.json` are runtime churn and deliberately
  left out.

## Not fixed by this PR — please don't read it as resolved

- **No track record.** 15 predictions logged, 6 directional, 0 resolved; 20
  resolved *directional* calls are needed. Only elapsed time produces them, and
  **nothing was backfilled**.
- **The backend is not running and no scheduled task starts it** — port 8000 is
  dead, `Get-ScheduledTask *ARIA*` returns nothing. The schedule added here has
  therefore never fired. `AUTORUN_ARIA.bat` at boot is a five-minute fix that
  gates everything above.
- **Branch protection** must be enabled in repo settings, or CI reports failures
  without blocking merges.
- **Single maintainer**, and **regulatory exposure (FCA/MiFID II)** — a staffing
  fact and a legal question respectively, neither addressable in code.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
