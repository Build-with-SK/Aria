"""
tests/test_model_registry_routing.py
====================================
No model is promoted by its numbers, routing is evidence-based or DEFAULT,
and both are point in time.

The registry may only ever say INSUFFICIENT_DATA or EXPERIMENTAL on its own.
VALIDATED and PRODUCTION are owner decisions that the promotion gate must
allow at the moment they are made; the router consults only models in those
states, only in a slice with measurable, Holm-corrected evidence.
"""
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_helpers import clustered_record  # noqa: E402
from src.intelligence import calibration, dataset, evaluation, registry, routing  # noqa: E402


@pytest.fixture(autouse=True)
def status_log(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "STATUS_LOG", tmp_path / "model_status.jsonl")
    return tmp_path / "model_status.jsonl"


def strong(source="technical", n_dates=40, per=8, hit=0.8, start="2026-01-05", **kw):
    """A record that genuinely beats a coin and always-long, steadily."""
    return clustered_record(n_dates, per, lambda k: hit, source=source, start=start,
                            direction_fn=lambda k, j: "bear" if j % 2 else "bull", **kw)


def build(rows, as_of=None):
    ds = dataset.build(rows, as_of=as_of)
    ev = evaluation.evaluate_all(ds)
    cal = {m: calibration.status(ds.for_model(m), model_id=m) for m in ds.models()}
    cards = registry.cards(ds, ev, cal, ledger_rows=rows, as_of=as_of)
    return ds, ev, cards


# ── evidence alone never promotes ───────────────────────────────────────────

def test_a_strong_record_is_eligible_but_still_only_experimental():
    rows = strong()
    ds, ev, cards = build(rows)
    c = cards["technical"]
    assert c["promotion_check"]["eligible"] is True, c["promotion_check"]["criteria"]
    assert c["status"] == "EXPERIMENTAL" and c["owner_status"] is None


def test_evidence_statuses():
    ds, ev, cards = build(clustered_record(3, 5, lambda k: 0.6))
    assert cards["technical"]["status"] == "INSUFFICIENT_DATA"
    assert cards["signal-engine"]["status"] == "NOT_RECORDED"
    assert cards["ml-ensemble"]["status"] == "INSUFFICIENT_DATA"


def test_the_gate_takes_no_text():
    params = list(inspect.signature(registry.promotion_check).parameters)
    assert params == ["model_id", "dataset", "ledger_rows", "family_pvalues"]


def test_validated_is_refused_when_the_gate_fails():
    ds, ev, cards = build(clustered_record(30, 10, lambda k: 0.5))
    check = registry.promotion_check("technical", ds, ledger_rows=[])
    assert check["eligible"] is False
    with pytest.raises(registry.PromotionRefused):
        registry.set_status("technical", "VALIDATED", by="owner", reason="looks good",
                            check=check)
    with pytest.raises(registry.PromotionRefused):
        registry.set_status("technical", "VALIDATED", by="owner", reason="trust me")


def test_production_needs_validated_first_and_retired_is_always_allowed(status_log):
    rows = strong()
    ds, _, _ = build(rows)
    check = registry.promotion_check("technical", ds, ledger_rows=rows)
    with pytest.raises(registry.PromotionRefused):
        registry.set_status("technical", "PRODUCTION", by="owner", reason="", check=check)
    registry.set_status("technical", "VALIDATED", by="owner", reason="gate passed", check=check)
    registry.set_status("technical", "PRODUCTION", by="owner", reason="", check=check)
    registry.set_status("technical", "RETIRED", by="owner", reason="enough")
    assert [e["status"] for e in registry.status_log()] == ["VALIDATED", "PRODUCTION", "RETIRED"]
    with pytest.raises(registry.PromotionRefused):
        registry.set_status("technical", "INSUFFICIENT_DATA", by="owner", reason="")
    with pytest.raises(registry.PromotionRefused):
        registry.set_status("made-up", "RETIRED", by="owner", reason="")


def test_validated_status_lapses_when_the_evidence_no_longer_supports_it():
    rows = strong()
    ds, _, _ = build(rows)
    registry.set_status("technical", "VALIDATED", by="owner", reason="",
                        check=registry.promotion_check("technical", ds, ledger_rows=rows))
    _, _, cards = build(rows)
    assert cards["technical"]["status"] == "VALIDATED"
    weak = clustered_record(30, 10, lambda k: 0.5)
    _, _, cards = build(weak)
    assert cards["technical"]["status"] == "EXPERIMENTAL"
    assert cards["technical"]["owner_status_lapsed"] is True


@pytest.mark.parametrize("breaker,criterion", [
    (lambda rows: [dict(r, model_version="technical|code=" + ("a" if i % 2 else "b") * 12)
                   for i, r in enumerate(rows)], "reproducible_model_version"),
    (lambda rows: rows[:150], "sufficient_sample"),
    (lambda rows: rows + [dict(rows[0], id="pit1", external_ref="pit1",
                               data_as_of="2027-01-01")], "no_pit_violation"),
])
def test_each_criterion_can_fail_on_its_own(breaker, criterion):
    rows = breaker(strong())
    ds, _, _ = build(rows)
    check = registry.promotion_check("technical", ds, ledger_rows=rows)
    assert check["criteria"][criterion]["passed"] is False
    assert check["eligible"] is False


def test_unknown_probability_semantics_blocks_promotion():
    rows = strong()
    ledger = rows + [dict(rows[0], id="u1", external_ref="u1", probability_kind=None,
                          resolved=0)]
    ds, _, _ = build(rows)
    check = registry.promotion_check("technical", ds, ledger_rows=ledger)
    assert check["criteria"]["known_probability_semantics"]["passed"] is False


def test_an_unstable_record_fails_stability():
    rows = clustered_record(60, 8, lambda k: 0.95 if k < 30 else 0.55, start="2026-01-05",
                            direction_fn=lambda k, j: "bear" if j % 2 else "bull")
    ds, _, _ = build(rows)
    check = registry.promotion_check("technical", ds, ledger_rows=rows)
    assert check["criteria"]["stable_performance"]["passed"] is False


# ── routing ─────────────────────────────────────────────────────────────────

def test_with_no_validated_model_every_route_is_the_default():
    ds, _, cards = build(strong())
    d = routing.route(routing.RouteRequest("AAPL", 10), cards, ds)
    assert d.reason_code == routing.INSUFFICIENT and d.route == routing.DEFAULT
    assert "nothing on this route may increase a position" in d.default_route
    assert all(not c["eligible"] for c in d.candidates)


def test_poor_data_abstains():
    ds, _, cards = build(strong())
    d = routing.route(routing.RouteRequest("AAPL", 10, data_quality="POOR"), cards, ds)
    assert d.reason_code == routing.ABSTAIN


def test_a_validated_model_with_slice_evidence_is_routed():
    rows = strong()
    ds, _, _ = build(rows)
    registry.set_status("technical", "VALIDATED", by="owner", reason="",
                        check=registry.promotion_check("technical", ds, ledger_rows=rows))
    ds, _, cards = build(rows)
    d = routing.route(routing.RouteRequest("AAPL", 10), cards, ds)
    assert d.reason_code == routing.ROUTED and d.route == "technical"
    # A slice the model has no record in is still the default.
    d2 = routing.route(routing.RouteRequest("BTC-USD", 10), cards, ds)
    assert d2.reason_code == routing.INSUFFICIENT


def test_routing_is_point_in_time_on_the_owner_decision():
    rows = strong()
    ds, _, _ = build(rows)
    registry.set_status("technical", "VALIDATED", by="owner", reason="",
                        check=registry.promotion_check("technical", ds, ledger_rows=rows))
    before = datetime.now(timezone.utc) - timedelta(days=1)
    ds_b, _, cards_b = build(rows, as_of=before)
    d = routing.route(routing.RouteRequest("AAPL", 10), cards_b, ds_b, as_of=before)
    assert d.reason_code == routing.INSUFFICIENT


def test_the_best_lower_bound_wins_and_nothing_is_averaged():
    tech = strong(source="technical", hit=0.8)
    v5 = strong(source="v5", hit=0.7, start="2026-01-05")
    rows = tech + [dict(r, id="v" + r["id"], external_ref="v" + r["external_ref"],
                        model_version="v5|code=fff000fff000") for r in v5]
    ds, _, _ = build(rows)
    for m in ("technical", "v5"):
        registry.set_status(m, "VALIDATED", by="owner", reason="",
                            check=registry.promotion_check(m, ds, ledger_rows=rows))
    ds, _, cards = build(rows)
    d = routing.route(routing.RouteRequest("AAPL", 10), cards, ds)
    assert d.route == "technical" and d.family_size == 2


def test_lookup_falls_back_to_the_default():
    d = routing.lookup({}, "equity", 10)
    assert d["reason_code"] == routing.INSUFFICIENT and d["route"] == routing.DEFAULT


def test_the_table_covers_every_declared_slice():
    ds, _, cards = build(strong())
    t = routing.table(cards, ds)
    assert "equity|1M" in t and "crypto|1D" in t
    assert all(v["reason_code"] == routing.INSUFFICIENT for v in t.values())
