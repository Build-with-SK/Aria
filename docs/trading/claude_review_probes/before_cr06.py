"""Run inside the 7385130 extraction: reproduce CR-06 as reviewed."""
import json, sys
from types import SimpleNamespace
sys.path.insert(0, ".")
from src.trading.mt5_readonly import MT5ReadOnlyAdapter

class SDK:
    ACCOUNT_TRADE_MODE_DEMO = 0
    def __init__(self):
        self.account = SimpleNamespace(login=123, server="fixture-demo", currency="USD", trade_mode=0,
                                       trade_allowed=True, trade_expert=True)
        self.terminal = SimpleNamespace(connected=True, trade_allowed=True)
        self.sent = 0
    def initialize(self, *a, **k): return True
    def shutdown(self): pass
    def account_info(self): return self.account
    def terminal_info(self): return self.terminal
    def order_send(self, *a, **k): self.sent += 1; return "sent"

sdk = SDK(); a = MT5ReadOnlyAdapter(sdk=sdk)
out = {}
a.connect(expected_login=123, expected_server="fixture-demo", expected_currency="USD")
out["trade_enabled_login_and_algo_terminal_connected"] = a.health()["connected"]
try:
    a.order_send({}); out["adapter_order_send"] = "allowed"
except Exception as e:
    out["adapter_order_send"] = type(e).__name__
out["raw_sdk_order_send_through_adapter._sdk"] = a._sdk.order_send({"action": 1})
out["sdk_order_send_calls"] = sdk.sent
print(json.dumps(out, indent=1))
