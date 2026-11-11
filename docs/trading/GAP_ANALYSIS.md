# Gap analysis

The existing app already researches, measures and simulates trading. Primary gaps
are provider-specific contracts and verified execution authority, not a replacement
dashboard or model. P0 priorities: exact money/volume, fail-closed rule profiles,
fresh quote identity, approval binding, atomic intent persistence and uncertainty.

P1 extends the existing research/indicator/backtest stack with typed evidence and
reproducible manifests. Ten strategy families are research scope, not ten validated
edges. No lifecycle promotion follows from implementing an interface.

P2 MT5 needs a local Windows terminal and actual authorised demo identity. No
credentials are available for this assignment; mocks cannot pass release gates
4/5. cTrader remains a specification. IBKR is preserved and remains separately
unverified against a real session.

P3 adds a workspace within the existing navigation. Prices, balances, order state
and profile verification must remain unavailable until backed by a configured
source. P4 third-party plugins remain disabled without genuine process sandboxing.

Full production FTMO compliance, broker-specific profit calculation across every
contract, durable broker reconciliation and long-running demo observation require
independent review and external evidence. See final implementation matrix rather
than interpreting the target architecture as a completion claim.
