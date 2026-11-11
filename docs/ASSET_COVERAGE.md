# Asset analysis coverage

Research exposes the same nine-timeframe technical panel above the overview
for every selected asset. The separate ensemble remains on demand. Exact dated
Gold contracts retain their explicit contract, venue and expiration checks.

Search results are suggestions: a URL lookup must match an exact display symbol
or one unique exact provider symbol. It cannot substitute the first fuzzy hit.
Switching assets invalidates pending responses from the old selection.

Catalogue membership does not guarantee provider coverage. Each timeframe can
report missing candles, insufficient warmup, stale data or incomplete calendar
coverage. These states must remain visible; another listing is not a substitute.

## Catalogue audit and mapping maintenance

Run `venv\Scripts\python.exe scripts/audit_asset_coverage.py --output <report.json>`
from the primary checkout. It reads every catalogue row without downloading
prices or changing data. Every row starts with `live_status: not_checked`.

The October 16, 2026 audit covered 22,023 rows, nine asset classes and 47 exchange
labels. It flagged 4,873 numeric BSE mappings for live checking and 2,777 empty
stored currency fields (including index levels, which do not require a currency).
Empty stored currency can be derived from an unambiguous provider namespace;
it is not evidence that a quote is unavailable.

Add `--verify-bse SAIL.BO RELIANCE.BO HDFCBANK.BO TCS.BO` to probe named BSE
candidates. A repair requires OHLC, exact provider symbol, BSE, INR and a matching
full company name or ISIN. A numeric provider is only a candidate for review;
the audit does not declare every numeric symbol broken. One valid bar verifies
feed identity, not enough history for technical indicators.

Repairs are explicit maintenance operations. Stop the primary backend first,
back up `data/universe.db` and the affected caches, then add `--apply --maintenance`.
Only this checkout's catalogue can be repaired. The writer checks that the row
did not change during verification and calls the core identity collision guard.
It preserves listing metadata, records verification evidence, and invalidates
affected quote, dossier and V5 disk caches. Restart the primary backend to clear
in-process caches before serving requests. No read endpoint performs repairs.

BSE refresh preserves a verified correction only for the same nonempty ISIN.
Predictions and historical grades are never rewritten. Live validation counts
and applied repairs are separate fields in the report; do not describe a
structural audit or a sample of live checks as live verification of all assets.
