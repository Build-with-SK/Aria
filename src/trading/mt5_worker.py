"""The MT5 terminal, in its own process, behind a read-only pipe (review CR-06).

`MetaTrader5` is a process-global module: once `initialize()` has run, any code
in that process can call `MetaTrader5.order_send`, whatever the adapter's own
method names say. So the SDK never enters ARIA's process. This module is the
only place allowed to import it (`tests/test_trading_structure.py` checks), and
it does so only inside `serve`, which runs in a child process.

The parent holds an `MT5ReadClient`: a pipe and a process handle, nothing else.
It can ask for the operations in `READ_OPERATIONS` and for health; the worker
refuses anything else before it reaches the SDK, and converts every result to
plain data, so no SDK object crosses the pipe. Errors cross as one of two kinds
with the worker's own message; SDK error text never does.

The worker connects only with an investor (read-only) login: the adapter
refuses an account whose `trade_allowed` is anything but False, and a terminal
with algorithmic trading on, at connect and before and after every read.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import importlib
import multiprocessing
from pathlib import Path
import sys
from types import SimpleNamespace

from .mt5_readonly import (MT5PermissionDenied, MT5ReadOnlyAdapter, MT5Unavailable,
                           READ_OPERATIONS, WRITE_OPERATIONS)

_SCALARS = (str, int, float, bool, type(None), datetime, Decimal)


def _plain(value):
    """SDK result -> plain data. Anything unrecognised is refused, not passed through."""
    if isinstance(value, _SCALARS):
        return value
    if hasattr(value, "_asdict"):                       # MT5 named tuples
        return {k: _plain(v) for k, v in value._asdict().items()}
    if isinstance(value, SimpleNamespace):              # test doubles
        return {k: _plain(v) for k, v in vars(value).items()}
    names = getattr(getattr(value, "dtype", None), "names", None)
    if names:                                           # numpy structured array (rates)
        return [dict(zip(names, row)) for row in value.tolist()]
    if hasattr(value, "tolist"):                        # numpy scalars and plain arrays
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    raise TypeError(f"unrecognised SDK result type {type(value).__name__}")


def _load_sdk(sdk_factory: str | None):
    if sdk_factory is None:
        # The terminal and its Python package are Windows-only; a Linux runtime
        # needs a separate Windows worker host (review OP-01, OPERATIONS.md).
        if sys.platform != "win32":
            raise MT5Unavailable("the MT5 worker needs a Windows host; this host cannot run the terminal")
        try:
            import MetaTrader5   # the only import of the SDK anywhere in ARIA
        except ImportError as exc:
            raise MT5Unavailable("the MetaTrader5 package is not installed in the worker environment") from exc
        return MetaTrader5
    module, _, name = sdk_factory.partition(":")
    return getattr(importlib.import_module(module), name)()


def serve(conn, sdk_factory: str | None, identity: dict) -> None:
    """Child-process entry point. Runs until the parent closes the pipe."""
    def failure(exc):
        if isinstance(exc, MT5PermissionDenied):
            return ("error", "denied", str(exc))
        if isinstance(exc, MT5Unavailable):
            return ("error", "unavailable", str(exc))
        return ("error", "unavailable", "MT5 worker failure")

    adapter = None
    try:
        adapter = MT5ReadOnlyAdapter(sdk=_load_sdk(sdk_factory))
        adapter.connect(**identity)
        conn.send(("ok", "connected"))
    except BaseException as exc:  # noqa: BLE001 - reported, then the worker exits
        conn.send(failure(exc))
        conn.close()
        return
    try:
        while True:
            try:
                message = conn.recv()
            except EOFError:
                break
            if message == ("close",):
                break
            try:
                if message == ("health",):
                    reply = ("ok", adapter.health())
                elif (isinstance(message, tuple) and len(message) == 4 and message[0] == "read"
                      and message[1] in READ_OPERATIONS):
                    _, operation, args, kwargs = message
                    reply = ("ok", _plain(adapter.read(operation, *args, **kwargs)))
                else:
                    reply = ("error", "denied", "only approved reads cross the worker pipe")
            except Exception as exc:  # noqa: BLE001
                reply = failure(exc)
            conn.send(reply)
    finally:
        adapter.disconnect()
        conn.close()


class MT5ReadClient:
    """ARIA's handle on the MT5 worker: reads and health only.

    `__slots__` keeps it to a pipe and a process; there is no SDK, adapter or
    terminal object here to reach around.
    """
    __slots__ = ("_conn", "_process", "_timeout")

    def __init__(self, conn, process, timeout: float):
        self._conn, self._process, self._timeout = conn, process, timeout

    @classmethod
    def start(cls, *, expected_login: int, expected_server: str, expected_currency: str,
              terminal_path: Path | None = None, sdk_factory: str | None = None,
              timeout: float = 30.0) -> "MT5ReadClient":
        """Spawn the worker and wait for it to connect, or raise why it could not."""
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        identity = dict(expected_login=expected_login, expected_server=expected_server,
                        expected_currency=expected_currency, terminal_path=terminal_path)
        process = context.Process(target=serve, args=(child, sdk_factory, identity),
                                  name="aria-mt5-read-worker", daemon=True)
        process.start()
        child.close()
        client = cls(parent, process, timeout)
        try:
            client._receive()
        except Exception:
            client.close()
            raise
        return client

    def _receive(self):
        if not self._conn.poll(self._timeout):
            self.close()
            raise MT5Unavailable("MT5 worker did not answer in time")
        try:
            reply = self._conn.recv()
        except EOFError as exc:
            raise MT5Unavailable("MT5 worker exited") from exc
        if reply[0] == "ok":
            return reply[1]
        _, kind, message = reply
        raise (MT5PermissionDenied if kind == "denied" else MT5Unavailable)(message)

    def read(self, operation: str, *args, **kwargs):
        if operation not in READ_OPERATIONS:
            raise MT5PermissionDenied("operation is not an approved read")
        self._conn.send(("read", operation, args, kwargs))
        return self._receive()

    def health(self) -> dict:
        self._conn.send(("health",))
        return self._receive()

    def close(self) -> None:
        try:
            self._conn.send(("close",))
        except (OSError, ValueError):
            pass
        self._process.join(timeout=5)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
        self._conn.close()

    def __getattr__(self, name):
        if name in WRITE_OPERATIONS:
            def refuse(*args, **kwargs):
                raise MT5PermissionDenied("stage 1 is read-only; all order mutations disabled")
            return refuse
        raise AttributeError(name)
