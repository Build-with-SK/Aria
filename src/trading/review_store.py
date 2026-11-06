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

from .contracts import RiskAssessment, TradeProposal, content_hash
from .multiasset_risk import ResearchRiskPolicy, RiskInputs, assess


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


class ReviewStore:
    #: The only policy the store assesses under. A caller cannot pass a looser
    #: one; changing it is a reviewed code change, and the policy hash binds
    #: every approval to it.
    POLICY = ResearchRiskPolicy()
    #: A caller-supplied `now` further than this from the store's clock is
    #: refused, so an approval cannot be made "in the past" against stale data.
    MAX_CLOCK_SKEW_SECONDS = 5

    def __init__(self, path: Path, *, clock=_system_clock):
        self._system_clock = clock
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
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
                CREATE TABLE IF NOT EXISTS rule_boundaries (
                    account_id TEXT NOT NULL, profile_hash TEXT NOT NULL,
                    day TEXT NOT NULL, snapshot_at TEXT NOT NULL,
                    balance TEXT NOT NULL, high_water TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    PRIMARY KEY(account_id,profile_hash,day));
                PRAGMA user_version=1;
            """)

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
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _verify(db):
        previous = "0" * 64
        for expected, row in enumerate(db.execute("SELECT * FROM audit ORDER BY sequence"), 1):
            calculated = hashlib.sha256((previous + row["body"]).encode()).hexdigest()
            if row["sequence"] != expected or row["previous_hash"] != previous or row["event_hash"] != calculated:
                raise ReviewRefused("audit integrity failure")
            previous = calculated
        return previous

    @classmethod
    def _event(cls, db, kind: str, proposal_id: str, now: datetime, **details):
        previous = cls._verify(db)
        body = json.dumps({"kind": kind, "proposal_id": proposal_id,
                           "at": now.isoformat(), **details}, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256((previous + body).encode()).hexdigest()
        db.execute("INSERT INTO audit(body,previous_hash,event_hash) VALUES(?,?,?)", (body, previous, digest))

    @classmethod
    def _state(cls, db, proposal_id, new, now):
        row=db.execute("SELECT state FROM proposals WHERE id=?",(proposal_id,)).fetchone()
        if row is None:
            raise ReviewRefused("proposal missing")
        validate_transition(State(row["state"]),State(new))
        db.execute("UPDATE proposals SET state=? WHERE id=?",(State(new).value,proposal_id))
        cls._event(db,"STATE_TRANSITION",proposal_id,now,old=row["state"],new=State(new).value)

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
        risk = assess(proposal, inputs, now=now, policy=self.POLICY)
        if risk.verdict != "ALLOW":
            raise ReviewRefused(f"risk engine verdict {risk.verdict}: {', '.join(risk.reasons)}")
        self._risk(proposal, risk, now)
        return risk

    def queue(self, proposal: TradeProposal, inputs: RiskInputs, *, now: datetime) -> RiskAssessment:
        """Assess `proposal` from `inputs` and queue it only if the engine allows it."""
        self._now(now)
        risk = self._assess(proposal, inputs, now)
        with self._transaction() as db:
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
                        proposal_hash=row["payload_hash"], expires_at=expires.isoformat())
        return token

    def reserve_intent(self, proposal: TradeProposal, token: str,
                       fresh_inputs: RiskInputs, *, now: datetime) -> str:
        """Atomic single-use reservation. No broker calls, no automatic retry.

        Risk is re-assessed here from fresh evidence inside the transaction; the
        result must still ALLOW, under the approved policy, at no more risk.
        """
        self._now(now)
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

    def verify_audit(self) -> str:
        with self._db() as db:
            return self._verify(db)

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

    def record_rule_boundary(self, profile, snapshot, *, evidence_hash: str,
                             now: datetime, cashflow):
        """Append one consecutive policy-local midnight account snapshot.

        A new ledger must begin at the original capital on account creation day;
        it cannot guess missing historical highs. Withdrawals/resets require a
        separately verified new account/profile lineage, never lowering this one.
        Caller supplies broker-derived snapshots, not a model's balance estimate.
        """
        import re
        from datetime import datetime, time, timedelta
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
            prior=db.execute("SELECT * FROM rule_boundaries WHERE account_id=? AND profile_hash=? ORDER BY day DESC LIMIT 1",
                             (profile.account_id,profile.profile_hash)).fetchone()
            if prior is None:
                if day!=profile.account_creation_date or snapshot.balance!=profile.account_size:
                    raise ReviewRefused("full initial boundary history required; no guessed high-water seed")
                high=profile.account_size
            else:
                self._boundary_integrity(db,prior)
                from datetime import date
                if day!=date.fromisoformat(prior["day"])+timedelta(days=1):
                    raise ReviewRefused("duplicate, reordered or missing daily boundary")
                high=max(Decimal(prior["high_water"]),snapshot.balance)
            db.execute("INSERT INTO rule_boundaries VALUES(?,?,?,?,?,?,?)",(
                profile.account_id,profile.profile_hash,day.isoformat(),snapshot.observed_at.isoformat(),
                str(snapshot.balance),str(high),evidence_hash))
            self._event(db,"RULE_BOUNDARY","rule:"+profile.account_id,now,
                        profile_hash=profile.profile_hash,day=day.isoformat(),
                        balance=str(snapshot.balance),high_water=str(high),evidence_hash=evidence_hash)

    def rule_state(self, profile, *, equity, now: datetime):
        from decimal import Decimal
        from .rule_profiles import RuleState, rule_day
        self._now(now)
        with self._db() as db:
            self._verify(db)
            row=db.execute("SELECT * FROM rule_boundaries WHERE account_id=? AND profile_hash=? AND day=?",
                (profile.account_id,profile.profile_hash,rule_day(profile,now).isoformat())).fetchone()
            if row is None:
                raise ReviewRefused("current rule-day boundary missing")
            self._boundary_integrity(db,row)
            return RuleState(account_id=profile.account_id,account_currency=profile.account_currency,
                daily_balance=Decimal(row["balance"]),daily_snapshot_at=datetime.fromisoformat(row["snapshot_at"]),
                equity=equity,balance_high_water=Decimal(row["high_water"]),
                high_water_observed_at=datetime.fromisoformat(row["snapshot_at"]))

    def backup(self, destination: Path):
        if Path(destination).resolve() == self.path.resolve() or Path(destination).exists():
            raise ReviewRefused("backup requires a new distinct path")
        with self._db() as source, sqlite3.connect(str(destination)) as target:
            self._verify(source)
            source.backup(target)
