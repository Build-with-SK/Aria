# MT5 setup and current boundary

Status: read-only worker implemented; genuine connection **BLOCKED**. No SDK was
installed and no terminal/account session was opened. CI uses an injected fake SDK
(`tests/mt5_fakes.py`) inside a real spawned worker process.

**Read-only is a process boundary (review CR-06).** The `MetaTrader5` module is
process-global: once a process has called `initialize()`, any code in it can
call `MetaTrader5.order_send`, whatever an adapter's method names say. So:

- `src/trading/mt5_worker.py` is the only module that imports `MetaTrader5`, and
  only inside `serve()`, which runs in a separate spawned process
  (`aria-mt5-read-worker`). A structure test fails the build if any other file
  imports the SDK or builds the adapter.
- ARIA holds an `MT5ReadClient`: a pipe and a process handle (`__slots__`), no
  SDK object. It may ask for health and the allowlisted reads below; the worker
  refuses any other message before the SDK sees it, and returns plain data only.
- The worker connects with the **investor (read-only) password** only. It
  refuses an account whose `account_info().trade_allowed` is anything but
  `False`, and a terminal whose `terminal_info().trade_allowed` (algorithmic
  trading) is anything but `False`, at connect and before and after every read.
  A master (trading) password is therefore refused even for reads.

The official Python package talks to a local terminal by IPC, not a standalone
REST broker. [Official reference](https://www.mql5.com/en/docs/python_metatrader5).

Future operator steps, after independent review:

1. On an authorised Windows machine, install the official terminal and optional
   `MetaTrader5` package in a dedicated worker environment. Record versions.
2. The owner manually configures an authorised demo login. Do not reuse a login
   discovered in a repo, auto-register an account or purchase anything.
3. Log the terminal in with the account's **investor** password and turn the
   terminal's "Algo Trading" off. Then call
   `MT5ReadClient.start(expected_login=..., expected_server=..., expected_currency=...)`
   with the authorised demo identity. The worker verifies account trade mode,
   exact server/login/currency and both `trade_allowed` flags before and after
   every read.
4. Record terminal/account permissions and authorised read-only account evidence.
   Treat None responses as unavailable.
5. Capture symbol specs, quotes, positions, orders, history and broker profit/margin
   calculations without orders. Approve identity/spec mappings independently.

Supported allowlist: account_info, terminal_info, symbols_get, symbol_info,
symbol_info_tick, copy_rates_range, positions_get, orders_get, history_orders_get,
history_deals_get, order_calc_profit, order_calc_margin and order_check.
All order_send/submit/pending/modify/stop/cancel/close methods are disabled.
Fill policy, stop/freeze levels, retcodes and order translation remain a future
demo-worker task, not a pretend implementation hidden behind a flag.

The worker is a child process of whatever starts it, on the same Windows
machine as the terminal; no network listener exists and none is deployed. The
active ARIA runtime is a Linux ARM host, which cannot run the terminal (review
OP-01; see OPERATIONS.md). Before stage 2, implement private
authenticated IPC, independent authorisation, send-time revalidation and durable
broker reconciliation; never expose it publicly. Gate 4 needs actual read evidence;
Gate 5 additionally needs explicit per-order approval and actual broker response.
