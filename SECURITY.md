# Security

ARIA holds a broker connection, a personal knowledge base and an LLM. This
document says what is defended, and — more usefully — what is not.

No software is unhackable, and a page claiming otherwise is a warning sign
rather than a reassurance. What follows is meant to be accurate enough to make
decisions from.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository, or email the
maintainer. Please don't open a public issue for anything exploitable.

---

## What is enforced

**Access control.** Two roles over a default-deny allowlist. A route not
explicitly named is owner-only, so an endpoint added tomorrow is invisible to
everyone else until someone decides otherwise. Anonymous callers reach only
`/health` and the sign-in routes.

**Three things are never a tier.** The Obsidian vault, the portfolio and
execution are unreachable for any role but owner, and no plan, flag or upgrade
changes that. The vault check lives inside the function that would ground a
reply in those notes, so there is no configuration that shares them.

**Identity.** One owner credential (`ARIA_OWNER_TOKEN`, or its scrypt hash in
`ARIA_OWNER_TOKEN_HASH`). Google, GitHub and Apple sign-in were removed. The
browser posts the credential once to `/api/auth/login` and receives a session
cookie; it never stores the credential. Scripts may send it as a bearer token.

**Ownership cannot be claimed.** A session exists only because the credential
was presented, so there is no stored owner flag to forge. Loopback-inferred
ownership (no credential configured at all) never reaches a broker.

**Sessions.** Server-side records (`src/auth/ownerauth.py`, shared with
SENTINEL): 7-day absolute and 2-day idle expiry, revocable one at a time or all
at once, listed at `/api/auth/sessions`. Cookies are `HttpOnly`,
`SameSite=Strict`, and `Secure` under HTTPS.

**Brute force.** Failed sign-ins back off exponentially after four attempts and
lock the client out for 15 minutes after twelve; a locked-out caller's
credential is not read at all.

**CSRF.** `SameSite=Strict`, plus: mutating requests carrying a browser `Origin`
must match the allowlist or the app's own origin.

**Rate limits.** Per-IP fixed windows, tightest on sign-in and chat. Applied
before authentication, so an unauthenticated flood is not free.

**Headers.** CSP with `script-src 'self'`, `frame-ancestors 'none'`, nosniff,
`Referrer-Policy`, `Permissions-Policy` denying hardware the app never uses,
and HSTS over real HTTPS.

**The service worker caches the app shell and nothing else.** `/api/*` is
network-only with no fallback. Caching authenticated responses would serve one
user's portfolio to the next person on that device, since the Cache API is
per-origin and not per-user — and a cached price looks exactly like a live one.

---

## What is NOT protected — read this part

**The machine itself.** The intended host is a 2014 Mac mini on macOS Monterey,
which **no longer receives Apple security updates**. That is a real and
unfixable-in-software exposure: an OS-level vulnerability has no patch. Keep the
box on a network you control, behind the tunnel, with FileVault on. Do not treat
it as a hardened server, because it is not one.

**Prompt injection.** ARIA reads news articles, filings and vault notes into
model prompts. Text in those sources can contain instructions aimed at the
model, and nothing here reliably prevents that. The mitigation is structural
rather than clever: the model cannot execute trades, cannot reach owner-only
routes, and every proposal stops at human approval. Treat model output as a
suggestion from an untrusted source, because in the presence of injection that
is exactly what it is.

**Rate limiting is in-memory and single-process.** It resets on restart and does
not span workers. Enough for one process behind one tunnel; not a substitute for
a gateway if this ever scales.

**No session revocation.** Cookies are valid for 14 days and there is no
server-side list to invalidate one early. Rotating `ARIA_SESSION_SECRET` signs
everybody out at once, which is the only lever available.

**No 2FA of ARIA's own.** Account security is whatever your Google or GitHub
account has. Turn on 2FA there — it is the actual front door to the owner role.

**Dependencies are not pinned or audited.** No lockfile discipline, no SCA
scanning. A compromised upstream package would be a compromised ARIA.

**Data at rest is not encrypted by the application.** Debate transcripts,
lessons and cached market data are plain files. Disk encryption is the operating
system's job here.

**No third-party audit.** This has not been penetration tested. The threat model
is "a small number of known people, plus whatever finds the domain" — not a
determined attacker who wants in specifically.

---

## If you are deploying this

- Set `ARIA_OWNER_TOKEN`. Without it, owner is granted by loopback, and on a
  server that means anything running on the box is you.
- Set `ARIA_TRUST_PROXY=1` **only** behind a proxy that rewrites forwarding
  headers. Set it on a directly exposed host and every rate limit becomes
  bypassable by inventing a client IP per request.
- Bind uvicorn to `127.0.0.1` and reach it through the tunnel. Never `0.0.0.0`.
- Run `scripts/mini_preflight.py` and clear every blocker. It fails the run if
  `auto_execute` is true, which on an unattended machine is the difference
  between a research tool and one that places orders while you sleep.
- Keep `auto_execute` false and the account on paper until you have a reason
  measured in resolved trades rather than in enthusiasm.
