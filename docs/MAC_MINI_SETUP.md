# ARIA — Mac mini 24/7 Deployment Guide
### Exact step-by-step for the late-2014 Mac mini (macOS Monterey 12.7.6)

**What you're building:** the Mac mini runs the ARIA backend + desk 24/7 (survives
the laptop sleeping). The laptop keeps Ollama (the local LLM) and the heavy nightly
ML pipeline, and pushes fresh signals to the mini each morning.

**Your concrete values** (already filled in below):
- Laptop Wi-Fi IP: **`10.77.224.78`** (both machines must be on the SAME Wi-Fi)
- Ollama models on the laptop: `qwen2.5-coder:7b`, `gemma3:4b`
- ⚠️ **NordVPN**: while it's connected on the laptop it can block the mini from
  reaching Ollama. Either allow "local network / LAN" in NordVPN settings, or
  disconnect the VPN, or accept that debates fall back to the Anthropic API when
  the VPN is up (the router handles that automatically).

> Tip: the laptop's Wi-Fi IP (`10.77.224.78`) is handed out by DHCP and can change
> on reconnect. If it does, update `OLLAMA_BASE` in the mini's `.env` and the
> `MINI_HOST` on the laptop. For a permanent fix, set a DHCP reservation in your
> router, or we can switch to hostname-based discovery later.

---

## PART 0 — On the laptop (Windows), one time

### 0.1 Make Ollama listen on the network (not just localhost)
By default Ollama only answers localhost. Set it to listen on all interfaces so
the mini can reach it, then restart Ollama.

PowerShell (as your normal user):
```powershell
[System.Environment]::SetEnvironmentVariable('OLLAMA_HOST','0.0.0.0','User')
# fully quit Ollama from the system tray, then relaunch it (or reboot)
```

### 0.2 Allow port 11434 through the Windows firewall, from the mini only
Run PowerShell **as Administrator**:
```powershell
New-NetFirewallRule -DisplayName "Ollama LAN (ARIA mini)" -Direction Inbound `
  -Protocol TCP -LocalPort 11434 -Action Allow -RemoteAddress 10.77.224.0/24
```

### 0.3 Verify Ollama is reachable (do this AFTER the mini is on the network, 1.x)
From the mini later you'll run: `curl http://10.77.224.78:11434/api/tags` — it should
return JSON listing the two models.

---

## PART 1 — On the Mac mini: prerequisites (one time)

Open **Terminal** (Applications → Utilities → Terminal) and run these in order.

### 1.1 Command Line Tools (gives you git + compilers)
```bash
xcode-select --install
```
Click "Install" in the popup, wait for it to finish (~5 min).

### 1.2 Python 3.12
Download the **macOS 64-bit universal2 installer** for Python **3.12.x** from:
https://www.python.org/downloads/macos/
(Use 3.12 — mature wheel coverage for every dependency. Avoid 3.14: too new,
some packages lack prebuilt wheels and would try to compile from source.)
Run the `.pkg`, accept defaults. Then verify:
```bash
python3.12 --version      # should print Python 3.12.x
```

### 1.3 Node.js (only to build the web UI once)
Download the **macOS LTS installer** from https://nodejs.org/ , run the `.pkg`, then:
```bash
node --version            # any LTS (v20/v22) is fine
```

### 1.4 Turn on Remote Login (so the laptop can push signals via scp)
System Settings → General → Sharing → toggle **Remote Login** ON.
Note the line it shows: `ssh username@10.77.224.xx` — that username + the mini's IP
is your `MINI_HOST` for Part 5. Find the mini's own IP with:
```bash
ipconfig getifaddr en0    # Wi-Fi address of the mini
```

### 1.5 Power settings (never sleep, restart after power cut)
System Settings → Energy (or Battery → Options):
- **Prevent automatic sleeping when the display is off** → ON
- **Start up automatically after a power failure** → ON

---

## PART 2 — Get the ARIA code onto the mini

You have the project on the laptop. Copy the whole folder to `~/aria` on the mini.
Easiest over the network (run ON THE MINI, it pulls from the laptop):

```bash
mkdir -p ~/aria
# from the laptop's project folder, EXCLUDING venv/node_modules/data caches:
# (replace LAPTOP_USER + laptop IP; you may need Remote Login on the laptop too)
```

**Simpler for a first setup:** put the project on a USB stick from the laptop
(exclude the `venv`, `frontend/node_modules`, and `__pycache__` folders — they get
rebuilt), copy it into `~/aria` on the mini. The folder must end up as
`~/aria/backend/main.py`, `~/aria/src/…`, `~/aria/scripts/…`, etc.

> If you use git: on the laptop `git remote` isn't set up for this, so USB or
> `scp -r` is the path. Ask me and I'll generate an rsync one-liner tuned to your
> setup.

---

## PART 3 — Configure the mini's `.env`

Copy your laptop's `.env` into `~/aria/.env` on the mini, then add two lines and
confirm one. On the mini:
```bash
cd ~/aria
nano .env
```
Ensure these are present/added:
```
# point the mini at the laptop's Ollama over Wi-Fi
OLLAMA_BASE=http://10.77.224.78:11434

# a shared secret so only your UI/tools can POST to the trading API (pick any
# long random string; you'll use the same value if you open the UI from a phone)
ARIA_API_KEY=choose-a-long-random-string-here

# MUST stay exactly this — the paper-only safety contract depends on it
ALPACA_PAPER=true
```
Keep all your existing keys (ANTHROPIC_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY,
NEWS_API_KEY, FRED_API_KEY). Save in nano: `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## PART 4 — One-shot install + autostart

The repo ships a setup script that builds the venv, compiles the web UI, installs
the launchd services (auto-start on boot, auto-restart on crash), and verifies.

```bash
cd ~/aria
bash scripts/mini_setup.sh
```

What it does:
1. `python3.12 -m venv venv` + `pip install -r requirements.txt`
2. `cd frontend && npm ci && npm run build` → UI served by the backend at `/app`
3. installs `~/Library/LaunchAgents/com.aria.backend.plist` (the API, KeepAlive)
   and `com.aria.watchdog.plist` (health check every 10 min)
4. waits for `http://localhost:8000/health` and prints the desk status

If it prints `✓ backend is up`, the mini is now running ARIA 24/7.

---

## PART 5 — On the laptop: nightly signal push to the mini

The heavy ML pipeline stays on the laptop (the mini's 2014 CPU is too slow for it).
After it runs at 07:30 it should copy the fresh signals to the mini.

### 5.1 Passwordless SSH from laptop → mini (one time)
In PowerShell on the laptop:
```powershell
# generate a key if you don't have one
if (-not (Test-Path ~/.ssh/id_ed25519)) { ssh-keygen -t ed25519 -N '""' -f $HOME\.ssh\id_ed25519 }
# copy it to the mini (use the mini's username + IP from step 1.4)
type $HOME\.ssh\id_ed25519.pub | ssh USERNAME@MINI_IP "mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
```
Test: `ssh USERNAME@MINI_IP "echo ok"` should print `ok` with no password.

### 5.2 Point the push script at the mini
```powershell
[System.Environment]::SetEnvironmentVariable('MINI_HOST','USERNAME@MINI_IP','User')
```

### 5.3 Add the push to the daily task
The `TradingIntelligenceSystem` scheduled task runs `main.py` at 07:30. Add a second
action (or we can wrap both in one script) that runs after it:
```
powershell.exe -ExecutionPolicy Bypass -File "C:\Users\sound\Documents\trading-intelligence-system\scripts\push_data_to_mini.ps1"
```
Ask me and I'll update the scheduled task to chain the push automatically.

---

## PART 6 — Cutover (stop the laptop from also trading)

Two desks trading the same paper account would double up. Once the mini is
confirmed running:
- On the **laptop**, disarm its desk: in the UI Desk page toggle auto-execute OFF,
  or set `"auto_execute": false` in the laptop's `data/desk_config.json`.
- The **mini** keeps `auto_execute: true`.
- The laptop's job is now: Ollama server + nightly pipeline + your dev machine.

---

## PART 7 — Using it & troubleshooting

### Open the dashboard
On the mini: `http://localhost:8000/app`
From the laptop or phone on the same Wi-Fi: `http://MINI_IP:8000/app`
(The API blocks cross-origin writes and, with `ARIA_API_KEY` set, requires that
header for POSTs — reads/dashboards work freely.)

### Is it alive?
```bash
curl http://localhost:8000/api/desk/health     # {"ok":true,...} = healthy
curl http://localhost:8000/api/desk/status      # gates, account, reflex
```

### Watch the logs
```bash
tail -f ~/aria/logs/backend.out       # desk activity
tail -f ~/aria/logs/watchdog.out      # health checks / restarts
```

### Manual control of the services
```bash
launchctl kickstart -k gui/$(id -u)/com.aria.backend   # restart the backend now
launchctl unload  ~/Library/LaunchAgents/com.aria.backend.plist   # stop it
launchctl load    ~/Library/LaunchAgents/com.aria.backend.plist   # start it
```

### Common issues
- **Desk shows Ollama/debates failing** → the mini can't reach the laptop's Ollama.
  Check NordVPN (Part 0 warning), that both are on the same Wi-Fi, and
  `curl http://10.77.224.78:11434/api/tags` from the mini. Debates fall back to the
  Anthropic API automatically, so trading still works — you just lose the local LLM.
- **`pip install` fails on a package** → make sure you used Python **3.12** (1.2),
  not the system Python. `~/aria/venv/bin/python --version` must say 3.12.
- **Backend won't start** → `cat ~/aria/logs/backend.err` for the traceback.
- **Laptop IP changed** → update `OLLAMA_BASE` in `~/aria/.env` and restart the
  backend (kickstart command above).

---

## What still needs YOUR accounts (not automatable)
- **ntfy topic** for phone push alerts on every fill: pick a name, subscribe in the
  ntfy app, set it in `data/desk_config.json` `"ntfy_topic"`.
- **LSE_API_KEY** (free, londonstrategicedge.com) for macro/insider evidence.
- The mini setup steps above (installing Python/Node, Remote Login, running the
  script) — I can't reach the mini; you run those, and I'll debug any output you
  paste back.
