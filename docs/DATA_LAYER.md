# Market data: where prices come from, and what travels with them

Last revised 2026-10-06. Describes `src/v5/marketdata.py`, `src/v5/vendors.py`,
`src/v5/dataquality.py` and `src/core/world.py` as they are.

## The central layer

`src/v5/marketdata.py` is the one place daily OHLCV should come from:
`history()`, `closes()` and `returns()` for symbols in the **master namespace**
(display symbols resolved through `universe.db`), and `provider_closes()` for
**exact provider symbols** that must never be re-resolved. The ledger grader
uses the exact form. In the master namespace `BA` is BAE Systems (`BA.L`,
pence); as a provider symbol it is Boeing. Exact symbols are cached under their
own key (`@BA`), so the two can never share an entry.

Sources, in order:
1. `data/raw/<provider>.csv` — the signals pipeline's download (yfinance,
   split+dividend adjusted), trusted for 3 days. Looked up by the **resolved**
   provider symbol. Looking it up by the bare symbol would have served Boeing's
   file for "BA" on days the pipeline ran and BAE otherwise.
2. The disk cache (6 h), with a `.meta.json` sidecar recording which vendor
   filled it, whether that vendor was the primary, and its adjustment. Without
   the sidecar a fallback series came back as clean primary data for six hours.
3. The vendor chain: yfinance → Alpha Vantage → Stooq. Any vendor after the
   first is **degraded**.
4. A stale cache, labelled `stale_cache`, when every vendor fails.

`as_of(date)` truncates every series for point-in-time replay (walk-forward).

## Adjustment conventions

| vendor | closes |
|---|---|
| yfinance (`auto_adjust=True`) | split + dividend adjusted |
| Alpha Vantage `TIME_SERIES_DAILY` | **as traded (unadjusted)** |
| Stooq | split adjusted; dividend treatment unverified |
| `data/raw` | split + dividend adjusted |

Provenance carries `adjustment`. An unadjusted Alpha Vantage series containing
a probable split (a one-bar move within 3% of a common split ratio) is
**refused**, and the chain moves on. Every return, ATR and momentum figure
across such a bar would be wrong by the split ratio. Unadjusted returns also
omit dividends; that is recorded as a warning.

## Quality checks (`dataquality.assess`)

Run on every series served; attached as `provenance["quality"]` and shown in
the V5 analysis `data` block.

* **bad**: non-positive closes, future-dated bars, a probable split in an
  unadjusted series. A bad series is still served for research, but it is
  marked stale, so the meta-review's stale-data penalty cuts the edge and the
  report says why.
* **warn**: High below Low, a close outside its bar's range, gaps over 5
  business days, single-bar moves over ~42% on an adjusted series (crashes
  happen; on adjusted data a big bar is information, not an error), unknown
  adjustment.

## Freshness of generated datasets

`src/core/world.py` holds the budgets (`STALE_AFTER_HOURS`) and a registry of
the generated JSON datasets (`DATASETS`), with each one's writer.
`/api/data/freshness` reports FRESH / STALE / UNKNOWN for each. The legacy
endpoints (`/api/options`, `/api/ml`, `/api/backtest`, `/api/portfolio`,
`/api/report`) carry `X-ARIA-Data-Status`, `-As-Of`, `-Age-Hours` and `-Legacy`
headers; the wrapper-shaped ones also carry a `freshness` key in the body.

`options_data.json`, `backtest_results.json`, `portfolio_analysis.json` and
`daily_report.json` have one writer, `main_phase4_backup.py`, which nothing
runs. They date from 2026-05-31 and are kept only because endpoints still read
them. `signals.json` and `ml_predictions.json` are written by the full pipeline
(`main.py`), a manual run.

**Stale signals gate execution.** `AutoExecutor.gates()` closes `auto_allowed`
when `signals.json` is past its 30 h budget. The trade then goes to the human
approval queue, and the reflex lane neither arms nor fires.

## Still outside the central layer

Daily OHLCV still fetched with yfinance directly: `src/macro/macro_data.py`
(regime inputs; the 6 h cache would make them staler than its own fetch),
`src/brain/cognitive/learner.py`, `src/dashboard/dashboard.py`
(Streamlit, legacy), and batch downloaders with their own caches
(`src/data/universe.py`, `src/brain/quant_lab.py`, `src/data/data_downloader.py`,
`src/data/technical_tracker._current_prices`).

These are different data types and correctly bypass the layer: live quotes
(`live_quote.py`), option chains (`options/chain.py`,
`strategies/options_engine.py`), news and fundamentals (`.news`, `.info`,
`.calendar`).
