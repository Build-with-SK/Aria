#!/usr/bin/env python3
"""
scripts/mini_preflight.py
=========================
Run this ON THE MAC MINI, before letting it run ARIA 24/7.

It answers one question: what is different here that will bite us?

The mini is not a smaller laptop. It is a late-2014 Intel machine on macOS
Monterey, it has no ChromaDB (deliberately — the dependency is commented out in
requirements.txt for low-power hosts), it reaches Ollama over Wi-Fi to another
machine that may be behind a VPN, and it is the box that will be left running
unattended with a broker connection. Every one of those is a place where
"works on my laptop" stops being evidence.

This script tests rather than assumes. Nothing here is a guess about macOS; each
check actually imports the module, opens the socket, or writes the file.

    python3 scripts/mini_preflight.py

Exit codes:  0 = ready   1 = blockers found
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BLOCKERS: list[str] = []
WARNINGS: list[str] = []

G, Y, R, B, X = "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    G = Y = R = B = X = ""


def ok(m):   print(f"  {G}PASS{X}  {m}")
def warn(m): print(f"  {Y}WARN{X}  {m}"); WARNINGS.append(m)
def bad(m):  print(f"  {R}FAIL{X}  {m}"); BLOCKERS.append(m)
def head(m): print(f"\n{B}{m}{X}\n" + "-" * len(m))


# ── 1. the machine ───────────────────────────────────────────────────────────

def check_machine():
    head("1. Machine")
    print(f"  {platform.platform()}")
    print(f"  python {sys.version.split()[0]} ({platform.machine()})")

    v = sys.version_info
    if v < (3, 11):
        bad(f"Python {v.major}.{v.minor} is too old — the codebase uses 3.11+ syntax "
            "(`X | None` unions, match). Install 3.12.")
    elif v[:2] == (3, 11) or v[:2] == (3, 12):
        ok(f"Python {v.major}.{v.minor} is supported")
    else:
        warn(f"Python {v.major}.{v.minor} is newer than tested (3.11/3.12). "
             "yfinance/pandas wheels may not exist yet for it.")

    if platform.system() != "Darwin":
        warn(f"This is {platform.system()}, not macOS — preflight assumes the mini.")

    # Rosetta / arch mismatch silently halves performance and breaks some wheels.
    if platform.system() == "Darwin" and platform.machine() == "x86_64":
        ok("Intel Mac — matches the late-2014 mini")


# ── 2. dependencies ──────────────────────────────────────────────────────────

REQUIRED = ["fastapi", "uvicorn", "pandas", "numpy", "yfinance", "apscheduler"]
# Absent on the mini BY DESIGN. The point is to prove absence degrades, not crashes.
OPTIONAL = ["chromadb", "sentence_transformers", "lightgbm", "torch"]


def check_deps():
    head("2. Dependencies")
    for m in REQUIRED:
        try:
            __import__(m)
            ok(f"{m}")
        except Exception as e:
            bad(f"{m} missing or broken: {e}")

    print()
    for m in OPTIONAL:
        try:
            __import__(m)
            print(f"  {G}present{X}  {m}")
        except Exception:
            print(f"  {Y}absent {X}  {m}  (expected on the mini)")


# ── 3. the thing that must work without ChromaDB ─────────────────────────────

def check_v5_without_chromadb():
    head("3. V5 engine runs without ChromaDB")
    try:
        import chromadb  # noqa: F401
        have_chroma = True
    except Exception:
        have_chroma = False

    try:
        from src.v5 import registry
        registry.load_modules()
        n = len(registry.list_modules())
        ok(f"src.v5 imports cleanly ({n} modules registered)"
           + ("" if have_chroma else " — with NO chromadb installed"))
    except Exception as e:
        bad(f"src.v5 failed to import: {e}")
        return

    # The vault/memory path MUST degrade rather than raise, or chat dies here.
    try:
        from src.brain.vault import get_vault
        try:
            get_vault()
            ok("vault constructed (chromadb present)")
        except Exception as e:
            ok(f"vault unavailable but raised cleanly ({type(e).__name__}) — "
               "callers catch this and return empty context")
    except Exception as e:
        warn(f"could not even import vault module: {e}")


# ── 4. filesystem ────────────────────────────────────────────────────────────

def check_filesystem():
    head("4. Filesystem")
    for d in ["data", "data/desk", "data/v5", "data/cache"]:
        p = ROOT / d
        try:
            p.mkdir(parents=True, exist_ok=True)
            t = p / ".preflight_write_test"
            t.write_text("ok", encoding="utf-8")
            t.unlink()
            ok(f"{d}/ writable")
        except Exception as e:
            bad(f"{d}/ not writable: {e}")

    # macOS is case-INsensitive by default; Linux/CI is not. A wrong-case import
    # passes here and fails on any case-sensitive host.
    probe = ROOT / "data" / ".CaseProbe"
    try:
        probe.write_text("x", encoding="utf-8")
        insensitive = (ROOT / "data" / ".caseprobe").exists()
        probe.unlink()
        if insensitive:
            warn("filesystem is case-INSENSITIVE — a wrong-case import will work "
                 "here and break on a case-sensitive host. Do not rely on it.")
        else:
            ok("filesystem is case-sensitive")
    except Exception:
        pass

    try:
        import shutil
        free = shutil.disk_usage(ROOT).free / 1e9
        (ok if free > 5 else warn)(f"{free:.1f} GB free")
    except Exception:
        pass


# ── 5. network: Ollama and the API ───────────────────────────────────────────

def _read_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def check_network():
    head("5. Network")
    env = {**_read_env(), **os.environ}

    # OLLAMA_BASE is where a CLIENT connects. OLLAMA_HOST is where the SERVER
    # binds — the setup guide sets it to 0.0.0.0 on the laptop. Treating a
    # bind-all address as a connect target produces "http://0.0.0.0", which can
    # never succeed and looks like a network fault instead of a config mix-up.
    base = (env.get("OLLAMA_BASE") or "").strip()
    if not base:
        host = (env.get("OLLAMA_HOST") or "").strip()
        bind_all = host.split(":")[0] in ("", "0.0.0.0", "::", "*")
        if host and not bind_all:
            base = host
        else:
            if bind_all and host:
                warn(f"OLLAMA_HOST={host} is a server BIND address, not somewhere to "
                     "connect. Set OLLAMA_BASE to the laptop's LAN IP "
                     "(e.g. http://192.168.1.50:11434).")
            base = "http://localhost:11434"

    if not base.startswith("http"):
        base = f"http://{base}"
    if "localhost" in base or "127.0.0.1" in base:
        warn(f"Ollama target is {base} — that is the MINI itself. The guide runs "
             "Ollama on the laptop; set OLLAMA_BASE to the laptop's LAN IP.")

    import urllib.request
    try:
        t0 = time.time()
        with urllib.request.urlopen(f"{base.rstrip('/')}/api/tags", timeout=6) as r:
            tags = json.loads(r.read())
        names = [m.get("name") for m in tags.get("models", [])]
        ok(f"Ollama reachable at {base} in {time.time()-t0:.1f}s — {len(names)} models")
        for n in names[:6]:
            print(f"          · {n}")
    except Exception as e:
        warn(f"Ollama NOT reachable at {base} ({type(e).__name__}). "
             "Debates fall back to the Anthropic API. Common causes: the laptop's "
             "VPN blocking LAN, OLLAMA_HOST not set to 0.0.0.0, firewall, or the "
             "laptop's DHCP address changed.")

    # Reading .env is NOT the same test the code performs. Only backend/main.py
    # calls load_dotenv(); the inference router reads os.environ. So cron, the
    # nightly pipeline and any manual script see no key at all even though .env
    # sits right there — the teacher fails silently and looks like a dead API.
    # Ask the question the way a plain subprocess would.
    in_file = _read_env().get("ANTHROPIC_API_KEY", "").startswith("sk-ant-")
    in_env = os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-ant-")

    if in_env:
        ok("ANTHROPIC_API_KEY visible in the environment (router + teacher work)")
    elif in_file:
        warn("ANTHROPIC_API_KEY is in .env but NOT in the environment. Anything "
             "that does not call load_dotenv() — cron, main.py, the teacher run "
             "standalone — will fail as 'ANTHROPIC_API_KEY not set'. Export it in "
             "the shell profile, or put it in the cron line, if those must work.")
    else:
        warn("no ANTHROPIC_API_KEY in .env or environment — with Ollama "
             "unreachable too, debates cannot run at all")

    # Port the backend wants.
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 8000))
        warn("port 8000 is ALREADY in use — an orphaned uvicorn will silently "
             "serve stale code. Kill it before starting.")
    except Exception:
        ok("port 8000 free")
    finally:
        s.close()


# ── 6. the safety contract, on the box that runs unattended ──────────────────

def check_safety():
    head("6. Safety contract (this box runs unattended)")
    try:
        from src.desk.config import load_config
        cfg = load_config()
    except Exception as e:
        bad(f"could not load desk config: {e}")
        return

    auto = cfg.get("auto_execute")
    if auto:
        bad("auto_execute is TRUE on a machine that runs unattended. Confirm this "
            "is deliberate AND that the account is paper before leaving it running.")
    else:
        ok("auto_execute is false — proposals stop at the approval queue")

    for k in ("teacher_enabled", "teacher_daily_cap", "teacher_model"):
        if k in cfg:
            print(f"          {k} = {cfg[k]}")

    mode = str(cfg.get("broker_mode") or cfg.get("mode") or "").lower()
    if "live" in mode:
        bad(f"broker mode reports '{mode}' — a live account must route to manual "
            "approval. Verify before running 24/7.")
    elif mode:
        ok(f"broker mode: {mode}")

    # The autonomous execution path (src/trading). Its mode decides whether an
    # order may exist at all, so it is checked here rather than trusted.
    try:
        from src.trading import limits as _limits
        from src.trading import modes as _modes
        st = _modes.status()
        if st["effective_mode"] == "LIVE_AUTONOMOUS":
            bad("execution mode is LIVE_AUTONOMOUS — real money trades without "
                "per-trade approval. Confirm this is deliberate.")
        else:
            ok(f"execution mode: {st['effective_mode']} "
               f"(stored {st['stored_mode']}, live gate "
               f"{'OPEN' if st['live_gate_open'] else 'closed'})")
        if st["stored_mode"] == "LIVE_AUTONOMOUS" and not st["live_gate_open"]:
            warn(f"stored mode is LIVE_AUTONOMOUS but {_modes.LIVE_ENV} is not "
                 f"'true', so it behaves as PAPER. Deliberate, or a half-finished "
                 f"change?")
        lim = _limits.load()
        print(f"          hard limits: max_order_value={lim.max_order_value}, "
              f"max_position_pct={lim.max_position_pct}, "
              f"max_daily_loss_pct={lim.max_daily_loss_pct}, "
              f"leverage={lim.max_leverage}")
        from src.trading.orders import OrderEngine
        halted = OrderEngine().halted()
        if halted:
            bad(f"the order engine is HALTED: {halted.get('reason')}. Reconcile "
                f"before trading (/api/trading/orders/reconcile).")
    except Exception as e:
        warn(f"could not read the trading execution state ({type(e).__name__}: {e})")


# ── 7. time ──────────────────────────────────────────────────────────────────

def _read_env_at(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _sentinel_home():
    env = (os.environ.get("SENTINEL_HOME") or "").strip()
    for c in ([Path(env).expanduser()] if env else
              [ROOT.parent / "atlas", ROOT.parent / "sentinel"]):
        if (c / "sentinel").is_dir():
            return c
    return None


def check_auth():
    """Can the owner sign in, and can nobody else?

    Blockers are the conditions under which the server would either admit
    nobody (no credential) or be wrong about who it is admitting (a service
    credential that is secretly the owner's). Warnings are the tunnel settings
    whose absence breaks things in ways that look like something else.
    """
    head("8. Authentication")
    env = {**_read_env(), **{k: v for k, v in os.environ.items()
                              if k.startswith("ARIA_")}}
    tunnel = (Path.home() / ".cloudflared" / "config.yml").exists()

    owner = env.get("ARIA_OWNER_TOKEN") or env.get("ARIA_OWNER_TOKEN_HASH")
    if not owner:
        bad("ARIA: SERVER NOT INITIALIZED — no ARIA_OWNER_TOKEN or "
            "ARIA_OWNER_TOKEN_HASH. Nobody can sign in. Run "
            "scripts/init_owner_auth.py")
    else:
        ok("ARIA: owner credential configured"
           + (" (hashed)" if env.get("ARIA_OWNER_TOKEN_HASH") else ""))
        tok = env.get("ARIA_OWNER_TOKEN", "")
        if tok and len(tok) < 24:
            warn(f"ARIA_OWNER_TOKEN is only {len(tok)} characters. Generate a "
                 "long one: scripts/init_owner_auth.py --force")

    svc = env.get("ARIA_SERVICE_TOKEN", "")
    if svc and svc == env.get("ARIA_OWNER_TOKEN"):
        bad("ARIA_SERVICE_TOKEN equals ARIA_OWNER_TOKEN — SENTINEL would hold "
            "the owner's key. Issue a different value.")

    stale = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                         "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
                         "APPLE_CLIENT_ID", "APPLE_CLIENT_SECRET",
                         "ARIA_OWNER_EMAIL", "ARIA_FRONTEND_URL") if env.get(k)]
    if stale:
        warn("unused since OAuth was removed — delete from .env: " + ", ".join(stale))

    if tunnel:
        if env.get("ARIA_TRUST_PROXY", "").lower() not in ("1", "true", "yes"):
            warn("a cloudflared config exists but ARIA_TRUST_PROXY is not set: "
                 "every caller looks like 127.0.0.1, the sign-in throttle "
                 "lumps the whole internet into one bucket, and the session "
                 "cookie is not marked Secure")
        if not env.get("ARIA_BASE_URL", "").startswith("https://"):
            warn("ARIA_BASE_URL is not https:// — set it to the tunnel hostname")
    else:
        warn("no ~/.cloudflared/config.yml — the server will only be reachable "
             "from this machine (see scripts/cloudflared-config.example.yml)")

    home = _sentinel_home()
    if home is None:
        warn("SENTINEL not found beside ARIA — set SENTINEL_HOME if it is elsewhere")
        return
    senv = _read_env_at(home / ".env")
    if not (senv.get("SENTINEL_TOKEN") or senv.get("SENTINEL_TOKEN_HASH")):
        bad("SENTINEL: SERVER NOT INITIALIZED — no SENTINEL_TOKEN. Run "
            "scripts/init_owner_auth.py --app sentinel")
    else:
        ok("SENTINEL: owner credential configured")
    if (senv.get("SENTINEL_TOKEN") and env.get("ARIA_OWNER_TOKEN")
            and senv["SENTINEL_TOKEN"] == env["ARIA_OWNER_TOKEN"]):
        warn("SENTINEL and ARIA share one owner credential. Allowed, but a leak "
             "of one is then a leak of both.")

    # Four credentials, four jobs. Any two being equal collapses a boundary:
    # a service token equal to an owner token turns a peer into the owner, and
    # one app's peer token equal to the other's lets either speak as the other.
    # Both apps read these from the environment OR from their key file, so a
    # check that looks only at .env reports a gap that is not there.
    def _keys(path: Path) -> dict:
        try:
            import json
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            return {}

    akeys = _keys(ROOT / "data" / "research_keys.json")
    skeys = _keys(home / "data" / "sentinel" / "keys.json")

    def _any(*values) -> str:
        return next((str(v).strip() for v in values if str(v or "").strip()), "")

    credentials = {
        "ARIA_OWNER_TOKEN (human -> ARIA)": env.get("ARIA_OWNER_TOKEN", ""),
        "SENTINEL_TOKEN (human -> SENTINEL)": senv.get("SENTINEL_TOKEN", ""),
        "ARIA_SERVICE_TOKEN (SENTINEL -> ARIA)": _any(
            env.get("ARIA_SERVICE_TOKEN"), senv.get("ARIA_SERVICE_TOKEN")),
        "SENTINEL_SERVICE_TOKEN (ARIA -> SENTINEL)": _any(
            env.get("SENTINEL_SERVICE_TOKEN"), env.get("SENTINEL_CONSULT_TOKEN"),
            akeys.get("SENTINEL_SERVICE_TOKEN"), akeys.get("SENTINEL_CONSULT_TOKEN"),
            senv.get("SENTINEL_SERVICE_TOKEN"), skeys.get("SENTINEL_SERVICE_TOKEN"),
            skeys.get("SENTINEL_CONSULT_TOKEN")),
    }
    present = {name: value for name, value in credentials.items() if value}
    names = list(present)
    clashes = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
               if present[a] == present[b]]
    for a, b in clashes:
        bad(f"{a} and {b} are the same value — that is one credential doing two "
            f"jobs, and revoking either revokes both")
    if not clashes and len(present) >= 3:
        ok(f"{len(present)} credentials configured, all distinct")
    missing = [n for n in credentials if n not in present]
    if missing:
        warn("not configured (peer features stay off until they are): "
             + ", ".join(m.split(" ")[0] for m in missing))
    if tunnel:
        if senv.get("SENTINEL_TRUST_PROXY", "").lower() not in ("1", "true", "yes"):
            warn("SENTINEL_TRUST_PROXY is not set — same consequences as for ARIA")
        if not senv.get("SENTINEL_ALLOWED_ORIGINS", "").startswith("https://"):
            warn("SENTINEL_ALLOWED_ORIGINS is not set to the https tunnel "
                 "hostname: pages will load but every button will fail with "
                 "403 forbidden_origin")


def check_exposure():
    """Who can reach this box? Authentication is checked in section 8; this is
    about the network and the vault."""
    head("9. Exposure")
    ok("both apps bind 127.0.0.1 in their launchd jobs; only cloudflared "
       "reaches them")
    try:
        from src.auth import policy
        # Every role that is not the owner. Enumerated from the policy itself,
        # so a role added later is covered without editing this line — the FREE
        # role this used to name no longer exists, and a check that names roles
        # by hand goes stale exactly when it matters.
        others = [r for r in policy.ROLES if r != policy.OWNER]
        if not any(policy.can_read_vault(r) for r in others):
            ok(f"the vault is owner-only by construction; refused for "
               f"{', '.join(others)}")
        else:
            bad("policy.can_read_vault admits a non-owner role")
    except Exception as e:
        warn(f"could not import the auth policy ({type(e).__name__})")


def check_time():
    head("10. Time")
    from datetime import datetime, timezone
    local = datetime.now().astimezone()
    print(f"  local  : {local:%Y-%m-%d %H:%M %Z} (UTC{local:%z})")
    print(f"  utc    : {datetime.now(timezone.utc):%Y-%m-%d %H:%M}")
    ok("timezone read — confirm it matches the market hours the scheduler assumes")

    if os.name != "nt":
        print(f"  {Y}note{X}   Windows Task Scheduler does not exist here. Use cron or "
              "launchd:\n         30 7 * * 1-5 cd " + str(ROOT) + " && venv/bin/python main.py")


def main():
    print(f"{B}ARIA — Mac mini preflight{X}")
    print(f"repo: {ROOT}")
    for fn in (check_machine, check_deps, check_v5_without_chromadb,
               check_filesystem, check_network, check_safety, check_auth,
               check_exposure, check_time):
        try:
            fn()
        except Exception as e:
            bad(f"{fn.__name__} crashed: {type(e).__name__}: {e}")

    head("Verdict")
    if BLOCKERS:
        print(f"  {R}{len(BLOCKERS)} blocker(s){X}")
        for b in BLOCKERS:
            print(f"    · {b}")
    if WARNINGS:
        print(f"  {Y}{len(WARNINGS)} warning(s){X}")
        for w in WARNINGS:
            print(f"    · {w}")
    if not BLOCKERS and not WARNINGS:
        print(f"  {G}clean — ready to run 24/7{X}")
    elif not BLOCKERS:
        print(f"  {G}no blockers{X} — safe to start, read the warnings")
    return 1 if BLOCKERS else 0


if __name__ == "__main__":
    sys.exit(main())
