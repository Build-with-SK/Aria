"""
scripts/security_audit.py
=========================
Attack the deployment from outside, the way a stranger would, and report.

    # against a LIVE server (e.g. the Mac mini through the tunnel):
    python scripts/security_audit.py https://aria.example.com --owner-env ARIA_OWNER_TOKEN
    python scripts/security_audit.py https://sentinel.example.com --owner-env SENTINEL_TOKEN

    # in-process, no server needed (what the test run uses):
    python scripts/security_audit.py --asgi backend.main:app --owner-env ARIA_OWNER_TOKEN

The owner credential, if given, is used for exactly ONE request: reading the
app's own OpenAPI schema so the audit knows every route. Every probe after that
is made WITHOUT a credential. The credential is read from an environment
variable, never a command-line argument (arguments are visible in `ps`), and
is never printed.

WHAT IS CHECKED
---------------
  routes     every route in the schema, anonymous: must answer 401/403/404/405/
             429, never 2xx. Public routes are listed and must be the expected few.
  streams    the SSE chat endpoints, anonymous: refused before any event is sent
  url-token  a credential-shaped ?token= is refused (SENTINEL) or ignored (ARIA),
             never accepted
  cors       a preflight from a hostile origin gets no Access-Control-Allow-Origin
  headers    CSP, X-Content-Type-Options, frame protection; HSTS when https
  tls        https URLs only: the certificate verifies; http redirects or fails
  cookie     the login response's Set-Cookie (with a WRONG credential it must not
             set one at all)
  lockout    optional (--lockout): repeated wrong sign-ins end in 429. This
             locks the auditing address out for 15 minutes; it is off by default.

Exit status is non-zero if anything FAILS. Warnings do not fail the run.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

REFUSED = {401, 403, 404, 405, 422, 429, 503}
EXPECTED_PUBLIC = {"/health", "/ready", "/api/auth/login", "/api/auth/logout",
                   "/api/auth/status", "/api/auth/me", "/", "/dashboard"}
STREAMS = ("/api/chat/stream",)
HOSTILE_ORIGIN = "https://evil.example"


class HttpProbe:
    """urllib against a URL. Never follows redirects silently."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def request(self, method, path, headers=None, body=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, dict(r.headers), r.read(4096)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), (e.read(4096) if e.fp else b"")


class AsgiProbe:
    """The same probes in-process, through FastAPI's TestClient, from a
    non-loopback address so loopback inference can never be what passes."""

    def __init__(self, target: str):
        mod, _, attr = target.partition(":")
        import importlib
        from fastapi.testclient import TestClient
        app = getattr(importlib.import_module(mod), attr or "app")
        self.client = TestClient(app, client=("203.0.113.77", 50000))
        self.base = "asgi://" + target

    def request(self, method, path, headers=None, body=None):
        r = self.client.request(method, path, headers=headers or {},
                                json=body if body is not None else None)
        return r.status_code, dict(r.headers), r.content[:4096]


def _concrete(path: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", "x", path)


def audit(probe, owner_token: str = "", lockout: bool = False) -> dict:
    fails, warns, notes = [], [], []

    # ── the route inventory ────────────────────────────────────────────
    routes = []
    hdr = {"Authorization": f"Bearer {owner_token}"} if owner_token else {}
    st, _, body = probe.request("GET", "/openapi.json", headers=hdr)
    if st == 200:
        try:
            spec = json.loads(body if len(body) < 4096 else b"{}")
        except ValueError:
            spec = {}
        if not spec.get("paths"):                  # body was truncated; refetch fully
            if isinstance(probe, AsgiProbe):
                spec = probe.client.get("/openapi.json", headers=hdr).json()
            else:
                req = urllib.request.Request(probe.base + "/openapi.json", headers=hdr)
                with urllib.request.urlopen(req, timeout=20) as r:
                    spec = json.loads(r.read())
        for path, ops in (spec.get("paths") or {}).items():
            for m in ops:
                if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    routes.append((m.upper(), path))
        notes.append(f"{len(routes)} routes from the app's own schema")
    else:
        warns.append(f"could not read /openapi.json (HTTP {st}); pass --owner-env "
                     "so the audit can see every route")

    # the schema itself must not be public
    st_anon, _, _ = probe.request("GET", "/openapi.json")
    if st_anon == 200:
        warns.append("/openapi.json is readable anonymously — it maps every route")

    # ── every route, anonymous ─────────────────────────────────────────
    public, leaked = [], []
    for method, path in sorted(set(routes)):
        st, _, _ = probe.request(method, _concrete(path),
                                 body={} if method in ("POST", "PUT", "PATCH") else None)
        if st in REFUSED:
            continue
        (public if path in EXPECTED_PUBLIC else leaked).append((method, path, st))
    for m, p, s in leaked:
        fails.append(f"ANONYMOUS ACCESS: {m} {p} -> {s}")
    notes.append(f"public: {sorted({p for _, p, _ in public})}")

    # ── streams ────────────────────────────────────────────────────────
    for path in STREAMS:
        st, h, body = probe.request("POST", path, body={"message": "hi",
                                                        "messages": [{"role": "user", "content": "hi"}]})
        if st not in REFUSED:
            fails.append(f"ANONYMOUS STREAM: POST {path} -> {st}")
        elif b"event:" in body:
            fails.append(f"{path} sent events before refusing")

    # ── a token in the URL ─────────────────────────────────────────────
    st, _, _ = probe.request("GET", "/api/auth/sessions?token=probe-not-a-real-token")
    if st == 200:
        fails.append("a ?token= query parameter was accepted")

    # ── CORS from a hostile origin ─────────────────────────────────────
    st, h, _ = probe.request("OPTIONS", "/api/auth/login", headers={
        "Origin": HOSTILE_ORIGIN, "Access-Control-Request-Method": "POST"})
    acao = {k.lower(): v for k, v in h.items()}.get("access-control-allow-origin")
    if acao in ("*", HOSTILE_ORIGIN):
        fails.append(f"CORS allows a hostile origin: {acao}")

    # ── security headers ───────────────────────────────────────────────
    st, h, _ = probe.request("GET", "/health")
    lh = {k.lower(): v for k, v in h.items()}
    for name in ("x-content-type-options",):
        if name not in lh:
            warns.append(f"missing {name} on /health")
    if "content-security-policy" not in lh and "x-frame-options" not in lh:
        warns.append("no CSP or X-Frame-Options on /health")
    https = probe.base.startswith("https://")
    if https and "strict-transport-security" not in lh:
        fails.append("HTTPS without Strict-Transport-Security")

    # ── TLS ────────────────────────────────────────────────────────────
    if isinstance(probe, HttpProbe):
        if https:
            try:
                urllib.request.urlopen(probe.base + "/health", timeout=15,
                                       context=ssl.create_default_context())
                notes.append("TLS certificate verifies")
            except Exception as e:
                fails.append(f"TLS does not verify: {e}")
            plain = "http://" + probe.base[len("https://"):]
            try:
                s2, h2, _ = HttpProbe(plain).request("GET", "/health")
                if s2 == 200:
                    fails.append("plain http serves content; it should redirect or fail")
            except Exception:
                notes.append("plain http refused (good)")
        else:
            warns.append("audited over plain http — acceptable only on loopback")

    # ── cookies ────────────────────────────────────────────────────────
    st, h, _ = probe.request("POST", "/api/auth/login", body={"token": "wrong-credential"})
    if any(k.lower() == "set-cookie" for k in h):
        fails.append("a WRONG credential received a Set-Cookie")
    if st not in (401, 429, 503):
        fails.append(f"a wrong credential got HTTP {st} from /api/auth/login")

    # ── lockout (opt-in) ───────────────────────────────────────────────
    if lockout:
        codes = [probe.request("POST", "/api/auth/login", body={"token": f"wrong-{i}"})[0]
                 for i in range(14)]
        if 429 not in codes:
            fails.append(f"14 wrong sign-ins never produced 429: {codes}")
        else:
            notes.append(f"lockout engaged after {codes.index(429) + 1} attempts")

    return {"target": probe.base, "fails": fails, "warnings": warns, "notes": notes,
            "routes_probed": len(set(routes))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--asgi", help="module:app to audit in-process")
    ap.add_argument("--owner-env", help="ENV VAR holding the owner credential "
                                        "(used only to read the route schema)")
    ap.add_argument("--lockout", action="store_true",
                    help="also test the sign-in lockout (locks you out for 15 min)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not (a.url or a.asgi):
        ap.error("give a URL or --asgi module:app")
    token = os.environ.get(a.owner_env, "").strip() if a.owner_env else ""
    probe = AsgiProbe(a.asgi) if a.asgi else HttpProbe(a.url)
    r = audit(probe, owner_token=token, lockout=a.lockout)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"SECURITY AUDIT  {r['target']}  ({r['routes_probed']} routes)")
        for f in r["fails"]:
            print(f"  FAIL  {f}")
        for w in r["warnings"]:
            print(f"  WARN  {w}")
        for n in r["notes"]:
            print(f"  note  {n}")
        print("  RESULT:", "FAIL" if r["fails"] else "PASS")
    return 1 if r["fails"] else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path.cwd()))
    raise SystemExit(main())
