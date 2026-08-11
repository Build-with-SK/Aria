# Handoff — read this before touching anything

Written for a fresh Claude Code session so it does not have to re-derive the
state of this repo. Owner: Soundariyan Karunakaran. Everything here was true at
the last commit on `fix/signal-breadth-discount`.

---

## 1. What this project is

A self-hosted trading **research** platform. It proposes; a human approves.
Nothing in it may place an order autonomously on real money — that is a safety
invariant with tests behind it, not a preference. See §5.

Stack: FastAPI backend (`backend/main.py`, ~2,700 lines, one app), React
frontend (`frontend/`), 41 research modules (`src/v5/modules/`), an autonomous
paper-trading desk (`src/desk/`), a cognitive brain loop (`src/brain/`), and an
Obsidian vault used as long-term memory.

---

## 2. Immediate state

- **Both PRs merged.** `main` is at the merge of PR #2. CI (`.github/workflows/security.yml`)
  runs `security-regression` and `full-suite` on every push and PR; both green.
- **517 tests pass** in the project venv (`venv\Scripts\python.exe -m pytest tests/ -q`)
  and in a clean env built from `requirements-ci.txt`.
- **Current branch `fix/signal-breadth-discount`** has one unmerged commit
  (8646e43). It is correct and tested but **its result file is stale** — see §4.

### Known open items, in priority order

| # | Item | Why it matters |
|---|---|---|
| 1 | `data/v5/walk_forward.json` predates the signal-breadth fix | Every "significant" number in it is overstated. Re-run before quoting any of it. |
| 2 | No OAuth provider configured | This is why there is **no login page** — see §3. |
| 3 | Backend is not running and nothing starts it at boot | The research loop never fires, so the track record cannot accumulate. `AUTORUN_ARIA.bat` at boot is the fix. |
| 4 | `ALPHAVANTAGE_API_KEY` not set | Failover chain is 2 vendors deep, not 3. yfinance has been rate-limiting. |
| 5 | Branch protection not enabled | CI reports failures without blocking merges. |
| 6 | UI copy reads like dev notes | Owner's explicit complaint. See §6. |

---

## 3. The "no login page" bug — diagnosed, not fixed

`frontend/src/pages/Login.jsx` exists and the route works. The cause is that
**no OAuth provider is configured**:

```
providers: google  available=False  reason='set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env'
           github  available=False  reason='set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in .env'
           apple   available=False  reason='set APPLE_CLIENT_ID and APPLE_CLIENT_SECRET in .env'
any_configured: False
```

So the login page renders with nothing to click, and the owner sees the
free-tier view instead.

Two ways forward, and the choice is the owner's:

- **Single-user (simplest).** Drop OAuth from the UI entirely. `ARIA_OWNER_TOKEN`
  is already set in `.env` and already grants owner access from anywhere. The
  login page becomes a token field, or disappears.
- **Multi-user.** Register a GitHub OAuth app (5 minutes, free) and set the two
  env vars. Google and Apple are more involved.

Do not "fix" this by weakening auth. `src/auth/policy.py` and `src/auth/guard.py`
survived four adversarial review passes; the rules there are load-bearing.

---

## 4. The measurement work — the part most likely to be misread

The 41 modules were walk-forward validated with the data layer pinned per
evaluation date (`marketdata.as_of`). Three corrections were made to the
*harness itself*, each because the previous version overstated evidence:

1. **Cross-sectional discount.** Forty tickers on one date are forty
   observations of one market day.
2. **Scaled successes.** A raw hit count against a discounted sample read
   624/1039 as a 76% hit rate and returned `p = 0.0` for every module.
3. **Signal breadth (newest, commit 8646e43).** `credit` and `breadth` emit the
   *identical* score for every ticker (sd 0.000 across names). They were scored
   as ~1,000 independent observations; they had made ~48 bets.

**Consequence: the numbers in `data/v5/walk_forward.json` are stale and
overstated.** Regenerate before quoting:

```bash
venv/Scripts/python.exe scripts/run_walkforward.py --splits 6 --dates 8
```

Takes 30-60 minutes. Compare two runs with `scripts/compare_walkforward.py`.

### What the last complete run said

31/41 modules measured, 12 significant, **every one negative**, none with a
significant positive edge. Two groups deserve opposite conclusions:

- **Negative IC** (`carry` −0.215, `value` −0.226, `breadth` −0.176): the score
  ranks forward returns the wrong way round.
- **Positive IC, negative edge** (`quality` +0.188, `growth` +0.150,
  `currency_strength` +0.096): ranking works, the bull/bear *threshold* does not.

The second group is the only place the data points at something recoverable —
most likely as a **cross-sectional** signal (long top decile / short bottom)
rather than a directional one, which sidesteps the 60-80% base rate entirely.

**Do not tune thresholds until the backtest looks good.** With 41 modules × 40
names × one decade you can manufacture any curve you like. `src/v5/weightfit.py`
exists specifically to refuse that, and it will refuse until ~200 resolved calls
exist.

---

## 5. Invariants — do not break these

1. **Nothing autonomous may reach a live broker.** `src/execution/live_guard.py`
   wraps the desk's broker in `PaperOnlyBroker`. Reads pass; writes are refused
   unless env + adapter flag + resolved endpoint all say paper.
2. **Ownership must be proven per request**, never inferred.
   `policy.resolve_role_with_basis` returns `token | session | loopback`, and
   `guard.require_owner` accepts only the first two. A loopback address is not
   proof — behind a tunnel, every caller arrives on loopback.
3. **The app refuses to start** if a state-changing or broker-touching route
   lacks `Depends(require_owner)`. That is deliberate; if it crashes on boot,
   add the guard, do not remove the check.
4. **Never backfill the prediction log.** A track record built from simulated
   history is not a track record. 0 calls have resolved; that is the honest
   number and it only moves with elapsed time.
5. Only ADD endpoints to `backend/main.py` — never remove or rename.

---

## 6. The copy problem (owner's explicit request)

The owner finds the UI microcopy unprofessional. Example he cited, from the
Research page:

> "Any listing - india .... Nothing is preloaded; everything is fetched fresh
> when you look it up."

That is a developer's implementation note shown to a user. The professional
version states the capability, not the mechanism:

> "Global coverage — 34 exchanges, resolved on demand."

**The task:** audit user-facing strings across `frontend/src/` and the API
`description=` / docstring fields that surface in the UI. Rules:

- State what it does for the user, not how it is implemented internally.
- No apologising, no hedging, no "nothing is preloaded".
- Numbers where possible ("34 exchanges") beat adjectives ("lots of markets").
- Keep the honesty. "No track record yet — 0 of 20 calls resolved" stays. That
  is not unprofessional, it is the most credible thing here. Do not let a copy
  pass quietly delete the caveats that four review rounds put in.

That last point matters. Polishing tone is welcome; polishing away the
uncertainty is not.

---

## 7. Useful commands

```bash
venv/Scripts/python.exe -m pytest tests/ -q                    # 517 tests
venv/Scripts/python.exe scripts/run_walkforward.py --splits 6 --dates 8
venv/Scripts/python.exe scripts/compare_walkforward.py A.json B.json
venv/Scripts/python.exe scripts/build_public_record.py
```

Backend: `START_ARIA.bat`, or uvicorn on `backend.main:app`. Port 8000.
Frontend: `cd frontend && npm run dev` (5173), or the built copy is served at
`/app`.

Reviewer packet for an independent audit: `docs/audit/REVIEWER_BRIEF.md`.
Draft email to Dr Fabio Dias (unsent): `docs/audit/EMAIL_DRAFT_DIAS.md`.

---

## 8. Tone to take with the owner

He wants directness. He has repeatedly asked for the system to be made
"perfect" and to "beat every index"; the useful response has been to say
plainly what is and is not achievable, then do the tractable part. He responds
well to that and badly to being told what he wants to hear.

The strongest asset this project has is not its returns — it has none yet. It
is that the measurement harness is honest enough to say the modules do not
work, and that three separate overstatements in that harness were found and
fixed. Protect that.
