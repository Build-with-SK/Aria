"""
tests/test_ownerauth.py
=======================
The shared owner-session core, tested identically in both repositories.

THIS FILE IS DUPLICATED ON PURPOSE, exactly like the module it tests. It imports
whichever copy exists — `src.auth.ownerauth` in ARIA, `sentinel.ownerauth` in
SENTINEL — so the same assertions run against both copies and neither can drift
into passing a weaker test than the other.

`test_copy_matches_canonical_checksum` is the mechanism that keeps them one file.
When the canonical module legitimately changes:

    python scripts/sync_ownerauth.py      # (in the ARIA repo)

and paste the printed checksum into EXPECTED_SHA256 below, in BOTH repositories.
A failure here means somebody edited one copy by hand.

WHAT THESE TESTS ARE FOR
------------------------
Each one pins a property that was absent before this module existed, and whose
absence was the actual finding in the audit:

  * a session can be ENDED server-side (revocation), not merely forgotten by a
    browser
  * a session EXPIRES, both absolutely and on idleness
  * a cookie cannot assert its own validity — the record decides
  * repeated wrong credentials get slower and then stop
  * nothing logged, returned or reported contains the credential
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest

try:                                    # ARIA
    from src.auth import ownerauth
    _MODULE_REL = Path("src") / "auth" / "ownerauth.py"
except ImportError:                     # SENTINEL
    from sentinel import ownerauth
    _MODULE_REL = Path("sentinel") / "ownerauth.py"

MODULE_PATH = Path(ownerauth.__file__)

#: sha256 of the canonical module with newlines normalised to \n.
#: Produced by scripts/sync_ownerauth.py. Update in BOTH repositories together.
EXPECTED_SHA256 = "dbaff0b5632aca63f4d21bdfccda53627f396e9c5c8001646ecfb6bf1e3a8cbb"

SECRET = "an-owner-credential-with-plenty-of-entropy-0123456789"


# ── the two copies are one file ──────────────────────────────────────────

def test_copy_matches_canonical_checksum():
    raw = MODULE_PATH.read_bytes().replace(b"\r\n", b"\n")
    got = hashlib.sha256(raw).hexdigest()
    assert got == EXPECTED_SHA256, (
        f"{_MODULE_REL} has drifted from the canonical copy.\n"
        f"  expected {EXPECTED_SHA256}\n"
        f"  found    {got}\n"
        "Edit ARIA's src/auth/ownerauth.py, run scripts/sync_ownerauth.py, and "
        "update EXPECTED_SHA256 in both repositories' tests/test_ownerauth.py.")


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def auth(tmp_path, monkeypatch):
    """A fully isolated OwnerAuth: its own store, its own key, its own env."""
    monkeypatch.setenv("TEST_OWNER_SECRET", SECRET)
    monkeypatch.delenv("TEST_OWNER_SECRET_HASH", raising=False)
    monkeypatch.setenv("TEST_SESSION_SECRET", "a-signing-key-for-the-test")
    return ownerauth.OwnerAuth(
        app="test",
        store=tmp_path / "sessions.json",
        secret_envs=("TEST_OWNER_SECRET",),
        hash_envs=("TEST_OWNER_SECRET_HASH",),
        session_secret_envs=("TEST_SESSION_SECRET",),
        session_key_file=tmp_path / ".session_key",
    )


@pytest.fixture
def clock(monkeypatch):
    """A movable clock, so expiry is tested by elapsing time rather than by
    sleeping through it."""
    state = {"t": 1_000_000.0}
    monkeypatch.setattr(ownerauth, "_now", lambda: state["t"])
    return state


# ── configuration ────────────────────────────────────────────────────────

def test_unconfigured_refuses_rather_than_inventing_a_default(tmp_path, monkeypatch):
    """An absent credential must never be a grant, and must not present as a
    wrong password either — there is no lock to pick."""
    monkeypatch.delenv("TEST_OWNER_SECRET", raising=False)
    a = ownerauth.OwnerAuth(app="test", store=tmp_path / "s.json",
                            secret_envs=("TEST_OWNER_SECRET",),
                            session_key_file=tmp_path / ".k")
    assert a.configured() is False
    outcome, cookie = a.begin("anything", client="1.2.3.4")
    assert outcome == ownerauth.UNCONFIGURED
    assert cookie == ""


def test_hashed_credential_is_preferred_over_a_stale_plaintext_one(tmp_path, monkeypatch):
    """If an operator stored a hash, a plaintext variable left behind in the
    environment must not quietly win."""
    monkeypatch.setenv("TEST_OWNER_SECRET", "the-old-one")
    monkeypatch.setenv("TEST_OWNER_SECRET_HASH", ownerauth.hash_secret("the-new-one"))
    a = ownerauth.OwnerAuth(app="test", store=tmp_path / "s.json",
                            secret_envs=("TEST_OWNER_SECRET",),
                            hash_envs=("TEST_OWNER_SECRET_HASH",),
                            session_key_file=tmp_path / ".k")
    assert a.begin("the-old-one", client="c")[0] == ownerauth.BAD_CREDENTIAL
    assert a.begin("the-new-one", client="c")[0] == ownerauth.OK


def test_scrypt_round_trip_and_rejection():
    stored = ownerauth.hash_secret(SECRET)
    assert stored.startswith("scrypt$")
    assert SECRET not in stored              # the point of hashing
    assert ownerauth.verify_secret(SECRET, stored) is True
    assert ownerauth.verify_secret(SECRET + "x", stored) is False


def test_a_malformed_hash_matches_nothing(tmp_path, monkeypatch, caplog):
    """A corrupted hash must fail closed, not fail open."""
    monkeypatch.setenv("TEST_OWNER_SECRET_HASH", "scrypt$not-hex$also-not-hex")
    a = ownerauth.OwnerAuth(app="test", store=tmp_path / "s.json",
                            secret_envs=(), hash_envs=("TEST_OWNER_SECRET_HASH",),
                            session_key_file=tmp_path / ".k")
    assert a.configured() is True
    assert a.begin("", client="c")[0] == ownerauth.BAD_CREDENTIAL
    assert a.begin("anything at all", client="c")[0] == ownerauth.BAD_CREDENTIAL


# ── the happy path ───────────────────────────────────────────────────────

def test_correct_credential_opens_a_readable_session(auth):
    outcome, cookie = auth.begin(SECRET, client="10.0.0.5", label="Phone")
    assert outcome == ownerauth.OK
    assert cookie
    s = auth.read(cookie)
    assert s is not None
    assert s["client"] == "10.0.0.5"
    assert s["label"] == "Phone"


def test_wrong_credential_opens_nothing(auth):
    outcome, cookie = auth.begin("not-the-secret", client="10.0.0.5")
    assert outcome == ownerauth.BAD_CREDENTIAL
    assert cookie == ""
    assert auth.sessions() == []


def test_the_cookie_is_not_the_credential(auth):
    """Whatever the browser holds, it must not be the thing that opens the door
    if it is stolen and replayed as a credential."""
    _, cookie = auth.begin(SECRET, client="c")
    assert SECRET not in cookie
    assert auth.begin(cookie, client="c")[0] == ownerauth.BAD_CREDENTIAL


# ── forgery ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("forged", [
    "",
    "no-separator",
    "sid.deadbeef",                      # right shape, wrong MAC
    "..",
    ".onlyamac",
    "onlyasid.",
])
def test_forged_cookies_are_refused(auth, forged):
    assert auth.read(forged) is None


def test_a_valid_mac_for_an_unknown_session_is_still_refused(auth):
    """The MAC proves this server minted the id. It does not prove the session
    still exists, and the record is what decides."""
    sid = "a-session-id-never-issued"
    cookie = f"{sid}.{auth._mac(sid)}"
    assert auth.read(cookie) is None


def test_swapping_the_sid_invalidates_the_mac(auth):
    _, cookie = auth.begin(SECRET, client="c")
    sid, _, mac = cookie.partition(".")
    assert auth.read(f"{sid}x.{mac}") is None


# ── revocation ───────────────────────────────────────────────────────────

def test_revoke_ends_the_session_server_side(auth):
    """The finding this module exists to fix: logging out used to delete the
    browser's copy of a cookie and nothing else, so a cookie captured
    beforehand kept working."""
    _, cookie = auth.begin(SECRET, client="c")
    assert auth.read(cookie) is not None
    assert auth.revoke(cookie) is True
    assert auth.read(cookie) is None


def test_revoke_all_signs_out_every_device(auth):
    cookies = [auth.begin(SECRET, client=f"c{i}")[1] for i in range(3)]
    assert len(auth.sessions()) == 3
    assert auth.revoke_all() == 3
    assert all(auth.read(c) is None for c in cookies)
    assert auth.sessions() == []


def test_rotate_issues_a_new_session_and_kills_the_old(auth):
    """Session fixation: an id that existed before a privilege change must not
    still be valid after it."""
    _, old = auth.begin(SECRET, client="c", label="Laptop")
    new = auth.rotate(old, client="c")
    assert new and new != old
    assert auth.read(old) is None
    assert auth.read(new) is not None
    assert auth.read(new)["label"] == "Laptop"     # carried over


def test_rotating_nothing_yields_nothing(auth):
    assert auth.rotate("garbage") == ""


# ── expiry ───────────────────────────────────────────────────────────────

def test_session_dies_at_the_absolute_ceiling(auth, clock):
    """Constant use must not be able to extend a session past its ceiling.

    Stepped in increments below the idle limit so the session is genuinely kept
    alive by activity — the interesting case. Jumping straight to the ceiling
    would prove only that idleness kills it, which the next test covers.
    """
    _, cookie = auth.begin(SECRET, client="c")
    step = auth.idle_seconds * 0.5
    elapsed = 0.0
    while elapsed + step < auth.absolute_seconds:
        clock["t"] += step
        elapsed += step
        assert auth.read(cookie) is not None, "use should hold off the idle limit"
    clock["t"] += auth.absolute_seconds - elapsed + 1
    assert auth.read(cookie) is None, (
        "an absolute ceiling that use can extend is not a ceiling")


def test_session_dies_when_left_idle(auth, clock):
    _, cookie = auth.begin(SECRET, client="c")
    clock["t"] += auth.idle_seconds + 1
    assert auth.read(cookie) is None


def test_use_refreshes_the_idle_clock(auth, clock):
    _, cookie = auth.begin(SECRET, client="c")
    for _ in range(3):
        clock["t"] += auth.idle_seconds * 0.6
        assert auth.read(cookie) is not None


def test_expired_sessions_do_not_accumulate_on_disk(auth, clock):
    for i in range(3):
        auth.begin(SECRET, client=f"c{i}")
    clock["t"] += auth.absolute_seconds + 1
    auth.begin(SECRET, client="fresh")       # issuing prunes
    assert len(auth.sessions()) == 1


# ── throttling ───────────────────────────────────────────────────────────

def test_the_first_few_mistakes_are_free(auth):
    """Typing a token wrong twice is a human being, not an attack."""
    for _ in range(ownerauth.FREE_ATTEMPTS):
        auth.begin("wrong", client="c")
    assert auth.retry_after("c") == 0.0


def test_backoff_grows_after_the_free_attempts(auth, clock):
    for _ in range(ownerauth.FREE_ATTEMPTS + 1):
        auth.begin("wrong", client="c")
    first = auth.retry_after("c")
    assert first > 0
    clock["t"] += first
    auth.begin("wrong", client="c")
    assert auth.retry_after("c") > first, "backoff must grow, not stay flat"


def test_lockout_refuses_even_the_correct_credential(auth):
    """Once locked out the credential is not read at all — so the compare cannot
    be used for timing, and a correct guess arriving mid-campaign does not win."""
    for _ in range(ownerauth.LOCKOUT_ATTEMPTS):
        auth.begin("wrong", client="c")
    assert auth.begin(SECRET, client="c")[0] == ownerauth.LOCKED_OUT
    assert auth.sessions() == []


def test_lockout_is_per_client(auth):
    for _ in range(ownerauth.LOCKOUT_ATTEMPTS):
        auth.begin("wrong", client="attacker")
    assert auth.begin(SECRET, client="the-owner")[0] == ownerauth.OK


def test_lockout_lifts_with_time(auth, clock):
    for _ in range(ownerauth.LOCKOUT_ATTEMPTS):
        auth.begin("wrong", client="c")
    clock["t"] += ownerauth.LOCKOUT_SECONDS + 1
    assert auth.begin(SECRET, client="c")[0] == ownerauth.OK


def test_a_successful_sign_in_clears_the_count(auth):
    for _ in range(ownerauth.FREE_ATTEMPTS + 1):
        auth.begin("wrong", client="c")
    assert auth.retry_after("c") > 0
    auth._attempts["c"] = [ownerauth.FREE_ATTEMPTS, 0.0]   # let one through
    auth.begin(SECRET, client="c")
    assert auth.retry_after("c") == 0.0
    assert auth.throttle_state("c")["failed_attempts"] == 0


def test_old_failures_are_forgotten(auth, clock):
    for _ in range(ownerauth.FREE_ATTEMPTS + 2):
        auth.begin("wrong", client="c")
    clock["t"] += ownerauth.ATTEMPT_MEMORY_SECONDS + 1
    assert auth.throttle_state("c")["failed_attempts"] == 0


# ── nothing leaks ────────────────────────────────────────────────────────

def test_status_reports_the_lock_never_the_key(auth):
    blob = repr(auth.status())
    assert SECRET not in blob
    assert auth.status()["authentication_configured"] is True


def test_the_session_list_carries_no_usable_credential(auth):
    _, cookie = auth.begin(SECRET, client="c", label="Phone")
    listing = repr(auth.sessions())
    assert SECRET not in listing
    assert cookie not in listing
    # A truncated id is enough to tell two rows apart and useless without the MAC.
    assert auth.sessions()[0]["id"] not in cookie.split(".")[1]


def test_nothing_writes_the_credential_to_a_log(auth, caplog):
    with caplog.at_level(logging.DEBUG):
        auth.begin(SECRET, client="c")
        auth.begin("a-wrong-guess-that-is-also-secret", client="c")
        auth.read("forged.cookie")
        auth.revoke_all()
    text = caplog.text
    assert SECRET not in text
    assert "a-wrong-guess-that-is-also-secret" not in text


def test_the_store_on_disk_holds_no_credential_and_no_cookie(auth):
    _, cookie = auth.begin(SECRET, client="c")
    raw = auth.store.read_text(encoding="utf-8")
    assert SECRET not in raw
    assert cookie not in raw, "the MAC must not be stored; it is recomputed"


# ── cookie parameters ────────────────────────────────────────────────────

def test_cookie_is_httponly_and_strict(auth):
    kw = auth.cookie_kwargs(https=True)
    assert kw["httponly"] is True
    assert kw["samesite"] == "strict"
    assert kw["secure"] is True


def test_secure_tracks_the_transport(auth):
    """Hardcoding secure=True would make local http development silently fail:
    the browser accepts the cookie and never sends it back, which presents as
    'signing in does nothing'."""
    assert auth.cookie_kwargs(https=False)["secure"] is False


def test_no_max_age_so_the_record_is_the_authority(auth):
    """An expiry the client enforces is a courtesy. The server's record is the
    control, and a session cookie also ends when the browser closes."""
    assert "max_age" not in auth.cookie_kwargs(https=True)


# ── persistence ──────────────────────────────────────────────────────────

def test_sessions_survive_a_restart(auth, tmp_path, monkeypatch):
    _, cookie = auth.begin(SECRET, client="c")
    reborn = ownerauth.OwnerAuth(
        app="test", store=auth.store,
        secret_envs=("TEST_OWNER_SECRET",),
        hash_envs=("TEST_OWNER_SECRET_HASH",),
        session_secret_envs=("TEST_SESSION_SECRET",),
        session_key_file=tmp_path / ".session_key")
    assert reborn.read(cookie) is not None, (
        "a restart must not sign the owner out — on a machine meant to run for "
        "months that presents as a random bug rather than a decision")


def test_an_unreadable_store_signs_everyone_out_rather_than_letting_them_in(auth):
    _, cookie = auth.begin(SECRET, client="c")
    auth.store.write_text("{ this is not json", encoding="utf-8")
    assert auth.read(cookie) is None


# ── step-up: authenticated is not the same as allowed right now ──────────

def test_a_fresh_sign_in_counts_as_confirmation(auth):
    """Nobody should be asked for the credential twice in the same minute."""
    _, cookie = auth.begin(SECRET, client="c")
    assert auth.recently_confirmed(cookie) is True


def test_confirmation_lapses(auth, clock):
    """A session open on a phone for days proves somebody signed in days ago,
    not that the owner is holding the phone when a trade is approved."""
    _, cookie = auth.begin(SECRET, client="c")
    clock["t"] += ownerauth.STEP_UP_SECONDS + 1
    assert auth.read(cookie) is not None, "the session itself is still alive"
    assert auth.recently_confirmed(cookie) is False


def test_confirm_renews_it(auth, clock):
    _, cookie = auth.begin(SECRET, client="c")
    clock["t"] += ownerauth.STEP_UP_SECONDS + 1
    assert auth.confirm(cookie, SECRET, client="c") == ownerauth.OK
    assert auth.recently_confirmed(cookie) is True


def test_confirm_with_the_wrong_credential_does_not_renew(auth, clock):
    _, cookie = auth.begin(SECRET, client="c")
    clock["t"] += ownerauth.STEP_UP_SECONDS + 1
    assert auth.confirm(cookie, "wrong", client="c") == ownerauth.BAD_CREDENTIAL
    assert auth.recently_confirmed(cookie) is False


def test_confirm_is_throttled_like_sign_in(auth):
    """A confirmation prompt is a sign-in form by another name. Unthrottled, a
    stolen cookie would be an unlimited oracle for the credential."""
    _, cookie = auth.begin(SECRET, client="c")
    for _ in range(ownerauth.LOCKOUT_ATTEMPTS):
        auth.confirm(cookie, "wrong", client="c")
    assert auth.confirm(cookie, SECRET, client="c") == ownerauth.LOCKED_OUT


def test_confirm_does_not_reveal_whether_a_session_exists(auth):
    """Right credential, dead cookie: same answer as a wrong credential, so the
    endpoint cannot be used to learn which cookies are alive."""
    _, cookie = auth.begin(SECRET, client="c")
    auth.revoke(cookie)
    assert auth.confirm(cookie, SECRET, client="c") == ownerauth.BAD_CREDENTIAL
    assert auth.confirm("forged.cookie", SECRET, client="c") == ownerauth.BAD_CREDENTIAL


def test_a_dead_session_is_never_recently_confirmed(auth):
    _, cookie = auth.begin(SECRET, client="c")
    auth.revoke(cookie)
    assert auth.recently_confirmed(cookie) is False
    assert auth.recently_confirmed("forged.cookie") is False


def test_a_session_from_before_step_up_existed_must_confirm_once(auth):
    """Records written before `confirmed_at` existed must not inherit an
    authorisation they were never given."""
    _, cookie = auth.begin(SECRET, client="c")
    records = auth._load()
    for r in records.values():
        r.pop("confirmed_at", None)
    auth._save(records)
    assert auth.read(cookie) is not None
    assert auth.recently_confirmed(cookie) is False


# ── nothing credential-shaped reaches ANY log ────────────────────────────

@pytest.mark.parametrize("line,secret", [
    ('GET /api/x?token=s3cr3t-value-here HTTP/1.1', "s3cr3t-value-here"),
    ('GET /api/x?a=1&api_key=abcdef123456&b=2', "abcdef123456"),
    ('POST /login?password=hunter2hunter2', "hunter2hunter2"),
    ('Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig', "eyJhbGciOiJIUzI1NiJ9"),
    ("headers={'x-aria-token': 'owner-token-value-123'}", "owner-token-value-123"),
    ('cookie=sentinel_session=abc.def0123456789', "abc.def0123456789"),
])
def test_redact_scrubs_credential_shapes(line, secret):
    out = ownerauth.redact(line)
    assert secret not in out
    assert ownerauth.REDACTED in out


@pytest.mark.parametrize("line", [
    "GET /api/v5/analyse/AAPL HTTP/1.1",
    "GET /api/universe/search?q=apple&limit=10",
    "session opened for 203.0.113.9",
])
def test_redact_leaves_ordinary_lines_alone(line):
    assert ownerauth.redact(line) == line


def test_an_access_log_line_with_a_url_token_is_scrubbed_before_any_handler(caplog):
    """Uvicorn's access log passes the request line as an ARGUMENT and writes it
    before the application refuses a URL token. Redaction at record creation is
    the only point that sees it in time."""
    ownerauth.install_log_redaction()
    ownerauth.install_log_redaction()              # idempotent
    log = logging.getLogger("uvicorn.access.test")
    with caplog.at_level(logging.INFO):
        log.info('%s - "%s" %d', "203.0.113.9",
                 "GET /api/permissions?token=a-real-looking-token-123 HTTP/1.1", 400)
    assert "a-real-looking-token-123" not in caplog.text
    assert ownerauth.REDACTED in caplog.text


def test_a_non_string_argument_carrying_a_credential_is_scrubbed(caplog):
    """httpx logs the URL as an object. A str-only check missed it — and that is
    the path an outbound vendor call with `?api_key=` would take."""
    class URL:
        def __str__(self):
            return "https://api.example.com/v1/series?id=GDP&api_key=vendor-key-0123456789"
    ownerauth.install_log_redaction()
    with caplog.at_level(logging.INFO):
        # Not "httpx.*": ARIA quietens that logger to WARNING, so an INFO
        # record there is never created and the test would measure nothing.
        logging.getLogger("ownerauth.test.outbound").info(
            'HTTP Request: %s %s "%d"', "GET", URL(), 200)
    assert "vendor-key-0123456789" not in caplog.text
    assert "200" in caplog.text, "numeric args must still format"
