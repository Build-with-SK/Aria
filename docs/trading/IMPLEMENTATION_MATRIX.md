# Implementation matrix

Statuses apply to the stated scope, not the whole target domain. Test execution
counts and skip reasons: TEST_EVIDENCE.md. Mock transport success is not integration.

| FEATURE | STATUS | FILE_OR_MODULE | TEST_EVIDENCE | EXTERNAL_DEPENDENCY | NEXT_ACTION |
|---|---|---|---|---|---|
| Repository audit / inventories | IMPLEMENTED_AND_TESTED | docs/trading/REPOSITORY_AUDIT.md, API_INVENTORY.md | AST inventory; baseline suite | None | Independent review |
| Baseline audit test-order fix | IMPLEMENTED_AND_TESTED | tests/test_stabilization.py | 22 focused tests; full suite | None | Review isolated fixture change |
| Decimal / UTC domain contracts | IMPLEMENTED_AND_TESTED | src/trading/contracts.py | test_multiasset_risk.py | None | Complete provider mappings |
| Nine asset applicability registry / universe distinctions | IMPLEMENTED_AND_TESTED | src/research/multiasset.py | test_multiasset_research.py | Provider/account inventories | Populate actual supported universes |
| Fixture provider and bounded research read wrapper | IMPLEMENTED_AND_TESTED | src/research/multiasset.py, src/data/provider_contracts.py | test_multiasset_providers.py, test_multiasset_research.py | None for fixtures | Add licensed providers |
| Real provider integrations for new workflow | NOT_STARTED | Existing feeds preserved | Existing feed tests only | Licensed data / credentials | Implement validated adapters |
| SMA/EMA/RSI/MACD/ATR/ADX/Bollinger / real-volume VWAP | IMPLEMENTED_AND_TESTED | src/research/multiasset.py reuses ta/technical_summary | Fixture SMA/completed candle tests + existing suite | None for fixtures | Independent reference extension |
| All advanced requested technical features | PARTIAL | Existing src/data/technical_engine.py + new research | Existing technical tests | Calendar/volume/order book sources | Add gap/outlier/liquidity/structure verification |
| Structured hypotheses and ABSTAIN | IMPLEMENTED_AND_TESTED | src/research/multiasset.py, contracts.py | test_multiasset_research.py | No model required | Independent evidence corroboration |
| Ten strategy definitions | PARTIAL | src/research/multiasset.py | Warm-up/abstain/trigger tests | PIT macro and validated regimes | Macro strategy, explicit parameters and validation |
| Experiment manifests / overlap-purged splits / block bootstrap | IMPLEMENTED_AND_TESTED | src/backtesting/experiments.py | test_multiasset_research.py | Real datasets for results | Integrate provider-specific replay |
| Full multiasset realistic validation workflow | PARTIAL | Existing backtesting/walkforward + utilities | Existing suite retained | PIT data / fill assumptions | Gaps/partials/actions/survivorship and OOS experiments |
| Versioned profiles / financial loss and objective rules | IMPLEMENTED_AND_TESTED | src/trading/rule_profiles.py | test_multiasset_rules.py, test_multiasset_risk.py | No actual profile configured | Verify applicable real account rules |
| Persistent Prague boundaries / nondecreasing high-water | IMPLEMENTED_AND_TESTED | src/trading/review_store.py | test_multiasset_rule_store.py | Authoritative full account history | Wire read-only snapshot collector |
| Actual confirmed FTMO profile and operational compliance | BLOCKED | No active profile | UI/API POLICY_UNVERIFIED tests | Actual owner account/source confirmation | Independent policy review |
| Independent linear-contract research risk | IMPLEMENTED_AND_TESTED | src/trading/multiasset_risk.py | Financial/freshness/caps/costs/margin tests | Broker evidence for real use | Snapshot builder + nonlinear calculators |
| Full correlated/currency/liquidity/loss-streak engine | PARTIAL | Existing src/risk + new caps | Existing risk tests | Market/book data | Complete authoritative factor controls |
| Single-use bound review / atomic intent / recovery / audit backup | IMPLEMENTED_AND_TESTED | src/trading/review_store.py | Replay/race/crash/restart/tamper/backup tests | None for library tests | Authenticated demo worker integration |
| Complete broker-driven execution lifecycle | PARTIAL | review_store.py state vocabulary; legacy orders preserved | Persisted pre-send/recovery tests; legacy order tests | Broker worker/session | Verified ACK/partial/fill/reconciliation |
| New demo sender / modification / closing implementation | NOT_STARTED | MT5 writes intentionally refuse | All mutation refusal tests | Independent review + actual demo identity | Private gated worker only |
| MT5 read-only SDK adapter | MOCK_ONLY | src/trading/mt5_readonly.py, src/trading/mt5_worker.py | Injected SDK identity/switch/None/investor-only tests; spawned worker refuses writes | Official SDK + local terminal + authorised demo | Genuine read connection (Gate 4) |
| Actual MT5 demo order (Gate 5) | BLOCKED | No send transport | None claimed | Genuine session + individual approval | After Gate 4 and worker review |
| Existing IBKR transport | IMPLEMENTED_NOT_INTEGRATION_TESTED | src/execution/ibkr_broker.py unchanged | Existing fake broker/status tests | TWS/Gateway / authorised account | Separate session evidence |
| cTrader | NOT_STARTED | BROKER_ADAPTERS.md specification | No implementation claimed | Broker app/OAuth/account access | Review transport contract |
| Plugin declarations and default deny | IMPLEMENTED_AND_TESTED | src/trading/extension_policy.py | test_multiasset_providers.py | None | Keep loading disabled |
| Real plugin sandbox / MCP runtime gateway | NOT_STARTED | Disabled; PLUGIN_SYSTEM.md, MCP_SECURITY.md | Declaration/revocation tests only | Real isolation design | Independent security implementation |
| Owner-only research API / disconnected Portfolio panel | IMPLEMENTED_AND_TESTED | backend/main.py additions; frontend MultiassetWorkspace | test_multiasset_api.py; offline render verifiers | Existing app after separate integration | Real verified data / hydration tests |
| Full populated trading workspace | PARTIAL | Existing Portfolio pages reused | Offline routes / new empty-state renders | Verified account/data/worker | Connect and test actual review flows |
| Dependency remediation / reproducible minimal environment | PARTIAL | frontend lock; requirements-trading-research.lock | DEPENDENCY_AUDIT.md | Advisory registry availability | Shared deployment remediation / router upgrade |
| Independent Claude review | BLOCKED | REVIEW_PACKET.md | No approval claimed | Independent reviewer | Review packet and return findings |
