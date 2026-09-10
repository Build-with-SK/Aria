# PHASE — UNIVERSE IDENTITY AUDIT

**Date:** 2026-08-25
**Classification:** Instrument Identity Incident
**Mode:** AUDIT ONLY — no production mutation, no regrading, no schema change
**Status:** COMPLETE. Blocking decisions listed at the end.

Every claim below is labelled:

- **[FACT]** — verified by running code against real repository state, reproducible
- **[INFERENCE]** — a conclusion drawn from facts, stated as such
- **[RECOMMENDATION]** — a proposal, not a decision
- **[UNKNOWN]** — not established; do not act on it

---

## 1. Executive summary

**[FACT]** ARIA contains **two independent instrument universes** that both use a bare
ticker as canonical identity, and they assign different real-world securities to the
same ticker for 10 symbols.

```
configs/universe.yaml    770 tickers, 0 non-US venue suffixes   → BA means Boeing
src/data/universe.py     LSE_SYMBOLS seed → BA.L, BAE Systems   → BA means BAE Systems
```

**[FACT]** Both write to `universe.db.symbols`, keyed `symbol TEXT PRIMARY KEY`.
`_load_yaml_universe()` runs **last** in `refresh_index()` and upserts with
`ON CONFLICT(symbol) DO UPDATE SET name=excluded.name` — **name only**. For a ticker
the LSE seed already claimed, it overwrites the name and leaves `yahoo`, `exchange`
and `currency` London. The result is a row that is half one instrument and half
another.

**[FACT]** This is one line, `src/data/universe.py:566-567`. It is the entire root cause.

**[FACT]** Consequence chain, proven on live state:

```
signals.json["BA"].current_price = 214.20      (Boeing, USD — from the yaml universe)
native_currency("BA")            = "GBp"       (from the LSE row in universe.db)
to_base(214.20, "GBp", "USD")    = 2.92        (Boeing at $2.92)
```

**[FACT]** The data defect is **LIVE**. The user-visible symptom is **DORMANT** —
the only two consumers (`Signals.jsx`, `Recommendations.jsx`) were orphaned by the
workspace consolidation. That was incidental, not a repair.

**[FACT]** Historical predictions are **unaffected**: 16/16 VALID, 0 requiring regrading.

**[FACT]** Portfolio impact is **zero** — no open positions, and no per-symbol
conversion in sizing.

**[INFERENCE]** The intended universe is **not ambiguous**. The architecture answers
the question that looked like it needed an owner decision — see §12.

---

## 2. Root cause

**[FACT]** `refresh_index()` load order (`src/data/universe.py:138-145`):

| # | Loader | Writes on conflict |
|---|---|---|
| 1 | `_load_nse_equities` | `name`, `isin` only |
| 2 | `_load_bse_equities` | — |
| 3 | `_load_us_full` | full row |
| 4 | **`_load_lse_equities`** | **full row** — takes `yahoo`, `exchange`, `currency` |
| 5 | `_load_world_symbols` | full row |
| 6 | `_load_fx_pairs` | full row |
| 7 | **`_load_yaml_universe`** | **`name` only** |

**[FACT]** Step 4 sets `BA → ('BA.L', 'BAE Systems', 'LSE', 'GBP')`.
Step 7 sets `name = 'Boeing'` and nothing else.
Observed row today: `('BA', 'BA.L', 'Boeing', 'LSE', 'GBP')`. Reproduced exactly.

**[FACT]** The `LSE_SYMBOLS` seed list is **correct**: `("BA","BAE Systems")`,
`("AAL","Anglo American")`, `("JD","JD Sports Fashion")`, `("PRU","Prudential")`.
The names in the database were not seeded wrong; they were overwritten.

**[INFERENCE]** Step 7's intent was to INSERT a US row (`yahoo=ticker`,
`exchange='US'`). Blocked by the PK, it silently degraded to a partial update. A
half-write is worse than a refusal, and the schema gave it no way to refuse.

---

## 3. All identity sources

**[FACT]** Every place that creates or updates instrument identity:

| Source | Input ID | Normalisation | Resolution | Storage | Consumers |
|---|---|---|---|---|---|
| `configs/universe.yaml` | bare ticker | none | none — bare assumed US | `symbols` (name-only on conflict), `signals.json` keys | signal engine, alerts, ML, desk analysts |
| `universe.py::_load_lse_equities` | bare ticker | `f"{s}.L"` | none — assumed | `symbols` full row | search, `native_currency` |
| `universe.py::_load_nse/_bse` | NSE/BSE CSV | `.NS` / `.BO` | authoritative CSV | `symbols` | search |
| `universe.py::_load_us_full` | US listing file | none | none | `symbols` | search |
| `universe.py::resolve()` | free text | candidate suffixes | **live provider probe** | `symbols` upsert | Research, technical tracker |
| `technical_summary::_resolve_yahoo` | bare ticker | — | `universe.resolve()` | `recommendations_log.jsonl` (bare key) | ledger, technical page |
| `src/core/identity.py` | bare/provider | uppercase | `symbols` lookup | none (read-only) | ledger grading |
| `src/data/currency.py` | bare/provider | suffix → ccy | suffix, then `symbols` | memo cache | conversion, UI |
| `live_quote.py` | bare | `universe` → `yahoo` | provider | `quotes` (bare key) | quote endpoint |
| ledger | bare `subject` | uppercase | `identity.describe()` at grade time | `predictions.subject` | calibration, attribution |
| event bus | bare `subject` | uppercase | none | `events.subject` | activity stream |

---

## 4. Identity flow map — where the two universes diverge

```
                 configs/universe.yaml                 universe.py LSE_SYMBOLS
                 (770 tickers, US-only)                (FTSE 100/250)
                          │                                     │
                   ticker "BA"                            ("BA","BAE Systems")
                          │                                     │
              yfinance("BA") → Boeing                    yahoo = "BA.L"
                          │                                     │
                          ▼                                     ▼
        signals.json["BA"] = {214.20, "Boeing"}     symbols["BA"] = BA.L/LSE/GBP
                          │                                     │
                          │                                     ├─→ universe.search(name)
                          │                                     ├─→ native_currency → GBp
                          │                                     └─→ technical tracker → BA.L
                          │                                              │
                          │                                     ledger price_at = 2200 (pence)
                          ▼                                              ▼
              ┌───────────────────────────────────────────────────────────┐
              │  COLLISION: one key, two instruments, two price scales     │
              │  native_currency(signals price) → 100x error               │
              └───────────────────────────────────────────────────────────┘
```

**[FACT]** The ledger and technical tracker sit on the **London** side (consistent).
The signal engine sits on the **US** side (consistent). Only code that crosses the
two — `native_currency` applied to a `signals.json` price — is wrong.

---

## 5. Affected instruments

**[FACT]** 11 non-US instruments ARIA acts on. All 10 equities are price-unit
affected; 4 are also name-wrong; 1 is name-suspect.

| SYM | YAHOO | PROVIDER NAME | UNIVERSE NAME | SIG PX | NATIVE | NAME | PREDS |
|---|---|---|---|---|---|---|---|
| AAL | AAL.L | Anglo American plc | American Airlines | 13.82 | GBp | **WRONG** | 1 |
| BA | BA.L | BAE Systems plc | Boeing | 214.20 | GBp | **WRONG** | 6 |
| JD | JD.L | JD Sports Fashion | JD.com | 29.37 | GBp | **WRONG** | 5 |
| EDV | EDV.L | Endeavour Mining | Vanguard Ext Duration | 59.51 | GBp | **WRONG** | 0 |
| PRU | PRU.L | Prudential plc | Prudential Financial | 121.15 | GBp | **SUSPECT** | 0 |
| AZN | AZN.L | AstraZeneca PLC | AstraZeneca | 165.98 | GBp | ok | 0 |
| BP | BP.L | BP p.l.c. | BP | 44.76 | GBp | ok | 0 |
| GSK | GSK.L | GSK plc | GSK | 52.41 | GBp | ok | 0 |
| RIO | RIO.L | Rio Tinto Group | Rio Tinto | 105.30 | GBp | ok | 0 |
| SHEL | SHEL.L | Shell plc | Shell | 93.33 | GBp | ok | 4 |
| DX-Y.NYB | DX-Y.NYB | US Dollar Index | US Dollar Index | 98.80 | None | ok | 0 |

**[INFERENCE]** The five "ok" names are dual-listings — same company, different
listing. `signals.json` holds the **US ADR** price under a key whose currency
resolves to GBp, so they carry the same 100× exposure despite a correct-looking name.

---

## 6. Affected historical records

**[FACT]** 16 of 279 predictions (5.7%): AAL 1, BA 6, JD 5, SHEL 4.

| Classification | Count | Evidence |
|---|---|---|
| **VALID** | **16** | Reference price matches the London listing |
| IDENTITY_AMBIGUOUS | 0 | — |
| INVALID | 0 | — |
| REQUIRES_REGRADING | 0 | — |

**[FACT]** Evidence per row: every affected prediction came from a subsystem that
resolves through `universe.resolve()` → provider symbol, so its reference price is
the London one. Six were re-verified against the provider at **drift 0.000**. The
one row that failed a scale heuristic (AAL @ 3966) was checked directly:
AAL.L closed **4067** on 2026-08-22 — 2.55% drift; the bare `AAL` closed 13.82,
99.65% drift. London confirmed.

**[FACT]** `predictions` has no `provider_symbol` column. Identity is re-derived at
grade time from `subject`, so historical rows depend on `symbols` continuing to map
`BA → BA.L`. **[INFERENCE]** A migration that re-points `BA` to Boeing would silently
change what 16 historical predictions refer to. This is the principal migration
hazard and is addressed in §10.

---

## 7. Portfolio impact

**[FACT]** Zero. `desk/positions.json` is empty; `ACCOUNT_CURRENCY = "USD"`;
`capital.py::convert_to_account` converts the capital base, not per-symbol prices.
No portfolio record references an affected instrument.

---

## 8. Price / unit impact

**[FACT]** The pence model itself is **correct and load-bearing**:
`to_base(100,'GBp','GBP') == 1.00`; `convert(1,'GBP','GBp') == 100`;
`SUFFIX_CCY['.L'] == 'GBp'`; the frontend divides by 100.

**[FACT]** 218 LSE rows store `GBP` while the venue quotes `GBp`. Compensated at
`currency.py:105`. Not the defect.

**[FACT]** The defect is that a **US price** is paired with a **London currency**,
because the price and the currency come from different universes.

**[FACT]** Naked prices crossing boundaries without identity context:
`signals.json` values, `recommendations_log.jsonl.entry_price`,
`predictions.price_at`, `quotes.price`. **[RECOMMENDATION]** The minimum boundary
where identity metadata must become mandatory is **the point a price is persisted** —
each of those four stores should carry `provider_symbol` + `currency` + `price_unit`
alongside the number. Nothing further inland needs changing.

---

## 9. Search impact

**[FACT]** `universe.search` matches `UPPER(name) LIKE ?` (`universe.py:605`).
Live results:

```
search "Boeing"            → BA   → BA.L  (LSE)   ← BAE Systems, labelled Boeing
search "American Airlines" → AAL  → AAL.L (LSE)   ← Anglo American
search "JD.com"            → JD   → JD.L  (LSE)   ← JD Sports
```

**[FACT]** There is no correct US Boeing or American Airlines listing in the symbol
master at all — only `BA$A`, a preferred share.

**[INFERENCE]** Names therefore do cause damage: not arithmetic corruption, but
instrument misselection. `/research?symbol=BA` renders BAE Systems' dossier under
the heading "Boeing".

---

## 10. Proposed canonical identity model

**[FACT]** Test of `yahoo` as a canonical key over all 21,067 rows:

- duplicate `yahoo` values: **5**
  - 3 are display aliases for one instrument (`BRK-B`/`BRK.B`, `GBPUSD=X`/`GBPUSD`, `USDINR=X`/`USDINR`)
  - 2 are genuine BSE data errors (`531257.BO`, `539455.BO` each mapped to two companies)
- `yahoo` encodes venue for **every** asset class ARIA holds:
  `.NS`/`.BO`/`.L` (non-US equity), bare (US, provider-consolidated), `BTC-USD`
  (crypto), `ES=F` (futures), `EURUSD=X` (FX), `^GSPC` (index)
- within 13,096 US rows, exactly **1** duplicate bare `yahoo` (an alias)

**[INFERENCE]** `provider + provider_symbol` **is sufficient for every asset class
ARIA supports**. The provider already namespaces venue into the symbol. No
equity-shaped schema (ISIN, MIC, country) is needed.

**[RECOMMENDATION]** Minimum identity tuple:

```
instrument_id   = (provider, provider_symbol)     canonical, unique
display_symbol  = presentation + user input       MAY collide
display_name    = presentation + search only      NEVER determines identity
currency        = settlement currency  (GBP)
price_unit      = quoting unit         (GBp)
unit_multiplier = 0.01
```

`exchange`, `asset_class`, `isin` stay as **attributes**, not identity.

---

## 11. Alternatives considered, and why rejected

| Alternative | Why rejected |
|---|---|
| `UNIQUE(symbol)` kept as identity | **[FACT]** This is the defect. A bare ticker denotes ≥2 instruments for 10 symbols and 93 roots. |
| Full security master (ISIN/MIC/CFI) | **[INFERENCE]** ISIN is present on 0 rows outside NSE; MIC nowhere. Would require a paid vendor for 21k rows to fix 10. Fails "minimum complexity". |
| `UNIQUE(exchange, symbol)` | **[INFERENCE]** `exchange` is ARIA's own label, not a standard, and is wrong on exactly the rows in question. Encodes the bug into the key. |
| Rename all LSE rows to `.L` display symbols only | **[INFERENCE]** Fixes the 10 collisions but leaves `symbol` as identity, so the next seeder collision recurs. Necessary but insufficient. |
| Blanket `GBp → GBP` | **[FACT]** Would break a correct pence model and introduce a real 100× error where none exists today. |
| Delete the LSE rows | **[FACT]** Research advertises "Any listing — India · UK · US". Removes wanted capability. |

---

## 12. The intended universe — resolved from architecture

The instruction was to determine whether ARIA's own architecture settles this
before asking. **It does.**

**[FACT]**
- `configs/universe.yaml` contains **770 tickers with zero non-US venue suffixes**;
  the equities block is headed *"US Mega / Large Cap Equities (S&P 500 core)"*.
  The only dotted entry is `DX-Y.NYB` (US Dollar Index).
- `universe.py`'s docstring defines the symbol master as a **search index** —
  *"every listed asset ARIA knows about (all NSE equities + the global
  configs/universe.yaml universe)"* — Tier 0 of a three-tier LOD design.
- The Research workspace advertises *"Any listing — India (₹ NSE/BSE) · UK (£ LSE)
  · US ($)"*.

**[INFERENCE]** The signal universe owns the **bare** ticker namespace and is
US-listed by construction. The symbol master is a **superset search index** that
must not compete for those keys. `BA` should therefore mean **Boeing**, and the
London listing should be addressed as `BA.L`.

**This does not require an owner decision.** The one decision that genuinely
remains is in §17.

---

## 13. Migration design *(not executed)*

**[RECOMMENDATION]**

**Before**
```sql
symbols(symbol TEXT PRIMARY KEY, yahoo TEXT NOT NULL, name, exchange,
        asset_class, isin, sector, industry, mcap, updated_at,
        has_fno, lot_size, currency)
```

**After** (additive; PK unchanged)
```sql
symbols(... existing ...,
        provider        TEXT NOT NULL DEFAULT 'yahoo',
        price_unit      TEXT,          -- 'GBp' where the venue quotes minor units
        unit_multiplier REAL,          -- 0.01 for GBp, else 1.0
        name_status     TEXT,          -- VERIFIED | CONFLICT | UNKNOWN | STALE
        name_checked_at TEXT)
CREATE UNIQUE INDEX ux_symbols_instrument ON symbols(provider, yahoo);
```

**Order**
1. Add columns + backfill `provider='yahoo'`, `unit_multiplier=1.0` (no behaviour change).
2. Add `predictions.provider_symbol`; backfill the 279 rows from the resolution
   that was true at creation (verified in §6). **Historical `subject` untouched.**
3. Re-key the 10 colliding LSE rows: `symbol` `BA` → `BA.L`, etc.
4. Insert the US rows the yaml loader was blocked from creating.
5. Fix `_load_yaml_universe` to upsert the full row, and `_load_lse_equities` to
   seed `.L` display symbols.
6. Set `price_unit='GBp'`, `unit_multiplier=0.01` on the 218 LSE rows.
7. Add `UNIQUE(provider, yahoo)` **after** resolving the 2 BSE duplicates.

**Backup:** copy `universe.db` and `aria_core.db` to
`data/backups/pre-identity-migration-<ts>/` before step 1; the migration refuses
to run if the copy fails.
**Idempotent:** every step is `INSERT OR IGNORE` / conditional `UPDATE ... WHERE`.
**Reversible:** steps 1–2 additive; step 3 reversed from the backup or a recorded
`(old_symbol, new_symbol)` map written to `data/backups/.../rekey_map.json`.

---

## 14. Dry-run design *(not executed)*

**[RECOMMENDATION]** `scripts/migrate_identity.py --dry-run` — **no writes** —
emitting:

- proposed `(old_symbol → new_symbol)` for every re-key
- identity conflicts after the proposed change (must be 0)
- name conflicts: stored vs provider, with `name_status`
- currency/unit changes, count and sample
- prediction rows whose `subject` resolution would change (expected: 16)
- portfolio/event rows referencing affected symbols (expected: 0 / n)
- unresolved records requiring a human

Reviewed before any write. **[RECOMMENDATION]** Run it twice and diff — a
non-deterministic dry run means the migration is not idempotent.

---

## 15. Invariants to prove after migration

**[RECOMMENDATION]** All 15 requested, expressed as executable assertions:

1. `SELECT provider, yahoo, COUNT(*) ... HAVING COUNT(*)>1` → 0 rows
2. `UNIQUE(provider, yahoo)` exists and is enforced
3. display symbols may collide — asserted by a fixture, not forbidden
4. no code path derives identity from `name` (static check on `identity.py`)
5. `search("Boeing")` returns candidates, never a silent single wrong hit
6. every persisted price carries `provider_symbol`
7. every persisted price carries `currency` + `price_unit` where applicable
8. `to_base(100,'GBp','GBP') == 1.00` (unchanged)
9. all 279 predictions resolve to the same instrument as before migration
10. portfolio references resolve (vacuous today — 0 positions)
11. ledger references resolve
12. event references resolve
13. backtests resolve the same instruments intentionally
14. live quotes resolve the intended provider symbol
15. no historical row rewritten except the additive `provider_symbol` backfill

---

## 16. Tests

**[FACT]** Added this phase, all passing (1,208 total, 1 xfail, 1 skipped):

- `tests/test_price_unit_audit.py` — 6 pass, 1 xfail(strict), 1 skip
  - pence arithmetic correct; `.L` → GBp; US → USD; unknown → no conversion
  - **xfail(strict)** pins the live 100× defect; its flip to PASS is the fix proof
  - asserts the two universes currently disagree, so a one-sided edit is caught
  - asserts the UI dormancy is incidental — fails if either page is re-routed
  - ledger London-scale check (skips under isolation; verified directly instead)

**[RECOMMENDATION]** Required before migration: dry-run determinism test; a
re-key round-trip test; a test that a blocked upsert **refuses** rather than
half-writes.

---

## 17. Blocking decisions

**[FACT]** Only one genuine decision remains. Everything else is settled by evidence.

> **How should the 16 historical London predictions be addressed after `BA`,
> `AAL`, `JD` and `SHEL` come to mean the US instruments?**
>
> **Option A (recommended):** add `predictions.provider_symbol`, backfill the 16
> with the verified London symbols, leave `subject` immutable. History keeps its
> meaning; future resolution uses the explicit column. Additive, reversible.
>
> **Option B:** rewrite `subject` to `BA.L` etc. Cleaner forward, but mutates
> historical records — which this phase has been forbidden from doing, and which
> would break the audit's reproducibility.

**[UNKNOWN]** Whether you want LSE coverage to remain in the symbol master at all.
Architecture says yes (Research advertises it); I have not asked.

---

## 18. Remaining unknowns

- **[UNKNOWN]** 21,056 names unverified. **[RECOMMENDATION]** batch-verify against
  the provider at ~2 req/s over ~3 hours, writing `name_status` ∈
  `VERIFIED | CONFLICT | UNKNOWN | STALE` and `name_checked_at`. **Never overwrite
  on CONFLICT** — quarantine and report. Cost: one long background job, no vendor.
- **[FACT]** 2,627 rows lack currency. Classified: 2,395 derivable from venue,
  101 crypto (quote ccy in ticker), 59 FX, 52 futures (USD by contract),
  20 not applicable (index levels). **[INFERENCE]** All derivable or N/A — **0
  genuinely ambiguous**. No provider call needed.
- **[FACT]** 2 BSE rows map two companies to one provider symbol
  (`531257.BO`, `539455.BO`). Must be resolved before `UNIQUE(provider, yahoo)`.
- **[UNKNOWN]** Whether any `signals.json` snapshot was rendered through the buggy
  display path before the pages were orphaned. No render log exists.

---

## 19. Vision audit — smallest permanent fix

**Question:** what is the smallest architectural change that permanently eliminates
this class of bug?

**[INFERENCE]** Two changes, both small:

1. **`UNIQUE(provider, provider_symbol)`** — makes identity collision structurally
   impossible rather than a convention. One index.
2. **Make the blocked upsert refuse instead of half-writing.** The corruption was
   not the collision; it was `DO UPDATE SET name=...` writing half an identity when
   it could not write a whole one. One clause.

**[INFERENCE]** Everything else — the re-key, the `price_unit` columns, the name
verification — is cleanup of damage already done. Only these two prevent recurrence.
A full security master would be a large system built to fix ten rows.

---

## 20. Recommended next action

**[RECOMMENDATION]**

1. Decide §17 (Option A recommended).
2. Build `scripts/migrate_identity.py --dry-run`; review output.
3. Only then migrate, in the §13 order, with the §15 invariants asserted.

**No production mutation has occurred in this phase.** `universe.db` remains
21,067 rows with `BA = ('BA','BA.L','Boeing','GBP')`. The ledger remains 279
predictions, none regraded. The audit is reproducible.
