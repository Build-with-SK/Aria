from datetime import timedelta
from types import SimpleNamespace
import pytest

from src.trading.mt5_readonly import MT5ReadOnlyAdapter, MT5PermissionDenied, MT5Unavailable, WRITE_OPERATIONS
from src.data.provider_contracts import ReadPolicy, ReadProvider, ProviderUnavailable
from src.trading.extension_policy import plugin_activation, MCPServerPolicy, mcp_tool_allowed
from src.trading.contracts import PluginManifest
from test_multiasset_risk import NOW


class SDK:
    ACCOUNT_TRADE_MODE_DEMO=0
    def __init__(self):
        self.account=SimpleNamespace(login=123,server="fixture-demo",currency="USD",trade_mode=0,trade_allowed=False,trade_expert=False)
        self.terminal=SimpleNamespace(connected=True,trade_allowed=False)
        self.sent=0
        self.stopped=0
    def initialize(self,*a,**kw): return True
    def shutdown(self): self.stopped+=1
    def account_info(self): return self.account
    def terminal_info(self): return self.terminal
    def symbols_get(self): return ()
    def symbol_info_tick(self,*a): return None
    def copy_rates_range(self,*a): return a
    def order_send(self,*a,**kw): self.sent+=1


def connected():
    sdk=SDK()
    adapter=MT5ReadOnlyAdapter(sdk=sdk)
    adapter.connect(expected_login=123,expected_server="fixture-demo",expected_currency="USD")
    return adapter,sdk


def test_demo_reads_allow_investor_access_without_upgrading():
    a,s=connected()
    assert a.health()["environment"]=="DEMO"
    assert a.health()["execution"]=="DISABLED"
    assert a.health()["account_trade_allowed"] is False
    assert a.read("symbols_get")==()
    a.disconnect()
    assert a.health()["connected"] is False and s.stopped==1


@pytest.mark.parametrize("op",sorted(WRITE_OPERATIONS))
def test_all_mutations_block_even_with_environment_flags(monkeypatch,op):
    monkeypatch.setenv("ALLOW_LIVE_EXECUTION","true")
    monkeypatch.setenv("TRADING_EXECUTION_ENABLED","true")
    a,s=connected()
    with pytest.raises(MT5PermissionDenied): getattr(a,op)({"action":"fake"})
    with pytest.raises(MT5PermissionDenied): a.read(op,{})
    assert s.sent==0


@pytest.mark.parametrize("field,value",[("login",456),("server","other"),("currency","EUR"),("trade_mode",2)])
def test_account_switch_blocks_each_read(field,value):
    a,s=connected()
    setattr(s.account,field,value)
    with pytest.raises(MT5PermissionDenied): a.read("symbols_get")
    assert a.health()["connected"] is False


def test_disconnect_and_missing_tick_are_unknown():
    a,s=connected()
    with pytest.raises(MT5Unavailable): a.read("symbol_info_tick","EURUSD")
    s.terminal.connected=False
    with pytest.raises(MT5Unavailable): a.read("symbols_get")


def test_initial_live_account_is_refused_and_shutdown():
    s=SDK();s.account.trade_mode=2
    a=MT5ReadOnlyAdapter(sdk=s)
    with pytest.raises(MT5PermissionDenied):
        a.connect(expected_login=123,expected_server="fixture-demo",expected_currency="USD")
    assert s.stopped==1


def test_bar_dates_are_aware():
    a,_=connected()
    with pytest.raises(ValueError): a.bars("EURUSD",1,NOW.replace(tzinfo=None),NOW)
    args=a.bars("EURUSD",1,NOW-timedelta(days=1),NOW)
    assert args[2].utcoffset().total_seconds()==0


class Clock:
    def __init__(self): self.t=0
    def now(self): return self.t
    def sleep(self,s): self.t+=s


def test_bounded_retry_cache_and_no_executable_quote_substitution():
    clock=Clock();calls=[]
    def transport(key):
        calls.append(key)
        if len(calls)<3: raise TimeoutError()
        return "fixture"
    reader=ReadProvider(transport,str,clock=clock.now,sleep=clock.sleep)
    assert reader.read("one")=="fixture" and len(calls)==3
    assert reader.read("one")=="fixture" and len(calls)==3
    with pytest.raises(ProviderUnavailable): reader.read("one",executable=True)
    clock.sleep(6)
    reader.read("one")
    assert len(calls)==4


def test_schema_errors_are_not_retried():
    calls=[]
    def transport(key): calls.append(key);return None
    def validate(raw): raise ValueError("bad schema")
    reader=ReadProvider(transport,validate)
    with pytest.raises(ValueError): reader.read("fixture")
    assert len(calls)==1


def test_third_party_plugins_cannot_activate():
    manifest=PluginManifest(name="fixture",version="1",api_version="1",entrypoint="evil:load",
        capabilities=("analysis",),requested_permissions=("RUN_ANALYTICS","EXECUTE_DEMO_TRADE"),
        required_secret_names=(),dependency_constraints=(),resource_limits=(),network_scope=(),
        health_check="ping",environments=("test",))
    result=plugin_activation(manifest,explicitly_approved=True)
    assert not result["active"] and not result["execution_privileges"]
    assert "PRIVILEGED_PERMISSION_DENIED" in result["reasons"]


def test_mcp_default_deny_and_revocation():
    p=MCPServerPolicy(server_id="fixture",api_version="1",allowed_tools=("prices",),permissions=("READ_MARKET_DATA",),
                      max_concurrency=1,timeout_seconds=1,active=True)
    assert mcp_tool_allowed(p,tool="prices",permission="READ_MARKET_DATA")
    assert not mcp_tool_allowed(p,tool="order_send",permission="EXECUTE_DEMO_TRADE")
    assert not mcp_tool_allowed(p.model_copy(update={"revoked":True}),tool="prices",permission="READ_MARKET_DATA")
