# ARIA + SENTINEL — FINAL COMPLETION REPORT

**Date:** 2026-08-29
**Tests:** 1,383 passed · 1 skipped · **0 failed · 0 xfail**
**System:** running and verified — backend `:8000`, frontend `:3000`, 10/10 workers healthy


> **ADDENDUM — 2026-08-29, after a shipping audit.** This report is kept as the
> record of that day's work. Several of its figures and two of its "remaining
> limitations" have since moved, and the README is the current description of
> the system. What changed:
>
> * **Tests: 1,383 → 1,419**, still 0 failures and now 0 skips.
> * **`EDV.L` is fixed.** This report listed it as an unrepairable limitation.
>   The root cause was found instead: the LSE loader declared
>   `asset_class='equity'` on INSERT and omitted it from its ON CONFLICT
>   clause, so a display ticker already claimed by a US bond ETF kept
>   `fixed_income` forever. Same half-written-identity defect as `BA`, one
>   column over. Fixed at the loader.
> * **The four contaminated names are fixed at source.** A universe refresh
>   running with the repaired yaml upsert wrote the correct names into the
>   master, so `identity.KNOWN_BAD_NAMES` is now a dormant tripwire rather than
>   an active correction. Three tests that asserted the *broken* state had to be
>   rewritten — the defect being fixed is what made them fail.
> * **A silent bug in this session's own work was found and fixed:** the daily
>   report called a `calibration.report()` that has never existed, behind a
>   `hasattr` guard, so its calibration section was permanently empty while
>   looking populated.
> * **`repair_universe_identity.py`'s docstring was inaccurate** — it claimed to
>   write through `assert_provider_symbol_safe()`, which it never called.
>   Corrected.
> * **Four more BSE renames appeared** in a real refresh and were absorbed by the
>   ISIN classifier with no code change, which is the argument for structural
>   classification over a hardcoded list.
>
> The `UNIQUE(provider_symbol)` decision is unchanged and still correct: not
> added, because legitimate aliases would be deleted by it.

---
> The system is finished to a coherent, runnable state. Two things are marked
> **BLOCKED** at the end, both narrow and both stated rather than hidden.

---

## A. What was already complete (preserved, not rewritten)

Event bus · ledger · world model · worker registry · attribution · calibration
(Wilson intervals, population separation) · research evidence ladder · claim
classifier · canonical provider identity model · pence/GBp normalisation ·
ARIA cognitive brain · Obsidian integration · consolidated workspaces ·
fail-closed migration tooling.

Nothing in that list was rewritten to look different. Where a module changed,
it was extended.

---

## B. What changed today

### Data safety

| # | Defect | Resolution |
|---|---|---|
| 1 | **100× GBp / identity** | Namespaced resolution. `provider_identity()` + `provider_currency()` never consult the display column. Signals stamped with their own provider symbol and quote unit. |
| 2 | **GBPINR=X as US/equity** | Root cause fixed in the resolver; 13 contaminated rows re-filed. |
| 3 | **2 unresolved MRK predictions** | Resolved on evidence — exact price match plus source-resolver corroboration. |
| 4 | **2 BSE provider collisions** | **Not collisions.** ISINs are identical — company renames. |
| 5 | **Ledger growing during migration** | Snapshot + plan hash; `apply()` fails closed if the ledger moved. |

### The 100× defect, traced end to end

```
provider   yfinance("BA")                       -> Boeing, USD
raw        configs/universe.yaml ticker "BA"    -> the SAME string
stored     signals.json["BA"].current_price     -> 214.20
identity   signal_identity("BA")                -> BA / US / USD      ← was BA.L / LSE / GBp
API        /api/universe/currencies?symbols=BA  -> USD                ← was GBp
frontend   214.20 USD                                                 ← was $2.92
```

The rule is unchanged and was **not** replaced: `100 GBp = £1.00`, stored `GBP`,
`price_unit GBp`, multiplier `0.01`. Swapping GBp for GBP would *introduce* the
100× error, because every reader that already divides by 100 would do it twice.

What changed is *which symbol the currency is resolved from*. A display ticker
is a label; a provider symbol is the instrument.

Verified live:

```
BA USD   BA.L GBp      AAL USD   AAL.L GBp
JD USD   JD.L GBp      EDV USD   AAPL  USD
namespace=master -> BA GBp        (still correct for search)
```

### The two MRK predictions — resolved, not guessed

Three independent lines of evidence, all agreeing:

```
2026-08-13  recorded 135.55   MRK 135.55 (0.00%)   MRK.DE 139.50 (2.91%)
2026-08-17  recorded 135.97   MRK 135.97 (0.00%)   MRK.DE 136.40 (0.32%)
```

1. The recorded prices are **byte-identical** to Merck & Co's close. The old 5%
   band could not tell an exact match from a nearby one, so it called it a tie
   and refused. A new `EXACT_TOLERANCE` (0.1%) breaks that — and only when
   exactly one candidate is inside it.
2. `v5.marketdata.resolve("MRK")` → `MRK`. `MRK.DE` is a separate display row
   and is unreachable from the string `MRK`.
3. A third MRK prediction from the same source was already `PRICE_VERIFIED` to
   `MRK`.

Two exact matches still refuse. No candidate matching still refuses.

### The two BSE "collisions" — resolved by evidence

```
531257.BO   PRATIKSHA CHEMICALS LTD.  ==  VELLORA IMPACT LIMITED     INE530D01012
539455.BO   ARYAVAN ENTERPRISE LTD    ==  ECOFINITY ATOMIX LIMITED   INE360S01012
```

**The ISINs are identical.** A BSE scrip code is permanent; a ticker is not.
Both pairs are one security renamed between the 2026-07-28 and 2026-08-06
bhavcopy loads. The earlier audit had only the names — and the names were
exactly what had changed. Classification now uses ISIN first.

No company was deleted. `one_symbol_one_security: True`, `error_count: 0`.

**`UNIQUE(yahoo)` was still NOT added, deliberately.** Six legitimate aliases
share a provider symbol (superseded tickers, `BRK.B`/`BRK-B`, bare FX forms) and
a unique index would resolve each by *deleting a row*. The invariant and the
index are different questions and are now reported as two separate flags.

### Migration — applied, invariants held

```
plan hash       : 709826a9acfaf1e9
snapshot before : 4db4563b99fb47ab   339 rows
rows written    : 2
invariants held : True
```

| Invariant | Before | After |
|---|---|---|
| predictions | 339 | **339** |
| graded | 171 | **171** |
| scored | 171 | **171** |
| decisions | 116 | **116** |
| subject digest | `QQQ\|REMX\|…` | **unchanged** |
| price digest | `710.72\|66.08\|…` | **unchanged** |
| outcome digest | `-1\|1\|1\|…` | **unchanged** |

**339/339 predictions now carry a provider identity. 0 unresolved.**
`UNAMBIGUOUS 316 · PRICE_VERIFIED 21 · EXACT_PRICE_MATCH 2`

---

## C. What was deleted

**User-facing concepts:** `LIVE MIND` — gone as a destination, a tab and a label.
Not hidden with CSS; the duplicate architecture is removed.

**Files:** `BrainHub.jsx` (the Brain/Live-mind TabBar), `Intelligence.jsx`,
`Thinking.jsx`, `Report.jsx`, `Alerts.jsx`, `Brain.jsx`, and the orphans left by
the earlier consolidation — `Backtest`, `Futures`, `Macro`, `ML`, `Options`,
`QuantLab`, `Recommendations`, `Signals`, `TrackRecord`.

Nothing useful was dropped. Every capability was absorbed (§D), and a test now
fails if any page is left routed by nothing.

---

## D. What was consolidated

| Was | Now |
|---|---|
| Live Mind (page) | a section inside BRAIN |
| Live thought stream | LIVE COGNITION, inside BRAIN |
| Memory Browser | MEMORY, searchable inside BRAIN |
| Brain daemon console | BRAIN CONTROLS — every button kept |
| Chat / Ask ARIA | inside BRAIN |
| Alerts (page) | ALERTS, inside BRAIN |
| Daily report (a card) | **its own destination** |
| Obsidian vault | a capability of the one brain |

**One `/api/brain` composes all of it.** Panels no longer fetch their own slice
and then disagree on screen.

---

## E. Brain architecture

`src/brain/aggregate.py` returns ONE state: status · regime · cognition ·
memory · knowledge · world · capability. It aggregates; it computes nothing —
a second place that decides the regime is a second regime.

`_status()` is the only place liveness is decided, so the rail, the header and
the stream cannot disagree.

**Activity, not chain-of-thought.** `phase_activity()` is the single boundary:

```
ORIENT        -> "Reviewing the macro regime and market state"
ANALYSE_ARKK  -> "Comparing technical, fundamental and ML evidence — ARKK"
DECIDE        -> "Evaluating risk and forming a position"
<unknown>     -> "Working"        (never the raw step name)
```

Conclusions **are** shown — they are the output of a step, not its working, and
a user who cannot see conclusions cannot audit the system.

---

## F. Live Mind removal — verified

Enforced by `tests/test_ui_architecture.py`, which reads the real source:

- no page named Live Mind
- no user-facing string says it (comments explaining the removal excluded)
- `BrainHub` gone from disk and from `App.jsx`
- `/thinking`, `/live-mind`, `/memory`, `/chat` all redirect into `/brain`
- `/` and `/brain` render the *same element* — one front door
- the rail offers no `LIVE MIND`, `COGNITIVE BRAIN`, `THINKING`, `CHAT` or `MEMORY` entry
- no orphaned page, no route pointing at a deleted page

---

## G. Daily report

`data/reports/YYYY-MM-DD.json` — **date is the identity, write-once.**

- refuses to overwrite; superseding **archives** the previous version
- a missing day answers `NOT_GENERATED` and keeps saying it — never falls back
- the legacy single file was imported under **its own date** (2026-05-31), not today
- generated every 30 min by `_daily_report_loop` (worker `daily_report`, healthy)
- the date is also the path, so non-dates are refused (traversal guard)

Live:

```
2026-08-29  Expansion (Goldilocks)   GENERATED
2026-05-31  Mildly Bullish           (imported legacy)
2026-08-01  NOT_GENERATED            most_recent_available: 2026-08-29
```

Sections: executive summary · macro · market · news · ARIA's assessment ·
track record · risks/blind spots.

---

## H. Live news

`src/research/live_news.py` — a **reader** of the existing observation store, not
a second event store. Cursor-based (`next_since`), so silence is the ordinary
answer and is rendered as silence.

Two axes kept apart: **evidence state** (OBSERVATION vs VERIFIED) and **source
tier**. A rumour on a high-tier feed is still a rumour.

**Relevance was the real problem.** The eye searches a news backend for a
ticker, and a keyword search does not know what a ticker is:

```
ticker:bill  ->  "Bill Ackman's Pershing Square buys Netflix"
ticker:bill  ->  "Congress's stock trading bill doesn't solve the real problem"
ticker:bill  ->  "California man arrested after driving pickup with guillotine"
```

**103 of 197 stored observations were the query string matching as ordinary
English.** Association is now tested three ways — STRONG (`BNO`, `$BILL`,
`(NET)`), NAMED (the company's distinctive *multi-word* name), WEAK (neither).
Case-sensitive, because "Bill" in prose is not the ticker `BILL`.

Result, live: `raw 110 → shown 47 → 8 deduped, 55 held back`, SEC filings at the
top. Every drop is reported with its reason; nothing is filtered silently.

---

## I. Expansion (Goldilocks)

**Not hardcoded.** `src/macro/regime.py` is a growth/inflation quadrant and every
quadrant is reachable; each threshold flips it on its own (15 tests).

The dishonesty was never the regime — it was the **certainty**. Every missing
input fell back to a default on the benign side of its own threshold
(`3.0 < 3.5`, `0.0` is not `< 0`, `20 < 25`, `4.5 < 5.5`), so an empty snapshot
classified as Expansion with no hint that nothing had been measured.

`confidence` is now **input coverage**, never conviction. An empty snapshot
reports **0%**. A dramatic reading on two inputs still reports 50%.

Live, and genuinely earned:

```
Expansion (Goldilocks) — 100% input coverage, as of 2026-08-29
  CPI 3.37% is below the 3.5% high-inflation threshold
  10Y-2Y spread +0.39pp is positive
  VIX 14.5 is below the stress level of 25.0
  Unemployment 4.1% is below the 5.5% stress level
would flip if: CPI rises above 3.5%  —  0.13pp away
```

It sits **0.13pp** from Stagflation. That is a real reading, not a decoration.

---

## J. Information architecture

```
ARIA
├── BRAIN          status · regime · VIX · cycle · memory · model
│   ├── ASK ARIA        chat, with the world model already in context
│   ├── LIVE COGNITION  activity summaries + conclusions
│   ├── CURRENT WORLD   breadth, positions, record, what changed
│   ├── MEMORY          semantic search
│   ├── BRAIN CONTROLS  start/pause/run-now/interval/consult/vault reindex
│   ├── ALERTS
│   ├── TODAY           summary + link (never the report itself)
│   └── BLIND SPOTS
├── RESEARCH
├── MARKET         + REGIME·WHY + LIVE WORLD FEED
├── PORTFOLIO
├── STRATEGIES
├── DAILY REPORT   today · history · NOT GENERATED
├── TRACK RECORD
└── SYSTEM
```

---

## K. Tests

| | Before | After |
|---|---|---|
| passed | 1,236 | **1,383** |
| failed | **1** | **0** |
| xfailed | **1** | **0** |

**+147 tests.** The pre-existing failure (`test_the_five_freshness_states_are_distinguishable`)
was a time-fragile literal — `2026-08-21` was inside the 5-day window when
written and outside it six days later. Now computed relative to today.

**The strict xfail flipped to a real PASS** — that flip is the proof the 100×
defect is fixed rather than hidden.

New suites: `test_identity_namespaces.py` (32) · `test_live_news.py` (40) ·
`test_daily_report.py` (21) · `test_regime.py` (15) · `test_migration_freeze.py` (16) ·
`test_ui_architecture.py` (17).

One tripwire fired and was updated **deliberately**, as it instructed: the test
asserting the BSE collisions were unresolvable. The ISIN evidence resolved them.

---

## L. Worker health

```
ok 10 · late 0 · stalled 0 · failing 0 · healthy: true
desk · brain · fx_monitor · research_eye · world_model
daily_report · ledger · macro · outcome_resolver · quant_lab
```

`daily_report` is newly registered. The brain reports `THINKING`.

---

## M. Remaining limitations

1. **BLOCKED — authenticated visual pass.** The app is gated by
   `ARIA_OWNER_TOKEN`. I will not type a credential into a login field, so the
   signed-in UI was verified structurally (17 tests reading the real source),
   through the live API (every endpoint 200 with real content), and by
   confirming every new module loads in the browser without error — but not by
   looking at it. **Sign in at `http://localhost:3000/app/` to see it.**
   Screenshots were additionally unavailable: the browser pane does not
   composite frames in this environment.

2. **BLOCKED — `UNIQUE(provider, provider_symbol)`.** Not added, and it should
   not be: six legitimate aliases share a provider symbol and the index would
   delete one row of each pair. The invariant it was meant to protect
   (*one provider symbol, one security*) now holds and is enforced at the
   application layer. Reported as two separate flags so this stays a decision.

3. **21,051 company names remain unverified** against an authoritative security
   master. Four known-wrong names are corrected at read time.

4. **`EDV.L` is stored as `fixed_income`** — contamination from the Vanguard ETF
   sharing the ticker. Not repaired: `.L` does not determine asset class (London
   lists equities, ETFs and trusts alike), and guessing would be the same
   mistake being fixed.

5. **The observation store is thin** — 110 items over a week, dominated by
   aggregators (72) and social (37), with 1 primary filing. The ladder makes
   that visible rather than flattering it.

6. **Calibration cannot yet distinguish skill from chance** — 0.4971 over 171
   events, 95% CI [0.423, 0.571]. Reported honestly in the daily report's blind
   spots.

---

## N. Exact commands used

```bash
venv/Scripts/python.exe scripts/repair_universe_identity.py --apply
venv/Scripts/python.exe scripts/stamp_signal_identity.py --apply
venv/Scripts/python.exe scripts/migrate_identity.py --dry-run
venv/Scripts/python.exe scripts/migrate_identity.py --apply
venv/Scripts/python.exe -m pytest tests/ -q -p no:randomly
npm run build --prefix frontend
venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
npm run dev --prefix frontend
```

Backups written to `data/backups/` — `universe-repair-20260829-130952`,
`signals-identity-20260829-131154`, `identity-migration-20260829-131819`, each
with its plan and a restore command.

---

## O. URLs

```
http://localhost:3000/app/               the app  (vite dev, HMR)
http://localhost:8000/app/               the app  (backend-served build)
http://localhost:8000/docs               API reference
```

**The app is under `/app/`** — `vite.config.js` sets `base: '/app/'` because the
backend mounts the SPA there. `http://localhost:3000/brain` 404s;
`http://localhost:3000/app/brain` works.

---

## P. New backend surface

```
GET  /api/brain              GET  /api/regime
GET  /api/brain/activity     GET  /api/news/live
GET  /api/brain/memory       GET  /api/news/sources
GET  /api/world              GET  /api/daily-report
                             GET  /api/daily-report/history
                             POST /api/daily-report/generate
```

All additive. `/api/universe/currencies` gained a `namespace` parameter
(`signals` default, `master` explicit). Every endpoint that existed before still
answers exactly as it did.

---

## Q. Completion standard

| Requirement | State |
|---|---|
| Live Mind gone as a separate intelligence | **yes** — enforced by test |
| Brain contains chat | **yes** |
| Brain contains memory | **yes** — searchable |
| Brain contains live activity | **yes** — activity, not chain-of-thought |
| Daily Report is its own destination | **yes** |
| Reports never overwrite | **yes** — write-once + archive |
| Live news separate from daily report | **yes** — one store, two windows |
| Goldilocks not hardcoded | **yes** — every quadrant reachable |
| Identity cannot select the wrong venue | **yes** — namespaced |
| GBp 100× impossible | **yes** — xfail flipped to PASS |
| Tests | **1,383 pass, 0 fail** |
| Workers | **10/10 healthy** |
| Frontend runnable | **yes** — builds clean, serving |

**ONE INSTRUMENT → ONE CANONICAL PROVIDER IDENTITY → ONE CORRECT PRICE UNIT →
ONE REPRODUCIBLE HISTORICAL RECORD → ONE INTELLIGENCE.**
