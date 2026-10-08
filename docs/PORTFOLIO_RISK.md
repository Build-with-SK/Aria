# Portfolio, factor and currency risk: how much risk a trade adds

Last revised 2026-10-07. Describes `src/risk/` (`market_data`, `fx`,
`covariance`, `factors`, `portfolio_risk`, `pretrade`, `decisions`) and where
it sits in the execution paths. Sessions are in `docs/MARKET_SESSIONS.md`.

> **A signal's confidence is not permission to trade.** A high-confidence
> signal can still be reduced, deferred or rejected. That happens when
> portfolio, factor or currency risk is too high, liquidity is too thin, the
> market is closed or UNKNOWN, or a limit is already full.

## The decision path

```
PREDICTION
  -> CONVICTION          decomposed context (src/intelligence/conviction): direction,
                         probability AND its type, calibration, reliability,
                         disagreement, data quality, regime fit, routing
SIGNAL
  -> PORTFOLIO CONTEXT   the book (AccountState), valued in the base currency with cited FX
  -> RISK                RiskEngine sizing (src/trading/risk.py)
                         + pre-trade portfolio risk (src/risk/pretrade.assess)
  -> SESSION             the instrument's own session, at the send boundary
  -> EXECUTION CHECK     validator, permission (kill switch), mode gate, broker
```

A prediction never authorises a trade. Since 2026-10-08 `ProposedOrder`
carries an optional `conviction` (a plain dict; `pretrade` imports nothing
that could reach a model). It is recorded with the decision and has exactly
two possible effects on an ENTRY, both restrictive:

| conviction | effect |
|---|---|
| does not support the side (FLAT, or the opposite direction) | REJECT — `prediction_supports_side` |
| `data_quality` POOR (its horizon has elapsed, inputs unreadable) | DEFER — `prediction_data_usable` |
| anything else, however strong it looks | none: verdict and size are exactly what they would be without it |

Exits (reducing orders) are accepted before the conviction is read, so a
prediction can never block one. `OrderManager` attaches the conviction of the
debate a desk trade came from; a manual proposal, or any failure to build one,
attaches none and the gate decides alone, as before. `TradingPipeline.run`
takes it as an optional argument. See docs/MODEL_INTELLIGENCE.md.

Every gate must pass, and none can be skipped. The pipeline runs the cheap
session check first, before reading the account, and checks it again at the
send boundary. Order of evaluation is cheapest first; a pass from one gate is
never permission for another.

| path | where portfolio risk runs | limits come from |
|---|---|---|
| `TradingPipeline.run` (the autonomous layer, PAPER) | stage `portfolio_risk`, after RiskEngine sizing and before the order is built. REDUCE_SIZE goes back through `RiskEngine.resize`, which re-checks the minimum trade and cost viability at the smaller size | `src/trading/limits` (name = `max_instrument_exposure_pct`, sector, gross = `max_leverage`) + portfolio defaults |
| `OrderManager.execute` (owner approve, desk auto-execute, reflex lane) | at the moment of sending, against the broker's book **now** (`account.from_legacy_broker`) and a fresh, timestamped quote | the desk's risk profile (`src/risk/profiles`: name, sector, gross, correlated positions, allowed asset classes) + portfolio defaults |
| queued exits, protective orders, the kill-switch flatten | not here: they reduce risk and are checked by the reduce-only invariant (`src/execution/reduce_only`) | — |

Every decision is recorded. That covers ACCEPT, REDUCE_SIZE, DEFER and
REJECT, on both paths (`src/risk/decisions`, `GET /api/trading/risk-decisions`,
owner-only).

## Portfolio state: one record

`src/trading/account.AccountState` is **the** portfolio state:

* cash and equity
* gross, net, long and short exposure, and leverage
* buying power: cash net of reserved capital; there is no margin
* reserved capital: notional committed to resting buy orders
* realised and unrealised P&L, plus performance equity and drawdown
* positions (`Holding`: quote currency, venue, asset class, sector, strategy)
* pending orders

`src/risk/portfolio_risk.value()` *values* that record in a base currency. It
keeps no second copy of the positions. A legacy broker's book is read into the
same record by `account.from_legacy_broker()`. Shorts keep their sign, and any
read failure is a known-unknown state, never zeros.

## Currency (`src/risk/fx.py`)

Native currencies come from `src/data/currency.provider_currency`, so BA is
Boeing in dollars and BA.L is BAE in **pence**. GBp is a quoting unit (×0.01),
not a currency. A crypto pair without a separator (`BTCUSD`) is read with its
asset class. Stablecoin quotes (USDT, USDC) are not dollars and stay unknown.

Every conversion is an `FxRate` with the pair(s), the rate, the date of the
close used, and the source. Lookups try, in order:

1. identity;
2. the direct pair (`JPYUSD=X`);
3. the inverse pair, inverted;
4. a cross through USD, with both legs recorded.

**Point in time:** the close is the last FX bar known at the instant, so a 2025
position is valued at its 2025 rate. FX bars are known only after the New York
day ends.

A rate older than 5 days is **stale** and not used. A missing or stale rate
means the position is **unvalued**, and the pre-trade check DEFERs.

## Covariance and volatility (`src/risk/covariance.py`, `portfolio_risk.py`)

* **Frequency.** Daily returns when every instrument is on one venue; weekly
  (Friday) returns otherwise. Same-date daily returns of Tokyo and New York
  are measured over different windows, and that bias makes a global book look
  more diversified than it is.
* **Base currency.** Prices are converted date by date at each date's own FX
  close, before returns are taken, so a yen stock's volatility includes the yen.
  An FX close is carried forward at most 3 days.
* **Minimum history.** 60 daily or 26 weekly returns. Below that an
  instrument is excluded, with the reason, and never estimated from a shorter
  window. When overlap is short, the shortest history is dropped until what
  remains is enough.
* **Estimation.** Ledoit-Wolf (2004) shrinkage towards a scaled identity, then
  positive-semidefinite repair. The condition number, shrinkage and number of
  observations are reported. A singular sample (two identical series) becomes
  a stable, invertible matrix.
* **Contributions (Euler).** σ_p = √(wᵀΣw), marginal = Σw/σ_p, component =
  w·marginal, and the components sum to σ_p (tested). The diversification
  ratio and effective number of bets (DR²) count independent risks, not names.
* **Factor approximation.** Single-index: (Σwβ)²σ_m² + Σw²σ_ε².

## Factors (`src/risk/factors.py`)

Exposures are measured, not assigned. Each is a time-series regression of the
instrument's base-currency return on a tradable proxy:

| factor | proxy |
|---|---|
| market | ACWI |
| size | IWM − SPY |
| value | IWD − IWF |
| growth | IWF − SPY |
| momentum | MTUM − SPY |
| quality | QUAL − SPY |
| volatility | USMV − SPY (a negative exposure is a high-vol tilt) |
| rates | IEF |
| credit | HYG − IEF |
| fx | UUP |
| commodities | DBC |
| crypto | BTC-USD |

Every non-market factor is estimated with the market as a control, one factor
at a time, so that correlated style spreads do not destabilise each other.

Factors are applied only where they mean something:

* **US equity:** market, styles and macro factors.
* **Other equity:** market and macro factors. The style proxies are US
  portfolios, so a Tokyo stock's style exposure is UNAVAILABLE.
* **Futures:** by contract group (equity index → market, rates → rates,
  energy, metals, grains and softs → commodities, FX → fx, crypto → crypto).
* **FX pairs:** fx.
* **Crypto:** market and crypto.
* **Options:** nothing, because there is no delta model.

A missing proxy or too little overlap gives an UNAVAILABLE exposure with its
reason. Portfolio exposures state their **coverage** and are never scaled up
to look complete.

## Concentration (`portfolio_risk.concentration`, `correlated_clusters`)

Concentration is measured several ways:

* largest position, top 5, top 10, HHI and effective names;
* by sector, country, currency, asset class and venue (gross, % of equity);
* by factor exposure;
* **correlated clusters:** positions whose P&L moves together, meaning
  sign(wᵢ)·sign(wⱼ)·ρᵢⱼ ≥ 0.7. A long and a short in correlated names is a
  hedge, not a cluster.

Three positions in AAPL, MSFT and NVDA are one cluster and about one
independent bet (tested).

Sector comes from the symbol master's **provider** column only. An unknown
sector is its own bucket, so unknowns never pool into a fake sector.

## The pre-trade decision (`src/risk/pretrade.py`)

| verdict | meaning |
|---|---|
| ACCEPT | fits every limit at the requested size |
| REDUCE_SIZE | fits at a smaller size. `approved_qty` and the `binding` limit are given |
| DEFER | cannot be judged now: unknown or stale portfolio, price, FX or currency; an unvalued holding; a spread above the limit; a closed or UNKNOWN session (with an inert re-evaluation plan) |
| REJECT | no size fits. A limit is already full, the asset class is not allowed, the trade would be one correlated position too many, or an order labelled reduce-only would not reduce |

**Sizing.** Each limit is solved for the largest quantity it allows, and the
smallest wins:

* name, sector, gross;
* one foreign currency, and all foreign currencies;
* one foreign country;
* % of average daily volume;
* portfolio volatility after the trade (a quadratic: a trade that *lowers*
  volatility is never blocked);
* portfolio market beta after the trade;
* the correlated cluster.

**Missing data is conservative.** With no volatility estimate or no volume
history, the position is held to `unknown_risk_name_fraction` (½) of the name
limit. This always applies on a path that is given no market data, such as the
send-time check, which never fetches history unless given a source.

**Reducing is not adding.** An order that shrinks an existing position is
ACCEPTED in full.

**Liquidity.** Average daily volume (20 bars) and daily volatility (60 bars),
from history known at the instant. Participation and a square-root impact
estimate (daily vol × √(qty/ADV)) are recorded. The same law is in
`src/trading/costs.slippage_estimate`.

Policy defaults (`PortfolioRiskLimits`). These are stated choices, not
measurements:

| limit | default |
|---|---|
| one foreign currency | 25% of equity |
| all foreign currencies | 50% |
| one foreign country | 35% |
| portfolio volatility | 25% annualised |
| market beta | 1.25 |
| ADV participation | 5% (= `src/v5/risk_gate`) |
| spread | 50 bps (= `SessionPolicy`) |
| correlation threshold | 0.7 |
| portfolio snapshot age | 120 s |
| price age | 60 s (pipeline: its own quote-age limit) |

## What a decision records (`src/risk/decisions.py`)

Each record answers:

* why it was approved, reduced, deferred or rejected: `verdict`, `reasons`,
  `checks`, `binding`, `caps`;
* which exposure caused it: `before`, `after`, `incremental`;
* which factor exposure: `factors`;
* which session: `session`, `plan`;
* which data, and when: `data` (portfolio as-of, price time, FX pair, rate and
  date, the last bar per series, and covariance diagnostics);
* which limits, from where: `limits.source`.

No credentials are passed to the risk layer, so none can be recorded. The log
is `data/trading/risk_decisions.jsonl`, runtime state that is gitignored.

## Point-in-time guarantees

All history is read through `src/risk/market_data.MarketData.history(symbol,
at)`. Production uses `MdMarketData`: `src/v5/marketdata` under `as_of(at)`.
Tests and replays use `FrameMarketData`, cut by the **same** function
(`marketdata._truncate_point_in_time` → `sessions.bar_known_by`). A bar exists
once its own venue's session has closed. Nothing is cached across instants.

`tests/test_portfolio_risk.py` takes one full decision at instant T, which
includes covariance, factors, FX, liquidity and caps. It then rewrites every
bar not known at T for:

* price
* a factor proxy
* FX
* volatility
* a 2:1 split (corporate action)
* inverted returns (correlation)
* volume

Each time, the decision is **identical**. Rewriting known bars changes it,
proving the check can fail. With the point-in-time cut disabled, all 9
point-in-time tests fail.

## UNKNOWN

UNKNOWN is fail-safe and never authorizes execution. In this layer:

* an unknown portfolio, price, FX or currency means DEFER;
* an unknown volatility or liquidity means a smaller position;
* an unknown factor means UNAVAILABLE, with coverage stated;
* an UNKNOWN session means DEFER with a plan;
* an unknown venue means DEFER.

Nothing is fabricated to fill a gap.

## Performance (measured 2026-10-07, this machine, warm, median / p90)

| operation | median | p90 |
|---|---|---|
| portfolio state from a broker, 10 positions (`from_legacy_broker`) | 0.33 ms | 0.41 ms |
| valuation, exposure only | 0.07 ms | 0.08 ms |
| valuation with FX (one JPY holding) | 0.57 ms | 0.97 ms |
| **pre-trade check, exposure only (the send path)** | **0.12 ms** | 0.20 ms |
| covariance from history, 5 names, weekly (reads + point-in-time cuts + Ledoit-Wolf) | 12 ms | 13 ms |
| Ledoit-Wolf alone, 30 names × 252 days | 0.06 ms | 0.07 ms |
| factor exposures, 4 names (12 proxies, one regression per factor) | 89 ms | 93 ms |
| `analyze()` full report, 4 positions | 99 ms | 115 ms |
| **pre-trade check, full** (covariance + factors + liquidity, 4 positions) | **116 ms** | 146 ms |

These figures are for in-memory frames (`FrameMarketData`). The full check is
dominated by reading and cutting histories; the matrix algebra itself is under
a millisecond. With `MdMarketData`, vendor latency comes on top: it is not
measured here, and the series are served from `src/v5/marketdata`'s own
30-minute memory once fetched. Nothing is cached across instants, so the
point-in-time cut applies on every read.

## Not done

* No factor model for options (no delta), so their factor exposure is
  UNAVAILABLE.
* Style factors use US proxies only. Non-US equities get market and macro
  factors.
* Limits are code defaults. They are not yet editable through the hard-limits
  workflow (tighten now, loosen after 24 h).
* Intraday-frequency risk is not estimated; daily and weekly only.
