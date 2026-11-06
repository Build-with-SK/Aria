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
Nonlinear settlement, inverse contracts and limit-fill modelling are unverified.

Other checks: aggregate/correlated/pending risk, internal daily stop, position count,
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
