# ARIA — authentication setup

One credential, one session, one cookie. Shared implementation with SENTINEL.

---

## The short version

```bash
# 1. produce a credential (nothing is written into the repository)
venv\Scripts\python.exe scripts/init_owner_auth.py

# 2. put the printed lines in .env, or in the service environment
# 3. restart the backend
# 4. check it took
curl -s http://127.0.0.1:8000/api/auth/status
```

`/api/auth/status` reporting `"authentication_configured": true` is the whole
acceptance test for this page.

---

## What changed, and why you have to do something

Two things were removed at once.

**Google, GitHub and Apple sign-in are gone.** With them went
`src/auth/oauth.py`, the `/api/auth/providers`, `/api/auth/login/{provider}` and
`/api/auth/callback/{provider}` routes, `src/auth/users.py` and
`/api/auth/users`, and six environment variables. ARIA has one human now and
`ARIA_OWNER_EMAIL` no longer configures anything.

**The browser no longer holds a credential.** It used to keep the raw
`ARIA_OWNER_TOKEN` in `localStorage` and attach it to every request. That worked,
and it meant one XSS on this origin handed an attacker a permanent key to the
broker routes — permanent literally: no expiry, no revocation, no way to end a
session short of rotating the token and signing every device out at once.

What replaced both:

```
owner credential  ──►  POST /api/auth/login  ──►  session record on disk
                                                        │
                                                        ▼
                                          HttpOnly cookie naming that record
```

The cookie is not a credential and cannot be replayed as one. It names a record
that expires, that can be revoked, and that shows up in a list of signed-in
devices.

---

## Configuring the credential

### Generated (recommended)

```bash
venv\Scripts\python.exe scripts/init_owner_auth.py
```

256 bits of randomness. There is no dictionary to work through, so storing it in
a file only you can read is sound and is the simplest thing that works.

### Hashed

```bash
venv\Scripts\python.exe scripts/init_owner_auth.py --hash
venv\Scripts\python.exe scripts/init_owner_auth.py --passphrase
```

Stores an scrypt hash in `ARIA_OWNER_TOKEN_HASH` instead of the value in
`ARIA_OWNER_TOKEN`. Choose it when the credential is something a human picked
and might have reused, or when the environment file is more exposed than the
machine — a shared host, config management, a backup that leaves the box. The
server verifies against the hash and cannot recover the credential from it.

When both variables are set the **hash wins**, so a stale plaintext value left
behind in a `.env` cannot quietly re-open an old credential.

### The variables

| Variable | What it is |
|---|---|
| `ARIA_OWNER_TOKEN` | the credential, in the clear |
| `ARIA_OWNER_TOKEN_HASH` | the credential as `scrypt$salt$hash` — wins if both are set |
| `ARIA_SESSION_SECRET` | signs session ids. Persisted to `data/.session_key` if unset |
| `ARIA_SERVICE_TOKEN` | SENTINEL's scoped peer credential. **Must differ** from the owner's |
| `SENTINEL_SERVICE_TOKEN` | ARIA's credential for consulting SENTINEL. Must differ from both of the above |
| `ARIA_TRUST_PROXY` | set to `1` when something sits in front of this app |
| `ARIA_BASE_URL` | the public URL, e.g. `https://aria.example.com` |
| `ARIA_LIVE_TRADING_ENABLED` | must be exactly `true` before `LIVE_AUTONOMOUS` is reachable. Unset here |

`ARIA_SERVICE_TOKEN` equal to `ARIA_OWNER_TOKEN` disables the service role
outright and logs an error. A scoped credential that equals the owner's is not
scoped.

### Four credentials, four jobs

| credential | direction | held by |
|---|---|---|
| `ARIA_OWNER_TOKEN` | human → ARIA | ARIA only |
| `SENTINEL_TOKEN` | human → SENTINEL | SENTINEL only |
| `ARIA_SERVICE_TOKEN` | SENTINEL → ARIA | both, same value |
| `SENTINEL_SERVICE_TOKEN` | ARIA → SENTINEL | both, same value |

Never reuse one for another job. Each side refuses a peer credential that equals
an owner credential or the other direction's peer credential, and logs why — a
value that opens both directions means a leak at either end opens both.

```bash
python scripts/init_service_tokens.py           # report
python scripts/init_service_tokens.py --apply   # generate what is missing
python scripts/mini_preflight.py                # confirms all four are distinct
```

Neither script ever prints a credential.

---

## If nothing is configured

```
SERVER NOT INITIALIZED
```

The login route answers **503**, the login page says so, and the startup log
says so. It does not invent a default. Every system that has invented one has
shipped `admin` or `changeme`.

There is one deliberate exception, unchanged from before: with **nothing**
configured, a request arriving on a real loopback socket with no proxy anywhere
is treated as the owner, so you are not locked out of your own laptop before you
have set a credential. That basis is `loopback` and it is **INFERRED**, not
proven — every route that can reach a broker refuses it (see `PROVEN_BASES` in
`src/auth/policy.py`), and the first request carrying a forwarding header
retires it for the life of the process.

---

## Signing in

### From the browser

Open ARIA, paste the credential once. The response sets an HttpOnly cookie the
page cannot read. Nothing is stored in `localStorage`.

### From a script

```bash
curl -s http://127.0.0.1:8000/api/summary \
     -H "Authorization: Bearer $ARIA_OWNER_TOKEN"
```

or `X-ARIA-Token: <token>`. This is the **break-glass path** and it is not going
anywhere — it is what the deploy check, the watchdog and a terminal use, and the
way back in if the session store is lost. What changed is that the browser no
longer uses it.

**Never in a query string.** A URL is recorded by proxies, browser history and
`Referer` headers.

---

## Sessions

| | |
|---|---|
| Absolute lifetime | 7 days, whatever you are doing |
| Idle lifetime | 2 days |
| Cookie | HttpOnly, `SameSite=Strict`, `Secure` over HTTPS, no `Max-Age` |
| Store | `data/auth/sessions.json`, 0600, gitignored |

No `Max-Age` is deliberate. An expiry the browser enforces is a courtesy; the
record is the control, and a session cookie also ends when the browser closes.

```
GET    /api/auth/status              public — is a lock installed
POST   /api/auth/login               credential -> cookie
POST   /api/auth/logout              revokes THIS session, then clears the cookie
GET    /api/auth/me                  who the server thinks you are
GET    /api/auth/sessions            owner-only — which devices are signed in
POST   /api/auth/logout-everywhere   owner-only — end all of them
```

`/api/auth/sessions` carries truncated ids and no MACs, so the listing cannot be
turned back into a working cookie.

---

## Failed sign-ins

Implemented once, in `src/auth/ownerauth.py`, so ARIA and SENTINEL cannot drift
into different answers.

| Failures | What happens |
|---|---|
| 1–4 | nothing. Typing a token wrong twice is a human being |
| 5+ | exponential backoff: 2s, 4s, 8s … capped at 15 minutes |
| 12 | locked out for 15 minutes |
| — | a run is forgotten after an hour without a failure |
| — | a successful sign-in clears the count |

A locked-out caller's credential is **not read at all**, so the comparison
cannot be used for timing and a correct guess arriving mid-campaign does not
win. Counting is per client address; the per-IP limiter in `_role_guard` is a
second, coarser layer above it.

**Behind a tunnel, set `ARIA_TRUST_PROXY=1`.** Every request then arrives from
127.0.0.1 — cloudflared's own socket — and without it the whole internet lands
in one bucket, so any stranger can lock you out of your own machine.

---

## Rotating the credential

```bash
venv\Scripts\python.exe scripts/init_owner_auth.py --force
```

Then restart. **Every session dies with the credential**, which is the point:
sessions minted under the old one must not survive it. Sign in again on each
device.

To end sessions without changing the credential:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/logout-everywhere \
     -H "Authorization: Bearer $ARIA_OWNER_TOKEN"
```

---

## Verifying it yourself

```bash
# 1. anonymous callers get nothing
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/portfolio
# 401

# 2. a wrong credential is refused
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     http://127.0.0.1:8000/api/auth/login \
     -H 'Content-Type: application/json' -d '{"token":"wrong"}'
# 401

# 3. the right one opens a session
curl -s -c jar.txt -X POST http://127.0.0.1:8000/api/auth/login \
     -H 'Content-Type: application/json' \
     -d "{\"token\":\"$ARIA_OWNER_TOKEN\"}"
# {"ok":true,"role":"owner"}

# 4. the cookie works, and carries no credential
grep -o 'aria_session.*' jar.txt      # not your token
curl -s -b jar.txt http://127.0.0.1:8000/api/auth/me

# 5. logging out ends it SERVER-SIDE
curl -s -b jar.txt -X POST http://127.0.0.1:8000/api/auth/logout
curl -s -o /dev/null -w '%{http_code}\n' -b jar.txt \
     http://127.0.0.1:8000/api/auth/me
# still 200, but "authenticated": false — the session is gone, not just the cookie

rm jar.txt
```

The same properties are pinned by `tests/test_ownerauth.py` (40 assertions, run
against both ARIA's and SENTINEL's copies of the module) and
`tests/test_auth_session.py`.

---

## Related

- `src/auth/ownerauth.py` — the shared core. Canonical copy; SENTINEL's is
  synchronised by `scripts/sync_ownerauth.py` and pinned by a checksum test in
  both repositories.
- `src/auth/policy.py` — authorization: who may reach which route.
- `src/auth/guard.py` — `require_owner`, on every route that can reach a broker.
- `atlas/docs/AUTH_SETUP.md` — SENTINEL's half, and the two peer credentials.
