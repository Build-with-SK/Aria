"""Transactional manual-review state and intent reservation, without transport.

This is not a broker sender. Consuming a review reserves one durable intent; it
cannot make an order exist. Only a future independently reviewed worker may
process that intent. Restart recovery holds uncertain intents for reconciliation.
An explicit database path is required; import never touches production data.

The store never accepts a risk verdict from its caller. `queue` and
`reserve_intent` take the evidence (`RiskInputs`) and run the deterministic
engine themselves, under the store's own fixed policy, so a hand-built ALLOW
cannot reach an intent (review finding CR-02).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3

from pydantic import ValidationError

from .contracts import RiskAssessment, TradeProposal, content_hash, revalidated
from .multiasset_risk import ResearchRiskPolicy, RiskInputs, assess
from .rule_profiles import order_stage_permitted
from .snapshot_source import BoundRiskSource


class State(str, Enum):
    DRAFT = "DRAFT"
    ANALYSED = "ANALYSED"
    RISK_CHECKED = "RISK_CHECKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNKNOWN_STATUS = "UNKNOWN_STATUS"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


TRANSITIONS = {
    State.DRAFT: {State.ANALYSED, State.REJECTED, State.CANCELLED},
    State.ANALYSED: {State.RISK_CHECKED, State.REJECTED, State.CANCELLED},
    State.RISK_CHECKED: {State.AWAITING_APPROVAL, State.REJECTED, State.EXPIRED},
    State.AWAITING_APPROVAL: {State.APPROVED, State.REJECTED, State.CANCELLED, State.EXPIRED},
    State.APPROVED: {State.SUBMISSION_PENDING, State.CANCELLED, State.EXPIRED},
    State.SUBMISSION_PENDING: {State.SUBMITTED, State.UNKNOWN_STATUS, State.RECONCILIATION_REQUIRED, State.REJECTED},
    State.SUBMITTED: {State.ACKNOWLEDGED, State.PARTIALLY_FILLED, State.FILLED, State.REJECTED, State.CANCELLED, State.UNKNOWN_STATUS},
    State.ACKNOWLEDGED: {State.PARTIALLY_FILLED, State.FILLED, State.REJECTED, State.CANCELLED, State.UNKNOWN_STATUS},
    State.PARTIALLY_FILLED: {State.PARTIALLY_FILLED, State.FILLED, State.CANCELLED, State.UNKNOWN_STATUS},
    State.UNKNOWN_STATUS: {State.RECONCILIATION_REQUIRED},
    State.RECONCILIATION_REQUIRED: {State.ACKNOWLEDGED, State.PARTIALLY_FILLED, State.FILLED, State.REJECTED, State.CANCELLED},
}


class ReviewRefused(ValueError):
    pass


def validate_transition(old: State, new: State):
    if new not in TRANSITIONS.get(State(old), set()):
        raise ReviewRefused(f"invalid transition {old} -> {new}")


def _clock(now: datetime):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ReviewRefused("timezone-aware clock required")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _system_clock() -> datetime:
    return datetime.now(timezone.utc)


UNKEYED = "UNKEYED"


def _key_id(key: bytes) -> str:
    """Names a key without revealing it: HMAC of a fixed label."""
    return hmac.new(key, b"aria-review-audit-key-id", hashlib.sha256).hexdigest()[:32]


class ReviewStore:
    #: The only policy the store assesses under. A caller cannot pass a looser
    #: one; changing it is a reviewed code change, and the policy hash binds
    #: every approval to it.
    POLICY = ResearchRiskPolicy()
    #: A caller-supplied `now` further than this from the store's clock is
    #: refused, so an approval cannot be made "in the past" against stale data.
    MAX_CLOCK_SKEW_SECONDS = 5

    def __init__(self, path: Path, *, clock=_system_clock, audit_key: bytes | None = None,
                 evidence_source: BoundRiskSource | None = None):
        """`audit_key` (32+ bytes, kept outside this database) makes every audit
        event an HMAC-SHA256 link (review SEC-01): someone who can write the
        file but does not hold the key cannot rebuild a valid chain. A chain is
        keyed or unkeyed from its first event and never changes; only a keyed
        store may reserve an intent."""
        if audit_key is not None and (not isinstance(audit_key, bytes) or len(audit_key) < 32):
            raise ReviewRefused("audit key must be at least 32 bytes")
        self._audit_key = audit_key
        self._evidence_source = evidence_source
        self._system_clock = clock
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ReviewRefused("unsupported review schema version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    risk TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
                    token_hash TEXT NOT NULL, actor TEXT NOT NULL,
                    expires_at TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS intents (
                    id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
                    idempotency_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY, body TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
            """)
            if version == 1:
                # Schema 1 keyed balances by (account, profile hash), which let a
                # re-confirmed profile start a fresh, lower high-water (CR-01).
                # Its rows cannot be trusted to merge, so they are set aside,
                # not deleted, and the account history must be rebuilt from
                # broker evidence. Until then rule_state() fails closed.
                db.execute("ALTER TABLE rule_boundaries RENAME TO rule_boundaries_v1_unmigrated")
            # One row per account per policy day. A midnight balance is a fact
            # about the account, not about whichever profile version read it.
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rule_boundaries (
                    account_id TEXT NOT NULL, day TEXT NOT NULL,
                    snapshot_at TEXT NOT NULL, balance TEXT NOT NULL,
                    high_water TEXT NOT NULL, evidence_hash TEXT NOT NULL,
                    profile_hash TEXT NOT NULL,
                    PRIMARY KEY(account_id,day));
                CREATE TABLE IF NOT EXISTS audit_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1), key_id TEXT NOT NULL);
                PRAGMA user_version=3;
            """)
            wanted = UNKEYED if audit_key is None else _key_id(audit_key)
            row = db.execute("SELECT key_id FROM audit_meta WHERE id=1").fetchone()
            if row is None:
                events = db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
                # A chain that already has unkeyed events stays unkeyed.
                db.execute("INSERT INTO audit_meta VALUES(1,?)", (wanted if not events else UNKEYED,))
                row = db.execute("SELECT key_id FROM audit_meta WHERE id=1").fetchone()
            if row["key_id"] != wanted:
                if row["key_id"] == UNKEYED:
                    raise ReviewRefused("this audit chain is unkeyed and cannot be re-keyed; start a new store")
                raise ReviewRefused("audit key missing or wrong for this chain")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._verify(db)
                self._verify_projection(db)
                yield db
                self._verify_projection(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _link(self, previous: str, body: str) -> str:
        data = (previous + body).encode()
        if self._audit_key is None:
            return hashlib.sha256(data).hexdigest()
        return hmac.new(self._audit_key, data, hashlib.sha256).hexdigest()

    def _verify(self, db):
        previous = "0" * 64
        for expected, row in enumerate(db.execute("SELECT * FROM audit ORDER BY sequence"), 1):
            calculated = self._link(previous, row["body"])
            if row["sequence"] != expected or row["previous_hash"] != previous or row["event_hash"] != calculated:
                raise ReviewRefused("audit integrity failure")
            previous = calculated
        return previous

    def _verify_projection(self, db):
        """Authenticate mutable authority against the complete event history.

        Called at both ends of a transaction, never between its individual
        writes. Valid HMAC links alone do not authorize materialized rows.
        """
        proposals, approvals, intents, boundaries = {}, {}, {}, {}
        consumed = set()
        for row in db.execute("SELECT body FROM audit ORDER BY sequence"):
            e = json.loads(row["body"])
            pid, kind = e["proposal_id"], e["kind"]
            if kind == "DRAFT":
                if pid in proposals:
                    raise ReviewRefused("duplicate proposal in audit record")
                proposals[pid] = [e["proposal_hash"], e["risk_hash"], State.DRAFT.value]
            elif kind == "STATE_TRANSITION":
                if pid not in proposals or proposals[pid][2] != e["old"]:
                    raise ReviewRefused("inconsistent state transition in audit record")
                validate_transition(State(e["old"]), State(e["new"]))
                proposals[pid][2] = e["new"]
            elif kind == "APPROVED":
                if pid in approvals or not e.get("approval"):
                    raise ReviewRefused("approval authority missing from audit record; rebuild store")
                approvals[pid] = e["approval"]
            elif kind == "INTENT_RESERVED_NO_SEND":
                if pid in consumed or pid not in approvals:
                    raise ReviewRefused("duplicate consumption in audit record")
                consumed.add(pid)
                intents[pid] = (e["intent_id"], e["proposal_hash"], State.SUBMISSION_PENDING.value)
            elif kind == "RECONCILIATION_REQUIRED":
                old = intents.get(pid)
                if old is None or old[0] != e["intent_id"]:
                    raise ReviewRefused("unknown reconciliation intent in audit record")
                intents[pid] = (old[0], old[1], State.RECONCILIATION_REQUIRED.value)
            elif kind == "RULE_BOUNDARY":
                key = (pid.removeprefix("rule:"), e["day"])
                if key in boundaries:
                    raise ReviewRefused("duplicate account boundary in audit evidence")
                boundaries[key] = e
        actual = {r["id"]: r for r in db.execute("SELECT * FROM proposals")}
        if set(actual) != set(proposals):
            raise ReviewRefused("proposal set differs from audit record")
        for pid, (ph, rh, state) in proposals.items():
            r = actual[pid]
            if (r["state"] != state or r["payload_hash"] != ph
                    or content_hash(TradeProposal.model_validate_json(r["payload"])) != ph
                    or content_hash(RiskAssessment.model_validate_json(r["risk"])) != rh):
                raise ReviewRefused("proposal differs from audit record")
        actual = {r["proposal_id"]: r for r in db.execute("SELECT * FROM approvals")}
        if set(actual) != set(approvals):
            raise ReviewRefused("approval set differs from audit record")
        for pid, expected in approvals.items():
            r = actual[pid]
            if any(r[k] != v for k, v in expected.items()) or r["consumed"] != int(pid in consumed):
                raise ReviewRefused("approval authority differs from audit record")
        actual = {r["proposal_id"]: r for r in db.execute("SELECT * FROM intents")}
        if set(actual) != set(intents):
            raise ReviewRefused("intent set differs from audit record")
        for pid, (iid, ph, state) in intents.items():
            r = actual[pid]
            if (r["id"], r["idempotency_key"], r["payload_hash"], r["state"]) != (iid, iid, ph, state):
                raise ReviewRefused("intent differs from audit record")
        actual = {(r["account_id"], r["day"]): r for r in db.execute("SELECT * FROM rule_boundaries")}
        if set(actual) != set(boundaries):
            raise ReviewRefused("account boundary set differs from audit evidence")
        for key, e in boundaries.items():
            r = actual[key]
            if any(r[k] != e[k] for k in ("balance", "high_water", "evidence_hash", "profile_hash")):
                raise ReviewRefused("rule boundary differs from its audit evidence")
            if "snapshot_at" in e and r["snapshot_at"] != e["snapshot_at"]:
                raise ReviewRefused("boundary timestamp differs from audit evidence")

    def _event(self, db, kind: str, proposal_id: str, now: datetime, **details):
        previous = self._verify(db)
        body = json.dumps({"kind": kind, "proposal_id": proposal_id,
                           "at": now.isoformat(), **details}, sort_keys=True, separators=(",", ":"))
        digest = self._link(previous, body)
        db.execute("INSERT INTO audit(body,previous_hash,event_hash) VALUES(?,?,?)", (body, previous, digest))

    def _state(self, db, proposal_id, new, now):
        row=db.execute("SELECT state FROM proposals WHERE id=?",(proposal_id,)).fetchone()
        if row is None:
            raise ReviewRefused("proposal missing")
        validate_transition(State(row["state"]),State(new))
        db.execute("UPDATE proposals SET state=? WHERE id=?",(State(new).value,proposal_id))
        self._event(db,"STATE_TRANSITION",proposal_id,now,old=row["state"],new=State(new).value)

    def _now(self, now: datetime):
        """A timezone-aware caller clock that agrees with the store's own."""
        _clock(now)
        skew = abs((now - self._system_clock()).total_seconds())
        if skew > self.MAX_CLOCK_SKEW_SECONDS:
            raise ReviewRefused("caller clock disagrees with the store clock")

    @staticmethod
    def _risk(proposal, risk, now):
        if risk.verdict != "ALLOW" or risk.proposal_hash != content_hash(proposal) or risk.quantity != proposal.quantity:
            raise ReviewRefused("allowing risk assessment for this exact proposal required")
        if not risk.assessed_at <= now < risk.expires_at or not now < proposal.expires_at:
            raise ReviewRefused("expired proposal/risk or future risk timestamp")

    def _assess(self, proposal: TradeProposal, inputs, now: datetime) -> RiskAssessment:
        """Run the deterministic engine here; never take a verdict from outside."""
        if not isinstance(proposal, TradeProposal):
            raise ReviewRefused("trade proposal required")
        if not isinstance(inputs, RiskInputs):
            raise ReviewRefused("risk evidence required; a caller-built assessment is not accepted")
        try:
            profile = revalidated(inputs.profile)
        except ValidationError as exc:
            raise ReviewRefused("rule profile fails validation") from exc
        if not order_stage_permitted(profile):
            # Research may size a trade on any account; only a PRACTICE account
            # may enter an order stage in this release (review CR-07).
            raise ReviewRefused(f"order stages are PRACTICE-only in this release; account class is {profile.account_class}")
        if self._evidence_source is None:
            raise ReviewRefused("trusted account-bound evidence source required; order review disabled")
        try:
            acquired = self._evidence_source.acquire(proposal, now)
        except Exception as exc:
            raise ReviewRefused("authoritative snapshot acquisition failed") from exc
        if content_hash(inputs) != content_hash(acquired):
            raise ReviewRefused("risk evidence differs from authoritative snapshot")
        # An existing ledger is authoritative over a transport's reconstructed
        # baseline. Missing reset history cannot be replaced by a caller value.
        with self._db() as db:
            has_history = db.execute("SELECT 1 FROM rule_boundaries WHERE account_id=? LIMIT 1",
                                     (proposal.account_id,)).fetchone() is not None
        with self._db() as db:
            for event in db.execute("SELECT body FROM audit ORDER BY sequence"):
                body = json.loads(event["body"])
                if body["kind"] == "ACCOUNT_REGISTERED" and body["proposal_id"] == "account:"+proposal.account_id:
                    if (body["profile_hash"] != acquired.profile.profile_hash
                            or body["provider_id"] != self._evidence_source.provider_id):
                        raise ReviewRefused("account registration changed; verified migration required")
        if has_history:
            state = self.rule_state(acquired.profile, equity=acquired.account.equity, now=now)
            acquired = acquired.model_copy(update={"rule_state": state})
        risk = assess(proposal, acquired, now=now, policy=self.POLICY)
        if risk.verdict != "ALLOW":
            raise ReviewRefused(f"risk engine verdict {risk.verdict}: {', '.join(risk.reasons)}")
        self._risk(proposal, risk, now)
        return risk

    def queue(self, proposal: TradeProposal, inputs: RiskInputs, *, now: datetime) -> RiskAssessment:
        """Assess `proposal` from `inputs` and queue it only if the engine allows it."""
        self._now(now)
        with self._transaction() as db:
            risk = self._assess(proposal, inputs, now)
            if not any(json.loads(row["body"])["kind"] == "ACCOUNT_REGISTERED"
                       and json.loads(row["body"])["proposal_id"] == "account:"+proposal.account_id
                       for row in db.execute("SELECT body FROM audit")):
                self._event(db, "ACCOUNT_REGISTERED", "account:"+proposal.account_id, now,
                            profile_hash=self._evidence_source.profile.profile_hash,
                            provider_id=self._evidence_source.provider_id)
            try:
                db.execute("INSERT INTO proposals VALUES(?,?,?,?,?)", (
                    proposal.proposal_id, proposal.model_dump_json(), content_hash(proposal),
                    risk.model_dump_json(), State.DRAFT.value))
            except sqlite3.IntegrityError as exc:
                raise ReviewRefused("proposal id already exists") from exc
            self._event(db, "DRAFT", proposal.proposal_id, now,
                        proposal_hash=content_hash(proposal), policy_hash=risk.policy_hash,
                        risk_hash=content_hash(risk), inputs_hash=content_hash(inputs))
            for state in (State.ANALYSED,State.RISK_CHECKED,State.AWAITING_APPROVAL):
                self._state(db,proposal.proposal_id,state,now)
        return risk

    @staticmethod
    def _stored_risk(db, proposal_id: str, raw: str) -> RiskAssessment:
        """The assessment this store computed, checked against its DRAFT audit event."""
        risk = RiskAssessment.model_validate_json(raw)
        digest = content_hash(risk)
        for event in db.execute("SELECT body FROM audit ORDER BY sequence"):
            body = json.loads(event["body"])
            if body.get("kind") == "DRAFT" and body.get("proposal_id") == proposal_id:
                if body.get("risk_hash") == digest:
                    return risk
                break
        raise ReviewRefused("stored risk assessment differs from its audit record")

    def approve(self, proposal_id: str, *, authenticated_owner: str, now: datetime,
                ttl_seconds: int = 30) -> str:
        """Trusted server calls only; identity must come from existing auth.

        This module is not a security boundary against arbitrary local Python.
        No API exposes this method in the read-only release.
        """
        self._now(now)
        if not authenticated_owner or not 1 <= ttl_seconds <= 60:
            raise ReviewRefused("authenticated owner and bounded TTL required")
        token = secrets.token_urlsafe(32)
        with self._transaction() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None or row["state"] != State.AWAITING_APPROVAL:
                raise ReviewRefused("proposal is not awaiting approval")
            proposal = TradeProposal.model_validate_json(row["payload"])
            risk = self._stored_risk(db, proposal_id, row["risk"])
            self._risk(proposal, risk, now)
            if content_hash(proposal) != row["payload_hash"]:
                raise ReviewRefused("proposal integrity failure")
            expires = min(proposal.expires_at, risk.expires_at, now + timedelta(seconds=ttl_seconds))
            db.execute("INSERT INTO approvals(id,proposal_id,token_hash,actor,expires_at) VALUES(?,?,?,?,?)",
                       (content_hash({"proposal": proposal_id, "token_hash": _token_hash(token)}),
                        proposal_id, _token_hash(token), authenticated_owner, expires.isoformat()))
            self._state(db,proposal_id,State.APPROVED,now)
            self._event(db, "APPROVED", proposal_id, now, actor=authenticated_owner,
                        proposal_hash=row["payload_hash"], expires_at=expires.isoformat(),
                        approval={"id": content_hash({"proposal": proposal_id, "token_hash": _token_hash(token)}),
                                  "proposal_id": proposal_id, "token_hash": _token_hash(token),
                                  "actor": authenticated_owner, "expires_at": expires.isoformat()})
        return token

    def reserve_intent(self, proposal: TradeProposal, token: str,
                       fresh_inputs: RiskInputs, *, now: datetime) -> str:
        """Atomic single-use reservation. No broker calls, no automatic retry.

        Risk is re-assessed here from fresh evidence inside the transaction; the
        result must still ALLOW, under the approved policy, at no more risk.
        """
        self._now(now)
        if self._audit_key is None:
            raise ReviewRefused("reserving an intent requires a keyed audit chain (review SEC-01)")
        with self._transaction() as db:
            fresh_risk = self._assess(proposal, fresh_inputs, now)
            row = db.execute("SELECT p.*, a.token_hash, a.expires_at, a.consumed FROM proposals p JOIN approvals a ON a.proposal_id=p.id WHERE p.id=?",
                             (proposal.proposal_id,)).fetchone()
            if row is None or row["state"] != State.APPROVED or row["consumed"]:
                raise ReviewRefused("approval unavailable or already consumed")
            if not hmac.compare_digest(_token_hash(token), row["token_hash"]):
                raise ReviewRefused("invalid approval")
            if now >= datetime.fromisoformat(row["expires_at"]):
                raise ReviewRefused("approval expired")
            if content_hash(proposal) != row["payload_hash"] or content_hash(TradeProposal.model_validate_json(row["payload"])) != row["payload_hash"]:
                raise ReviewRefused("proposal substitution")
            prior_risk = self._stored_risk(db, proposal.proposal_id, row["risk"])
            if prior_risk.policy_hash != fresh_risk.policy_hash or fresh_risk.monetary_risk > prior_risk.monetary_risk:
                raise ReviewRefused("risk policy or authorised risk changed")
            intent_id = content_hash({"proposal": row["payload_hash"], "approval": row["token_hash"]})
            db.execute("INSERT INTO intents VALUES(?,?,?,?,?,?)", (intent_id, proposal.proposal_id,
                       intent_id, row["payload_hash"], State.SUBMISSION_PENDING.value, now.isoformat()))
            db.execute("UPDATE approvals SET consumed=1 WHERE proposal_id=? AND consumed=0", (proposal.proposal_id,))
            self._state(db,proposal.proposal_id,State.SUBMISSION_PENDING,now)
            self._event(db, "INTENT_RESERVED_NO_SEND", proposal.proposal_id, now, intent_id=intent_id,
                        proposal_hash=row["payload_hash"], risk_hash=content_hash(fresh_risk))
            return intent_id

    def recover(self, *, now: datetime) -> int:
        """Hold pending/ambiguous intents after restart. Never resend."""
        self._now(now)
        with self._transaction() as db:
            rows = db.execute("SELECT * FROM intents WHERE state IN (?,?,?)", (
                State.SUBMISSION_PENDING.value, State.SUBMITTED.value, State.UNKNOWN_STATUS.value)).fetchall()
            for row in rows:
                db.execute("UPDATE intents SET state=? WHERE id=?", (State.RECONCILIATION_REQUIRED.value, row["id"]))
                if row["state"] == State.SUBMITTED:
                    self._state(db,row["proposal_id"],State.UNKNOWN_STATUS,now)
                self._state(db,row["proposal_id"],State.RECONCILIATION_REQUIRED,now)
                self._event(db, "RECONCILIATION_REQUIRED", row["proposal_id"], now, intent_id=row["id"])
            return len(rows)

    def inspect(self, proposal_id: str) -> dict | None:
        with self._db() as db:
            self._verify(db)
            row = db.execute("SELECT id,payload_hash,state FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            return dict(row) if row else None

    def verify_audit(self, anchor: dict | None = None) -> str:
        """Verify the chain; with `anchor`, also that it still contains that event.

        An anchor is what `anchor()` returned earlier, stored outside this
        database. A keyed chain cannot be rebuilt without the key, and an
        anchored one cannot be truncated below or rewritten through its anchor.
        """
        with self._db() as db:
            head = self._verify(db)
            if anchor is not None:
                row = db.execute("SELECT event_hash FROM audit WHERE sequence=?",
                                 (anchor["sequence"],)).fetchone()
                if row is None or not hmac.compare_digest(row["event_hash"], anchor["event_hash"]):
                    raise ReviewRefused("audit chain no longer contains its external anchor")
            return head

    def anchor(self) -> dict:
        """The chain head, to be stored outside this database (review SEC-01)."""
        with self._db() as db:
            self._verify(db)
            row = db.execute("SELECT sequence, event_hash FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
            if row is None:
                raise ReviewRefused("empty audit chain has nothing to anchor")
            return {"sequence": row["sequence"], "event_hash": row["event_hash"]}

    @staticmethod
    def _boundary_integrity(db, row):
        expected={"kind":"RULE_BOUNDARY","proposal_id":"rule:"+row["account_id"],
                  "profile_hash":row["profile_hash"],"day":row["day"],"balance":row["balance"],
                  "high_water":row["high_water"],"evidence_hash":row["evidence_hash"]}
        for event in db.execute("SELECT body FROM audit ORDER BY sequence DESC"):
            body=json.loads(event["body"])
            if all(body.get(k)==v for k,v in expected.items()):
                return
        raise ReviewRefused("rule boundary differs from its audit evidence")

    @staticmethod
    def _account_history(db, account_id: str):
        """(first row, latest row, highest high-water) for the account, any profile."""
        first=db.execute("SELECT * FROM rule_boundaries WHERE account_id=? ORDER BY day ASC LIMIT 1",
                         (account_id,)).fetchone()
        latest=db.execute("SELECT * FROM rule_boundaries WHERE account_id=? ORDER BY day DESC LIMIT 1",
                          (account_id,)).fetchone()
        highs=[row["high_water"] for row in db.execute(
            "SELECT high_water FROM rule_boundaries WHERE account_id=?",(account_id,))]
        from decimal import Decimal
        return first, latest, max((Decimal(h) for h in highs), default=None)

    def record_rule_boundary(self, profile, snapshot, *, evidence_hash: str,
                             now: datetime, cashflow):
        """Append one consecutive policy-local midnight account snapshot.

        History is per ACCOUNT, not per profile version (review CR-01): a
        re-confirmed or re-versioned profile continues the same history, so it
        cannot re-seed a lower high-water mark. The first boundary must be the
        original capital on the account creation day; every later one must be
        the next calendar day; the high-water mark is the highest balance ever
        recorded for the account and can only rise. Withdrawals/resets require a
        separately verified new account, never a lower line for this one.
        Caller supplies broker-derived snapshots, not a model's balance estimate.
        """
        import re
        from datetime import date, time
        from decimal import Decimal
        from zoneinfo import ZoneInfo
        from .rule_profiles import rule_day
        self._now(now)
        if profile.verification_reasons(now) or snapshot.account_id!=profile.account_id or snapshot.currency!=profile.account_currency:
            raise ReviewRefused("verified matching account policy required")
        if not re.fullmatch(r"[0-9a-f]{64}",evidence_hash) or cashflow != Decimal("0"):
            raise ReviewRefused("source hash required; cashflows/reset cannot alter existing lineage")
        day=rule_day(profile,snapshot.observed_at)
        boundary=datetime.combine(day,time(profile.reset_hour),ZoneInfo(profile.reset_timezone))
        if snapshot.observed_at!=boundary or snapshot.observed_at>now or snapshot.balance<=0:
            raise ReviewRefused("exact past policy boundary and positive balance required")
        with self._transaction() as db:
            first,latest,account_high=self._account_history(db,profile.account_id)
            if latest is None:
                if day!=profile.account_creation_date or snapshot.balance!=profile.account_size:
                    raise ReviewRefused("full initial boundary history required; no guessed high-water seed")
                high=profile.account_size
            else:
                self._boundary_integrity(db,latest)
                if Decimal(first["balance"])!=profile.account_size or first["day"]!=profile.account_creation_date.isoformat():
                    raise ReviewRefused("profile account size/creation day differs from the account's recorded start")
                if day!=date.fromisoformat(latest["day"])+timedelta(days=1):
                    raise ReviewRefused("duplicate, reordered or missing daily boundary")
                high=max(account_high,snapshot.balance)
            db.execute("INSERT INTO rule_boundaries(account_id,day,snapshot_at,balance,high_water,evidence_hash,profile_hash) VALUES(?,?,?,?,?,?,?)",(
                profile.account_id,day.isoformat(),snapshot.observed_at.isoformat(),
                str(snapshot.balance),str(high),evidence_hash,profile.profile_hash))
            self._event(db,"RULE_BOUNDARY","rule:"+profile.account_id,now,
                        profile_hash=profile.profile_hash,day=day.isoformat(),
                        balance=str(snapshot.balance),high_water=str(high),evidence_hash=evidence_hash,
                        snapshot_at=snapshot.observed_at.isoformat())

    def rule_state(self, profile, *, equity, now: datetime):
        from decimal import Decimal
        from .rule_profiles import RuleState, rule_day
        self._now(now)
        with self._db() as db:
            self._verify(db)
            self._verify_projection(db)
            row=db.execute("SELECT * FROM rule_boundaries WHERE account_id=? AND day=?",
                (profile.account_id,rule_day(profile,now).isoformat())).fetchone()
            if row is None:
                raise ReviewRefused("current rule-day boundary missing")
            self._boundary_integrity(db,row)
            first,_,account_high=self._account_history(db,profile.account_id)
            if Decimal(first["balance"])!=profile.account_size or first["day"]!=profile.account_creation_date.isoformat():
                raise ReviewRefused("profile account size/creation day differs from the account's recorded start")
            # The account's highest recorded high-water, whatever profile recorded it.
            high=max(Decimal(row["high_water"]),account_high)
            return RuleState(account_id=profile.account_id,account_currency=profile.account_currency,
                daily_balance=Decimal(row["balance"]),daily_snapshot_at=datetime.fromisoformat(row["snapshot_at"]),
                equity=equity,balance_high_water=high,
                high_water_observed_at=datetime.fromisoformat(row["snapshot_at"]))

    def backup(self, destination: Path):
        if Path(destination).resolve() == self.path.resolve() or Path(destination).exists():
            raise ReviewRefused("backup requires a new distinct path")
        # Closed explicitly (review CR-14): `with sqlite3.connect(...)` commits
        # but does not close, and on Windows the open handle locks the file.
        target = sqlite3.connect(str(destination))
        try:
            with self._db() as source:
                self._verify(source)
                source.backup(target)
        finally:
            target.close()
