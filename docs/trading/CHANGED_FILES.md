# Exact feature file list

Baseline: `58f5f25`; branch: `feature/aria-multiasset-trading`.
Generated from the staged final tree; final review can reproduce with
`git diff --name-status 58f5f25 HEAD`. No production data or protected guard file
is included. All product files are additions except the two additive API routes,
Portfolio component mounting, dependency manifest/lock and isolated baseline test.

- `backend/main.py`
- `docs/collab/log-codex.md`
- `docs/trading/API_INVENTORY.md`
- `docs/trading/ARCHITECTURE.md`
- `docs/trading/ARCHITECTURE_DECISIONS.md`
- `docs/trading/BACKTEST_VALIDATION.md`
- `docs/trading/BROKER_ADAPTERS.md`
- `docs/trading/CHANGED_FILES.md`
- `docs/trading/DEPENDENCY_AUDIT.md`
- `docs/trading/EXISTING_CAPABILITIES.md`
- `docs/trading/FTMO_RULE_PROFILES.md`
- `docs/trading/GAP_ANALYSIS.md`
- `docs/trading/IMPLEMENTATION_MATRIX.md`
- `docs/trading/KNOWN_LIMITATIONS.md`
- `docs/trading/MCP_SECURITY.md`
- `docs/trading/MT5_SETUP.md`
- `docs/trading/OPERATIONS.md`
- `docs/trading/PLUGIN_SYSTEM.md`
- `docs/trading/PR_SUMMARY.md`
- `docs/trading/README.md`
- `docs/trading/REPOSITORY_AUDIT.md`
- `docs/trading/REVIEW_PACKET.md`
- `docs/trading/RISK_POLICY.md`
- `docs/trading/ROADMAP.md`
- `docs/trading/SECURITY_MODEL.md`
- `docs/trading/TEST_EVIDENCE.md`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/scripts/verify-multiasset.mjs`
- `frontend/src/components/MultiassetWorkspace.jsx`
- `frontend/src/pages/PortfolioWorkspace.jsx`
- `requirements-trading-research.lock`
- `requirements-trading-research.txt`
- `scripts/multiasset_research.py`
- `src/backtesting/experiments.py`
- `src/data/provider_contracts.py`
- `src/research/multiasset.py`
- `src/trading/contracts.py`
- `src/trading/extension_policy.py`
- `src/trading/mt5_readonly.py`
- `src/trading/multiasset_risk.py`
- `src/trading/review_store.py`
- `src/trading/rule_profiles.py`
- `tests/test_multiasset_api.py`
- `tests/test_multiasset_providers.py`
- `tests/test_multiasset_research.py`
- `tests/test_multiasset_review.py`
- `tests/test_multiasset_risk.py`
- `tests/test_multiasset_rule_store.py`
- `tests/test_multiasset_rules.py`
- `tests/test_stabilization.py`
