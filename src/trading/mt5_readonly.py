"""Official MetaTrader5 terminal IPC, stage 1: investor-login demo reads only.

This adapter never imports the SDK. It is handed one, and only by the MT5
worker process (`mt5_worker.py`), which is the one module allowed to import
`MetaTrader5` (review CR-06): the SDK is process-global, so read-only has to be
a process boundary, not a set of method names.

Each read rechecks the terminal's actual account identity, and that the login
is the investor (read-only) one: `account_info().trade_allowed` must be False
and the terminal's algorithmic trading must be off. An environment variable
cannot classify the account. No HTTP endpoint creates this adapter or reaches
the terminal.
"""
from datetime import datetime
from pathlib import Path
import threading


class MT5Unavailable(RuntimeError):
    pass


class MT5PermissionDenied(PermissionError):
    pass


READ_OPERATIONS = frozenset({
    "account_info", "terminal_info", "symbols_get", "symbol_info", "symbol_info_tick",
    "copy_rates_range", "positions_get", "orders_get", "history_orders_get",
    "history_deals_get", "order_calc_profit", "order_calc_margin", "order_check",
})
WRITE_OPERATIONS = frozenset({
    "order_send", "submit_order", "place_pending_order", "modify_order",
    "modify_stop_loss_take_profit", "cancel_order", "close_position",
})


class MT5ReadOnlyAdapter:
    name = "MT5_READ_ONLY"

    def __init__(self, *, sdk):
        self._sdk = sdk
        self._identity = None
        self._lock = threading.RLock()

    def connect(self, *, expected_login: int, expected_server: str,
                expected_currency: str, terminal_path: Path | None = None):
        """Explicit authorised local invocation only; no password discovery."""
        if expected_login <= 0 or not expected_server or len(expected_currency) != 3:
            raise MT5PermissionDenied("explicit demo account identity required")
        with self._lock:
            if self._identity is not None:
                raise MT5PermissionDenied("disconnect before changing account")
            if self._sdk is None:
                raise MT5Unavailable("the MetaTrader5 SDK is loaded only inside the MT5 worker process")
            args = (str(terminal_path),) if terminal_path is not None else ()
            try:
                if not self._sdk.initialize(*args, timeout=10000):
                    raise MT5Unavailable("terminal initialization failed")
                self._identity = (expected_login, expected_server, expected_currency)
                self._assert_identity()
            except Exception:
                self._identity = None
                self._sdk.shutdown()
                raise

    def _assert_identity(self):
        if self._identity is None:
            raise MT5PermissionDenied("adapter disconnected")
        account, terminal = self._sdk.account_info(), self._sdk.terminal_info()
        if account is None or terminal is None or not getattr(terminal, "connected", False):
            raise MT5Unavailable("account or terminal unavailable")
        actual = (account.login, account.server, account.currency)
        demo_mode = getattr(self._sdk, "ACCOUNT_TRADE_MODE_DEMO", None)
        if demo_mode is None or actual != self._identity or account.trade_mode != demo_mode:
            raise MT5PermissionDenied("connected account is not the authorised demo identity")
        # Investor login only (CR-06): an account that may trade, or a terminal
        # with algorithmic trading on, is refused even for reads.
        if getattr(account, "trade_allowed", None) is not False:
            raise MT5PermissionDenied("investor (read-only) login required; this login can trade")
        if getattr(terminal, "trade_allowed", None) is not False:
            raise MT5PermissionDenied("terminal algorithmic trading must be off")
        return account, terminal

    def disconnect(self):
        with self._lock:
            self._identity = None
            if self._sdk is not None:
                self._sdk.shutdown()

    def health(self) -> dict:
        with self._lock:
            try:
                account, terminal = self._assert_identity()
            except (MT5Unavailable, MT5PermissionDenied):
                return {"connected": False, "environment": "UNKNOWN", "execution": "DISABLED"}
            return {"connected": True, "environment": "DEMO", "execution": "DISABLED",
                    "terminal_trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
                    "account_trade_allowed": bool(getattr(account, "trade_allowed", False)),
                    "account_expert_allowed": bool(getattr(account, "trade_expert", False))}

    def capabilities(self) -> dict:
        return {"read_only": True, "supported": sorted(READ_OPERATIONS),
                "disabled": sorted(WRITE_OPERATIONS), "execution": "DISABLED"}

    def read(self, operation: str, *args, **kwargs):
        if operation not in READ_OPERATIONS:
            raise MT5PermissionDenied("operation is not an approved read")
        with self._lock:
            self._assert_identity()
            try:
                result = getattr(self._sdk, operation)(*args, **kwargs)
            except Exception as exc:
                # SDK errors can contain terminal/account information. No raw
                # exception text or last_error payload is passed to web clients.
                raise MT5Unavailable("terminal read failed") from exc
            if result is None:
                raise MT5Unavailable("terminal returned unavailable data")
            self._assert_identity()  # don't publish data after an account switch
            return result

    def bars(self, symbol: str, timeframe: int, start: datetime, end: datetime):
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("aware increasing UTC-compatible interval required")
        from datetime import timezone
        return self.read("copy_rates_range", symbol, timeframe,
                         start.astimezone(timezone.utc), end.astimezone(timezone.utc))

    def __getattr__(self, name):
        if name in WRITE_OPERATIONS:
            def refuse(*args, **kwargs):
                raise MT5PermissionDenied("stage 1 is read-only; all order mutations disabled")
            return refuse
        raise AttributeError(name)
