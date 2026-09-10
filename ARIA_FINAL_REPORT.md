# ARIA — FINAL REPORT

**Date:** 2026-09-06
**Tests:** 1,556 passed · 0 failed · 0 skipped · 0 xfail
**Live checks:** 19/19 against the running system
**Workers:** 10/10 ok
**Status:** **READY TO SHIP** for V1 — with two limitations stated in §21, neither of which blocks.

---


> **ADDENDUM — 2026-09-06, after the signal refresh.** Running the pipeline
> surfaced two further defects, both now fixed:
>
> * **The ML stage never persisted its output.** A full run trains ~1,500 models
>   over roughly six hours, logs every prediction, and dropped all of it:
>   `run_ml()` returned into a variable `main()` never wrote. The only writer of
>   `data/ml_predictions.json` was `main_phase4_backup.py`, which nothing runs —
>   so the live file had not moved since 2026-05-31 while `/api/ml`, the chat
>   context, the summary endpoint and the brain's perception layer all read it
>   as current. `main.py` now serialises to the same contract those five readers
>   expect, and refuses to overwrite a good file with an empty run.
> * **The world model did not age ML predictions.** It tracked six artefacts and
>   this was not one of them, so a 98-day-old file could never be reported as
>   old. `ml` now has a staleness budget and appears in blind spots — ARIA
>   currently volunteers *"My ML predictions are stale — 98.7 days old"* rather
>   than serving them as a current view.
> * **Log encoding on Windows.** Every `✗` in a pipeline warning raised
>   `UnicodeEncodeError` inside the logging handler, replacing the message with
>   a five-line traceback. It never broke a run, which is why it survived.
>
> Tests: 1,503 → **1,556**. Signals refreshed: 724 tickers, zero missing
> provider identity — the first real pipeline run since identity moved to the
> write path, so the guarantee now holds by construction rather than by
> migration.

---

## 1. Executive summary

This pass was an independent audit of a system that had already been declared finished. It was not: six real defects were found, and four of them were in work done during the previous pass. All are fixed.

The most consequential is worth stating first. The identity migration was a **one-off backfill** — it repaired history and stopped. Nothing taught the *write path* to stamp identity, so every prediction written afterwards had none, and **88 accumulated in a single week**. Worse, the identity columns lived only in the migration's `ALTER TABLE`, never in `CREATE TABLE`, so a fresh clone of this repository would have produced a ledger that could never carry identity at all. A guarantee that has to be re-established by hand is not a guarantee; it is a chore that gets forgotten. Identity is now stamped at write time and present in the base schema.

The second is the SENTINEL bridge. It had a client, a token, and a chosen port — and no caller. It is now wired: gated by a deterministic `should_consult`, advisory-only, off by default on the trading path, and exercised end to end against a stub peer.

Nothing was deleted to make a number look better. No test was weakened. Four tests failed during this pass **because defects were fixed** — they had encoded the broken state as the expected one — and each was rewritten to assert the outcome rather than the symptom.

---

## 2. Final architecture

```
ARIA (this repository)                          SENTINEL (separate process)
├── identity / market data                             ▲
│   src/core/identity.py, data/currency.py             │ one authenticated
│   data/universe.py — 22,022 symbols, 47 venues       │ HTTP call, timeout,
├── world model — src/core/world.py                    │ fails soft
├── macro regime — src/macro/regime.py                 │
├── research — eye.py · quality.py · live_news.py      │
├── cognition — src/brain/ + aggregate.py ─────────────┘
├── ledger / attribution / calibration — src/core/
├── portfolio & strategies — src/desk/, execution/, v5/
├── daily reports — src/report/daily.py
├── workers — src/core/workers.py (10 registered)
└── UI — frontend/src/  (8 destinations, one front door)
```

Eight destinations: **Brain · Research · Market · Portfolio · Strategies · Daily Report · Track Record · System.** `/` and `/brain` render the same component.

---

## 3. What existed before this pass

A working system with 1,419 passing tests, the GBp identity defect closed, Live Mind consolidated into Brain, a dated daily-report store, a live news feed with an evidence ladder, and a regime classifier that reports input coverage rather than conviction. That work stands and was not rewritten.

---

## 4. What changed

| Area | Change |
|---|---|
| Ledger | Identity stamped **at write time**; identity columns added to `CREATE TABLE` |
| SENTINEL | `src/consult/bridge.py` added; wired into the planner and four API endpoints |
| Event bus | Three consultation event kinds registered (they were being silently dropped) |
| Security | `pull_model` validates its argument; 51 traversal/injection tests added |
| Universe | LSE loader's `ON CONFLICT` now refreshes `asset_class`; post-refresh invariant check |
| Docs | README rewritten against reality; `test_docs_match_code.py` keeps it honest |
| Cleanup | 14 dead hooks removed, 13 BOMs stripped, one stale spec marked superseded |

---

## 5. Major bugs found

1. **Identity decay** — the write path never stamped identity; 88 predictions in a week had none. *High. Affects V1.*
2. **Fresh installs could never carry identity** — `provider_symbol` existed only in the migration's `ALTER TABLE`, not in `CREATE TABLE`. *High. Affects V1.*
3. **Consultation events silently dropped** — the bus rejects unknown kinds, so every SENTINEL event vanished. A consultation nobody can see is unauditable. *Medium.*
4. **`repair_universe_identity.py` docstring was false** — claimed to write through `assert_provider_symbol_safe()`, which it never called. *Low, but a lie in the code.*
5. **A silent `hasattr` guard** — the daily report called a `calibration.report()` that has never existed, so its calibration section was permanently empty while looking populated. *Medium.*
6. **Argument injection in `pull_model`** — no shell, so never command injection, but an unvalidated argv element means `--help` is read as a flag. *Low, owner-only.*

---

## 6. Bugs fixed

All six above, plus:

- **`EDV.L` filed as `fixed_income`** — a gold miner, because the LSE loader declared `asset_class='equity'` on INSERT and omitted it from `ON CONFLICT`, leaving a US bond ETF's classification in place. Same half-written-identity defect as `BA`, one column over. Fixed at the loader.
- **Thirteen `__init__.py` files carried a UTF-8 BOM** — Python imports them fine; `ast.parse` does not.
- **A syntax error I introduced in `planner.py`** mid-pass, caught by the very test that walks `src/` — which is the argument for that test existing.

---

## 7. Security fixes

The vault traversal concern was **investigated and is not a vulnerability**. `read_note` canonicalises with `.resolve()` and checks containment with `is_relative_to`, not a prefix — which matters here specifically, because a `DigitalBrain_Backup_…` directory already sits next to the vault and would satisfy a `startswith` test. **32 attacks** were fired at it (`../`, absolute paths, Windows paths, encoded traversal, the prefix-sibling case, nulls, 400-character names, directories): all refused, none raised.

Fixed: `pull_model` now validates its argument. Verified clean: no `shell=True` anywhere in `src/` or `backend/`; no `pickle`/`yaml.load`; CORS is an explicit four-origin allowlist, not `*`; SSRF surface is a fixed Ollama base only; authorization is deny-by-default.

One thing worth naming because it looks wrong and is not: `/api/auth/` is anon-reachable. It has to be — you cannot put the door behind the lock. The sensitive routes *inside* it (`/api/auth/users`, and the DELETE) carry `require_owner` at route level, and a test now asserts exactly that split.

---

## 8. Data / identity fixes

- Identity stamped at write time (`UNAMBIGUOUS` / `MASTER_RESOLVED`), **failing open** — an unidentifiable subject still records the claim, because losing a falsifiable statement is worse than storing it with a null identity the migration can resolve later on price evidence.
- 87 accumulated rows backfilled; invariants held (counts and subject/price/outcome digests unchanged).
- **426 of 427** predictions now carry identity. The one exception is an MRK row whose recorded price matches neither candidate on its date — a refusal, not a gap. That is the system working.
- Four more ISIN-identical BSE renames appeared in a real refresh and were absorbed with **no code change** — the argument for structural classification over a hardcoded list.

---

## 9. Brain / world model

One `/api/brain` aggregate: status, regime, cognition, memory, knowledge, world, capability. Verified live — `status=THINKING`, regime *Expansion (Goldilocks)* at 100% input coverage, VIX 14.53, 1,643 memories, local model up.

The activity boundary holds: `/api/brain/activity` returns activity summaries and conclusions, and a live check asserts no `thought` field ever appears in the response.

The world model surfaces staleness unprompted. Live, right now, it volunteers: *"The signal pipeline is stale — signals.json is 8.0 days old"*, and five more blind spots including its own inability to distinguish its record from chance.

---

## 10. Research

The eye writes observations; `live_news.py` reads them with a cursor. Live: 112 raw → 40 shown, 3 deduplicated, 69 held back. Every row carries evidence state and source tier as separate axes. Source mix over two weeks: 165 aggregator, 63 anonymous, and ARIA says so rather than flattering it.

---

## 11. Market

Regime with evidence, coverage and invalidation conditions. Signals carry their own provider symbol and quote unit — verified live: `BA provider=BA unit=USD price=214.20`. The 100× defect stays closed: `BA→USD`, `BA.L→GBp` through the live API.

---

## 12. Portfolio

Holdings and the approval queue both answer (0 pending). Execution keys on ticker and prices through the provider symbol; a test asserts company names never reach that layer, which is what makes ~22,000 unverified names a display concern rather than an execution defect.

---

## 13. Strategies

Quant lab and evolution untouched this pass; the worker runs hourly and recovered to `ok` during verification.

---

## 14. Track record

Calibration counts **distinct events**, keeps traded and non-traded populations apart, and puts a Wilson interval on every rate. Live: *201 independent resolved events, 52.2% correct (95% CI 45.4%–59.0%) — indistinguishable from chance.* That sentence is generated, not written, and now appears in the daily report where it previously rendered an empty section.

---

## 15. Learning / adaptation

Verified end to end on an isolated ledger: prediction created → identity stamped at write → event published → horizon elapsed → resolution attempted.

The resolution **failed closed**, which is the interesting part. The test prediction recorded AAPL at 200 when it actually trades near 320, and the grader refused: `GRADING_SKIPPED / IDENTITY_UNRESOLVED — the price source returns 320 for AAPL but the prediction…`. It would not force a win or a loss onto a claim whose reference price does not match reality, and calibration counted zero events rather than one bad one.

---

## 16. Workers

Ten registered, all `ok`. Failure **and recovery** were both observed live: two workers were `failing`/`stalled` from a transient Ollama outage the previous evening; after a real brain cycle completed they returned to `ok` unaided, in about three minutes. Five tests now pin that `failing` is derived from `last_error_at > last_ok` and is therefore a comparison, not a latch — a health surface that can go red but not green again is worse than none, because it looks like monitoring.

The `daily_report` worker added last pass has produced a report every day since: **8 stored**, including 2026-09-05, 09-02, 09-01, 08-31, 08-30.

---

## 17. SENTINEL bridge

**Wired.** ARIA and SENTINEL remain two systems: ARIA imports no SENTINEL code and reads no SENTINEL state — a test walks every module in `src/` to assert it.

- `should_consult()` gates on conflicting evidence, anomaly, outside-domain, unfamiliarity, or a high-impact call held under 0.75 confidence.
- **Silence is not agreement.** Unreachable returns `ok: False` with explicit guidance, and the human approving a trade sees `[No independent review: SENTINEL was unreachable. This is not agreement.]` attached to the thesis.
- **Advisory only.** A test asserts the consultation block contains no `continue`/`return`/`raise` — SENTINEL cannot veto a trade.
- Off by default (`ARIA_SENTINEL_CONSULT`); a cheap liveness check avoids paying the full timeout to a dead peer.

Exercised end to end against a stub peer on port 8300: consultation returned, objections preserved into the summary, outcome recorded, both events on the bus. **No SENTINEL runs on this machine**, so in normal operation every consultation returns `unreachable` — which is the path most carefully tested.

---

## 18. UI

Eight destinations, one front door. Live Mind does not exist as a page, tab or label; 20 UI-architecture tests read the real source and fail if it returns. `LiveNews` now shows the source mix. Frontend builds clean.

---

## 19. Tests

| | Start of pass | End |
|---|---|---|
| passed | 1,419 | **1,503** |
| failed | 0 | **0** |
| skipped | 0 | **0** |
| xfail | 0 | **0** |

**+84.** New suites: `test_sentinel_bridge.py` (24), `test_path_traversal.py` (51 assertions across 20 tests), plus worker-recovery, identity-decay and docs-consistency tests.

**Four tests failed because defects were fixed** and were rewritten to assert outcomes:
`test_search_bae_systems_returns_the_london_instrument`, `test_a_known_bad_name_is_flagged_rather_than_silently_served`, `test_provider_symbol_uniqueness_holds_except_for_known_exceptions`, `test_the_migration_is_idempotent`.

---

## 20. Runtime verification

19/19 live checks against the running system: brain aggregate, activity-only cognition, memory search, regime with evidence, world model, signal identity, the 100× defect, live news, source mix, today's report, report history, missing-day behaviour, ledger, calibration, attribution, holdings, approval queue, consultation status, worker health.

---

## 21. Known limitations

1. **The signed-in UI has no automated visual pass.** Routes, component loading, API responses, the auth boundary and 20 architecture tests are all checked mechanically, and the frontend builds clean — but nobody has scripted a screenshot of the authenticated deck, because that would mean handing an owner token to an automated agent. **Does not block V1**; sign in and look.
2. **SENTINEL is not running here.** The bridge is wired and tested; no peer exists on this machine. **Does not block V1** — ARIA is designed to work without it and says so when asked.
3. **The signal pipeline is 8 days stale.** `signals.json` was last written 2026-08-29. ARIA reports this itself in its blind spots. Run `venv\Scripts\python.exe main.py` to refresh. **Does not block V1** — but the number in Market is old until you do.
4. **~22,000 company names unverified** against an authoritative security master. Confined to display and search; execution keys on ticker.
5. **No demonstrated edge.** 201 resolved events, indistinguishable from chance. Honest state, not an oversight.
6. **One MRK prediction remains unidentified** — a deliberate refusal.

---

## 22. Deferred to V2

- Automated visual regression on the authenticated UI.
- An authoritative security master to verify company names.
- `UNIQUE(provider_symbol)` remains **deliberately not added** — ten legitimate aliases share a provider symbol and the index would delete one row of each pair. The invariant is enforced at the application layer and checked after every refresh.
- Walk-forward validation across all 41 modules.
- Automatic SENTINEL consultation on by default, once a peer exists to consult.

---

## 23. Definition of done

| | |
|---|---|
| Tests pass, no skips, no xfails | ✅ 1,503 / 0 / 0 |
| Frontend builds clean | ✅ |
| Backend starts with no errors | ✅ (optional IBKR gateway absent, logged) |
| All workers healthy | ✅ 10/10, recovery observed live |
| Identity holds for new writes | ✅ stamped at write time |
| Fresh install carries identity | ✅ in `CREATE TABLE` |
| 100× defect closed | ✅ verified through the live API |
| Path traversal | ✅ 32 attacks refused |
| No shell injection | ✅ no `shell=True` anywhere |
| Authorization deny-by-default | ✅ router-wide test |
| Dead code removed | ✅ 14 hooks, 13 BOMs, 0 orphan pages |
| Docs match code | ✅ 20 enforcing tests |
| SENTINEL: wired or deleted | ✅ wired, advisory, fails soft |
| Learning loop verified | ✅ including a correct fail-closed refusal |
| Stale data surfaced honestly | ✅ unprompted, in blind spots |

**One instrument → one canonical provider identity → one correct price unit → one reproducible historical record → one intelligence.**
