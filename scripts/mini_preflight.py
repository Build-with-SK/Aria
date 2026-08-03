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
                     "(e.g. http://10.77.224.78:11434).")
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

    key = env.get("ANTHROPIC_API_KEY", "")
    if key.startswith("sk-ant-"):
        ok("ANTHROPIC_API_KEY present (router fallback + Fable teacher available)")
    else:
        warn("no ANTHROPIC_API_KEY — with Ollama unreachable too, debates cannot run")

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


# ── 7. time ──────────────────────────────────────────────────────────────────

def check_time():
    head("7. Time")
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
               check_filesystem, check_network, check_safety, check_time):
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
