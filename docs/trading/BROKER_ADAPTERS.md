# Broker adapters

Keep `src/trading/broker.py:BrokerAdapter` and `src/execution/broker_base.py`
unchanged. Legacy Alpaca/IBKR types use equity/share conventions and are wrapped
by existing mode/paper gates. They are not silently substituted for FX-lot APIs.
IBKR code is real but no TWS session was verified by this work.

MT5 stage 1 is `src/trading/mt5_readonly.py`. Its explicit operation allowlist and
capability response avoid pretending every broker supports the same operation.
No generic method proxy or writable raw SDK object is returned by a web route.
SDK fixtures verify account change, missing data and investor access behaviour.

Future common transport contract must cover connect/disconnect, health, account,
instruments, quotes, bars, orders, positions/history, estimate/preflight, submit,
modify, cancel, close and reconcile with explicit UNSUPPORTED responses. Extending
the existing adapter is preferable to another broad incompatible base class.

## cTrader specification only

Use official app/account authentication, demo/live endpoint separation, token
scope, symbol IDs and broker/account identity. Translate ProtoOA/JSON messages
at a provider boundary, persist correlation/idempotency before send, reconcile
orders/deals/positions after timeout, and map volume units explicitly. No SDK,
OAuth tokens, account registration or send implementation is included.
[Official Open API](https://help.ctrader.com/open-api/).

All future stores/approvals must key by broker + account + environment; never join
IBKR, MT5 and cTrader balances on a generic currency or display name.
