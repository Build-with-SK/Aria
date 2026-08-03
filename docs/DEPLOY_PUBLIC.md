# Putting ARIA on the internet

The shape: **frontend static on Cloudflare Pages, backend on the Mac mini behind
a Cloudflare Tunnel.** Nothing is port-forwarded, your home IP is never
published, and you get HTTPS on a real domain — which OAuth requires anywhere
but localhost.

```
  phone / laptop
        │  https://aria.yourdomain.com
        ▼
  Cloudflare edge ──── Pages (static frontend)
        │
        │  outbound-only tunnel, no open ports
        ▼
  Mac mini  →  uvicorn on 127.0.0.1:8000
```

The tunnel is the important part. `cloudflared` dials **out** to Cloudflare and
keeps the connection open, so the mini needs no inbound firewall rule, no static
IP and no port forwarding. There is no port on your router for anyone to find.

---

## 1. On the mini — install and authenticate

```bash
brew install cloudflared          # or the .pkg from Cloudflare's downloads
cloudflared tunnel login          # opens a browser; pick your domain
cloudflared tunnel create aria    # prints a tunnel UUID and writes a credentials JSON
```

## 2. Route the hostname

```bash
cloudflared tunnel route dns aria aria.yourdomain.com
```

## 3. Config

Copy `scripts/cloudflared-config.example.yml` to `~/.cloudflared/config.yml`
and fill in your UUID and hostname. Then:

```bash
cloudflared tunnel run aria                    # test in the foreground
sudo cloudflared service install                # then run it permanently
```

## 4. Point ARIA at its new address

In `.env` on the mini:

```bash
ARIA_BASE_URL=https://aria.yourdomain.com
ARIA_FRONTEND_URL=https://aria.yourdomain.com
ARIA_TRUST_PROXY=1
ARIA_OWNER_TOKEN=<generate one>
```

`ARIA_TRUST_PROXY=1` tells ARIA to read the real client IP from Cloudflare's
headers. **Only set it when actually behind the tunnel.** Set it on a directly
exposed server and anyone can spoof `X-Forwarded-For` and walk straight through
every rate limit by inventing a new IP per request.

Set `ARIA_OWNER_TOKEN` before you go public. Without it, owner is granted by
loopback — which is fine on a laptop, but on the mini means anything running
*on that machine* is you.

Generate one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 5. Update the OAuth callbacks

Both must change together — the provider rejects any mismatch.

| Provider | New callback |
|---|---|
| Google | `https://aria.yourdomain.com/api/auth/callback/google` |
| GitHub | `https://aria.yourdomain.com/api/auth/callback/github` |

Google allows several redirect URIs on one client, so keep localhost alongside
it for development. **GitHub allows only one** — either change it and stop
developing against localhost, or register a second OAuth app for the mini and
keep the two sets of credentials in separate `.env` files.

## 6. Serve the frontend

```bash
cd frontend && node node_modules/vite/bin/vite.js build
```

Point Cloudflare Pages at `frontend/dist`, or let the tunnel serve it — the
example config has a commented ingress rule for that.

Whichever you choose, the frontend and API must end up on the **same origin**.
The session cookie is `SameSite=Lax`, and the CSP says `connect-src 'self'`;
splitting them across hostnames breaks both.

---

## Before you tell anyone the URL

- [ ] `ARIA_OWNER_TOKEN` set, and `ARIA_OWNER_EMAIL` matching your Google account
- [ ] `venv/bin/python scripts/mini_preflight.py` reports no blockers
- [ ] `auto_execute` is **false** — the preflight fails the run if it is not
- [ ] Signed in from your phone: research works, `/api/portfolio` returns 403
      when signed in as anyone else
- [ ] `curl -sI https://aria.yourdomain.com/health` shows
      `strict-transport-security` (it only appears over real HTTPS)

## What this does not protect against

Read `SECURITY.md`. Short version: this hardens the front door. It does not
make the machine invulnerable, and the honest limitations are written down
there rather than left implied.
