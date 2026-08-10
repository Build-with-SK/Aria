# Changelog

## Remediation sprint — 2026-08-06

A security and correctness pass against three named audit findings, plus the
data, statistical and track-record work that came with them. Every fix has a
regression test, and every one of those tests runs in CI on each push and pull
request (`.github/workflows/security.yml`).

Read the "Still open" section at the bottom before treating anything here as
finished.

---

### Finding 1 — "Unauthenticated execution endpoints"

The global default-deny role middleware from the previous audit round was
present and working. These are the holes it still left.

**Reverse-proxy privilege escalation (critical).** `policy.resolve_role()`
granted OWNER to loopback callers when no owner token was set, and the address
it was handed came from `hardening.client_ip()` — which reads `X-Forwarded-For`
whenever `ARIA_TRUST_PROXY` is on. Behind nginx or Cloudflare every request
arrives from 127.0.0.1, so the fallback would have handed ownership, including
execution, to the entire internet.

- Authorisation now uses `hardening.peer_ip()` — the socket peer, which no
  header can forge. Rate limiting still uses the forwarded address, which is
  correct for counting humans.
- `policy.loopback_owner_enabled()` retires the loopback fallback entirely
  whenever `ARIA_TRUST_PROXY` is set: a proxied deployment is by definition
  remotely reachable.

**`"testclient"` was in the loopback allowlist.** Every FastAPI `TestClient`
request authenticated as the owner, which is why no test had ever been able to
prove that an execution endpoint refuses an anonymous caller. Removed.

**Auth enforced only by URL prefix.** All 24 order-touching routes now declare
`Depends(require_owner)` (`src/auth/guard.py`). The guard re-derives the role
from the request rather than reading `request.state`, so reordering or removing
the middleware cannot silently open them. `backend/main.py` refuses to start if
any route matching `policy.EXECUTION_PREFIXES` lacks the guard — adding an
unguarded execution endpoint is now an import-time crash, not a quiet hole.

Tests: `tests/test_security_execution_auth.py` (11). The suite discovers
execution routes from the app's own routing table, so endpoints added later are
covered the day they are written.

---

### Finding 2 — "IBKR adapter order-status misreporting"

The previous round fixed absence-means-FILLED. These remained.

- **Partial fills were invisible.** IBKR labels a half-filled order
  `"Submitted"` — identical to one that has not traded a share. `OrderStatus.PARTIAL`
  existed and the adapter never produced it. The fill counters now override the
  label.
- **Status is reconciled against `trade.fills`** (actual execution events)
  rather than the `orderStatus` summary alone. Where they disagree the smaller
  fill wins and the discrepancy is flagged, so protective orders are never
  sized on shares that were not bought.
- **A successful placement whose status read threw was reported REJECTED** — a
  live order at IBKR that nobody polls and nothing brackets. Placement and
  reading are now separate operations; the order id survives a failed read.
- Disconnection and read errors no longer masquerade as rejections. Rejections
  carry IBKR's own reason from `trade.log`. `cancel_order` distinguishes
  already-cancelled from already-filled instead of returning one `False`.
- **`ApprovalQueue.reject()` could not record a broker rejection.**
  `OrderManager.execute()` marks a trade `approved` before calling the broker,
  and `reject()` only accepted `pending` — so every broker rejection left the
  audit trail claiming an approved trade that never existed. Found by the new
  tests, not by reading.

Tests: `tests/test_security_ibkr_status.py` (25), covering partial fill,
rejection and cancel plus the transitions around them.

---

### Finding 3 — "Live-account auto-execute gate"

The entry path was gated. Three other paths were not, and none of them look
like "execution" from outside: **bracket healing**, **trailing-stop
replacement**, and **stale-order cleanup** all place and cancel real orders
from the desk's 5-minute management tick.

- The desk no longer receives a broker. It receives `PaperOnlyBroker`
  (`src/execution/live_guard.py`), so the refusal sits at the boundary every
  order must cross — including methods not yet written. The human approval path
  still uses the raw broker, so an approved live trade still executes.
- Blocked orders are pushed to the approval queue rather than swallowed.
  Refusing to place a protective stop on a live position is not obviously the
  safe direction, so the human is told.
- **"Checked against both the env var and the broker's own paper flag" was not
  two checks.** `AlpacaBroker.paper` is derived from `ALPACA_PAPER`; both read
  one variable. A third, independent check now inspects the client's resolved
  `_base_url` — where the requests will actually go.

Tests: `tests/test_security_live_gate.py` (31), written as an attacker's
checklist: each test is an attempt to get an order onto a live account and
passes only when the attempt fails.

---

### Data reliability

- **A second and third market-data vendor** (`src/v5/vendors.py`): yfinance →
  Alpha Vantage (`ALPHAVANTAGE_API_KEY`) → Stooq. Stooq needs no credentials,
  so failover works on a fresh checkout — a fallback that requires setup is not
  there on the night it is needed.
- **The silent stale-cache fallback is gone.** `marketdata` records provenance
  for every series it serves: vendor, fetch time, newest bar, and whether that
  makes it stale. Old cached data is still used (this is a research system that
  cites its dates) but it comes back labelled.
- **Staleness propagates into confidence**, not just into a log line. It is
  applied in `meta.review()` alongside the other penalties, so the risk gate
  sizes on the reduced edge and the prediction log records the reduced
  confidence. The recommendation headline itself is prefixed `[STALE DATA — as
  of …]`, because the headline is what gets quoted.
- A total vendor outage is now reported as an outage rather than as "not a
  listed symbol", which used to send users to fix the wrong thing.
- New: `GET /api/v5/data-health`.

Tests: `tests/test_data_failover.py` (17).

---

### Statistical rigor

- **`src/v5/walkforward.py`** replays all 41 modules across history.
  `marketdata.as_of()` pins the data layer to each evaluation date in the one
  function every module reads through, so no module can see a bar it could not
  have seen. Folds come from `PurgedWalkForwardCV` with the module's own
  horizon as the purge parameter.
- **First full run stored** in `data/v5/walk_forward.json`: 24 of 41 modules
  produced enough directional calls to grade; several are measurably worse than
  the base rate over the sample. Results are stored with base rates, per-side
  hit rates and written caveats, because a hit rate published without its base
  rate is the most flattering possible presentation of nothing.
- **Modules that read the wall clock are detected and flagged**
  (`point_in_time: false`) rather than quietly averaged in — found when a module
  reported on "calendar month 8" with the data layer pinned to February.
- **`walk_forward_validated` now travels on every module report**, so an
  untested engine's opinion is visibly an untested engine's opinion in the API
  response. New: `GET /api/v5/walk-forward`.
- **`src/v5/weightfit.py`** is the out-of-sample harness for the hand-set
  ensemble family weights. It refuses to fit below 200 resolved calls, scores
  every candidate on expanding-window walk-forward blocks against the current
  weights, shrinks towards the prior, and applies nothing. It cannot run yet and
  says so with the exact shortfall. New: `GET /api/v5/weight-fit`.

Tests: `tests/test_walkforward.py` (16), `tests/test_weightfit.py` (13).

---

### Track record infrastructure

- **The flywheel was not turning.** `learning.resolve_pending()` had no caller
  except a manual POST endpoint, and predictions were logged only when a human
  opened an analysis page. The honest description of the missing track record
  was not "too early to tell" — nothing was running that would ever produce one.
  `src/v5/loop.py` is that loop: PREDICT across a watchlist fixed in advance,
  RESOLVE on a schedule, with a heartbeat so "is it running" has an answer.
- **Scheduler robustness.** APScheduler's default `misfire_grace_time` is one
  second: on a loaded box a job that cannot start within a second of its slot is
  dropped with a log line nobody reads. Now an hour, with coalescing, plus a
  15-minute watchdog that restarts the scheduler if it dies or loses jobs.
- **`/api/v5/track-record` took roughly eight minutes per call** — two of its
  four sources fetch live prices across the whole recommendation log. Those
  sources now run under a 10-second deadline and degrade to "unavailable"
  instead of holding the response open. Measured after: 3.4s.
- **A minimal public page** (`scripts/build_public_record.py` →
  `docs/public/track-record.html`): one self-contained static file, no scripts,
  no fetches, no link back into the system. It shows the resolved count while it
  is still too small to mean anything, and lists every module that has never
  been validated.
- New: `GET /api/v5/loop-health`, `POST /api/v5/loop/run` (owner-only).

Tests: `tests/test_track_record_threshold.py` (15), including seeding 20 fake
resolved calls and asserting the endpoint flips from `measurable: false` to real
numbers.

---

### Second pass — findings from the independent review

The three fixes above were sent to a reviewer with no part in writing them and
no framing suggesting they worked. It returned **PARTIAL on all three**. What
it found, and what was done about it:

**Finding 1 — the "structural" guard was circular.** The startup assertion only
inspected routes already under `/api/execute` or `/api/desk`, which is the same
path-prefix reasoning the per-route guard exists to escape. A write endpoint
under `/api/v5` or `/api/universe` was invisible to it — and four were
reachable by any signed-in free user, including `POST /api/v5/learning/resolve`,
which resolves predictions and moves the ensemble weights the desk sizes trades
from, and `POST /api/v5/learning/revert/{version}`.

- The check now keys on HTTP **method**: every POST/PATCH/PUT/DELETE in the app
  must carry the guard or be named in `policy.PUBLIC_WRITE_ROUTES`. It found 19
  unguarded writes on its first run; 17 were guarded, 2 declared public
  (`/api/chat/local`, `/api/quant/greeks` — a completion and a Black-Scholes
  call, neither of which writes anything).
- It also re-runs in a startup event: the import-time call executes at whatever
  line it sits on, and the bottom of the file is where routes get appended.

**Finding 1 — loopback fail-open behind a tunnel (the serious one).** With
neither `ARIA_OWNER_TOKEN` nor `ARIA_TRUST_PROXY` set, cloudflared connects to
localhost, so every caller on earth arrives from `127.0.0.1` and inherits
OWNER — reopening all 24 guarded routes. Retiring the fallback had been made
conditional on an operator remembering a variable that `.env.example` did not
even list.

- `require_owner` now refuses **inferred** ownership: with no owner token and no
  owner email configured, a loopback caller may browse the app but may not
  reach a broker. The cost of forgetting is "I must set a token before I can
  trade from my own machine", not "the internet can place orders".
- `ARIA_TRUST_PROXY` is now documented in `.env.example` with the reason.

**Finding 2 — the fix introduced a new contradictory state.** Taking
`min(orderStatus.filled, executions)` was wrong: `execDetails` routinely arrives
ahead of the final `orderStatus`, so a 100-share execution with
`orderStatus.filled=0` reported **"FILLED, 0 shares"** — which reads downstream
as "entry complete, place brackets" over a real position.

- Executions are now authoritative, as the docstring had claimed all along.
- A `Filled` label with no executed quantity anywhere is reported as SUBMITTED,
  not as a completed order.
- A rejection arriving after a partial fill kept its PARTIAL status but lost
  IBKR's reason, because the reason was attached only when the final mapped
  status was REJECTED. Now keyed on the IBKR label.
- `IBKRBroker.get_open_orders` was inheriting `BrokerBase`'s `[]` default, so
  bracket healing, stale-order cleanup and the "is an exit already in flight"
  check all silently did nothing for every IBKR position. Implemented.

**Finding 3 — the wrapper failed open on unknown methods.** `PaperOnlyBroker`
claimed to cover "methods nobody has written yet" and did the opposite: anything
outside a hand-maintained `WRITE_METHODS` set passed through raw. Two of its
seven entries did not exist on either adapter.

- Inverted: `READ_METHODS` is the allowlist and anything callable outside it is
  gated. Fail-closed.
- **The gate certified a live IBKR account as confirmed paper.** It read
  `ALPACA_PAPER` regardless of which broker it was gating, and could not read an
  endpoint shape it did not recognise — and an unreadable endpoint abstained.
  `endpoint_is_paper` now understands IBKR's ports (7496/4001 are live) and
  `env_paper_confirmed` reads the right variable per broker.
- `paper_status` reports `endpoint_checked` separately, so "the endpoint says
  paper" and "nobody could tell" are distinguishable.
- The header claiming "three independent facts" was wrong for Alpaca, where all
  three descend from `ALPACA_PAPER`. Rewritten to say what the third check
  actually buys.
- The public `wrapped` accessor — an unwrapping escape hatch in the middle of
  the guard — was removed.
- `position_manager._close` and the auto-execute arming route now use the same
  `paper_confirmed` gate rather than weaker two-check versions; arming requires
  a positively confirmed paper account rather than "not known to be live".

One further thing the review pass turned up on its own: `policy.warn_if_unprotected()`
had been written and was never called from anywhere — the startup warning about
unconfigured ownership existed in the source and nowhere else, which is the same
failure mode as a guard nobody invokes. It runs from a startup event now, and
says what the current configuration actually means.

## Overnight session — measuring the research engine

Separate from the security sprint. Full write-up in
`docs/audit/WALKFORWARD_FINDINGS.md`.

**Thirteen of 41 modules had never expressed a single opinion**, and four of
them were silenced by a bug rather than by having nothing to say.
`hit_rate_probability` wants 20 independent observations; `conditional_hit_rate`
correctly discounts overlapping windows to `n_raw / horizon`. A tercile state
over five years yields *exactly* 20 — so `momentum`, `market_regime`,
`volatility` and `clustering` sat on the threshold and fell under it whenever
their state was slightly rarer than a third. The ten years they needed was
already fetched and cached; they were discarding half of it. Widening the
universe from 8 to 40 tickers did not help, because the threshold is per-ticker.

The four now report ~1,000 calls each. **This bought coverage, not skill** — an
A/B under identical harness code found nothing statistically significant in
either arm.

**Two bugs in the measuring instrument, both written this session:**

- *No cross-sectional discount.* Forty tickers on one date are forty
  observations of one market day. `_util` already refuses to count overlapping
  windows as independent; the harness was making that exact error across the
  cross-section. Measured rho on 21-day forward returns is 0.21–0.40.
- *Raw successes against a discounted sample.* `hits/n_eff` read 624 hits in
  1,039 calls discounted to 824 as a 76% hit rate instead of 60%, returning
  `p = 0.0` for every module — including one whose edge was negative. Every
  significance flag in that code's first run was wrong.

After both fixes, `clustering`'s 85% hit rate and "+17.6% edge" — which would
have looked like the best module in the system — turns out to be 14 effective
observations, and collapses to +3.3% on a real sample.

Consequently the language was overclaiming too. `_verdict` called anything past
a three-point edge "positive" or "NEGATIVE" on raw counts; it now requires
significance and quotes the discounted sample. And `walk_forward_validated`
means *measured*, not *works* — `walk_forward_skill_demonstrated` is now a
separate field, separate because the answer is usually no.

**Track record:** the loop ran and logged 10 predictions (15 total). Only
directional calls count toward calibration and only 6 of 15 are directional, so
the watchlist went from 10 names to 30 — pre-committed and dated in
`data/v5/watchlist.json`, because a universe edited after seeing results is a
highlight reel, not a track record. Nothing was backfilled. Earliest possible
first calibration remains roughly five weeks out.

### Third pass — the review found the second pass had not closed it either

The same reviewer was given the second-round changes with the same instructions.
It returned PARTIAL on all three again, and the first item is the one worth
reading twice.

**The loopback fix did not work, and a test I wrote asserted the bypass as
correct behaviour.** `owner_is_configured()` was satisfied by *either*
`ARIA_OWNER_TOKEN` *or* `ARIA_OWNER_EMAIL`, but the loopback fallback in
`resolve_role` is only retired by the token. Set `ARIA_OWNER_EMAIL` — which
`docs/DEPLOY_PUBLIC.md` tells you to do, for OAuth — and inferred loopback
ownership reached brokers again. Worse, `test_a_configured_owner_on_loopback_still_works`
asserted exactly that combination returns 200, so the hole was pinned open by a
passing test.

The mistake was asking a question about the *environment* ("is an owner
configured") when the security-relevant question is about the *request* ("how
did this caller prove it"). `resolve_role_with_basis()` now returns
`token` / `session` / `loopback` / `none`, and `require_owner` accepts only the
first two. There is no environment variable that turns a loopback address into
proof. The test is now parametrised over both environments and asserts 403 for
each.

Also from this pass:

- **A broker-touching GET under a free prefix** would have been caught by
  neither the method rule nor the path rule. The assertion now also scans each
  handler's source for broker-reaching names, verified with a negative control.
- **`max()`, not "executions win".** The second-round rule was wrong in the
  opposite direction from the first: `trade.fills` only holds executions seen
  on the current connection and clientId, so a reconnect mid-fill truncates it
  and a completed 100-share order reported as 40. Neither source invents
  shares, so the evidenced maximum is the honest number.
- **`IBKRBroker.get_open_orders` was unreachable by every consumer it claimed
  to fix** — `open_orders_by_ticker` and `cancel_stale_orders` both defaulted
  to `self._alpaca`. They now iterate every connected broker. The field it
  populated for `submitted_at` (`trade.logTime`) does not exist on
  `ib_insync.Trade`, so it would have been `""` → `fromisoformat("")` →
  swallowed by a bare `except` → every order silently skipped.
- **The wrapper handed out the broker's SDK client.** "Non-callables cannot
  place an order" was the assumption; `_client` is an alpaca-py `TradingClient`
  and `_ib` is an `ib_insync.IB` — objects with live order methods on them. Only
  inert values pass now.
- **`last_closed_fill` was being refused as a write.** The caller wraps it in a
  broad `except`, so the refusal was swallowed and the exit path fell through to
  an *estimated* exit price — writing invented P&L into the closed-trades
  ledger the track record and the teacher's lessons are computed from. It fired
  for any account where `paper_confirmed` was False, including an ordinary paper
  account with `ALPACA_PAPER="True"` (wrong case). A test now asserts every
  public adapter member is explicitly classified read or write.
- **An unknown IBKR port voted "paper".** Anything outside `{7496, 4001}`
  affirmatively confirmed — a Docker port map or SSH tunnel in front of a live
  gateway. Ports outside both documented sets now abstain.
- **Name-substring dispatch** (`"ibkr" in name`) put the next adapter straight
  back into the bug just fixed. Adapters now declare `PAPER_ENV_VAR` and their
  own `endpoint_is_paper()`; one that declares nothing fails closed.

One further thing the review pass turned up on its own: `policy.warn_if_unprotected()`
had been written and was never called from anywhere — the startup warning about
unconfigured ownership existed in the source and nowhere else, which is the same
failure mode as a guard nobody invokes. It runs from a startup event now, and
says what the current configuration actually means.

### Fourth pass — brokers held, the vault did not

The reviewer confirmed it could not reach a broker any more: the basis-based
guard held against every variant it tried, including the `ARIA_OWNER_EMAIL`
route that defeated the previous fix. It then pointed out that the fix was
correct for its stated scope and the scope was wrong.

**The same inferred ownership still opened the vault.** The middleware called
the basis-blind `resolve_role`, so through a tunnel `/api/vault/status`,
`/api/portfolio`, `/api/report` and `/api/stats` all answered 200 — and
`/api/chat/local`, deliberately exempt from the guard, passed `request.state.role`
into `_vault_context()` and would ground a stranger's reply in the owner's
Obsidian notes. `policy.py`'s own header names that as the thing that must
never happen.

The fix is not another flag. `note_proxy_evidence()` reads what the traffic
already says: a tunnelled request carries `CF-Connecting-IP` or
`X-Forwarded-For`, which a direct caller has no reason to send. The first such
request retires the loopback fallback for the life of the process. This is
self-configuring — every previous protection here was keyed to `ARIA_TRUST_PROXY`
and so depended on an operator remembering a variable, which is what all three
rounds of holes reduced to. Direct localhost keeps working; a proxied
deployment closes itself. A hostile caller can forge the header against a
directly-exposed instance and lock the owner out of the fallback until restart —
that costs the owner a token, where the alternative costs them their notes.

- **Reconciliation instead of a fourth tiebreak.** Three passes produced three
  rules for the two fill counters (min → executions-only → max), each justified
  by naming the previous one's failure, and the reviewer produced a counter-case
  for each. The invariant was unused three lines away: `filled + remaining`
  must equal the order size. Counts are now clamped to the order quantity and
  reported `reconciled: false` when they do not add up.
- **The price rule was defeated by its own fallback.** With `avgFillPrice` at 0
  — the normal state while the summary leads the executions — a 100-share fill
  was priced at the VWAP of the 40 visible executions, exactly what the comment
  above it said must not happen. An unknown price is now reported as unknown.
- **`err = err or …` meant the first condition silenced the rest**, and the one
  silenced was the reconciliation failure. Notes accumulate.
- **The multi-broker merge was reverted.** Merging every broker's orders into
  one dict keyed by ticker made consumers pass IBKR order ids to Alpaca's
  `cancel_order`, and made bracket healing conclude an Alpaca position was
  protected because an IBKR stop existed on the same symbol — a naked position
  produced by a safety check. Callers that mean "every broker" now say so.
- **Name mangling is namespacing, not containment.** `self.__broker` was still
  `wrapper._PaperOnlyBroker__broker` and still in `vars(wrapper)`, and
  `guarded.__closure__` held the raw broker's bound `submit_order`. The wrapped
  broker now lives in a module-level `WeakKeyDictionary`, and the guarded
  closure re-looks-up rather than closing over the method. Verified: `vars()` is
  empty of it and the closure holds nothing useful. This is containment against
  accident, not a boundary against in-process code — the docstring now says so.
- **Four `WRITE_METHODS` entries existed on no adapter.** The classification
  test passes if a member is in either set, so an aspirational write list is a
  place to file a new read by mistake — which is what silently blinded
  `last_closed_fill`. Removed, with a test that every declared write is real.

Every one of these has a named regression test. The three security files grew
from 67 tests to 100; the suite as a whole is 488.

### CI

`.github/workflows/security.yml` runs each finding as a named step plus the
full suite, on every push to `main` and every pull request.
`requirements-ci.txt` is deliberately minimal — the security tests must not be
able to fail because a data vendor's SDK moved. The whole suite was verified in
a clean environment without `ib_insync` or `alpaca-py`, which caught two tests
that had only passed because those happened to be installed locally.

**Branch protection is not something this sprint could set.** Until "Require
status checks to pass before merging" is enabled for the `security` workflow in
the repository settings, these tests report failures but do not block a merge.

---

### Still open — deliberately not claimed as fixed

- **There is still no track record.** Phase 4 built the machinery that can
  produce one; the numbers require elapsed time with the loop running. No code
  change shortens that. As of this commit: 5 predictions logged, 0 resolved, and
  20 resolved calls are needed before calibration reports anything.
- **Single maintainer, no peer review.** This sprint was one pass by one agent,
  and the independent review phase must run in a separate session that did not
  write the code. A second human's eyes on `src/auth/` and `src/execution/`
  specifically would be worth more than any further solo pass.
- **Regulatory exposure (FCA/MiFID II).** Untouched and out of scope for a
  coding session. If this is ever exposed to anyone else's money, it needs a
  real legal read.
- **The walk-forward sample is one market and one decade.** Eight large-cap US
  names over roughly ten years, in which the market mostly rose. The figures do
  not transfer to small caps, other geographies, or a falling decade.
