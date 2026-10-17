"""Historical recovery must never turn missing evidence into another POST."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from src.execution import approval_queue as aq, order_manager as om
from src.execution.broker_base import OrderRequest, OrderSide, OrderResult, OrderStatus


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(aq, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(om, "RECONCILE_LOG", tmp_path / "reconcile.jsonl")
    queue = aq.ApprovalQueue()
    return queue


def proposal(queue):
    return queue.push(OrderRequest(ticker="BTC-USD", side=OrderSide.BUY, qty=2), broker="alpaca")


def historical(queue, status="approved", oid="old-order"):
    row = asdict(proposal(queue))
    row.pop("submission_version")
    row.pop("legacy_original")
    row.pop("reconciliation_note")
    row.update(status=status, broker_order_id=oid)
    aq.QUEUE_FILE.write_text(json.dumps([row]), encoding="utf-8")
    return row


class Broker:
    name = "Alpaca"

    def __init__(self, answers=()):
        self.answers, self.reads = list(answers), []

    def get_order_by_broker_id(self, oid):
        self.reads.append(oid)
        got = self.answers.pop(0) if self.answers else None
        if isinstance(got, Exception):
            raise got
        return got

    def submit_order(self, req):
        pytest.fail("legacy recovery must NEVER submit")


def evidence(state=OrderStatus.FILLED, filled=2, **raw):
    return OrderResult("old-order", state, filled, 123,
                       raw={"symbol": "BTCUSD", "side": "buy", "qty": 2, **raw})


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled", "executed", "unknown", "submitting"])
def test_history_quarantined_once_and_preimage_audited(world, status):
    old = historical(world, status)
    row = world.get_by_id(old["id"])
    assert row.status == "unknown" and row.legacy_original == old
    assert row.client_order_id == ""  # never invent a key for an old POST
    audit = aq.QUEUE_FILE.with_suffix(".audit.jsonl")
    before = audit.read_bytes()
    assert json.loads(before.splitlines()[-1])["trade"]["legacy_original"] == old
    assert om.OrderManager(alpaca=Broker()).execute(row.id)["ok"] is False
    assert world.cancel(row.id) is False
    assert world.reject(row.id) is None
    assert world.approve(row.id) is None
    assert aq.ApprovalQueue().get_by_id(row.id).legacy_original == old
    assert audit.read_bytes() == before


def test_truly_unsent_legacy_pending_can_be_approved(world):
    old = historical(world, "pending", "")
    assert world.get_by_id(old["id"]).status == "pending"
    assert world.approve(old["id"]).submission_version == 1
    assert world.get_by_id(old["id"]).status == "approved"


def test_missing_ids_and_delayed_ack_block_new_claims_across_restart(world):
    old = historical(world, oid="")
    broker = Broker()
    mgr = om.OrderManager(alpaca=broker)
    assert mgr.reconcile_unresolved()["still_unresolved"] == [old["id"]]
    assert broker.reads == []
    fresh = proposal(world)
    assert aq.ApprovalQueue().claim_for_submit(fresh.id, "new-key")[0] is None
    world.bind_legacy_evidence(old["id"], by="owner", note="matched broker statement", broker_order_id="old-order")
    broker.answers = [None, TimeoutError("delayed"), evidence()]
    for _ in range(2):
        assert mgr.reconcile_unresolved()["still_unresolved"] == [old["id"]]
        assert world.claim_for_submit(fresh.id, "new-key")[0] is None
    assert mgr.reconcile_unresolved()["settled"] == {old["id"]: "filled"}
    assert world.get_by_id(old["id"]).legacy_original == old
    assert world.claim_for_submit(fresh.id, "new-key")[0] is not None


@pytest.mark.parametrize("raw", [{"symbol": "ETHUSD"}, {"side": "sell"}, {"qty": 9}])
def test_mismatching_broker_evidence_never_settles(world, raw):
    old = historical(world)
    report = om.OrderManager(alpaca=Broker([evidence(**raw)])).reconcile_unresolved()
    assert old["id"] in report["errors"]
    assert world.get_by_id(old["id"]).status == "unknown"


def test_partial_duplicate_stale_callback_and_terminal_remainder(world):
    old = historical(world)
    broker = Broker([evidence(OrderStatus.PARTIAL, 1), evidence(OrderStatus.PARTIAL, 1),
                     evidence(OrderStatus.SUBMITTED, 0),
                     evidence(OrderStatus.PARTIAL, 1, status="canceled")])
    mgr = om.OrderManager(alpaca=broker)
    mgr.reconcile_unresolved()
    log = aq.QUEUE_FILE.with_suffix(".audit.jsonl")
    before = log.read_bytes()
    mgr.reconcile_unresolved()
    assert log.read_bytes() == before, "duplicate callback has no new state transition"
    mgr.reconcile_unresolved()
    assert world.get_by_id(old["id"]).filled_qty == 1
    assert len(world.in_flight()) == 1
    mgr.reconcile_unresolved()
    row = world.get_by_id(old["id"])
    assert row.filled_qty == 1 and row.order_terminal
    assert world.in_flight() == []
    world.mark_executed(row.id, "old-order", 0, order_status="submitted", filled_qty=0)
    assert world.get_by_id(row.id).filled_qty == 1
    world.mark_ended_unfilled(row.id, "cancelled", "stale zero-fill callback")
    assert world.get_by_id(row.id).filled_qty == 1


def test_migration_crash_after_snapshot_before_audit_is_fail_closed(world, monkeypatch):
    old = historical(world)
    path_open = aq.Path.open

    def fail_audit(path, *args, **kwargs):
        if path.name.endswith(".audit.jsonl"):
            raise OSError("simulated worker crash after durable snapshot, before audit")
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(aq.Path, "open", fail_audit)
    with pytest.raises(OSError):
        world.get_by_id(old["id"])
    monkeypatch.setattr(aq.Path, "open", path_open)
    restarted = aq.ApprovalQueue()
    assert restarted.get_by_id(old["id"]).legacy_original == old
    assert restarted.get_by_id(old["id"]).status == "unknown"
    assert om.OrderManager(alpaca=Broker()).execute(old["id"])["ok"] is False


def test_migration_replace_failure_preserves_original_for_retry(world, monkeypatch):
    old = historical(world)
    before = aq.QUEUE_FILE.read_bytes()
    replace = aq.os.replace
    monkeypatch.setattr(aq.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        world.in_flight()
    assert aq.QUEUE_FILE.read_bytes() == before
    monkeypatch.setattr(aq.os, "replace", replace)
    assert aq.ApprovalQueue().in_flight()[0].legacy_original == old


def test_manual_evidence_and_release_require_owner_notes(world):
    old = historical(world, oid="")
    with pytest.raises(ValueError):
        world.bind_legacy_evidence(old["id"], by="", note="", broker_order_id="x")
    row = world.bind_legacy_evidence(old["id"], by="owner", note="statement #42", broker_order_id="old-order")
    assert row.status == "unknown"
    with pytest.raises(aq.QueueStateError):
        world.bind_legacy_evidence(row.id, by="owner", note="conflict", broker_order_id="other")
    with pytest.raises(ValueError):
        world.release_unresolved(row.id, by="owner", note="")
    row = world.release_unresolved(row.id, by="owner", note="broker confirmed never accepted")
    assert row.released_by == "owner" and row.legacy_original == old
    assert aq.ApprovalQueue().get_by_id(row.id).status == "rejected"


def test_ibkr_session_order_id_is_insufficient(world):
    old = historical(world)
    items = json.loads(aq.QUEUE_FILE.read_text())
    items[0]["broker"] = "ibkr"
    aq.QUEUE_FILE.write_text(json.dumps(items))
    broker = SimpleNamespace(name="IBKR", get_order_status=lambda _: pytest.fail("unsafe session-local lookup"))
    report = om.OrderManager(ibkr=broker).reconcile_unresolved()
    assert report["still_unresolved"] == [old["id"]]


def test_duplicate_persisted_broker_identity_requires_manual_review(world):
    old = historical(world)
    duplicate = {**old, "id": "another-history-row"}
    aq.QUEUE_FILE.write_text(json.dumps([old, duplicate]))
    broker = Broker([evidence(), evidence()])
    report = om.OrderManager(alpaca=broker).reconcile_unresolved()
    assert set(report["still_unresolved"]) == {old["id"], duplicate["id"]}
    assert len(report["errors"]) == 2 and broker.reads == []


@pytest.mark.parametrize("filled", [-1, float("nan"), 3, 0])
def test_invalid_legacy_fill_evidence_does_not_release_reservation(world, filled):
    old = historical(world)
    report = om.OrderManager(alpaca=Broker([evidence(filled=filled)])).reconcile_unresolved()
    assert old["id"] in report["errors"]
    assert world.get_by_id(old["id"]).status == "unknown"


def test_alpaca_legacy_lookup_identity_and_404(world):
    from src.execution.alpaca_broker import AlpacaBroker
    broker = object.__new__(AlpacaBroker)
    order = SimpleNamespace(id="old-order", symbol="BTCUSD", side="buy", qty="2",
                            status="canceled", filled_qty="1", filled_avg_price="123", filled_at=None)
    broker._client = SimpleNamespace(get_order_by_id=lambda _: order)
    result = broker.get_order_by_broker_id("old-order")
    assert result.terminal and result.status == OrderStatus.PARTIAL and result.filled_qty == 1
    class NotFound(Exception):
        status_code = 404
    broker._client.get_order_by_id = lambda _: (_ for _ in ()).throw(NotFound())
    assert broker.get_order_by_broker_id("old-order") is None


def test_failed_legacy_intent_check_cannot_fall_back_to_bare_status(world):
    old = historical(world)
    broker = Broker([evidence(symbol="ETHUSD")])
    broker.get_order_status = lambda _: pytest.fail("legacy identity check bypassed")
    report = om.OrderManager(alpaca=broker).reconcile_unresolved()
    assert old["id"] in report["errors"]
    assert world.get_by_id(old["id"]).status == "unknown"


def test_owner_bound_original_client_id_still_requires_legacy_intent(world):
    old = historical(world, oid="")
    world.get_by_id(old["id"])
    world.bind_legacy_evidence(old["id"], by="owner", note="original reference", client_order_id="old-ref")
    broker = Broker()
    broker.get_order_by_client_id = lambda _: evidence(side="sell")
    report = om.OrderManager(alpaca=broker).reconcile_unresolved()
    assert old["id"] in report["errors"]
    assert world.get_by_id(old["id"]).status == "unknown"


@pytest.mark.parametrize("answer", [OrderResult("wrong", OrderStatus.CANCELLED),
    OrderResult("old-order", OrderStatus.CANCELLED, float("nan")),
    OrderResult("old-order", OrderStatus.FILLED, 3, 100)])
def test_modern_status_fallback_requires_identity_and_valid_fills(world, answer):
    trade = proposal(world)
    assert world.claim_for_submit(trade.id, "modern-ref")[0]
    world.mark_executed(trade.id, "old-order", 0, order_status="submitted")
    broker = Broker()
    broker.get_order_by_client_id = lambda _: None
    broker.get_order_status = lambda _: answer
    report = om.OrderManager(alpaca=broker).reconcile_unresolved()
    assert trade.id in report["errors"]
    assert world.get_by_id(trade.id).order_status == "submitted"
    assert len(world.in_flight()) == 1
