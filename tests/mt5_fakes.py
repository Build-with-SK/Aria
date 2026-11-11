"""MetaTrader5 test doubles that the MT5 worker loads in its child process.

Kept apart from the test modules so the spawned worker imports only this.
"""
from types import SimpleNamespace


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self, *, trade_allowed=False, terminal_trade=False):
        self.account = SimpleNamespace(login=123, server="fixture-demo", currency="USD", trade_mode=0,
                                       trade_allowed=trade_allowed, trade_expert=False)
        self.terminal = SimpleNamespace(connected=True, trade_allowed=terminal_trade)

    def initialize(self, *args, **kwargs):
        return True

    def shutdown(self):
        pass

    def account_info(self):
        return self.account

    def terminal_info(self):
        return self.terminal

    def symbols_get(self):
        return (SimpleNamespace(name="EURUSD", digits=5),)

    def positions_get(self):
        return ()

    def order_send(self, *args, **kwargs):
        raise AssertionError("order_send reached the SDK")


def investor():
    return FakeMT5()


def trading_login():
    return FakeMT5(trade_allowed=True)


def algo_terminal():
    return FakeMT5(terminal_trade=True)
