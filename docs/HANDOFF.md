# Handoff — read this before touching anything

Written for a fresh Claude Code session so it does not have to re-derive the
state of this repo. Owner: Soundariyan Karunakaran. §2, §3, §4, §6 and §7 were
re-verified against the working tree on **2026-09-27**; the rest dates from
August and is still accurate. For the September server, auth and trading work,
read `docs/OWNER_LOGIN_AND_TRADING_REPORT.md` and `docs/TRADING_ARCHITECTURE.md`
after this file.

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

## 1b. Self-modification, and whose brain she runs on

Steps 1-4 and the last bullet of step 7 of `docs/ARIA_NEXT_SESSION.md` are
built. In order, because each one only makes sense after the one before it:

| What | Where | What it does |
|---|---|---|
| The fence | `src/selfmod/protected.py`, `tests/test_protected_paths.py`, `scripts/check_protected_paths.py` | An ARIA-authored commit touching `src/auth/`, `live_guard.py`, `approval_queue.py`, `.github/workflows/` or the fence itself fails the suite and CI. Author identity **or** a `Co-Authored-By: ARIA` trailer counts. |
| The sandbox | `src/selfmod/sandbox.py`, `scripts/aria_propose.py` | Worktree off `main`, never the owner's tree. Refuses on: main, fenced path, deleted test, red suite, missing/placeholder rationale. Every refusal at once, ledgered under `data/selfmod/proposals/`. A PR needs `--open-pr` from a human; merging is not automatable. |
| Baselines | `src/inference/baseline.py`, `scripts/capture_brain_baseline.py` | Five fixed tickers. Module abstention and judge conviction are **equality** checks — they cannot move on a brain swap, and `compare()` calls drift MISWIRED rather than "worse". First capture: `data/baselines/brain_baseline_20260811-192123.json`. |
| Self-hosted brain | `src/inference/policy.py`, `router.py` | No vendor API in the reasoning path — filtered by provider *locality*, not by a blocklist. When her brain is down she abstains and says so; a second model answers only under `ARIA_DEGRADE=disclose`, tagged `degraded`. |
| Bounded context | `src/inference/context.py` | A stated truncation rule, and a `CONTEXT NOTICE` telling her what she cannot see. `num_ctx` is now set explicitly in the Ollama provider — it was defaulting, and Ollama truncates silently. |
| Her own state | `src/brain/self_state.py`, `GET /api/aria/state` | Brain reachable, data fresh, loop turning, calls resolved. Fails to "unknown", never to "fine". `speak` is empty when nothing is wrong. |

Two consequences worth knowing before you debug something:

- **`/api/chat` no longer needs an Anthropic key** and no longer uses one. The
  503 gate at the top of the handler is gone; a missing vendor key is not a
  reason she cannot think.
- **Fable's teacher and the frontier judge-prose consult are refused** while
  `self_hosted_only()` is true. Both are wrapped in try/except and fall back,
  so nothing breaks — but if you are wondering why the teacher went quiet,
  that is why. `ARIA_ALLOW_VENDOR_BRAIN=1` is the owner's override.

Preflight before serving: `venv/Scripts/python.exe scripts/brain_preflight.py`.

### Steps 5 and 6 — built, and waiting on data rather than on code

`src/training/` is written the way `src/v5/weightfit.py` is written: in
advance, refusing to produce a number today, starting on its own the day the
sample arrives.

| What | Where | The rule it enforces |
|---|---|---|
| The corpora | `src/training/dataset.py`, `scripts/build_training_set.py` | Chronological split, never shuffled, cut on a **date boundary** so no market day straddles it. The label is the realised outcome — `predicted_direction` is structurally barred from being a target, because training toward it teaches her to reproduce the system's opinions including the wrong ones. `label_leakage()` refuses a corpus whose prompt contains the answer. |
| De-identification | same | Emails, keys, account numbers, home paths, and the owner's name. Deliberately conservative on numbers: this is a trading vault, and a phone rule loose enough to catch a domestic number also eats every date and strike range in it. Verified on 200 real notes — one legitimate redaction, no false positives. |
| The promotion gate | `src/training/evaluate.py`, `adapters.py`, `scripts/promote_adapter.py` | One metric (Brier on P(direction)), scored **paired** on the same held-out rows, and an improvement must clear a bootstrap interval that excludes zero **and** be materially large. A tie goes to the incumbent. A first adapter is compared against the base model, not waved through. Abstentions score as nothing, never as 0.5. |
| The run | `src/training/qlora.py` | Renders a SLURM job; never submits one. **Refuses to render a cluster job for any corpus carrying personal data** — the vault trains locally or not at all. |

Two numbers to know: the outcomes corpus needs **200 resolved calls** (the same
floor and the same reasoning as `weightfit.MIN_RESOLVED`) and has **0**. At
roughly twenty calls a month that is about ten months of the paper loop
running — and it is never backfilled. `scripts/build_training_set.py --readiness`
prints the gap.

The voice corpus is ready today (864 notes). It is deliberately not built
automatically: an adapter trained on it carries the vault in its weights, so
the corpus and the adapter are both as sensitive as the notes themselves.

---

## 2. Immediate state

- **The September work is committed but NOT PUSHED.** Eight commits on
  `fix/signal-breadth-discount` ahead of `main` (0ec8c3f), in dependency
  order: owner sign-in and the service role, server operations, chat
  streaming, `src/trading/`, the Market crash fix, the copy pass, this
  handoff, and a market-data refresh. Each commit's tree was exported and
  its full suite run on its own before it was committed — none depends on a
  later one to pass. Pushing and the PR are the owner's call.
- **The full suite passes at HEAD**: 2,079
  (`venv\Scripts\python.exe -m pytest tests/ -q`, ~3.5 min).
- CI (`.github/workflows/security.yml`) runs `security-regression` and
  `full-suite` on push and PR — it has not seen these commits yet.
  `test_a_weekend_does_not_make_friday_stale` used to fail on every weekday
  except Monday: it built a frame ending `now - 3 days` and asserted it was
  not stale, but on a Wednesday that lands on a Sunday whose newest *business*
  bar is 5.5 days old, past `STALE_AFTER_DAYS = 5`. The intent was right and
  the date arithmetic assumed a Monday run. It is now written in calendar age
  (3 and 4 days, the Monday and the post-holiday-Tuesday cases), with a
  companion test asserting a missed week is still stale — so the fix cannot
  drift into "widen the threshold until it passes". `STALE_AFTER_DAYS` is
  unchanged.

### Why the pipeline reports 39 modules and not 41

Both numbers are right. 41 are registered; two — `reinforcement_learning` and
`alternative_data` — are marked `research_only=True` in `src/v5/registry.py`
and excluded from `run_all()` unless `include_research_only=True`. So 41
exist, 39 vote, and `module_count.total` reports the ones that voted. Nothing
is broken; the docs quote the registry and the API quotes the ballot.
- The breadth-fix commit (8646e43) **is on `main`**, and
  `data/v5/walk_forward.json` was regenerated after it (2026-08-11 17:18) —
  see §4 for what it now says.

### Known open items, in priority order (checked 2026-09-27)

| # | Item | Why it matters |
|---|---|---|
| 1 | The September commits are not pushed | See above. Until they are, CI has never run on them and the only copy is this disk. |
| 2 | Backend is not running, and nothing starts it at boot | Nothing on :8000. The research loop never fires, so the track record cannot accumulate. The Mac mini (`scripts/serverctl install`, `docs/MAC_MINI_SERVER.md`) is the plan; `AUTORUN_ARIA.bat` is the Windows stopgap. |
| 3 | Desk `auto_execute` is `true` | Paper-ceilinged in every mode, but `mini_preflight.py` flags it on an unattended box. The owner's call. |
| 4 | `ALPHAVANTAGE_API_KEY` not set | Failover chain is 2 vendors deep, not 3. yfinance has been rate-limiting. |
| 5 | Branch protection not enabled | `gh api .../branches/main/protection` → 404. CI reports failures without blocking merges. Repo settings are the owner's to change. |

Closed since August: the stale walk-forward (regenerated), "no login page"
(§3), and the UI copy (§6).

---

## 3. Login — resolved (single-user)

The owner chose single-user. OAuth, `src/auth/oauth.py`, `src/auth/users.py`
and the FREE role are gone; roles are `OWNER`, `SERVICE`, `ANON`. The login
page is one password field for the owner token, exchanged once for an HttpOnly
session cookie (`src/auth/ownerauth.py`, shared byte-for-byte with SENTINEL —
see `docs/OWNER_LOGIN_AND_TRADING_REPORT.md` §1–7).

Consequence worth knowing: **every user who gets past the gate is the owner.**
`OwnerOnly.jsx` and `BrainPublic.jsx` only render if the client and server
disagree, so copy in them is defence in depth, not a product surface.

Viewing the UI without the owner token: `.env` loads with `override=True`, so
an env var cannot swap in a test credential. A copy of `backend/`, `src/`,
`configs/` and `frontend/dist` run from a scratch directory **with no `.env`**
gets the designed loopback break-glass (owner-only routes still 403 by
design). That is how the 2026-09-27 copy pass was checked. Never type the real
token into an automated browser.

Do not weaken auth. `src/auth/policy.py` and `src/auth/guard.py` survived four
adversarial review passes; the rules there are load-bearing.

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

`data/v5/walk_forward.json` (gitignored) was regenerated **after** the breadth
fix — 2026-08-11 17:18, six splits × eight dates. To re-run:

```bash
venv/Scripts/python.exe scripts/run_walkforward.py --splits 6 --dates 8
```

Takes 30-60 minutes. Compare two runs with `scripts/compare_walkforward.py`.

### What the current run says

33/41 modules validated, **9 significant, every one negative**, none with a
significant positive edge. Hit rate minus base rate, in percentage points
(adjusted p < 0.05): `carry` −33.2, `commodity_cycle` −31.2, `value` −29.0,
`quality` −19.0, `cross_asset_correlation` −14.8, `crowding` −10.8, `growth`
−8.3, `sector_rotation` −6.6, `kalman` −6.0. The breadth fix did what it was
for: `credit` went from being scored as 1,093 independent observations to 73
and is no longer significant (adj. p 0.32), and `breadth` dropped out with it. Two groups deserve opposite conclusions:

- **Negative IC** (`carry` −0.218, `value` −0.225): the score ranks forward
  returns the wrong way round.
- **Positive IC, negative edge** (`quality` +0.174, `growth` +0.159; `earnings`
  +0.113 has significant IC but no significant edge): ranking works, the
  bull/bear *threshold* does not. `currency_strength` no longer clears the IC
  bar.

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

## 6. The copy problem (owner's explicit request) — done 2026-09-27

**What changed.** The cited Research line now reads "Global coverage — 22,022
symbols across 40+ exchanges…", with the count fetched from
`/api/universe/status` rather than typed in. Three hardcoded counts were
already stale (766, 3,000+, 3,143). Beyond tone, the pass found copy that had
become **false**:

- the chat's default mode was labelled "☁ CLOUD — Claude API", and every reply
  was tagged `[CLOUD]`. It is the self-hosted DEEP tier; the label is now
  `◆ ARIA BRAIN`, and the tag shows the model that answered and `DEGRADED`
  when a fallback did;
- Settings offered "New here? Create an account" via OAuth providers that no
  longer exist, and claimed the owner token was stored on the device;
- Strategies said "every order still needs a human approval", but armed
  auto-execute fills paper orders without asking;
- `OwnerOnly` promised non-owners "the 41-module research engine", which the
  server refuses them;
- News & Sentiment said "set NEWS_API_KEY" whenever a query found no headlines,
  even with the key set (`src/data/enrichment.py`).

Env-var names, `.env`, `ollama serve` and a Windows uvicorn line were removed
from the interface. Restart commands live behind the connection banner's
"How to restart" disclosure. Every caveat that §6 said must stay, stayed.

**Found on the way: the Market workspace crashed on every visit** from
2026-09-10 (9461c28) to 2026-09-27. `useRegime` was imported and never called.
The source-reading tests could not see it.
`tests/test_ui_architecture.py` now has two guards: an imported hook must be
called, and no operator strings in the UI. Both were checked failing on the
old code.

Not touched: `CoreTalk`, `PressToTalk` and `OptionsChain` are imported by
nothing. The `/api/chat/local` docstring still says "the surface free users
get". Backend `description=` strings shown only at `/docs` were not audited.

The original brief, for reference — the owner found the UI microcopy
unprofessional. The example cited, from the Research page:

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
venv/Scripts/python.exe -m pytest tests/ -q                    # ~2,080 tests, ~3.5 min
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
