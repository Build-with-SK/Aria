"""
ownerauth.py
============
THE SHARED OWNER-SESSION CORE. One implementation, two applications.

CANONICAL COPY
--------------
This file is canonical at ARIA's `src/auth/ownerauth.py`. SENTINEL carries a
byte-identical copy at `sentinel/ownerauth.py`, produced by
`scripts/sync_ownerauth.py` and pinned by a checksum test in **both** repos. Edit
the canonical copy, run the sync script, and the two tests agree again. Edit
either copy by hand and the test in that repo fails and says so.

WHY A COPY RATHER THAN AN IMPORT
--------------------------------
ARIA must run when SENTINEL is not installed, and SENTINEL must run when ARIA is
not installed — that independence is load-bearing, stated in `sentinel/aria.py`
and enforced by the fact that the only coupling between them is an HTTP call with
a timeout. A shared package would make each one's *authentication* depend on the
other's source tree being present and on the same version, which is a far worse
failure mode than a duplicated file that a test keeps honest.

So: one source of truth, mechanically enforced, with no runtime dependency.

WHAT THIS REPLACES, AND WHY
---------------------------
Both applications previously turned "you hold the owner credential" into "you are
signed in" by a route that never expires:

  * ARIA's browser kept the raw owner token in `localStorage` and attached it to
    every request. One XSS on the origin and an attacker holds a permanent
    credential that opens the broker routes.
  * SENTINEL's cookie was `HMAC(token, label)` — a constant. `max_age` is advice
    to the browser; the server accepted a captured cookie forever, and logging
    out deleted only the client's copy. There was no way to revoke one session,
    no way to see which sessions existed, and no expiry that anything enforced.

Both are honest designs for one person on one laptop. Neither survives being
reachable from the internet, which is what this work is for.

THE SHAPE
---------
    credential  ──►  begin()  ──►  a session RECORD on disk
                                        │
                                        ▼
                       cookie value = "<sid>.<mac>"

`sid` is random and meaningless; `mac` is HMAC(session secret, sid). The MAC is
checked *before* the store is touched, so a forged or truncated cookie is thrown
out without a disk read. Then the record is looked up, and it is the record —
not the cookie — that says whether the session is still alive.

That inversion is the entire point. A cookie cannot assert its own validity, so
revocation, expiry and "sign out everywhere" become possible; with a derived
constant they are not possible at all.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Authorization. Which routes exist, which roles may reach them, what counts as a
high-risk action — those differ between a trading system and a general assistant
and belong to each application. This module answers exactly one question: *is
there a live owner session behind this request, and may this caller try again.*

Also not here: any web framework. Nothing imports FastAPI, Starlette or a
request object. The caller passes strings in and gets values out, which is what
makes this testable without a server and identical in two codebases.

NO CREDENTIAL EVER REACHES A LOG
--------------------------------
There is no branch in this file that writes a presented value, a stored value, a
hash or a cookie to a logger, and there must never be one. Failures are logged by
*shape* — "credential rejected", "session expired" — never by content.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re as _re
import secrets
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Bumped when the on-disk record shape or the cookie format changes. Recorded in
#: every session so an old record is recognisably old rather than mysteriously
#: malformed, and so a migration has something to branch on.
CONTRACT_VERSION = 1

#: Separates the session id from its MAC. Not base64's alphabet, not hex's, so it
#: can never occur inside either half.
SEP = "."

# ── outcomes ─────────────────────────────────────────────────────────────────
#
# Named rather than boolean because the caller must answer them differently: an
# unconfigured server is 503 and a wrong credential is 401, and collapsing the
# two invites somebody to keep guessing at a lock that was never installed.
OK = "ok"
UNCONFIGURED = "unconfigured"
BAD_CREDENTIAL = "bad_credential"
LOCKED_OUT = "locked_out"

# ── throttling ───────────────────────────────────────────────────────────────

#: Failures tolerated before a delay is imposed at all. Typing a token wrong
#: twice is a human; the fifth attempt in a row is not.
FREE_ATTEMPTS = 4

#: Backoff after that: 2s, 4s, 8s … doubling, capped. Exponential rather than
#: fixed because a fixed delay is a rate an attacker can simply plan around,
#: whereas doubling makes a sustained campaign take longer than it is worth.
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 900.0        # 15 minutes

#: Failures within one window before the client is locked out outright.
LOCKOUT_ATTEMPTS = 12
LOCKOUT_SECONDS = 900.0

#: A run of failures is forgotten after this long without one. Without it a
#: mistyped token in the morning still counts against you at midnight.
ATTEMPT_MEMORY_SECONDS = 3600.0

# ── session lifetime ─────────────────────────────────────────────────────────

#: Absolute ceiling. A session dies at this age whatever the owner is doing —
#: the guarantee that a credential stolen today does not work indefinitely.
DEFAULT_ABSOLUTE_SECONDS = 60 * 60 * 24 * 7        # 7 days

#: Idle ceiling. Closing the laptop for a fortnight signs you out.
DEFAULT_IDLE_SECONDS = 60 * 60 * 24 * 2            # 2 days

#: `last_seen` is only rewritten when it has drifted by more than this. Without
#: it every authenticated request rewrites the session file, which on a busy
#: dashboard is a disk write per poll.
TOUCH_RESOLUTION_SECONDS = 60.0

# ── step-up ──────────────────────────────────────────────────────────────────
#
# AUTHENTICATED is not the same as ALLOWED TO DO ANYTHING RIGHT NOW. A session
# that has been open on a phone for five days proves someone signed in five
# days ago; it does not prove the owner is holding the phone when a trade is
# approved. So the few actions that move money or loosen a safety control ask
# for the credential again unless it was presented recently.
#
# Which actions those are is each application's decision — this module only
# records WHEN the credential was last presented for a session, and answers
# "was that recent". A fresh sign-in counts: nobody should be asked twice in the
# same minute.

#: How long a confirmation lasts. Long enough to approve a handful of trades in
#: one sitting; short enough that a phone left unlocked on a table is not a
#: standing authorisation.
STEP_UP_SECONDS = 600.0


def _now() -> float:
    return time.time()


# ── keeping credentials out of every log, not just this module's ─────────────
#
# This module never logs a credential. That is not enough: a web server's
# ACCESS log records the request line, query string included, and it does so
# BEFORE the application runs — so a caller who puts `?token=...` in a URL gets
# the value written to disk even though both applications then refuse the
# request. The same goes for a library that logs a header on an error path.
#
# So the redaction happens at the one point every log record passes through:
# record creation. Installed once per process, it applies to every logger and
# every handler, including ones configured after it and ones this code never
# sees.

_SECRET_PARAM = _re.compile(
    r"(?i)([?&;](?:token|access_token|api[_-]?key|key|secret|password|passwd|"
    r"auth|session|credential)=)[^&\s\"'#;]+")
_BEARER = _re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_HEADER = _re.compile(
    r"(?i)((?:x-aria-token|x-sentinel-consult-token|authorization|cookie)"
    r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{8,}")
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    """Scrub credential-shaped values out of one string."""
    if not text or not isinstance(text, str):
        return text
    text = _SECRET_PARAM.sub(r"\1" + REDACTED, text)
    text = _BEARER.sub(r"\1" + REDACTED, text)
    return _HEADER.sub(r"\1" + REDACTED, text)


def _redact_arg(a):
    """Strings are scrubbed directly. Anything else that is not a number is
    checked through its string form and replaced ONLY if that form carried a
    credential — httpx logs the request URL as a `URL` object, not a str, so a
    str-only check let `?api_key=` from an outbound vendor call straight
    through. Numbers are left alone so `%d` still formats."""
    if isinstance(a, str):
        return redact(a)
    if a is None or isinstance(a, (bool, int, float)):
        return a
    try:
        text = str(a)
    except Exception:
        return a
    cleaned = redact(text)
    return cleaned if cleaned != text else a


_redaction_installed = False


def install_log_redaction() -> None:
    """Wrap the process's log-record factory so every record is scrubbed.

    Idempotent. Rewrites `msg` and any string `args` — uvicorn's access log
    passes the request line as an argument, which is where a URL token lives.
    """
    global _redaction_installed
    if _redaction_installed:
        return
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        try:
            record.msg = redact(record.msg) if isinstance(record.msg, str) else record.msg
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        except Exception:                               # never break logging
            pass
        return record

    logging.setLogRecordFactory(factory)
    _redaction_installed = True


def _scrypt(secret: str, salt: bytes) -> bytes:
    """Deliberately slow. Parameters are RFC 7914's interactive-login set, which
    costs ~16 MB and a few hundred milliseconds here and correspondingly more for
    somebody working through a dictionary."""
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt,
                          n=2 ** 14, r=8, p=1, dklen=32)


def hash_secret(secret: str) -> str:
    """`scrypt$<salt hex>$<hash hex>` — what goes in OWNER_SECRET_HASH.

    Offered so a human-chosen passphrase never sits in a file in the clear. A
    long random token does not need it (there is no dictionary to work through),
    which is why `verify_secret` accepts both forms.
    """
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_scrypt(secret, salt).hex()}"


def verify_secret(presented: str, stored: str) -> bool:
    """Constant-time, and correct for both storage forms.

    A stored value beginning `scrypt$` is a hash and is verified as one. Anything
    else is a literal credential and is compared directly — which is sound for a
    high-entropy token and is how both applications are configured today.
    """
    if not presented or not stored:
        return False
    if stored.startswith("scrypt$"):
        try:
            _, salt_hex, want_hex = stored.split("$", 2)
            got = _scrypt(presented, bytes.fromhex(salt_hex))
            return hmac.compare_digest(got, bytes.fromhex(want_hex))
        except (ValueError, TypeError):
            logger.error("ownerauth: the stored credential hash is malformed; "
                         "no credential can match it. Re-run the setup command.")
            return False
    return hmac.compare_digest(presented, stored)


class OwnerAuth:
    """One application's owner sessions.

    Constructed once at import in each app and then asked questions. Every
    environment variable is read at CALL time, never cached at construction, so a
    test can monkeypatch one and a deployment can rotate a credential with a
    restart rather than a code change.
    """

    def __init__(self, *, app: str, store: Path,
                 secret_envs: tuple[str, ...],
                 hash_envs: tuple[str, ...] = (),
                 session_secret_envs: tuple[str, ...] = (),
                 session_key_file: Path | None = None,
                 idle_seconds: float = DEFAULT_IDLE_SECONDS,
                 absolute_seconds: float = DEFAULT_ABSOLUTE_SECONDS):
        self.app = app
        self.store = Path(store)
        self.secret_envs = secret_envs
        self.hash_envs = hash_envs
        self.session_secret_envs = session_secret_envs
        self.session_key_file = session_key_file
        self.idle_seconds = float(idle_seconds)
        self.absolute_seconds = float(absolute_seconds)
        self._lock = threading.RLock()
        self._attempts: dict[str, list] = {}       # client -> [count, last_at]

    # ── configuration ────────────────────────────────────────────────────

    def _stored_secret(self) -> str:
        """The configured credential, hashed form preferred.

        Hash first: if an operator has taken the trouble to store a hash, a stale
        plaintext variable left in the environment must not quietly win.
        """
        for env in self.hash_envs:
            v = (os.environ.get(env) or "").strip()
            if v:
                return v
        for env in self.secret_envs:
            v = (os.environ.get(env) or "").strip()
            if v:
                return v
        return ""

    def configured(self) -> bool:
        return bool(self._stored_secret())

    def verify_credential(self, presented: str) -> bool:
        """Does this value match the configured credential? Constant time.

        The BREAK-GLASS path, for a caller that holds the credential directly
        rather than a session: a deploy script, a health probe, a terminal. It
        deliberately does NOT throttle — throttling is keyed to a client
        identifier that only the HTTP layer knows, and `begin()` is where a
        browser sign-in goes. A caller exposing this on a public route without
        its own rate limit has made a mistake; the two applications each apply
        one before reaching here.
        """
        return verify_secret((presented or "").strip(), self._stored_secret())

    def session_secret(self) -> str:
        """The MAC key. Environment first, then a persisted random key.

        Persistence matters more than it looks: a key regenerated per boot signs
        everybody out on every restart, which on a machine meant to run for
        months presents as an intermittent bug rather than as a decision.
        """
        for env in self.session_secret_envs:
            v = (os.environ.get(env) or "").strip()
            if v:
                return v
        path = self.session_key_file
        if path is not None:
            try:
                if path.exists():
                    k = path.read_text(encoding="utf-8").strip()
                    if k:
                        return k
                path.parent.mkdir(parents=True, exist_ok=True)
                k = secrets.token_urlsafe(48)
                path.write_text(k, encoding="utf-8")
                try:
                    os.chmod(path, 0o600)     # no-op on Windows, right on macOS
                except OSError:
                    pass
                logger.info("ownerauth[%s]: generated a session signing key",
                            self.app)
                return k
            except OSError as exc:
                logger.warning("ownerauth[%s]: could not persist a session key "
                               "(%s) — sessions will not survive a restart",
                               self.app, exc)
        # Derive from the credential as a last resort. Weaker (rotating the
        # credential invalidates every session) but never absent, and an absent
        # MAC key would mean unsigned cookies.
        return hashlib.sha256(
            (self._stored_secret() + "|" + self.app).encode()).hexdigest()

    # ── the store ────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not self.store.exists():
            return {}
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as exc:
            # Losing the session file signs the owner out. It does not open
            # anything, so continuing with none is the safe direction.
            logger.warning("ownerauth[%s]: session store unreadable (%s); "
                           "treating every session as absent", self.app, exc)
            return {}

    def _save(self, records: dict) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, indent=1, default=str),
                       encoding="utf-8")
        tmp.replace(self.store)
        try:
            os.chmod(self.store, 0o600)
        except OSError:
            pass

    def _prune(self, records: dict) -> dict:
        now = _now()
        return {sid: r for sid, r in records.items()
                if not self._dead(r, now)}

    def _dead(self, record: dict, now: float) -> bool:
        if record.get("revoked"):
            return True
        if now >= float(record.get("expires_at") or 0):
            return True
        idle = now - float(record.get("last_seen") or 0)
        return idle > self.idle_seconds

    # ── cookie values ────────────────────────────────────────────────────

    def _mac(self, sid: str) -> str:
        return hmac.new(self.session_secret().encode(), sid.encode(),
                        hashlib.sha256).hexdigest()[:32]

    def _split(self, cookie_value: str) -> str:
        """Cookie value -> sid, or "" if the MAC does not check out.

        Verified before the store is consulted: a cookie that was not issued here
        is rejected without a disk read, so a flood of junk cookies costs a
        hash each rather than a file read each.
        """
        if not cookie_value or SEP not in cookie_value:
            return ""
        sid, _, mac = cookie_value.partition(SEP)
        if not sid or not mac:
            return ""
        return sid if hmac.compare_digest(mac, self._mac(sid)) else ""

    # ── throttling ───────────────────────────────────────────────────────

    def _attempt_state(self, client: str) -> tuple[int, float]:
        count, last = self._attempts.get(client, [0, 0.0])
        if last and _now() - last > ATTEMPT_MEMORY_SECONDS:
            return 0, 0.0
        return int(count), float(last)

    def retry_after(self, client: str) -> float:
        """Seconds this client must wait before another attempt is even read.

        Zero when they may try now. The check happens before the credential is
        compared, so a locked-out caller cannot use timing against the compare to
        learn anything.
        """
        count, last = self._attempt_state(client)
        if count == 0:
            return 0.0
        if count >= LOCKOUT_ATTEMPTS:
            remaining = LOCKOUT_SECONDS - (_now() - last)
            return max(0.0, remaining)
        if count <= FREE_ATTEMPTS:
            return 0.0
        delay = min(BACKOFF_CAP_SECONDS,
                    BACKOFF_BASE_SECONDS * (2 ** (count - FREE_ATTEMPTS - 1)))
        return max(0.0, delay - (_now() - last))

    def _record_failure(self, client: str) -> None:
        with self._lock:
            count, last = self._attempt_state(client)
            self._attempts[client] = [count + 1, _now()]
            if count + 1 == LOCKOUT_ATTEMPTS:
                logger.warning("ownerauth[%s]: %s locked out after %d failed "
                               "sign-ins", self.app, client, LOCKOUT_ATTEMPTS)

    def _clear_failures(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)

    def throttle_state(self, client: str) -> dict:
        """What the dashboard shows. Never the credential, only the counting."""
        count, last = self._attempt_state(client)
        return {"failed_attempts": count,
                "retry_after": round(self.retry_after(client), 1),
                "locked_out": count >= LOCKOUT_ATTEMPTS
                              and self.retry_after(client) > 0}

    def recent_failures(self) -> list[dict]:
        with self._lock:
            return [{"client": c, "failures": int(n),
                     "last_attempt": time.strftime(
                         "%Y-%m-%dT%H:%M:%S", time.localtime(t))}
                    for c, (n, t) in sorted(self._attempts.items(),
                                            key=lambda kv: -kv[1][1])]

    # ── the flow ─────────────────────────────────────────────────────────

    def begin(self, presented: str, *, client: str = "unknown",
              label: str = "") -> tuple[str, str]:
        """Credential -> (outcome, cookie value). Cookie value is "" unless OK.

        `label` is a free-text hint about the device, shown in the session list so
        the owner can tell which line is their phone. It is never trusted for
        anything and is truncated hard, because it comes from a User-Agent.
        """
        if not self.configured():
            return UNCONFIGURED, ""
        wait = self.retry_after(client)
        if wait > 0:
            return LOCKED_OUT, ""
        if not verify_secret((presented or "").strip(), self._stored_secret()):
            self._record_failure(client)
            logger.warning("ownerauth[%s]: credential rejected for %s",
                           self.app, client)
            return BAD_CREDENTIAL, ""
        self._clear_failures(client)
        return OK, self._issue(client=client, label=label)

    def _issue(self, *, client: str, label: str) -> str:
        sid = secrets.token_urlsafe(24)
        now = _now()
        with self._lock:
            records = self._prune(self._load())
            records[sid] = {
                "v": CONTRACT_VERSION,
                "issued_at": now,
                "last_seen": now,
                # Signing in IS presenting the credential, so a fresh session
                # starts confirmed rather than asking again a second later.
                "confirmed_at": now,
                "expires_at": now + self.absolute_seconds,
                "client": str(client)[:64],
                "label": str(label or "")[:120],
                "revoked": False,
            }
            self._save(records)
        logger.info("ownerauth[%s]: session opened for %s", self.app, client)
        return f"{sid}{SEP}{self._mac(sid)}"

    def read(self, cookie_value: str | None) -> dict | None:
        """The live session behind this cookie, or None.

        None covers every way of not being signed in — no cookie, a forged MAC, a
        revoked session, an expired one, one idle too long. The caller does not
        need to tell them apart and should not branch on the difference.
        """
        sid = self._split(cookie_value or "")
        if not sid:
            return None
        now = _now()
        with self._lock:
            records = self._load()
            record = records.get(sid)
            if record is None or self._dead(record, now):
                return None
            if now - float(record.get("last_seen") or 0) > TOUCH_RESOLUTION_SECONDS:
                record["last_seen"] = now
                records[sid] = record
                self._save(records)
        return {"sid": sid, **record}

    def rotate(self, cookie_value: str | None, *, client: str = "unknown",
               label: str = "") -> str:
        """Issue a fresh session and revoke the old one. "" if there was none.

        Session fixation: an identifier that existed before the owner
        authenticated must not still be valid afterwards. Anything that raises a
        caller's privileges should rotate.
        """
        current = self.read(cookie_value)
        if current is None:
            return ""
        self.revoke(cookie_value)
        return self._issue(client=client or current.get("client", "unknown"),
                           label=label or current.get("label", ""))

    def confirm(self, cookie_value: str | None, presented: str, *,
                client: str = "unknown") -> str:
        """Re-present the credential for an existing session. Returns an outcome.

        Throttled exactly like `begin()` and against the same counters — a
        confirmation prompt is a sign-in form by another name, and an attacker
        holding a stolen cookie would otherwise have an unthrottled oracle for
        the credential.

        Returns BAD_CREDENTIAL too when there is no live session to confirm, so
        the caller cannot distinguish "wrong credential" from "no session" and
        this cannot be used to probe which cookies are alive.
        """
        if not self.configured():
            return UNCONFIGURED
        if self.retry_after(client) > 0:
            return LOCKED_OUT
        sid = self._split(cookie_value or "")
        ok = verify_secret((presented or "").strip(), self._stored_secret())
        if not ok:
            self._record_failure(client)
            logger.warning("ownerauth[%s]: confirmation rejected for %s",
                           self.app, client)
            return BAD_CREDENTIAL
        with self._lock:
            records = self._load()
            record = records.get(sid) if sid else None
            if record is None or self._dead(record, _now()):
                return BAD_CREDENTIAL
            self._clear_failures(client)
            record["confirmed_at"] = _now()
            record["last_seen"] = _now()
            records[sid] = record
            self._save(records)
        logger.info("ownerauth[%s]: session re-confirmed for %s",
                    self.app, client)
        return OK

    def recently_confirmed(self, cookie_value: str | None,
                           within: float = STEP_UP_SECONDS) -> bool:
        """Was the credential presented for this session within `within` s?

        False for any cookie that is not a live session, so a caller can use
        this alone as the gate on a high-risk action.
        """
        s = self.read(cookie_value)
        if s is None:
            return False
        # Records written before step-up existed have no `confirmed_at`, and are
        # treated as never confirmed rather than as confirmed at sign-in: an
        # old session must re-present the credential once, not inherit an
        # authorisation it was never given.
        return _now() - float(s.get("confirmed_at") or 0) <= within

    def revoke(self, cookie_value: str | None) -> bool:
        """Kill one session, server-side. This is what logout must call.

        Deleting the browser's copy of a cookie is housekeeping, not revocation —
        a cookie captured beforehand keeps working. Marking the record is what
        actually ends the session.
        """
        sid = self._split(cookie_value or "")
        if not sid:
            return False
        with self._lock:
            records = self._load()
            if sid not in records:
                return False
            records[sid]["revoked"] = True
            records[sid]["revoked_at"] = _now()
            self._save(self._prune(records))
        logger.info("ownerauth[%s]: session revoked", self.app)
        return True

    def revoke_all(self) -> int:
        """Sign out everywhere. Returns how many sessions were live.

        The button to press when a device is lost, and what credential rotation
        should call so that changing the credential does not leave sessions
        minted under the old one alive.
        """
        with self._lock:
            records = self._load()
            live = [sid for sid, r in records.items()
                    if not self._dead(r, _now())]
            for sid in live:
                records[sid]["revoked"] = True
                records[sid]["revoked_at"] = _now()
            self._save(self._prune(records))
        if live:
            logger.warning("ownerauth[%s]: revoked %d session(s)",
                           self.app, len(live))
        return len(live)

    def sessions(self) -> list[dict]:
        """Live sessions, newest first. Carries no secret: `sid` alone is useless
        without the MAC, which is never stored and never returned."""
        now = _now()
        out = []
        for sid, r in self._load().items():
            if self._dead(r, now):
                continue
            out.append({
                "id": sid[:8],
                "client": r.get("client"),
                "label": r.get("label"),
                "opened": time.strftime("%Y-%m-%dT%H:%M:%S",
                                        time.localtime(r.get("issued_at", 0))),
                "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S",
                                           time.localtime(r.get("last_seen", 0))),
                "expires_in_hours": round(
                    (float(r.get("expires_at", 0)) - now) / 3600.0, 1),
            })
        return sorted(out, key=lambda s: s["last_seen"], reverse=True)

    # ── cookie parameters ────────────────────────────────────────────────

    def cookie_kwargs(self, *, https: bool, name_hint: str = "") -> dict:
        """What to pass to `set_cookie`.

        `secure` tracks the actual transport rather than being hardcoded on. A
        Secure cookie over plain http is simply never sent back, which presents
        as "signing in does nothing" — the most confusing possible symptom, and
        one that costs an hour every time somebody meets it in local
        development.

        `samesite="strict"` because this is a single-origin application with no
        third-party sign-in flow to come back from. Nothing legitimate arrives
        here as a cross-site navigation, so nothing legitimate is lost, and
        strict removes a whole class of CSRF without further argument.
        """
        return {
            "httponly": True,
            "samesite": "strict",
            "secure": bool(https),
            "path": "/",
            # Deliberately NOT max_age. An expiry the browser enforces is a
            # convenience; the record's `expires_at` is the control. Making the
            # cookie a session cookie means closing the browser also ends it,
            # and nothing depends on the client honouring a number.
        }

    def status(self) -> dict:
        """What a status endpoint may say. States whether a lock exists, never
        anything about the key."""
        return {
            "app": self.app,
            "contract": CONTRACT_VERSION,
            "authentication_configured": self.configured(),
            "credential_is_hashed": self._stored_secret().startswith("scrypt$"),
            "scheme": "POST the owner credential once; an HttpOnly session "
                      "cookie is returned. Tokens in URLs are refused.",
            "session_absolute_hours": round(self.absolute_seconds / 3600, 1),
            "session_idle_hours": round(self.idle_seconds / 3600, 1),
            "live_sessions": len(self.sessions()),
        }
