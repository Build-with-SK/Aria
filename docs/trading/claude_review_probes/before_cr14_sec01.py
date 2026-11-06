"""Run inside the 7385130 extraction: reproduce CR-14 and SEC-01 as reviewed.

Everything is written to a temporary directory and removed afterwards.
"""
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "tests")
sys.path.insert(0, ".")
import src.trading.review_store as review_store  # noqa: E402
from src.trading.review_store import ReviewStore  # noqa: E402
from test_multiasset_risk import case as fixture, NOW  # noqa: E402

p, inputs = fixture.__wrapped__() if hasattr(fixture, "__wrapped__") else fixture()
from src.trading.multiasset_risk import assess  # noqa: E402
i = assess(p, inputs, now=NOW)  # at 7385130 the store took a caller-built assessment
out = {}
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    tmp = Path(tmp)
    store = ReviewStore(tmp / "review.sqlite")
    store.queue(p, i, now=NOW)
    token = store.approve(p.proposal_id, authenticated_owner="fixture-owner", now=NOW)

    # CR-14: does backup() call close() on the connections it opens?
    opened = []

    class Tracked(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    real = sqlite3.connect
    review_store.sqlite3.connect = lambda *a, **k: opened.append(real(*a, factory=Tracked, **k)) or opened[-1]
    try:
        store.backup(tmp / "backup.sqlite")
    finally:
        review_store.sqlite3.connect = real
    out["CR14_connections_opened_by_backup"] = len(opened)
    out["CR14_closed_explicitly"] = [c.closed for c in opened]
    opened.clear()

    # SEC-01: edit event 1 and recompute every link with plain sha256.
    head_before = store.verify_audit()
    with sqlite3.connect(str(store.path)) as db:
        rows = db.execute("SELECT sequence, body FROM audit ORDER BY sequence").fetchall()
        previous = "0" * 64
        for sequence, body in rows:
            if sequence == 1:
                body = body[:-1] + ',"tampered":true}'
            digest = hashlib.sha256((previous + body).encode()).hexdigest()
            db.execute("UPDATE audit SET body=?, previous_hash=?, event_hash=? WHERE sequence=?",
                       (body, previous, digest, sequence))
            previous = digest
    sqlite3.connect(str(store.path)).close()
    try:
        head_after = store.verify_audit()
        out["SEC01_rewritten_chain_verify_audit"] = "ACCEPTED"
        out["SEC01_head_changed"] = head_after != head_before
    except Exception as exc:  # noqa: BLE001
        out["SEC01_rewritten_chain_verify_audit"] = f"REFUSED: {exc}"
    try:
        store.reserve_intent(p, token, i, now=NOW)
        out["SEC01_reserve_on_rewritten_chain"] = "RESERVED"
    except Exception as exc:  # noqa: BLE001
        out["SEC01_reserve_on_rewritten_chain"] = f"REFUSED: {exc}"
    with sqlite3.connect(str(store.path)) as db:
        db.execute("DELETE FROM audit WHERE sequence >= 2")
    sqlite3.connect(str(store.path)).close()
    try:
        store.verify_audit()
        out["SEC01_truncated_chain_verify_audit"] = "ACCEPTED"
    except Exception as exc:  # noqa: BLE001
        out["SEC01_truncated_chain_verify_audit"] = f"REFUSED: {exc}"
    out["SEC01_store_accepts_a_key"] = "audit_key" in ReviewStore.__init__.__code__.co_varnames
print(json.dumps(out, indent=1))
