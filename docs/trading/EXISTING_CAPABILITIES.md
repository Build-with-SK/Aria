# Capabilities at baseline 58f5f25

Statuses describe existing local unit/fixture capability, not actual broker or
production integration. Test names are evidence locations; the baseline/final
suite results live in `TEST_EVIDENCE.md`.

| Capability | Classification | Source / evidence |
|---|---|---|
| Owner authentication, role policy, step-up | EXISTING_AND_TESTED | `src/auth/`; `tests/test_auth_policy.py`, `test_step_up.py` |
| Vault owner boundary | EXISTING_AND_TESTED | `backend/main.py:2067`, `src/auth/policy.py:500`; `test_vault_security.py` |
| Research / market dashboard | EXISTING_AND_TESTED | `frontend/src/App.jsx`; `frontend/scripts/` offline renders |
| Paper execution / mode / kill controls | EXISTING_AND_TESTED | `src/trading/`, `src/execution/`; `test_trading_modes.py`, `test_kill_switch_and_profiles.py` |
| Idempotent journal / reconciliation | EXISTING_AND_TESTED | `src/trading/orders.py`; `test_trading_orders.py` |
| Portfolio risk | EXISTING_AND_TESTED | `src/risk/pretrade.py`; `test_portfolio_risk.py`, `test_send_time.py` |
| IBKR paper transport | BLOCKED_BY_EXTERNAL_DEPENDENCY | `src/execution/ibkr_broker.py:293`; fake status tests `test_security_ibkr_status.py`; TWS evidence absent |
| Alpaca transport | BLOCKED_BY_EXTERNAL_DEPENDENCY | `src/execution/alpaca_broker.py`; no authenticated session attempted |
| Technical indicators / candles | EXISTING_AND_TESTED | `src/data/technical_engine.py`; `test_technical_engine.py`, `test_candle_sessions.py` |
| Evidence and macro research | EXISTING_AND_TESTED | `src/intelligence/research/`, `src/macro/feeds.py`; PIT/research tests; live subscriptions unverified |
| Walk forward / multiple testing | EXISTING_AND_TESTED | `src/v5/walkforward.py`, `src/intelligence/walkforward.py`; corresponding tests |
| Strategy lifecycle and promotion | EXISTING_AND_TESTED | `src/trading/lifecycle.py`; `test_trading_lifecycle.py` |
| Legacy aliases | DEPRECATED_OR_DUPLICATE | Redirects `frontend/src/App.jsx:204`; retained for compatibility |
| Canonical Decimal multiasset contracts | MISSING | Legacy `OrderSpec.qty` and risk arithmetic use floats |
| FTMO versioned confirmed profiles | MISSING | No provider profile engine found |
| Transactional approval outbox | MISSING | Existing journal is JSONL, not atomic approval consumption |
| MT5 read-only adapter | MISSING | No MetaTrader5 integration found |
| Third-party plugin sandbox / MCP gateway | MISSING | No trading extension registry found |
| Real FTMO demo execution validation | BLOCKED_BY_EXTERNAL_DEPENDENCY | Account not purchased/configured; no credentials used |
