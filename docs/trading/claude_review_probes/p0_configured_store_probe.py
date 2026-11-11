"""Claude's independent P0 probes against a CONFIGURED ReviewStore (source + checkpoint).

Fixture-only: no SDK, no transport, no credentials. Usage: python p0_probe.py <code root>
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import pytest  # noqa: E402
from test_multiasset_risk import case, NOW, snapshot_source  # noqa: E402
from test_review_findings import _trailing_profile, _record, twenty_dollar_trade  # noqa: E402
from src.trading.review_store import ReviewStore, ReviewRefused  # noqa: E402
from src.trading.multiasset_risk import assess  # noqa: E402

KEY = b"claude-independent-p0-probe-key-000000"
out = {}


def run(name, fn):
    try:
        out[name] = fn()
    except Exception as exc:  # recorded, not hidden
        out[name] = {"exception": type(exc).__name__, "message": str(exc)}


def sql(path, *statements):
    db = sqlite3.connect(str(path))
    try:
        for s, args in statements:
            db.execute(s, args)
        db.commit()
    finally:
        db.close()


def refusal(fn):
    try:
        fn()
        return None
    except ReviewRefused as exc:
        return str(exc)


with tempfile.TemporaryDirectory() as td, pytest.MonkeyPatch.context() as mp:
    tmp = Path(td)
    p, i = case.__wrapped__(mp, tmp)

    def store(name, source_inputs, anchored=True):
        return ReviewStore(tmp / (name + ".sqlite"), clock=lambda: NOW, audit_key=KEY,
                           evidence_source=snapshot_source(source_inputs),
                           anchor_path=(tmp / (name + ".anchor")) if anchored else None)

    def history(s, profile, balances):
        for n, bal in enumerate(balances):
            _record(s, profile, date(2026, 11, 1) + timedelta(days=n), bal)

    # --- SQL-only attacker (no audit key) -------------------------------------------
    def forge_approval():
        s = store("forge", i)
        s.queue(p, i, now=NOW)
        import hashlib
        sql(s.path, ("UPDATE proposals SET state='APPROVED'", ()),
            ("INSERT INTO approvals VALUES(?,?,?,?,?,0)", ("x", p.proposal_id,
             hashlib.sha256(b"t").hexdigest(), "invented", (NOW + timedelta(seconds=20)).isoformat())))
        return {"refused": refusal(lambda: s.reserve_intent(p, "t", i, now=NOW))}

    def replay_consumed():
        s = store("replay", i)
        s.queue(p, i, now=NOW)
        token = s.approve(p.proposal_id, authenticated_owner="owner", now=NOW)
        s.reserve_intent(p, token, i, now=NOW)
        sql(s.path, ("DELETE FROM intents", ()), ("UPDATE approvals SET consumed=0", ()),
            ("UPDATE proposals SET state='APPROVED'", ()))
        return {"refused": refusal(lambda: s.reserve_intent(p, token, i, now=NOW))}

    def payload_swap():
        s = store("swap", i)
        s.queue(p, i, now=NOW)
        token = s.approve(p.proposal_id, authenticated_owner="owner", now=NOW)
        from src.trading.contracts import content_hash
        big = p.model_copy(update={"quantity": D("0.06")})
        sql(s.path, ("UPDATE proposals SET payload=?, payload_hash=?",
                     (big.model_dump_json(), content_hash(big))))
        return {"refused": refusal(lambda: s.reserve_intent(big, token, i, now=NOW))}

    def sql_lower_high_water():
        prof = _trailing_profile((p, i))
        honest = i.model_copy(update={"profile": prof})
        s = store("hw-sql", honest)
        history(s, prof, ["10000", "10400", "10800", "10600", "10500", "10450"])
        sql(s.path, ("UPDATE rule_boundaries SET high_water='10000'", ()))
        return {"refused": refusal(lambda: s.rule_state(prof, equity=D("10450"), now=NOW))}

    def rollback_before_reservation():
        s = store("rollback", i)
        s.queue(p, i, now=NOW)
        token = s.approve(p.proposal_id, authenticated_owner="owner", now=NOW)
        copy = tmp / "rollback.copy"
        shutil.copyfile(s.path, copy)
        s.reserve_intent(p, token, i, now=NOW)
        shutil.copyfile(copy, s.path)
        return {"refused": refusal(lambda: s.reserve_intent(p, token, i, now=NOW))}

    def rollback_releases_reconciliation_hold():
        s = store("hold", i)
        s.queue(p, i, now=NOW)
        token = s.approve(p.proposal_id, authenticated_owner="owner", now=NOW)
        s.reserve_intent(p, token, i, now=NOW)      # checkpoint pinned here
        copy = tmp / "hold.copy"
        shutil.copyfile(s.path, copy)
        unanchored = ReviewStore(s.path, clock=lambda: NOW, audit_key=KEY)
        held = unanchored.recover(now=NOW)          # mutation without a checkpoint
        state_held = s.inspect(p.proposal_id)["state"]
        shutil.copyfile(copy, s.path)               # roll the file back
        state_after = s.inspect(p.proposal_id)["state"]
        # The next anchored mutation: does the checkpoint notice the rollback?
        detected = refusal(lambda: s.recover(now=NOW))
        return {"unanchored_mutation_allowed": held == 1, "state_before_rollback": state_held,
                "state_after_rollback": state_after, "anchored_mutation_refused": detected}

    # --- caller-recorded history vs the trusted source --------------------------------
    def fabricated_midnight_boundary():
        q, honest = twenty_dollar_trade((p, i), equity="10100", daily_balance="10400", high_water="10400")
        s = store("midnight", honest)
        history(s, honest.profile, ["10000", "10000", "10000", "10000", "10000", "10100"])
        try:
            verdict = s.queue(q, honest, now=NOW).verdict
        except ReviewRefused as exc:
            verdict = "REFUSED: " + str(exc)
        return {"source_verdict": assess(q, honest, now=NOW).verdict, "store_verdict": verdict}

    def fabricated_low_high_water():
        prof = _trailing_profile((p, i))
        q = p.model_copy(update={"rule_profile_hash": prof.profile_hash})
        honest = i.model_copy(update={
            "profile": prof,
            "account": i.account.model_copy(update={"equity": D("9850"), "balance": D("9850")}),
            "rule_state": i.rule_state.model_copy(update={"equity": D("9850"), "daily_balance": D("9850"),
                                                          "balance_high_water": D("10800")})})
        s = store("low-hw", honest)
        history(s, prof, ["10000", "10000", "10000", "10000", "9900", "9850"])
        try:
            verdict = s.queue(q, honest, now=NOW).verdict
        except ReviewRefused as exc:
            verdict = "REFUSED: " + str(exc)
        return {"source_verdict": assess(q, honest, now=NOW).verdict, "store_verdict": verdict}

    def masked_source_flags(field, value):
        def probe():
            honest = i.model_copy(update={"rule_state": i.rule_state.model_copy(update={field: value})})
            s = store("flag-" + field, honest)
            history(s, honest.profile, ["10000"] * 6)
            try:
                verdict = s.queue(p, honest, now=NOW).verdict
            except ReviewRefused as exc:
                verdict = "REFUSED: " + str(exc)
            return {"source_verdict": assess(p, honest, now=NOW).verdict, "store_verdict": verdict}
        return probe

    # --- other P0 boundaries -----------------------------------------------------------
    def kill_switch_on_configured_store():
        from src.execution import kill_switch
        s = store("kill", i)
        with pytest.MonkeyPatch.context() as m:
            m.setenv(kill_switch.ENV_VAR, "1")
            return {"refused": refusal(lambda: s.queue(p, i, now=NOW))}

    def other_account_proposal():
        s = store("acct", i)
        other = p.model_copy(update={"account_id": "demo-2"})
        return {"refused": refusal(lambda: s.queue(other, i, now=NOW))}

    def recorder_ignores_source_account():
        s = store("rec-acct", i)
        prof = i.profile.model_copy(update={"account_id": "someone-else"})
        try:
            history(s, prof, ["10000"])
            return {"recorded_for_unbound_account": True}
        except ReviewRefused as exc:
            return {"recorded_for_unbound_account": False, "message": str(exc)}

    def caller_substitution(change):
        """The caller submits altered evidence; the configured source stays honest."""
        def probe():
            from test_multiasset_risk import book, holding, activity
            honest = {"book": i.model_copy(update={"book": book(holding("EURUSD"))}),
                      "activity": i.model_copy(update={"activity": activity(server_requests=1600)})}.get(change, i)
            if change == "book":
                caller = honest.model_copy(update={"book": book()})
            elif change == "activity":
                caller = honest.model_copy(update={"activity": activity(server_requests=0)})
            elif change == "calculator":
                pv = i.broker_calculation.provenance.model_copy(update={"provider": "other-broker"})
                caller = i.model_copy(update={"broker_calculation": i.broker_calculation.model_copy(
                    update={"provenance": pv})})
            else:  # relabel the account class on the caller's copy
                caller = i.model_copy(update={"profile": i.profile.model_copy(update={"account_class": "FUNDED"})})
            s = store("sub-" + change, honest)
            return {"source_verdict": assess(p, honest, now=NOW).verdict,
                    "refused": refusal(lambda: s.queue(p, caller, now=NOW))}
        return probe

    def approve_actor_is_caller_attested():
        s = store("actor", i)
        s.queue(p, i, now=NOW)
        token = s.approve(p.proposal_id, authenticated_owner="any-string-a-caller-chooses", now=NOW)
        return {"approval_issued": bool(token)}

    for name, fn in [
        ("SQL_FORGE_APPROVAL", forge_approval), ("SQL_REPLAY_CONSUMED", replay_consumed),
        ("SQL_PAYLOAD_SWAP", payload_swap), ("SQL_LOWER_HIGH_WATER", sql_lower_high_water),
        ("ROLLBACK_BEFORE_RESERVATION", rollback_before_reservation),
        ("ROLLBACK_RELEASES_HOLD", rollback_releases_reconciliation_hold),
        ("RECORDED_MIDNIGHT_BELOW_SOURCE", fabricated_midnight_boundary),
        ("RECORDED_HIGH_WATER_BELOW_SOURCE", fabricated_low_high_water),
        ("SOURCE_CASHFLOW_MASKED", masked_source_flags("cashflows_since_snapshot", D("-500"))),
        ("SOURCE_RESET_MASKED", masked_source_flags("balance_reset", True)),
        ("CALLER_EMPTY_BOOK", caller_substitution("book")),
        ("CALLER_ZERO_ACTIVITY", caller_substitution("activity")),
        ("CALLER_CALCULATOR_SWAP", caller_substitution("calculator")),
        ("CALLER_CLASS_RELABEL", caller_substitution("relabel")),
        ("KILL_SWITCH", kill_switch_on_configured_store), ("OTHER_ACCOUNT", other_account_proposal),
        ("RECORDER_UNBOUND_ACCOUNT", recorder_ignores_source_account),
        ("APPROVE_ACTOR_ATTESTED", approve_actor_is_caller_attested),
    ]:
        run(name, fn)

print(json.dumps(out, indent=2, default=str))
