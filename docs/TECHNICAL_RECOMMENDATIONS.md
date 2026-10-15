# Independent technical recommendations — aria-technical-1

Status: implemented on `codex/work`; requires integrator review/merge and normal
deployment to reach the running app. No production process was restarted.

## Discovery and boundaries (2026-10-15)

ARIA is FastAPI/React plus V5 research, central daily market data, macro/news,
ledger evaluation and human-approved execution. SENTINEL is a separate project
(`atlas`), generalist model/agent service, memory and permission system. Both
answered local `/health` requests on ports 8000/8300. Codex actually queried
ARIA `/api/specialist/analysis` using its scoped service credential and SENTINEL
`/api/consult` using its consultation credential. No keys were displayed or copied.

ARIA's structured reply reported stale signals and ML data. Its free-text
capability claims are model interpretations, not architecture verification.
SENTINEL answered, but mixed capability statements with unrelated web sources;
those claims do not establish an independent OHLCV feed or technical engine.
Source inspection confirms the HTTP consultation interface and its two-pass
reasoning (first reconstruct evidence, then inspect ARIA's hypothesis).

Communication already exists: `src/consult/sentinel_client.py` and `bridge.py`,
authenticated consultation, explicit unavailable/timeout/malformed/position states.
The UI reuses owner-only `POST /api/consult`; no auth grants, order paths or backend
routes change. SENTINEL's service token still cannot read the technical endpoint,
vault or execution queues. Evidence is sent explicitly through the existing
scrubbed bridge. No vault contents are included. A human requests each review.

ARIA supplies deterministic technical evidence alongside its existing macro/news
research. SENTINEL critiques that evidence, structure, conflicts and limitations.
Its independence is in reasoning, not a verified independent price source. Its
output never approves a trade or overwrites ARIA's calculation. Silence, an empty
answer or failure is never agreement. Model confidence percentages are not shown.

## Public reference and independent choices

[Investing.com's Gold technical page](https://www.investing.com/commodities/gold-technical)
describes moving-average and indicator summaries across timeframes. Its
[support explanation](https://www.investing-support.com/hc/en-us/articles/115003809349-How-Often-Does-the-Technical-Table-Refresh-on-Investing-com)
lists moving averages, common indicators, summary signals and pivots, and describes
different refresh intervals. These pages do not establish an exact replicable
proprietary weighting/threshold formula. The rules below are ARIA's independent,
versioned design, not a claim to reproduce Investing.com's ratings. No protected
timeframes, screenshot values, paywalls or hidden APIs are ingested.

## Calculation rules

Closed candles only. Each valid directional vote is +1/-1; neutral is 0 and remains
in the denominator. Each moving average has equal weight within its family; each
directional indicator has equal weight within its family. Overall score is
`0.5 * mean(MA votes) + 0.5 * mean(indicator votes)`. Correlated indicators are
not treated as statistically independent trials.

| Score | Label |
| --- | --- |
| >= 0.60 | Strong Buy |
| > 0.15 and < 0.60 | Buy |
| -0.15 through 0.15 | Neutral |
| > -0.60 and < -0.15 | Sell |
| <= -0.60 | Strong Sell |

| Component | Transparent vote rule |
| --- | --- |
| SMA 20/50/100/200; EMA 9/21/50/200 | Close above/below average by more than 0.1% of absolute close; otherwise neutral. EMA uses adjust=False and at least period observations. |
| RSI 14 | Wilder RSI >55 buy, <45 sell; flat closes neutral RSI 50. 70/30 extremes remain observable, not assumed reversals. |
| Stochastic 14,3 | %K minus smoothed %D >2 buy, <-2 sell. |
| StochRSI 14,3,3 | Smoothed K minus D >0.02 buy, <-0.02 sell, values displayed on 0–100 scale. |
| MACD 12,26,9 | Histogram above/below 0.01% of absolute price. |
| ADX 14 and DI | ADX >=25 gates direction of +DI minus -DI; otherwise neutral. |
| Williams %R 14 | Above -40 buy, below -60 sell. |
| CCI 20 | Above +100 buy, below -100 sell. |
| ROC 12 | Above +0.1% buy, below -0.1% sell. |
| Ichimoku 9/26/52 | Price above/below both spans in the cloud visible now (spans calculated 26 bars earlier); within cloud neutral. No future information. |

`ta` implements Wilder RSI/ADX/ATR and the listed oscillators. ATR14 and Bollinger
20/2 describe volatility and receive no directional vote. ATR relative to the
preceding 20 ATR observations: >1.2x expanding, <0.8x contracting, otherwise typical.
Volume ratio compares the current closed candle with the preceding 20. Missing
volume remains unavailable. Support/resistance use the preceding 20 highs/lows,
excluding the last candle. Structure compares highs/lows in two adjacent 20-bar
windows; it is a descriptive range comparison, not an order-block/BOS model.

## Coverage, identity and time

The existing `/api/technical/summary` entry point returns full per-timeframe
details via `technical_recommendations.py`. Existing scans keep their list shape.
Legacy `score_frame`, tally and ATR stop helpers remain for pre-existing point-in-
time snapshot consumers; the new dashboard never presents those stops as a
validated setup. No new stop/target or position-sizing recommendation is created.

Supported: 1m, 5m, 15m, 30m, 1h, 5h, 1d, 1w, 1M. `1wk` remains an alias.
Case distinguishes minute/month. Unknown timeframes return an explicit error,
never daily fallback. At least 30 closed bars are needed for diagnostics; 200 are
required for a current summary. Missing indicator values are omitted and coverage
counts remain visible; no zero-fill. EMA200 initialization can retain seed error
even after 200 bars. These rules are deliberately not calibrated confidence.

Daily generic instruments use the existing central market-data loader/provenance.
Other intervals use the existing yfinance provider, unadjusted. The daily
adjustment convention is reported separately and must not be assumed identical.
Timestamp-naive data is refused. OHLC must be finite, ordered, unique and within
its candle range. Negative/infinite volume is refused; missing volume is explicit.
Negative futures prices are permitted; malformed ranges are not. Open/future bars
cannot contribute to a signal. Daily/weekly/monthly completion conservatively uses
the next local day/period boundary. Weekly starts Monday; monthly starts day 1.

5h is built from five consecutive hourly bars on a UTC epoch-anchored grid.
Partial buckets and bins spanning missing hours are omitted. This is explicitly
labelled and is not claimed to match Investing.com or an exchange session grid.
Gap counts remain visible because a gap may be a market closure or missing feed
data. No verified session calendar is claimed. Intraday staleness is three frame
durations; daily five days, weekly 14 days, monthly 62 days. These are conservative
heuristics, not exchange liveness guarantees. Provider latency is unknown unless
provided. No response is labelled real-time.

`GCZ6`, `GCZ26`, `GCZ2026`, `GCZ26.CMX` map explicitly to Yahoo `GCZ26.CMX`,
COMEX (`CMX`), December 2026. The provider must confirm exact symbol, exchange,
expiration month and an unexpired contract. No substitution with `GC=F` or XAU/USD.
Other dated contracts are refused until explicitly mapped. An explicitly requested
`GC=F` is labelled provider-managed continuous, with no claim to be GCZ6.

Actual GCZ6 provider probe on 2026-10-15: the history metadata lacked expiration,
so the first probe correctly withheld its recommendation. The secondary quote
metadata lookup then confirmed exact symbol, exchange and expiration
`2026-12-29T00:00:00+00:00`; the implemented fallback requires all three to match
before publishing analysis. Vendor delay remains unknown and calendar gaps are
explicitly unverified. No price or signal is inferred from the reference screenshot. Dated futures naturally lack decades of monthly
history, so long-period MAs can legitimately remain unavailable for that contract.

Stale/short-history rows retain diagnostic calculations but have `summary=null`.
Evidence quality describes coverage, freshness and calendar limitations, not a
probability of profit. Missing rows withhold aggregate direction. Opposing usable
timeframes produce explicit pairwise warnings and `consensus=null`; higher and
lower horizon conclusions remain intact. There is no unconditional aggregate Buy.

## Verification and research replay

`tests/test_technical_engine.py` tests known indicator values, flat/up/down candles,
all labels, neutral weighting, open/stale/malformed/missing prices, timezone,
contract substitution/expiration, weekly/monthly identity, hourly resampling,
conflicts, consultation disagreement/failure and the existing public entry point.
The established technical/SENTINEL regression tests are also run.

`scripts/technical_replay.py` exposes `src/backtesting/technical_replay.py:replay`
for owner-supplied verified daily CSV files. It is an offline illustrative daily
open-to-close experiment. It requires exact contract and expiration columns,
explicit tick size, multiplier, fees per side and slippage per side. A decision
uses only candles completed before entry, with 200-bar warm-up separately per
contract. Each trade closes that same candle, so no position crosses a rollover
and no artificial contract-price jump earns profit. Tests perturb the execution
candle and truncate future data to verify causal decisions. Synthetic fixtures
verify accounting only; they are not evidence of market profitability.

No audited historical GCZ6 dataset with complete fees, slippage and rollover provenance was
available, so no real GCZ6 performance/edge claim is made.
The existing live strategies and execution paths are not modified. A performance
claim would additionally require the project's evaluation/baseline comparison
and an out-of-sample, cost-inclusive experiment. Full suite/build results and any
baseline failures are recorded in `docs/collab/log-codex.md`.


Browser screenshot inspection was blocked because the browser policy prohibits
local file URLs. No alternate browser surface or local server was used to bypass
that policy. Production build and offline React rendering are verified; pixel-level
browser review and a live authenticated UI click remain deployment checks.


End-to-end exact-contract probe: all nine horizons were requested from the actual
provider. At that snapshot 1m was stale and monthly history lacked full warm-up;
12 opposing valid timeframe pairs were identified and aggregate direction was
withheld. These are historical probe observations, not current trading advice.
The running SENTINEL accepted this calculated evidence and returned AVAILABLE /
UNCERTAIN (request `c-ea0f6fde78`). It nevertheless described stale 1m and
short-history monthly indicators directionally and appended extraneous research.
That is a measured limitation of its model interpretation. The dashboard explicitly
names withheld horizons beside any successful review and marks directional claims
on them unsupported. This is not independent market-data verification.


Final verification: 171 focused Python tests passed; final engine tests 43 passed.
Dashboard offline checks and production build passed. Full suite: 2,985 passed,
29 failed, 9 skipped; every final failure also fails with unmodified HEAD source
in the same isolated data environment. The suite is not green. Missing/partial
symbol-master and sector fixtures are required before integrator acceptance.
No claim of improved predictive performance is made.
