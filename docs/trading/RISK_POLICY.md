# Research risk policy

`src/trading/multiasset_risk.py` is deterministic and imports no model/web layer.
Ordinary research cap: 0.5% of current equity. The 1% exceptional value is metadata
only; no exception approval or escalation path is implemented. Changes to source
policy invalidate the assessment hash. No API permits policy mutation.

Required: exact account identity/environment/currency; complete positions and
pending-order risk; fresh executable quote; verified linear specification; correctly
directed fresh FX conversion when needed; confirmed profile and exact current
Prague reset snapshot; independently checked operational constraints. Unknown
inputs return UNVERIFIED. Kill/financial-limit breaches return EMERGENCY_HALT.

Per-volume stop risk = (absolute entry-stop distance + allowed entry tolerance)
× contract size × quote-to-account conversion. Total risk includes spread,
commission, slippage and financing allowances in account currency. Volume is
floored to the broker step; minimum lot beyond budget is denied. Contract tick
value must agree with the linear model or a broker calculator is required.

Contract size is checked three ways (review CR-03). (1) Mandatory: the broker's
own one-order figures (MT5 `order_calc_profit` / `order_calc_margin`, as a
`BrokerCalculation`, fresh within 15 s) must match the engine's loss within 0.5%,
or the verdict is UNVERIFIED; the larger of the two margin figures is used. (2) A
registry of conventional sizes (`instrument_registry.py`: FX 100,000; gold 100 oz;
silver 5,000 or 1,000 oz) returns UNVERIFIED for anything else; instruments with
no convention rely on (1). (3) A per-asset-class lot cap per $10,000 of equity
(FX 1.0, metals 0.2, energy/agriculture 0.5, indices 2.0, equity/ETF CFDs 50,
crypto 0.1, other 0) denies sizes a mapping error could make look small.
Nonlinear settlement, inverse contracts and limit-fill modelling are unverified.

Internal daily stop: today's loss is the policy-local 00:00 balance minus current
equity (floating P/L included), never a separately reported P&L figure; at 2% of
equity the engine returns EMERGENCY_HALT (review CR-05). Floor buffer: new risk
must leave equity above the provider floor plus max(0.5% of initial capital, 3 ×
slippage allowance), and risk equal to the remaining budget is refused (CR-10).

Other checks: aggregate/correlated/pending risk, position count,
order frequency, available margin, leverage, spread, stop distance, exact price
ticks, duplicates, cooldown, abnormal markets and closed/unknown session. Sizing
never rounds upward. ALLOW means research checks passed, never broker authority.

Important limitations: book risk, costs, session/operational checks must come from
an authoritative upstream snapshot builder. This branch does not expose a request
body that can self-assert these booleans to place an order. Broker-specific margin,
gap/liquidity shocks, currency concentration, loss-streak logic and real-time book
reconciliation still need dedicated adapters/testing before execution.

`rule_profiles.py` calculates equity floors and objective eligibility separately.
Failure to reach a profit/consistency objective never forces another trade.
`ReviewStore` persists consecutive exact-day balances and nondecreasing high-water
marks, requiring original-capital history. Missing days/cashflows/resets are held
for manual baseline verification; no silent high-water decrease is allowed.
