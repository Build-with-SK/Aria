"""
tests/test_reduce_only.py
=========================
Every order that may bypass the normal entry gates — protective stops and
targets, OCO exits, the exit engine's close, queued manual exits, the
kill-switch flatten — must satisfy the reduce-only invariant at the moment it
is sent (src/execution/reduce_only):

    |position after every resting reducing order and this one fill|
        <= |position now|, and the sign never flips.

Found and fixed while writing these (2026-10-07):

* The exit engine sized its market exit from the snapshot the tick began
  with; a stop that filled in between turned the "exit" into a new position.
* Brackets were placed as a full-size STOP plus a full-size LIMIT at any
  broker without OCO — both can fill and reverse the position.
* A queued manual exit became an ordinary order: approved after the
  position had gone, it would have opened a short.
* The halt cancelled EVERY resting order in production — `held` was never
  passed, so no order looked protective — stripping positions of their stops
  under the PRESERVE policy that promises to keep them.
* FLATTEN sold the whole position even with protective sells still resting.

The fourteenth and fifteenth cases document what is deliberately NOT gated
(a halt outside the session; a resting stop during UNKNOWN) and prove those
paths still cannot increase exposure.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from src.execution import reduce_only as RO                                   # noqa: E402
from src.execution.broker_base import (AssetClass, OrderRequest, OrderResult,  # noqa: E402
                                       OrderSide, OrderStatus, OrderType)

UTC = timezone.utc
TUE_OPEN = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_approval_queue(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")


# ── a broker that keeps a book ──────────────────────────────────────────────

class _Book:
    """A legacy-interface broker with real holdings and resting orders.
    Market orders fill at once (changing holdings); stop/limit orders rest.
    `fill_resting(id)` fills one later, as a broker would on a price move,
    cancelling its OCO sibling. No OCO support — see _OcoBook."""
    name = "book"
    paper = True
    quote_time = None

    def get_account(self):
        from src.execution.broker_base import AccountInfo
        return AccountInfo(broker="book", cash=1e6, portfolio_value=1e6,
                           buying_power=1e6, currency="USD")

    def get_quote(self, ticker):
        return {"bid": 9.99, "ask": 10.01, "mid": 10.0,
                "timestamp": self.quote_time.isoformat() if self.quote_time else None}

    def __init__(self, holdings=None):
        self.holdings = dict(holdings or {})
        self.resting = {}                     # id -> dict
        self.sent = []
        self._n = 0
        self.reject_next = False
        self.partial_next = None
        self.client_orders = {}

    # reads
    def is_connected(self):
        return True

    def get_positions(self):
        return [{"ticker": t, "qty": abs(q), "side": "long" if q > 0 else "short"}
                for t, q in self.holdings.items() if q]

    def get_open_orders(self):
        return [dict(o) for o in self.resting.values()]

    def get_order_status(self, oid):
        return OrderResult(broker_order_id=oid, status=OrderStatus.FILLED)

    # writes
    def _id(self):
        self._n += 1
        return f"o{self._n}"

    def submit_order(self, req: OrderRequest):
        result = self._submit_order(req)
        if req.client_order_id:
            self.client_orders[req.client_order_id] = result
        return result

    def get_order_by_client_id(self, cid):
        result = self.client_orders.get(cid)
        if result and result.status == OrderStatus.SUBMITTED and result.broker_order_id not in self.resting:
            return OrderResult(result.broker_order_id, OrderStatus.CANCELLED)
        return result

    def _submit_order(self, req: OrderRequest):
        side = req.side.value if hasattr(req.side, "value") else req.side
        self.sent.append((req.order_type.value, req.ticker, side, req.qty))
        if self.reject_next:
            self.reject_next = False
            return OrderResult(broker_order_id="", status=OrderStatus.REJECTED,
                               error_message="rejected by test")
        oid = self._id()
        if req.order_type == OrderType.MARKET:
            filled = req.qty if self.partial_next is None else self.partial_next
            self.partial_next = None
            self.holdings[req.ticker] = self.holdings.get(req.ticker, 0.0) + (
                filled if side == "buy" else -filled)
            status = OrderStatus.FILLED if filled == req.qty else OrderStatus.PARTIAL
            return OrderResult(broker_order_id=oid, status=status, filled_qty=filled,
                               avg_fill_price=10.0)
        self.resting[oid] = {"id": oid, "ticker": req.ticker, "side": side, "qty": req.qty,
                             "order_type": req.order_type.value}
        return OrderResult(broker_order_id=oid, status=OrderStatus.SUBMITTED)

    def cancel_order(self, oid):
        return self.resting.pop(oid, None) is not None

    def fill_resting(self, oid):
        o = self.resting.pop(oid)
        q = o["qty"]
        self.holdings[o["ticker"]] = self.holdings.get(o["ticker"], 0.0) + (
            q if o["side"] == "buy" else -q)
        for k in [k for k, v in self.resting.items()
                  if v.get("parent_id") == oid or o.get("parent_id") == k
                  or (o.get("parent_id") and v.get("parent_id") == o.get("parent_id"))]:
            self.resting.pop(k, None)                 # OCO: the sibling is cancelled


class _OcoBook(_Book):
    def submit_oco_exit(self, ticker, qty, entry_side, stop, target, *, client_order_id=None):
        side = "sell" if entry_side in ("buy", "long") else "buy"
        pid = self._id()
        if client_order_id:
            self.client_orders[client_order_id] = OrderResult(pid, OrderStatus.SUBMITTED)
        self.resting[pid] = {"id": pid, "ticker": ticker, "side": side, "qty": qty,
                             "order_type": "limit", "limit_price": target}
        leg = self._id()
        self.resting[leg] = {"id": leg, "ticker": ticker, "side": side, "qty": qty,
                             "order_type": "stop", "stop_price": stop, "parent_id": pid}
        self.sent.append(("oco", ticker, side, qty))
        return pid


def Book(holdings=None, oco=True):
    return (_OcoBook if oco else _Book)(holdings)


def worst_case(book, ticker: str) -> float:
    """|position| if EVERY resting order on `ticker` fills, OCO groups once."""
    pos = book.holdings.get(ticker, 0.0)
    groups = {}
    for o in book.resting.values():
        if o["ticker"] != ticker:
            continue
        key = o.get("parent_id") or o["id"]
        groups.setdefault(key, []).append(o)
    for legs in groups.values():
        o = legs[0]
        pos += o["qty"] if o["side"] == "buy" else -o["qty"]
    return pos


# ═══ the arithmetic ═════════════════════════════════════════════════════════

@pytest.mark.parametrize("side,req,pos,resting,qty,why", [
    ("sell", 10, 10, 0, 10, "reduces"),                      # 1 long + sell
    ("buy", 5, -5, 0, 5, "reduces"),                         # 2 short + buy
    ("sell", 3, 0, 0, 0, "no position"),                     # 3 zero position
    ("sell", 15, 10, 0, 10, "clipped"),                      # 4 oversized
    ("buy", 5, 10, 0, 0, "wrong side"),                      # 5 wrong side (long)
    ("sell", 5, -10, 0, 0, "wrong side"),                    # 5 wrong side (short)
    ("sell", 10, 10, 10, 0, "already covered"),              # 6 duplicate
    ("sell", 10, 10, 4, 6, "clipped"),                       # 6 partly covered
    ("sell", 10, None, 0, 0, "position unreadable"),         # 7/8 unreadable
    ("sell", 10, 10, None, 0, "resting orders unreadable"),
    ("sell", float("nan"), 10, 0, 0, "nothing to send"),
    ("sell", -1, 10, 0, 0, "nothing to send"),
])
def test_check(side, req, pos, resting, qty, why):
    r = RO.check(side, req, pos, resting)
    assert r.qty == qty and why in r.reason, r
    if pos not in (None,) and resting is not None and r.ok:
        after = pos - r.qty - resting if pos > 0 else pos + r.qty + resting
        assert abs(after) <= abs(pos) and (after == 0 or (after > 0) == (pos > 0))


def test_oco_legs_count_once_and_unlinked_orders_count_in_full():
    orders = [{"id": "p", "ticker": "AAPL", "side": "sell", "qty": 10},
              {"id": "l", "ticker": "AAPL", "side": "sell", "qty": 10, "parent_id": "p"},
              {"id": "x", "ticker": "AAPL", "side": "sell", "qty": 3},
              {"id": "b", "ticker": "AAPL", "side": "buy", "qty": 7},
              {"id": "m", "ticker": "MSFT", "side": "sell", "qty": 9}]
    assert RO.resting_reduce_qty(orders, "AAPL", "sell") == 13
    assert RO.resting_reduce_qty(orders, "AAPL", "sell", exclude_ids=["x"]) == 10
    assert RO.same_symbol("BTC-USD", "BTCUSD") and RO.same_symbol("btc/usd", "BTCUSD")
    assert RO.signed_position([{"ticker": "BTCUSD", "qty": 2, "side": "long"},
                               {"ticker": "AAPL", "qty": 5, "side": "short"}], "BTC-USD") == 2
    assert RO.signed_position([{"symbol": "AAPL", "qty": -5}], "AAPL") == -5


# ═══ protective orders: OrderManager.place_protective_orders ═══════════════

def _mgr(book):
    from src.execution.order_manager import OrderManager
    return OrderManager(alpaca=book, ibkr=book)


def test_1_long_gets_one_oco_sized_to_the_position():
    book = Book({"AAPL": 10})
    out = _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert book.sent == [("oco", "AAPL", "sell", 10.0)]
    assert out["stop_loss_order"] and out["take_profit_order"]
    assert abs(worst_case(book, "AAPL")) <= 10


def test_2_short_gets_a_buy_stop():
    book = Book({"AAPL": -5}, oco=False)
    out = _mgr(book).place_protective_orders("AAPL", 5, "sell", stop=11.0, target=None)
    assert book.sent == [("stop", "AAPL", "buy", 5.0)] and out["stop_loss_order"]
    assert worst_case(book, "AAPL") == 0


def test_3_zero_position_gets_nothing():
    book = Book({})
    out = _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert book.sent == [] and "no position" in out["reduce_only"]["reason"]


def test_4_oversized_protection_is_clipped_to_the_position():
    book = Book({"AAPL": 6})
    _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert book.sent == [("oco", "AAPL", "sell", 6.0)]


def test_5_wrong_side_protection_is_refused():
    book = Book({"AAPL": 10})
    out = _mgr(book).place_protective_orders("AAPL", 10, "sell", stop=11.0, target=None)
    assert book.sent == [] and "wrong side" in out["reduce_only"]["reason"]


def test_6_a_duplicate_protection_is_refused():
    book = Book({"AAPL": 10})
    m = _mgr(book)
    m.place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    out = m.place_protective_orders("AAPL", 10, "buy", stop=9.5, target=12.5)
    assert len(book.sent) == 1 and "already covered" in out["reduce_only"]["reason"]
    assert abs(worst_case(book, "AAPL")) <= 10


def test_no_oco_means_a_stop_only_never_two_independent_exits():
    """The hazard: target fills on the way up, stop fills on the way down,
    and a 10-share long is a 10-share short."""
    book = Book({"AAPL": 10}, oco=False)
    out = _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert book.sent == [("stop", "AAPL", "sell", 10.0)]
    assert out["take_profit_order"] is None and "no OCO" in out["target_skipped"]
    stop_id = next(iter(book.resting))
    book.fill_resting(stop_id)
    assert worst_case(book, "AAPL") == 0


def test_10_a_partial_entry_gets_no_full_size_brackets(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")
    book = Book({})
    book.quote_time = TUE_OPEN
    book.partial_next = 40
    q = aq.ApprovalQueue().push(OrderRequest(
        ticker="AAPL", side=OrderSide.BUY, qty=100, order_type=OrderType.MARKET,
        stop_loss=9.0, take_profit=12.0), current_price=10.0, broker="alpaca")
    monkeypatch.setattr("src.execution.order_manager.OrderManager._await_fill",
                        lambda self, b, r, **k: r)
    res = _mgr(book).execute(q.id, now=TUE_OPEN)
    assert res["status"] == "partial" and res["brackets_pending"] is True
    assert [s[0] for s in book.sent] == ["market"]            # no brackets for 100
    assert worst_case(book, "AAPL") == 40


def test_11_a_rejected_protective_order_is_reported_not_retried_blind():
    book = Book({"AAPL": 10}, oco=False)
    book.reject_next = True
    out = _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=None)
    assert out["stop_loss_order"] is None and len(book.sent) == 1
    assert book.holdings["AAPL"] == 10


def test_12_an_oco_sibling_dies_with_its_leg_and_a_leftover_blocks_new_exits():
    book = Book({"AAPL": 10})
    _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    target_id = next(k for k, v in book.resting.items() if "parent_id" not in v)
    book.fill_resting(target_id)
    assert book.resting == {} and book.holdings["AAPL"] == 0
    # a sibling that did NOT die (no OCO at that broker) is counted as resting
    book.holdings["AAPL"] = 10
    book.resting["left"] = {"id": "left", "ticker": "AAPL", "side": "sell", "qty": 10,
                            "order_type": "stop"}
    out = _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert "already covered" in out["reduce_only"]["reason"]


def test_15_a_resting_stop_is_placed_during_unknown_but_stays_reduce_only(monkeypatch):
    """Protective orders are not session-gated — a stop must stand through a
    closed or UNKNOWN market — and are still reduce-only."""
    from src.markets import sessions as S
    assert not S.session("AAPL", datetime(2031, 3, 4, 15, 0, tzinfo=UTC)).tradable
    book = Book({"AAPL": 4})
    _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    assert book.sent == [("oco", "AAPL", "sell", 4.0)]


# ═══ the exit engine: PositionManager._close ═══════════════════════════════

def _freeze(monkeypatch, instant):
    from src.markets import sessions as S

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is None else instant.astimezone(tz)
    monkeypatch.setattr(S, "datetime", Frozen)


class _Mgr:
    def __init__(self, book):
        self._alpaca = book

    def open_orders_by_ticker(self):
        out = {}
        for o in self._alpaca.get_open_orders():
            out.setdefault(o["ticker"], []).append(o)
        return out

    def _await_fill(self, broker, result, **k):
        return result


def _close(monkeypatch, book, ticker="AAPL", snapshot_qty=10, side="long",
           asset_class="equity", when=TUE_OPEN):
    from src.desk import position_manager as pm
    _freeze(monkeypatch, when)
    pmgr = pm.PositionManager.__new__(pm.PositionManager)
    pmgr.cfg = {}
    for name in ("_log_execution", "_log_closed", "_update_scorecard", "_record_outcome_memory"):
        monkeypatch.setattr(pmgr, name, lambda rec: None, raising=False)
    monkeypatch.setattr("src.desk.notify.push", lambda msg, cfg: None)
    monkeypatch.setattr("src.execution.live_guard.paper_confirmed", lambda b: True)
    tracked = {ticker: {"asset_class": asset_class, "side": side, "entry_price": 10.0,
                        "qty": snapshot_qty}}
    return pmgr._close(tracked, ticker, {"qty": snapshot_qty, "side": side}, _Mgr(book),
                       reason="test exit")


def test_1_and_7_the_exit_uses_the_position_now_not_the_snapshot(monkeypatch):
    book = Book({"AAPL": 4})                     # a stop took 6 since the snapshot
    _close(monkeypatch, book, snapshot_qty=10)
    assert [s for s in book.sent if s[0] == "market"] == [("market", "AAPL", "sell", 4.0)]
    assert book.holdings["AAPL"] == 0


def test_2_a_short_is_bought_back_not_doubled(monkeypatch):
    book = Book({"AAPL": -6})
    _close(monkeypatch, book, snapshot_qty=6, side="short")
    assert book.sent == [("market", "AAPL", "buy", 6.0)] and book.holdings["AAPL"] == 0


def test_8_the_position_disappeared_so_nothing_is_sent(monkeypatch):
    book = Book({})                               # closed at the broker meanwhile
    assert _close(monkeypatch, book, snapshot_qty=10) is None
    assert book.sent == []


def test_9_a_different_symbol_is_not_this_position(monkeypatch):
    book = Book({"AAPLX": 10})                    # e.g. the listing changed
    assert _close(monkeypatch, book, ticker="AAPL", snapshot_qty=10) is None
    assert book.sent == []


def test_6_an_exit_already_in_flight_is_not_doubled(monkeypatch):
    book = Book({"AAPL": 10})
    book.resting["mkt"] = {"id": "mkt", "ticker": "AAPL", "side": "sell", "qty": 10,
                           "order_type": "market"}
    assert _close(monkeypatch, book) is None and book.sent == []


def test_protective_orders_are_cancelled_then_the_exit_is_sized_net_of_survivors(monkeypatch):
    book = Book({"AAPL": 10})
    _mgr(book).place_protective_orders("AAPL", 10, "buy", stop=9.0, target=12.0)
    _close(monkeypatch, book)
    assert book.resting == {} and book.holdings["AAPL"] == 0
    assert worst_case(book, "AAPL") == 0


def test_15_a_market_exit_waits_for_continuous_trading(monkeypatch):
    book = Book({"AAPL": 10})
    assert _close(monkeypatch, book, when=datetime(2031, 3, 4, 15, 0, tzinfo=UTC)) is None
    assert _close(monkeypatch, book, when=SATURDAY) is None
    assert book.sent == []


# ═══ queued manual exits: OrderManager.execute(reduce_only) ═════════════════

def _queue_exit(tmp_path, monkeypatch, ticker="AAPL", side=OrderSide.SELL, qty=10):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")
    return aq.ApprovalQueue().push(OrderRequest(
        ticker=ticker, side=side, qty=qty, order_type=OrderType.MARKET,
        situation="EXIT — test", reduce_only=True), broker="alpaca")


@pytest.mark.parametrize("held,side,qty,sent_qty", [
    ({"AAPL": 10}, OrderSide.SELL, 10, 10),       # normal
    ({"AAPL": 4}, OrderSide.SELL, 10, 4),         # 4/7 oversized or stale: clipped
    ({}, OrderSide.SELL, 10, None),               # 3/8 gone: refused
    ({"AAPL": 10}, OrderSide.BUY, 10, None),      # 5 wrong side: refused
    ({"AAPL": -3}, OrderSide.BUY, 10, 3),         # 2 short: clipped buy-back
    ({"AAPLX": 10}, OrderSide.SELL, 10, None),    # 9 symbol changed: refused
])
def test_a_queued_exit_is_rechecked_when_it_is_sent(tmp_path, monkeypatch, held, side, qty,
                                                     sent_qty):
    from src.execution import approval_queue as aq
    book = Book(held)
    q = _queue_exit(tmp_path, monkeypatch, side=side, qty=qty)
    assert q.reduce_only is True
    res = _mgr(book).execute(q.id, now=TUE_OPEN)
    if sent_qty is None:
        assert res["ok"] is False and book.sent == []
        assert aq.ApprovalQueue().get_by_id(q.id).status == "rejected"
    else:
        assert book.sent == [("market", "AAPL", side.value, float(sent_qty))]
        assert abs(book.holdings.get("AAPL", 0)) < abs(held.get("AAPL", 0)) + 1e-9


def test_a_queued_entry_is_not_reduce_only(tmp_path, monkeypatch):
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")
    q = aq.ApprovalQueue().push(OrderRequest(ticker="AAPL", side=OrderSide.BUY, qty=1),
                                broker="alpaca")
    assert q.reduce_only is False


def test_the_exit_engine_queues_exits_as_reduce_only(tmp_path, monkeypatch):
    from src.desk import position_manager as pm
    from src.execution import approval_queue as aq
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "q.json")
    pmgr = pm.PositionManager.__new__(pm.PositionManager)
    pmgr._queue_manual_exit("AAPL", {"asset_class": "equity"}, 5, True, "stop hit")
    assert aq.ApprovalQueue().get_pending()[0].reduce_only is True


# ═══ the kill switch ════════════════════════════════════════════════════════

@pytest.fixture
def halted_state():
    from src.execution import kill_switch
    from src.trading import killswitch, modes
    modes.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    modes.STATE_FILE.write_text('{"mode": "PAPER"}', encoding="utf-8")
    yield killswitch
    kill_switch.release(actor="test")


def _sim(cash=1000.0):
    from src.trading.broker import SimulatedBroker
    from trading_helpers import make_quote
    b = SimulatedBroker(cash=cash)
    b.set_quote(make_quote())
    return b


def _spec(cid, side, qty, **kw):
    from src.trading.broker import OrderSpec
    return OrderSpec(client_order_id=cid, symbol="AAPL", side=side, qty=qty, **kw)


@pytest.mark.usefixtures("clean_trading_state")
def test_preserve_keeps_stops_without_being_told_what_is_held(halted_state):
    """Production calls halt() without `held`. It used to cancel every order,
    stops included."""
    ks = halted_state
    b = _sim()
    b.submit_order(_spec("entry", "buy", 1.0))
    b.submit_order(_spec("stop", "sell", 1.0, order_type="limit", limit_price=120.0))
    b.submit_order(_spec("bid", "buy", 0.5, order_type="limit", limit_price=90.0))
    report = ks.halt("test", broker=b)
    assert report["steps"]["cancel"]["kept_protective"] == ["stop"]
    assert report["steps"]["cancel"]["cancelled"] == ["bid"]
    assert b.orders["stop"].status == "NEW"


@pytest.mark.usefixtures("clean_trading_state")
def test_13_flatten_nets_out_resting_protection_and_never_flips(halted_state):
    ks = halted_state
    ks.set_policy(ks.FLATTEN, False, actor="test")
    b = _sim()
    b.submit_order(_spec("entry", "buy", 2.0))
    b.submit_order(_spec("stop", "sell", 1.5, order_type="limit", limit_price=120.0))
    report = ks.halt("flatten", broker=b)
    flat = report["steps"]["flatten"]["AAPL"]
    assert flat["status"] == "FILLED" and flat["filled_qty"] == pytest.approx(0.5)
    held = b.positions["AAPL"]["qty"]
    resting = sum(b.specs[o.client_order_id].qty for o in b.get_open_orders())
    assert held == pytest.approx(1.5) and held - resting == pytest.approx(0.0)


@pytest.mark.usefixtures("clean_trading_state")
def test_13_flatten_with_protection_cancelled_sells_the_whole_position(halted_state):
    ks = halted_state
    ks.set_policy(ks.FLATTEN, True, actor="test")
    b = _sim()
    b.submit_order(_spec("entry", "buy", 2.0))
    b.submit_order(_spec("stop", "sell", 2.0, order_type="limit", limit_price=120.0))
    report = ks.halt("flatten", broker=b)
    assert report["steps"]["cancel"]["cancelled"] == ["stop"]
    assert report["steps"]["flatten"]["AAPL"]["filled_qty"] == pytest.approx(2.0)
    assert b.positions["AAPL"]["qty"] == pytest.approx(0.0)


@pytest.mark.usefixtures("clean_trading_state")
def test_13_flatten_refuses_a_symbol_it_cannot_account_for(halted_state):
    ks = halted_state
    ks.set_policy(ks.FLATTEN, False, actor="test")
    b = _sim()
    b.submit_order(_spec("entry", "buy", 1.0))
    b.submit_order(_spec("bid", "buy", 0.5, order_type="limit", limit_price=90.0))
    b.specs.pop("bid")                      # the adapter can no longer describe it...
    b.cancel_order = lambda cid: False      # ...and cannot cancel it either
    report = ks.halt("flatten", broker=b)
    assert report["steps"]["cancel"]["cancelled"] == []      # the cancel failed
    assert "cannot describe" in report["steps"]["flatten"]["AAPL"]["skipped"]
    assert b.positions["AAPL"]["qty"] == 1.0


class _ShortBook:
    """A trading-layer adapter holding a short, for the parts SimulatedBroker
    cannot model (it does not short)."""
    name = "shorts"

    def __init__(self):
        self.sent, self.cancelled = [], []

    def get_positions(self):
        return [{"symbol": "TSLA", "qty": -3.0, "side": "short"}]

    def get_open_orders(self):
        from src.trading.broker import OrderAck
        return [OrderAck("cover-stop", "NEW"), OrderAck("add-short", "NEW")]

    def open_order_details(self):
        return [{"client_order_id": "cover-stop", "symbol": "TSLA", "side": "buy", "qty": 3.0},
                {"client_order_id": "add-short", "symbol": "TSLA", "side": "sell", "qty": 1.0}]

    def cancel_order(self, cid):
        self.cancelled.append(cid)
        return True

    def submit_order(self, spec):
        self.sent.append(spec)
        raise AssertionError("nothing should be sent for a short")


@pytest.mark.usefixtures("clean_trading_state")
def test_2_and_13_a_short_keeps_its_buy_stop_and_is_not_flattened(halted_state):
    ks = halted_state
    ks.set_policy(ks.FLATTEN, False, actor="test")
    b = _ShortBook()
    report = ks.halt("test", broker=b)
    assert report["steps"]["cancel"]["kept_protective"] == ["cover-stop"]
    assert b.cancelled == ["add-short"]
    assert "short position" in report["steps"]["flatten"]["TSLA"]["skipped"]
    assert b.sent == []


@pytest.mark.usefixtures("clean_trading_state")
def test_14_a_halt_outside_the_session_still_flattens_reduce_only(halted_state, monkeypatch):
    """The halt is an emergency path and is deliberately not session-gated;
    reduce-only is what keeps it safe."""
    _freeze(monkeypatch, SATURDAY)
    ks = halted_state
    ks.set_policy(ks.FLATTEN, False, actor="test")
    b = _sim()
    b.submit_order(_spec("entry", "buy", 1.0))
    report = ks.halt("weekend halt", broker=b)
    assert report["steps"]["flatten"]["AAPL"]["status"] == "FILLED"
    assert b.positions["AAPL"]["qty"] == 0.0


@pytest.mark.usefixtures("clean_trading_state")
def test_the_production_flatten_is_paper_only_and_refused_below_the_kill_switch(
        halted_state, monkeypatch):
    """The route builds access.paper_adapter(). Under it, FLATTEN reaches the
    mode gate (RESEARCH after the halt) and stops: nothing touches the raw
    broker. The kill switch is not turned into an order path."""
    from trading_helpers import FakeLegacyBroker
    from src.trading import access
    monkeypatch.setenv("ALPACA_PAPER", "true")
    ks = halted_state
    ks.set_policy(ks.FLATTEN, False, actor="test")
    raw = FakeLegacyBroker(paper=True)
    raw.get_positions = lambda: [type("P", (), {"ticker": "AAPL", "qty": 2.0, "avg_cost": 10.0,
                                                "market_value": 20.0, "side": "long"})()]
    raw.get_open_orders = lambda: []
    adapter = access.paper_adapter(raw)
    report = ks.halt("route", broker=adapter)
    assert "error" in report["steps"]["flatten"]["AAPL"]
    assert getattr(raw, "submitted", []) == []
    from src.execution import kill_switch
    from src.trading import modes
    assert kill_switch.is_engaged() and modes.can_place_orders() is False
    assert modes.can_access_live_broker() is False


def test_the_halt_route_only_ever_builds_a_paper_adapter():
    src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def trading_halt("):src.index("def trading_kill_policy(")]
    assert "access.paper_adapter()" in body and "live_adapter" not in body


def test_flatten_is_the_kill_switchs_only_sender():
    import inspect
    from src.trading import killswitch
    src = inspect.getsource(killswitch)
    assert src.count("submit_order(") == 1
    flat = inspect.getsource(killswitch._flatten)
    assert 'side="sell"' in flat and "reduce_only.check(" in flat
