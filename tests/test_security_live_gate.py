"""
tests/test_security_live_gate.py
================================
AUDIT FINDING 3 — "live-account auto-execute gate".

The claim: no code path — config flag, environment variable, API call, admin
override, or a desk routine nobody thinks of as "executing" — lets a LIVE
account place, change or cancel an order without a human approving it.

This file is written as an attacker's checklist rather than a feature test.
Each test is an attempt to get an order onto a live account, and passes only
when the attempt fails. The paths that are not the obvious one matter most:
bracket healing, trailing-stop replacement and stale-order cleanup all reach
the broker, and none of them look like "execution" from the outside.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution.broker_base import (AssetClass, OrderRequest,  # noqa: E402
                                       OrderSide, OrderStatus, OrderType)
from src.execution.live_guard import (PaperOnlyBroker, WRITE_METHODS,  # noqa: E402
                                      paper_confirmed, paper_status)


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeAlpacaClient:
    """Stands in for alpaca-py's TradingClient, whose _base_url is the only
    honest answer to "which account is this"."""
    def __init__(self, base_url):
        self._base_url = base_url


class FakeBroker:
    """Stands in for AlpacaBroker — and must declare what the gate requires of
    a real adapter, or it is not standing in for one. An adapter that declares
    no PAPER_ENV_VAR now fails closed, deliberately: a third broker used to
    inherit ALPACA_PAPER by falling through a name check."""
    name = "Alpaca"
    PAPER_ENV_VAR = "ALPACA_PAPER"

    def __init__(self, paper=True, endpoint="https://paper-api.alpaca.markets"):
        self._paper = paper
        self._client = FakeAlpacaClient(endpoint) if endpoint else None
        self.submitted = []
        self.cancelled = []

    @property
    def paper(self):
        return self._paper

    def is_connected(self):
        return True

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def submit_order(self, req):
        from src.execution.broker_base import OrderResult
        self.submitted.append(req)
        return OrderResult(broker_order_id="live-1", status=OrderStatus.FILLED,
                           filled_qty=req.qty, avg_fill_price=10.0)

    def submit_oco_exit(self, ticker, qty, side, stop, target):
        self.submitted.append(("oco", ticker, qty))
        return "oco-1"

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        return True


LIVE = "https://api.alpaca.markets"
PAPER = "https://paper-api.alpaca.markets"


def an_order():
    return OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=10,
                        order_type=OrderType.MARKET,
                        asset_class=AssetClass.EQUITY)


@pytest.fixture
def isolated_queue(tmp_path, monkeypatch):
    """Blocked orders get pushed to the approval queue — never the real one."""
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    return aq


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER", raising=False)


# ── what "confirmed paper" means ─────────────────────────────────────────────

def test_env_var_alone_does_not_confirm_paper(monkeypatch):
    """The operator saying "paper" does not make the account paper."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    assert paper_confirmed(FakeBroker(paper=False, endpoint=LIVE)) is False


def test_broker_flag_alone_does_not_confirm_paper(monkeypatch):
    """And the adapter's flag does not either — it is derived from the same
    environment variable, which is why "checked in two places" was one check."""
    monkeypatch.setenv("ALPACA_PAPER", "false")
    assert paper_confirmed(FakeBroker(paper=True, endpoint=PAPER)) is False


def test_live_endpoint_overrides_a_paper_flag(monkeypatch):
    """A broker mislabelled paper while pointed at the live endpoint is live.
    This is the check that cannot be talked out of by configuration."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    st = paper_status(FakeBroker(paper=True, endpoint=LIVE))
    assert st["endpoint_is_paper"] is False
    assert st["confirmed_paper"] is False


def test_all_three_agreeing_confirms_paper(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "true")
    assert paper_confirmed(FakeBroker(paper=True, endpoint=PAPER)) is True


@pytest.mark.parametrize("value", ["True", "TRUE", "1", "yes", "", "  true  ",
                                   "false", "0", "no"])
def test_no_spelling_of_the_env_var_other_than_true_confirms(monkeypatch, value):
    """Case, whitespace and truthy-looking values are all rejected. A gate that
    accepts "True" accepts a typo as authorisation."""
    monkeypatch.setenv("ALPACA_PAPER", value)
    assert paper_confirmed(FakeBroker(paper=True, endpoint=PAPER)) is False


def test_unknown_endpoint_does_not_confirm_on_its_own(monkeypatch):
    """An endpoint that cannot be read abstains — it does not vote yes."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    st = paper_status(FakeBroker(paper=True, endpoint=None))
    assert st["endpoint_is_paper"] is None
    assert st["confirmed_paper"] is True        # env + flag still had to pass
    monkeypatch.setenv("ALPACA_PAPER", "false")
    assert paper_confirmed(FakeBroker(paper=True, endpoint=None)) is False


# ── the wrapper refuses every write on a live account ────────────────────────

def test_live_entry_is_refused_and_queued(monkeypatch, isolated_queue):
    monkeypatch.setenv("ALPACA_PAPER", "true")
    broker = FakeBroker(paper=False, endpoint=LIVE)
    guarded = PaperOnlyBroker(broker)

    result = guarded.submit_order(an_order())

    assert result.status == OrderStatus.REJECTED
    assert broker.submitted == [], "a live order reached the broker"
    # The human is told, not ignored.
    pending = isolated_queue.ApprovalQueue().get_pending()
    assert len(pending) == 1 and pending[0].ticker == "AAPL"


def test_live_cancel_is_refused(monkeypatch):
    """Stale-order cleanup cancels real orders. On a live account that is a
    human decision, and a cancelled protective stop is a naked position."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    broker = FakeBroker(paper=False, endpoint=LIVE)
    assert PaperOnlyBroker(broker).cancel_order("abc") is False
    assert broker.cancelled == []


def test_live_oco_exit_is_refused(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "true")
    broker = FakeBroker(paper=False, endpoint=LIVE)
    assert PaperOnlyBroker(broker).submit_oco_exit("AAPL", 10, "buy", 9, 12) is None
    assert broker.submitted == []


def test_reads_still_pass_through_on_a_live_account(monkeypatch):
    """The desk must still be able to SEE a live account — refusing to read
    would leave existing positions unwatched, which helps nobody."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    guarded = PaperOnlyBroker(FakeBroker(paper=False, endpoint=LIVE))
    assert guarded.is_connected() is True
    assert guarded.get_positions() == []
    assert guarded.name == "Alpaca"


def test_paper_writes_are_untouched(monkeypatch):
    """The gate must not break the thing it is guarding."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    broker = FakeBroker(paper=True, endpoint=PAPER)
    r = PaperOnlyBroker(broker).submit_order(an_order())
    assert r.status == OrderStatus.FILLED
    assert len(broker.submitted) == 1


def test_an_unknown_method_is_refused_not_waved_through(monkeypatch):
    """The gate fails CLOSED. A method nobody has written yet — `modify_order`,
    `flatten`, `amend` — must not reach a live broker just because it is not on
    a hand-maintained list of writes. This is the hole an independent review
    found in the first version, where the allowlist was the writes."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    broker = FakeBroker(paper=False, endpoint=LIVE)
    broker.modify_order = lambda oid, **kw: broker.submitted.append(("modify", oid))
    broker.flatten_everything = lambda: broker.submitted.append("flatten")
    guarded = PaperOnlyBroker(broker)

    for method in ("modify_order", "flatten_everything"):
        with pytest.raises(Exception) as e:
            getattr(guarded, method)("x")
        assert "paper-only" in str(e.value)
    assert broker.submitted == []


def test_unknown_methods_still_work_on_a_paper_account(monkeypatch):
    """Failing closed must not mean failing always."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    broker = FakeBroker(paper=True, endpoint=PAPER)
    broker.modify_order = lambda oid: f"modified {oid}"
    assert PaperOnlyBroker(broker).modify_order("abc") == "modified abc"


def test_every_public_adapter_member_is_classified():
    """Each public member of every adapter must be explicitly a read or a
    write. Not a naming heuristic — that is how `last_closed_fill` slipped
    through: it reads the fill history, matched no get_/is_/supports_ prefix,
    and so was treated as a write. The refusal was then swallowed by a broad
    except in the exit path, which fell back to an ESTIMATED exit price and
    wrote invented P&L into the ledger the track record is built on.

    A silent misclassification is worse than either answer, so the rule is that
    there is no unclassified member.
    """
    from src.execution import alpaca_broker, ibkr_broker
    from src.execution.live_guard import READ_METHODS
    unclassified = []
    for adapter in (alpaca_broker.AlpacaBroker, ibkr_broker.IBKRBroker):
        for attr in dir(adapter):
            if attr.startswith("_"):
                continue
            if attr in READ_METHODS or attr in WRITE_METHODS:
                continue
            unclassified.append(f"{adapter.__name__}.{attr}")
    assert not unclassified, (
        f"adapter members classified as neither read nor write: {unclassified}. "
        f"Add each to READ_METHODS or WRITE_METHODS in src/execution/live_guard.py "
        f"— an unclassified read is refused and may be swallowed by a caller's "
        f"except block; an unclassified write is the hole this gate exists to close.")


def test_last_closed_fill_is_readable_through_the_gate(monkeypatch):
    """The concrete case, named so a regression names itself."""
    monkeypatch.setenv("ALPACA_PAPER", "wrong-case-so-not-confirmed")
    broker = FakeBroker(paper=True, endpoint=PAPER)
    broker.last_closed_fill = lambda ticker, side: {"price": 101.5}
    guarded = PaperOnlyBroker(broker)
    # Not a confirmed paper account, yet a READ must still answer — otherwise
    # the exit path silently records an estimate as a realised price.
    assert paper_confirmed(broker) is False
    assert guarded.last_closed_fill("AAPL", "sell") == {"price": 101.5}


def test_the_gate_will_not_hand_out_a_broker_client(monkeypatch):
    """`_client` is an alpaca-py TradingClient and `_ib` is an ib_insync.IB —
    objects, not callables, with live order methods on them. Passing them
    through as "non-callables cannot place an order" made
    `mgr._alpaca._client.submit_order(...)` a complete bypass."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    guarded = PaperOnlyBroker(FakeBroker(paper=False, endpoint=LIVE))
    with pytest.raises(Exception) as e:
        _ = guarded._client
    assert "bypass" in str(e.value)


def test_inert_values_still_pass_through(monkeypatch):
    """Refusing objects must not refuse the numbers and strings a status report
    is made of."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    broker = FakeBroker(paper=False, endpoint=LIVE)
    broker._port = 7497
    broker._label = "something"
    guarded = PaperOnlyBroker(broker)
    assert guarded._port == 7497
    assert guarded._label == "something"


def test_the_wrapped_broker_is_not_a_public_attribute(monkeypatch):
    """`wrapped` was removed and `_broker` left in its place — a shorter unwrap
    than the accessor that was deleted."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    raw = FakeBroker(paper=False, endpoint=LIVE)
    guarded = PaperOnlyBroker(raw)
    for name in ("wrapped", "_broker", "broker"):
        assert getattr(guarded, name, None) is not raw, (
            f"PaperOnlyBroker.{name} hands back the unwrapped broker")


def test_paper_status_works_through_the_wrapper(monkeypatch):
    """Refusing to hand out `_client` must not break the gate's own reporting,
    which reads exactly that attribute."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    raw = FakeBroker(paper=True, endpoint=PAPER)
    assert paper_status(PaperOnlyBroker(raw)) == paper_status(raw)
    assert paper_confirmed(PaperOnlyBroker(raw)) is True


def test_write_methods_are_all_outside_the_read_allowlist():
    from src.execution.live_guard import READ_METHODS
    assert not (WRITE_METHODS & READ_METHODS)


def test_no_phantom_entries_in_the_write_list():
    """Every declared write must exist on an adapter.

    The classification test passes if a member is in EITHER set, so an
    aspirational write list is a place to file a new read by mistake — which
    silently refuses it, which a caller's broad except then swallows. Four
    entries here existed on neither adapter."""
    from src.execution import alpaca_broker, ibkr_broker
    real = set(dir(alpaca_broker.AlpacaBroker)) | set(dir(ibkr_broker.IBKRBroker))
    phantom = sorted(WRITE_METHODS - real)
    assert not phantom, f"WRITE_METHODS entries that exist on no adapter: {phantom}"


# ── the IBKR blind spot ──────────────────────────────────────────────────────

class FakeIBKR:
    """Shaped like IBKRBroker: no _client, a _port, its own env var, and its
    own endpoint answer derived from that port."""
    name = "IBKR"
    PAPER_ENV_VAR = "IBKR_PAPER"

    def __init__(self, port):
        self._port = port

    def endpoint_is_paper(self):
        from src.execution.ibkr_broker import LIVE_PORTS, PAPER_PORTS
        if self._port in LIVE_PORTS:
            return False
        if self._port in PAPER_PORTS:
            return True
        return None

    @property
    def paper(self):
        import os
        return os.getenv("IBKR_PAPER", "true").lower() != "false"


def test_a_live_ibkr_port_is_not_confirmed_paper(monkeypatch):
    """The gate certified a LIVE IBKR account as confirmed paper: it read
    ALPACA_PAPER (a different broker's variable) and could not read an endpoint
    it did not recognise, and an unreadable endpoint abstains. Port 7496 is
    real money whatever any flag says."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("IBKR_PAPER", "true")
    st = paper_status(FakeIBKR(port=7496))
    assert st["endpoint_is_paper"] is False
    assert st["confirmed_paper"] is False


def test_the_ibkr_paper_port_is_confirmed(monkeypatch):
    monkeypatch.setenv("IBKR_PAPER", "true")
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    st = paper_status(FakeIBKR(port=7497))
    assert st["endpoint_is_paper"] is True
    assert st["confirmed_paper"] is True


def test_ibkr_is_graded_on_its_own_env_var(monkeypatch):
    """ALPACA_PAPER=true said nothing about an IBKR account, but was being
    counted as if it did."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("IBKR_PAPER", "false")
    assert paper_confirmed(FakeIBKR(port=7497)) is False


def test_an_unreadable_endpoint_is_reported_as_unchecked(monkeypatch):
    """"The endpoint says paper" and "nobody could tell" must be
    distinguishable in the report, even though both currently abstain."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    st = paper_status(FakeBroker(paper=True, endpoint=None))
    assert st["endpoint_checked"] is False
    st2 = paper_status(FakeBroker(paper=True, endpoint=PAPER))
    assert st2["endpoint_checked"] is True


# ── the desk's own paths ─────────────────────────────────────────────────────

def test_the_desk_only_ever_gets_a_wrapped_broker(monkeypatch):
    """The structural claim: autonomous code cannot obtain a raw broker,
    so it cannot reach around the gate."""
    import src.desk.auto_executor as ae
    monkeypatch.setattr(ae, "_order_manager", None)

    class FakeAlpacaBroker(FakeBroker):
        def __init__(self):
            super().__init__(paper=False, endpoint=LIVE)

    monkeypatch.setattr("src.execution.alpaca_broker.AlpacaBroker",
                        FakeAlpacaBroker)
    mgr = ae.get_order_manager(rebuild=True)
    assert isinstance(mgr._alpaca, PaperOnlyBroker)


def test_bracket_healing_cannot_place_live_orders(monkeypatch, isolated_queue):
    """The path the audit missed. Healing a missing stop submits a real GTC
    order; on a live account it must be refused like any other write."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    from src.execution.order_manager import OrderManager
    broker = FakeBroker(paper=False, endpoint=LIVE)
    mgr = OrderManager(alpaca=PaperOnlyBroker(broker))

    out = mgr.place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)

    assert broker.submitted == [], "bracket healing placed live orders"
    assert out["stop_loss_order"] is None and out["take_profit_order"] is None


def test_stale_order_cleanup_cannot_cancel_live_orders(monkeypatch):
    """Cleanup cancels orders it judges stale. Not on real money it doesn't."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    from src.execution.order_manager import OrderManager
    broker = FakeBroker(paper=False, endpoint=LIVE)
    broker.get_open_orders = lambda: [{
        "id": "old-1", "ticker": "AAPL", "side": "buy", "qty": 10,
        "order_type": "limit", "submitted_at": "2020-01-01T00:00:00",
        "status": "new"}]
    mgr = OrderManager(alpaca=PaperOnlyBroker(broker))

    cancelled = mgr.cancel_stale_orders(max_age_days=1.0)

    assert cancelled == []
    assert broker.cancelled == []


def test_auto_execute_gate_is_closed_without_a_confirmed_paper_account(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "true")
    from src.desk.auto_executor import AutoExecutor
    ex = AutoExecutor({"auto_execute": True, "daily_notional_budget": 10000.0,
                       "max_trades_per_day": 10})
    gates = ex.gates({"equity": 100000.0, "connected": True, "paper": False,
                      "paper_confirmed": False})
    assert gates["auto_allowed"] is False
    assert "LIVE account" in ex._gate_reason(gates)


def test_arming_auto_execute_cannot_be_forced_by_config(monkeypatch):
    """The config flag is a kill switch, never an authorisation. Setting it by
    hand in desk_config.json must not open the gate on a live account."""
    monkeypatch.setenv("ALPACA_PAPER", "true")
    from src.desk.auto_executor import AutoExecutor
    ex = AutoExecutor({"auto_execute": True, "daily_notional_budget": 10000.0,
                       "max_trades_per_day": 10})
    for account in (
        {"equity": 1e5, "connected": True, "paper": False, "paper_confirmed": False},
        {"equity": 1e5, "connected": True, "paper": None, "paper_confirmed": None},
        {"equity": 1e5, "connected": False, "paper": True, "paper_confirmed": True},
        {"equity": 1e5, "connected": True, "paper": True},   # key absent entirely
    ):
        assert ex.gates(account)["auto_allowed"] is False, account


def test_desk_config_endpoint_cannot_set_auto_execute():
    """PATCH /api/desk/config drops auto_execute, so the arming route with its
    live-account refusal is the only way to arm."""
    import inspect
    from backend import main
    src = inspect.getsource(main.desk_config)
    assert 'updates.pop("auto_execute", None)' in src


def test_arming_route_refuses_a_live_account(monkeypatch):
    """POST /api/desk/auto-execute?enabled=true on a live account → 409."""
    from fastapi import HTTPException
    from backend import main
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setattr("src.desk.auto_executor.account_snapshot",
                        lambda: {"connected": True, "paper": False,
                                 "paper_confirmed": False, "equity": 1e5})
    with pytest.raises(HTTPException) as e:
        main.desk_auto_execute(enabled=True)
    assert e.value.status_code == 409
    assert "LIVE" in str(e.value.detail)


def test_arming_route_refuses_a_bad_env_var(monkeypatch):
    from fastapi import HTTPException
    from backend import main
    monkeypatch.setenv("ALPACA_PAPER", "True")          # wrong case on purpose
    with pytest.raises(HTTPException) as e:
        main.desk_auto_execute(enabled=True)
    assert e.value.status_code == 409


def test_disarming_is_always_allowed(monkeypatch, tmp_path):
    """A kill switch that can jam is not a kill switch."""
    from backend import main
    from src.desk import config as desk_config
    monkeypatch.setattr(desk_config, "CONFIG_FILE", tmp_path / "desk_config.json")
    monkeypatch.setenv("ALPACA_PAPER", "nonsense")
    out = main.desk_auto_execute(enabled=False)
    assert out["auto_execute"] is False


def test_position_manager_queues_live_exits_instead_of_firing_them():
    """The exit engine's own live check, still present and still first — and
    using the SAME gate as the broker wrapper. It used to check the env var and
    the adapter flag only, which is a weaker test than the one the wrapper
    applies: a second layer that passes what the first would refuse is not
    defence in depth, it is a disagreement waiting to be resolved wrongly."""
    import inspect
    from src.desk import position_manager
    src = inspect.getsource(position_manager.PositionManager._close)
    assert "_queue_manual_exit" in src
    assert "paper_confirmed" in src


APPROVE = "approve_and_execute"


def _references(tree, target: str) -> list[int]:
    """Line numbers where `target` is USED — called, aliased, or passed.

    A `def target(...)` is not a reference: defining the human's entry point
    is the point. Everything else that names it is.
    """
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == target and \
                isinstance(node.ctx, ast.Load):
            lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == target and \
                isinstance(node.ctx, ast.Load):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_nothing_autonomous_calls_approve_and_execute():
    """The project's hard rule: the brain proposes, the human approves.

    Read the CODE, not the text of the file. This check used to `git grep` for
    the name and excuse any line containing `#` or `\"\"\"` — a line-shaped
    heuristic that cannot tell a docstring's body from a statement. It had
    already been patched once with a literal exemption for the exact phrasing
    in `src/brain/cognitive/executor.py`, and it went red the moment another
    module's docstring said the same thing in different words.

    That is the dangerous kind of failing test: it fails for a reason that is
    not the invariant, and the quickest way to make it green is to delete the
    assertion. Parsing removes the pressure — and it is STRICTLY STRONGER than
    the grep, which would have waved through a real call written as
    `approve_and_execute(t)  # noqa` simply because the line held a `#`.
    """
    root = Path(__file__).parent.parent
    offenders = []
    for path in sorted([*(root / "src").rglob("*.py"),
                        *(root / "backend").rglob("*.py")]):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:                     # a file mid-edit is not a caller
            continue
        for lineno in _references(tree, APPROVE):
            offenders.append(f"{path.relative_to(root).as_posix()}:{lineno}")

    assert not offenders, (
        "the approval step is reachable from code, not only from the human: "
        + ", ".join(offenders))


def test_the_approval_guard_catches_a_real_caller(tmp_path):
    """The guard above passes today. This proves it passes because nothing
    calls the approval step, and not because it stopped looking."""
    for source in (f"{APPROVE}(trade_id)",
                   f"x = {APPROVE}  # noqa",
                   f"desk.{APPROVE}(t)",
                   f"schedule(callback={APPROVE})"):
        assert _references(ast.parse(source), APPROVE), \
            f"a real caller slipped past the guard: {source!r}"

    # ...and prose about it is not a caller.
    prose = f'"""Nothing here calls {APPROVE}; every trade waits."""\nx = 1\n'
    assert _references(ast.parse(prose), APPROVE) == []
    # ...nor is defining the endpoint itself.
    assert _references(ast.parse(f"def {APPROVE}(trade_id):\n    pass\n"), APPROVE) == []
