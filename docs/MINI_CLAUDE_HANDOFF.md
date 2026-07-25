# Handoff prompt — run a Claude Code session ON the Mac mini

Copy everything in the block below into a new Claude Code session started from
`~/aria` on the Mac mini. (Claude Code needs Node.js; if it's not installed,
install Node LTS from nodejs.org first — that also unblocks the optional web UI.)

---

You are finishing the deployment of ARIA, an autonomous **paper-only** trading
desk, onto this Mac mini (late-2014, 2.6 GHz dual-core i5, 16 GB, macOS Monterey
12.7.6). The project is at `~/aria`. Read `docs/MAC_MINI_SETUP.md` and
`docs/ARIA_V31_AUDIT_PACKET.md` (Part 1) for architecture, then do the work below.

## Non-negotiable safety rules (do not violate)
- This is PAPER trading only. `ALPACA_PAPER` must stay exactly `"true"` in `.env`.
  Never weaken the paper-only contract, the mutation-guard middleware, or the
  RiskOfficer/exit rules. Never enable or configure a live brokerage account.
- Do NOT run the heavy ML pipeline (`main.py`) on this machine — the 2014 CPU is
  too slow; that stays on the laptop.
- Do NOT install `torch` / `sentence_transformers` (huge, pointless here). Keep
  the research-only brain daemon DISABLED (see task 1).

## Current known state (already done — verify, don't redo)
- Python 3.12 venv at `~/aria/venv`; web stack installed manually (fastapi,
  uvicorn, python-dotenv, anthropic, APScheduler, python-multipart); chromadb
  installed but `sentence_transformers` is intentionally absent.
- `.env` is configured: `ALPACA_PAPER=true`, `ARIA_API_KEY` set,
  `OLLAMA_BASE=http://10.77.224.78:11434` (the laptop's Ollama — reachable and
  working). `ARIA_RUN_BRAIN=false` may already be appended.
- The backend RUNS and the desk is LIVE: `curl http://localhost:8000/api/desk/status`
  returns `"account":"paper"`, `"connected":true`, `"auto_allowed":true`,
  `equity ~10000`. It's currently started by hand with nohup, NOT yet permanent.
- Node.js may not be installed → the web UI (`frontend/`) was not built. Optional.
- The shell scripts in `scripts/` originally had Windows CRLF line endings; they
  were converted with `tr -d '\r'`. Re-check any script before relying on it.

## Tasks (do in order, verify each)

**1. Disable the research-only brain daemon (avoids torch on this CPU).**
In `backend/main.py`, function `_start_brain_if_ollama`, ensure it early-returns
when `ARIA_RUN_BRAIN=false`. The guard (after the existing `time.sleep(5)` line,
before the `try:`), noting `import os` already exists at module top:
```python
    if os.environ.get("ARIA_RUN_BRAIN", "true").lower() == "false":
        logger.info("ARIA_RUN_BRAIN=false - brain daemon disabled (desk only).")
        return
```
Ensure `ARIA_RUN_BRAIN=false` is present in `~/aria/.env` (append if missing).

**2. Fix Python SSL certificates** (python.org Python distrusts all certs until
this runs; without it, news/FRED/FX/LSE HTTPS fetches fail — Alpaca and Anthropic
bundle their own certs so they already work):
```bash
/Applications/Python\ 3.12/Install\ Certificates.command
```
If that path doesn't exist, achieve the same by `./venv/bin/pip install -U certifi`
and confirm `python -c "import ssl, certifi; print(certifi.where())"` resolves.

**3. Clean restart and confirm a SILENT startup log.** Restart the backend (kill
the old nohup process first) and confirm the log has NO brain traceback, NO
`python-dotenv could not parse` warning, and NO `CERTIFICATE_VERIFY_FAILED`. The
IBKR port-7497 connection error is EXPECTED and harmless (the desk uses Alpaca).
Then confirm `curl -s http://localhost:8000/api/desk/health` returns `"ok":true`.

**4. Make it permanent with launchd** (survives reboot, crash, and closing the
Terminal). Use the templates in `scripts/com.aria.backend.plist` and
`scripts/com.aria.watchdog.plist` — replace the `__ARIA_HOME__` placeholder with
the real absolute path (`$HOME/aria`), install to `~/Library/LaunchAgents/`,
`launchctl load` both. The backend plist must run
`~/aria/venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
(NO `--reload`), with `RunAtLoad` + `KeepAlive`, logging to `~/aria/logs/`. Make
sure no hand-started uvicorn is still holding port 8000 before loading.
`scripts/mini_setup.sh` does most of this but its frontend-build step fails when
npm is absent and aborts under `set -e` — either install Node first, or run just
the launchd portion, or make that step non-fatal. Do NOT let the optional UI block
the service install.

**5. Verify resilience:** kill the backend process and confirm launchd restarts it
within ~15s (`curl .../health` recovers). Confirm the watchdog agent is loaded.

**6. (Optional) Build the web UI** only if Node is installed:
`cd frontend && npm ci && npm run build` — the backend serves it at
`http://localhost:8000/app` (StaticFiles mount already in `backend/main.py`).

**7. Report, do NOT perform, the cutover.** The final step (disarming the laptop's
desk so two desks don't double-trade the same paper account) happens on the LAPTOP,
not here. Just remind the user to do it once you confirm this mini desk is running
under launchd.

## Definition of done
- `launchctl list | grep aria` shows both agents loaded.
- After a manual `kill` of the backend, `/api/desk/health` returns `"ok":true`
  again within ~20s on its own.
- Startup log is clean (only the harmless IBKR line).
- `/api/desk/status` shows `account:paper`, `auto_allowed:true`.
- You've told the user: (a) it's now permanent, (b) to run the laptop-side cutover,
  (c) the dashboard URL (`http://<mini-ip>:8000/app` if built, else the API works).

Work carefully, verify each step with a command, and don't change trading logic —
this is a deployment/ops task, not a code-behavior change.
