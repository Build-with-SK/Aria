# ARIA IDENTITY SAFETY REPORT

**Date:** 2026-08-26
**Phase:** Option A — identity preservation + search safety
**Run order:** A → B → C (stopped, shown) → D → E → F → G → H, as specified

> **PRODUCTION DATA WAS NOT MIGRATED.**
> `predictions.provider_symbol` does not exist. Ledger: 339 predictions / 144
> graded. `BA` is still `('BA','BA.L','Boeing','LSE','GBP')`. No historical
> subject, price, outcome, decision or timestamp was altered.

---

## 1. Canonical identity model

```
instrument_id   = (provider, provider_symbol)     canonical — 99.98% unique
display_symbol  = presentation + user input       MAY collide across venues
display_name    = presentation + search only      NEVER determines identity
currency        = GBP        settlement currency
price_unit      = GBp        quoting unit
unit_multiplier = 0.01
```

**A bare ticker is a namespace label. The provider symbol is the instrument.**
Two instruments are never merged because their display tickers match.

`provider_symbol` is sufficient for every asset class ARIA holds — the provider
already namespaces venue into the symbol: `.L`/`.NS`/`.BO` (non-US equity),
bare (US, provider-consolidated), `BTC-USD`, `ES=F`, `EURUSD=X`, `^GSPC`.
No ISIN/MIC security master is required.

---

## 2. Universe refresh fix — root cause closed

**Was** (`src/data/universe.py`, `_load_yaml_universe`, runs *last*):

```sql
ON CONFLICT(symbol) DO UPDATE SET name = excluded.name
```

Blocked by the primary key on a ticker the LSE seeder had already claimed, it
overwrote the **London** instrument's name with the US company's while leaving
`yahoo='BA.L'`, `exchange='LSE'`, `currency='GBP'` — a row half one security and
half another. The corruption was never the collision; it was a **half-written
identity** where the schema gave the writer no way to refuse.

**Now:**

```sql
ON CONFLICT(symbol) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at
WHERE symbols.yahoo = excluded.yahoo      -- only when it IS the same instrument
```

Plus an explicit pre-check: when the bare ticker is held by a different provider
symbol, the row is **skipped**, recorded to `meta.yaml_symbol_collisions`, and
logged. Re-keying stays a migration, never a nightly job.

The guard does not freeze the universe — when the provider symbol agrees, the
name still refreshes. Both behaviours are tested.

---

## 3. Search fix — live bug closed

| Query | Before | After |
|---|---|---|
| `Boeing` | **BA → BA.L (BAE Systems)** | `BA$A` only — the genuine preferred share |
| `BAE Systems` | *nothing* | `BA · BA.L · LSE · GBp` |
| `Anglo American` | *nothing* | `AAL · AAL.L · LSE · GBp` |
| `JD Sports` | *nothing* | `JD · JD.L · LSE · GBp` |
| `JD.com` | JD → JD.L (JD Sports) | `9618.HK` — the real JD.com |
| `BA.L` | — | LSE instrument, first |
| `BA` | one ambiguous line | identity-labelled: provider symbol, venue, currency |

Three mechanisms, none of which mutate the symbol master:

1. **Read-time name correction.** `identity.corrected_name()` substitutes the
   provider-verified name, and the query is **re-tested against the corrected
   name**. A label ARIA knows to be false can no longer select an instrument.
2. **Identity on every result** — `provider_symbol`, `venue`, `currency`,
   `price_unit`, `name_corrected`. Two rows sharing a display ticker are
   visibly different things.
3. **Corrected names are searchable, not merely displayable.** Rows whose
   *verified* name matches are pulled in explicitly — otherwise the fix would
   simply invert the bug (`search("BAE Systems")` returning nothing).

**Ranking (§7), deterministic:** exact provider symbol → exact company name →
exact bare symbol → whole-token name → partial name → symbol substring. A name
match is never satisfied by a symbol collision. Queries of ≤3 characters are
treated as tickers, so `BA` no longer matches inside "Bank of New York Mellon".

**Absence is reported, not filled.** There is no Boeing common line in this
universe — only `BA$A`, a preferred share. Nothing was fabricated and BAE
Systems is not offered in its place.

---

## 4. Pence normalization status — VERIFIED, unchanged

Proven through the internal pipeline, not assumed:

```
to_base(100,  'GBp', 'GBP') == 1.00      100 GBp = £1.00
to_base(2100, 'GBp', 'GBP') == 21.00
convert(1.0,  'GBP', 'GBp') == 100.00
```

Every London instrument reports `stored=GBP → price_unit=GBp → multiplier=0.01`.
Three resolution paths converge on `GBp`: yahoo suffix, exchange map, and the
symbol-master compensation at `currency.py:105`.

**No `GBp → GBP` replacement was made.** The model is correct and load-bearing;
replacing it would *introduce* the 100× error it prevents.

**The genuine defect is unchanged and still pinned:** `signals.json` holds a US
price under a bare ticker whose currency resolves to London, so
`BA 214.20 → $2.92`. That is fixed by the re-key migration, not by this phase,
and is held by a `strict` xfail whose flip to PASS will be the proof.

---

## 5. Database constraint (§5) — NOT added, deliberately

`UNIQUE(provider, provider_symbol)` is the right constraint and is **not yet
safe**. Duplicates are now classified structurally:

| Class | Count | Detail |
|---|---|---|
| **Aliases** (keep) | 4 | `BRK-B`, `GBPUSD=X`, `USDINR=X`, `GBPINR=X` — one instrument, two display symbols |
| **Errors** (resolve first) | 2 | `531257.BO` → PRATIKSHA CHEMICALS *vs* VELLORA IMPACT; `539455.BO` → ARYAVAN ENTERPRISE *vs* ECOFINITY ATOMIX |

A UNIQUE index today would resolve those two by **discarding a company**.
`identity.provider_symbol_duplicates()` reports `constraint_safe: False` and the
test fails deliberately if that ever flips, so adding the constraint becomes a
decision rather than an accident.

Meanwhile the protection exists at the **application layer**:
`identity.assert_provider_symbol_safe()` refuses to point one provider symbol at
a second company, while permitting a genuine new display alias.

Classification is structural, not a hardcoded list — `GBPINR=X` appeared as a
*new* alias mid-audit (a live `resolve()` added it, also mis-tagging it
`US/equity`), and a fixed list would have failed on it.

---

## 6. Historical prediction strategy — Option A

```
subject         = what ARIA historically called it        IMMUTABLE
provider_symbol = what instrument was actually evaluated  ADDITIVE
identity_basis  = how that was established                ADDITIVE
```

Nothing else is touched. Proven by running the real `apply()` against a
simulated ledger and asserting every column except the two new ones is
byte-identical afterwards.

---

## 7. Migration dry-run output

```
rows examined           : 339
rows needing backfill   : 337
rows already identified : 0
rows unresolved (SKIP)  : 2

UNAMBIGUOUS      316     symbol master resolves it; no other venue lists the root
PRICE_VERIFIED    21     recorded price matches one candidate on the creation date
UNRESOLVED         2     refused

conflicting subject mappings: none
NO PRODUCTION DATA WAS MODIFIED.
```

Every price-verified row cites its evidence:

```
subject=BA   -> provider_symbol=BA.L
   reason      : recorded 2200.0 matches BA.L close on 2026-08-06 (0.00% drift);
                 candidates BA.L=2200.0, BA=232.19
   verification: provider close on 2026-08-06
```

**It found instruments the audit missed** — `SAP` (218.68 matches `SAP`, not
`SAP.DE` at 188.12) — because it decides on price evidence, not on a ticker list
I wrote.

**It failed closed on 2 rows.** Both `MRK`: Merck (US) and Merck KGaA (XETRA)
each match the recorded price within 5%. Genuinely undecidable, so they are
skipped and reported rather than guessed.

---

## 8. Tests

**1,237 passed · 1 xfailed · 0 failed.**

28 in `tests/test_identity_safety.py` covering all 18 required items, plus 8 in
`tests/test_price_unit_audit.py`. The explicit search regression tests pass
individually — a green suite alone is not treated as proof of search identity:

```
test_search_boeing_cannot_return_bae_systems              PASSED
test_boeing_absence_is_reported_not_fabricated            PASSED
test_search_bae_systems_returns_the_london_instrument     PASSED
test_search_by_provider_symbol_returns_that_instrument    PASSED
test_a_bare_ticker_never_silently_picks_a_venue           PASSED
test_ranking_puts_an_exact_provider_symbol_first          PASSED
test_the_yaml_upsert_refuses_to_rename_another_venues_instrument  PASSED
```

Item 12–15 (post-migration invariants) are proven **now**, on a simulated
migration, rather than deferred until after the production run.

**Three bugs were caught by writing these tests, not by reading code:**

1. `assert_provider_symbol_safe()` was **inverted** — it compared against the
   *stored* name, so it accepted "Boeing" onto `BA.L` and rejected "BAE
   Systems". Fixed to compare the corrected name.
2. `search("BAE Systems")` returned nothing — the SQL prefilter matched stored
   names, so corrected names were undisplayable *and* unfindable.
3. `migrate_identity.apply()` wrote its backup into real `data/backups/`; the
   production-write guard refused it. The simulations now redirect their root.

---

## 9. Remaining risks

- **The 100× defect is live in data**, dormant only because `Signals.jsx` and
  `Recommendations.jsx` are orphaned. A test fails if either is re-routed.
- **2 MRK predictions** remain unidentified until a tighter tolerance or a venue
  hint resolves them.
- **2 genuine BSE collisions** block `UNIQUE(provider, yahoo)`.
- **`GBPINR=X` alias row** is mis-tagged `US/equity` by the live resolver.
- **21,051 names unverified** against an authoritative source.
- The ledger **grows continuously** (279 → 339 during this phase, from the
  running daemons), so the dry run must be re-read immediately before applying.

---

## 10. Exact production migration command

Re-run the dry run first, then:

```bash
venv/Scripts/python.exe scripts/migrate_identity.py --apply
```

It will:

1. refuse if any subject maps to more than one provider symbol;
2. refuse if `PRAGMA integrity_check` is not `ok`;
3. copy `aria_core.db` to `data/backups/identity-migration-<ts>/` with the plan
   and a pre-migration integrity snapshot;
4. add `provider_symbol` and `identity_basis` (additive only);
5. write only rows whose identity is UNAMBIGUOUS or PRICE_VERIFIED, skipping
   the 2 unresolved;
6. verify prediction / graded / scored / decision counts and the subject, price
   and outcome digests are unchanged;
7. print the restore command.

**It has not been executed.**

---

## 11. Confirmation

| Check | State |
|---|---|
| `predictions.provider_symbol` | **absent — not migrated** |
| Predictions / graded | 339 / 144 |
| `BA` row in symbol master | `('BA','BA.L','Boeing','LSE','GBP')` — unchanged |
| Historical subjects rewritten | **none** |
| Predictions regraded | **none** |
| Instruments invented | **none** |
| Identity mappings guessed | **none** — 2 refused |
| `.env` | byte-identical |
| HEAD | `215c1a2`, nothing committed |

**ONE INSTRUMENT → ONE CANONICAL PROVIDER IDENTITY → ONE CORRECT PRICE UNIT →
ONE REPRODUCIBLE HISTORICAL RECORD.**
