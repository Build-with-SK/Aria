# MT5 setup and current boundary

Status: read-only adapter implemented; genuine connection **BLOCKED**. No SDK was
installed and no terminal/account session was opened. Optional package import is
lazy; missing SDK/non-Windows hosts fail clearly. CI uses an injected SDK fixture.

The official Python package talks to a local terminal by IPC, not a standalone
REST broker. [Official reference](https://www.mql5.com/en/docs/python_metatrader5).

Future operator steps, after independent review:

1. On an authorised Windows machine, install the official terminal and optional
   `MetaTrader5` package in a dedicated worker environment. Record versions.
2. The owner manually configures an authorised demo login. Do not reuse a login
   discovered in a repo, auto-register an account or purchase anything.
3. Invoke `connect(expected_login=..., expected_server=..., expected_currency=...)`
   with actual authorised demo identity. The adapter verifies account trade mode
   and exact server/login/currency before and after every read.
4. Record terminal/account permissions and authorised read-only account evidence.
   Investor/read-only settings stay read-only. Treat None responses as unavailable.
5. Capture symbol specs, quotes, positions, orders, history and broker profit/margin
   calculations without orders. Approve identity/spec mappings independently.

Supported allowlist: account_info, terminal_info, symbols_get, symbol_info,
symbol_info_tick, copy_rates_range, positions_get, orders_get, history_orders_get,
history_deals_get, order_calc_profit, order_calc_margin and order_check.
All order_send/submit/pending/modify/stop/cancel/close methods are disabled.
Fill policy, stop/freeze levels, retcodes and order translation remain a future
demo-worker task, not a pretend implementation hidden behind a flag.

No worker service/IPC listener is deployed. Before stage 2, implement private
authenticated IPC, independent authorisation, send-time revalidation and durable
broker reconciliation; never expose it publicly. Gate 4 needs actual read evidence;
Gate 5 additionally needs explicit per-order approval and actual broker response.
