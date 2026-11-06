# Capabilities at baseline 58f5f25

Statuses describe discovery, not new implementation completion. Test names are
evidence locations; actual execution results live in `TEST_EVIDENCE.md`.

| Capability | Classification | Source / evidence |
|---|---|---|
| Owner authentication, role policy, step-up | EXISTING_UNVERIFIED | `src/auth/`; `tests/test_auth_policy.py`, `test_step_up.py` |
| Vault owner boundary | EXISTING_UNVERIFIED | `backend/main.py:2067`, `src/auth/policy.py:500`; `test_vault_security.py` |
| Research / market dashboard | EXISTING_UNVERIFIED | `frontend/src/App.jsx`; `frontend/scripts/` |
| Paper execution / mode / kill controls | EXISTING_UNVERIFIED | `src/trading/`, `src/execution/`; `test_trading_modes.py`, `test_kill_switch_and_profiles.py` |
| Idempotent journal / reconciliation | EXISTING_UNVERIFIED | `src/trading/orders.py`; `test_trading_orders.py` |
| Portfolio risk | EXISTING_UNVERIFIED | `src/risk/pretrade.py`; `test_portfolio_risk.py`, `test_send_time.py` |
| IBKR paper transport | BLOCKED_BY_EXTERNAL_DEPENDENCY | `src/execution/ibkr_broker.py:293`; fake status tests `test_security_ibkr_status.py`; TWS evidence absent |
| Alpaca transport | BLOCKED_BY_EXTERNAL_DEPENDENCY | `src/execution/alpaca_broker.py`; no authenticated session attempted |
| Technical indicators / candles | EXISTING_UNVERIFIED | `src/data/technical_engine.py`; `test_technical_engine.py`, `test_candle_sessions.py` |
| Evidence and macro research | EXISTING_UNVERIFIED | `src/intelligence/research/`, `src/macro/feeds.py`; PIT/research tests |
| Walk forward / multiple testing | EXISTING_UNVERIFIED | `src/v5/walkforward.py`, `src/intelligence/walkforward.py`; corresponding tests |
| Strategy lifecycle and promotion | EXISTING_UNVERIFIED | `src/trading/lifecycle.py`; `test_trading_lifecycle.py` |
| Legacy aliases | DEPRECATED_OR_DUPLICATE | Redirects `frontend/src/App.jsx:204`; retained for compatibility |
| Canonical Decimal multiasset contracts | MISSING | Legacy `OrderSpec.qty` and risk arithmetic use floats |
| FTMO versioned confirmed profiles | MISSING | No provider profile engine found |
| Transactional approval outbox | MISSING | Existing journal is JSONL, not atomic approval consumption |
| MT5 read-only adapter | MISSING | No MetaTrader5 integration found |
| Third-party plugin sandbox / MCP gateway | MISSING | No trading extension registry found |
| Real FTMO demo execution validation | BLOCKED_BY_EXTERNAL_DEPENDENCY | Account not purchased/configured; no credentials used |
