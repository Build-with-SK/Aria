# Mac mini — the private server for ARIA and SENTINEL

One machine, always on, reachable from anywhere over HTTPS, with nothing listening
on the LAN and no port open on the router.

```
   phone / laptop / tablet / any browser
                  │  HTTPS
                  ▼
        Cloudflare (TLS, your hostnames)
                  │  outbound tunnel — the mini dials out, nothing dials in
                  ▼
   ┌──────────── Mac mini ───────────────────────────────────┐
   │  cloudflared                                             │
   │     ├── aria.example.com     → 127.0.0.1:8000  ARIA      │
   │     └── sentinel.example.com → 127.0.0.1:8300  SENTINEL  │
   │  each app: owner sign-in → session cookie → its own API  │
   │  SENTINEL ⇄ ARIA: loopback HTTP, scoped credentials      │
   │  launchd: backend · sentinel · watchdog (every 10 min)   │
   │  Ollama: local models                                    │
   └──────────────────────────────────────────────────────────┘
```

ARIA and SENTINEL stay two products: two repositories, two venvs, two processes,
two launchd jobs, two hostnames. What they share is the machine, the tunnel, the
control script and the backups.

> **Written on Windows, not yet run on the mini.** Every script here was
> syntax-checked and the Python parts are tested, but launchd, `plutil`,
> `memory_pressure` and cloudflared behaviour can only be confirmed on macOS.
> Treat the first install as the test, and use `serverctl status` after each step.

---

## 0. The machine

The target is the late-2014 Intel Mac mini (8 GB, no usable GPU, macOS Monterey).
That sets the budget:

- **One local model at a time.** A 4B model answers in about a minute; a 7B at
  1–3 tokens/s; two concurrent requests do not fit. ARIA's free/local path already
  defaults to `gemma3:4b` for this reason. Do not configure SENTINEL's
  `strong_model` to anything larger than the machine can hold beside ARIA.
- **Training stays off this box.** It is paused anyway, and a training run would
  starve both servers.
- The watchdog warns when free memory drops below 10% and when disk drops below
  1 GB. Both apps report NOT READY below 512 MB of disk.

## 1. One-time setup (by hand, at the machine)

1. `xcode-select --install`
2. Python 3.12 from python.org; Node LTS (only to build ARIA's interface).
3. [Ollama](https://ollama.com), then `ollama pull gemma3:4b`.
4. Put both repositories side by side:
   ```
   ~/aria        (this repository)
   ~/atlas       (SENTINEL)
   ```
   Or set `SENTINEL_HOME` if SENTINEL lives elsewhere.
5. Venvs:
   ```bash
   cd ~/aria  && python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
   cd ~/atlas && python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
   cd ~/aria/frontend && npm ci && npm run build
   ```
6. **Energy settings:** System Settings → Energy Saver → *Prevent automatic
   sleeping*, *Start up automatically after a power failure*.

## 2. Credentials — the step only you can do

```bash
cd ~/aria
venv/bin/python scripts/init_owner_auth.py                 # ARIA
venv/bin/python scripts/init_owner_auth.py --app sentinel  # SENTINEL
```

Each prints the lines to put in that app's `.env` (`~/aria/.env`,
`~/atlas/.env`) and the credential you will sign in with. Store the credential in
your password manager. Nothing is written into either repository.

Then the two peer credentials (distinct from the owner's, and from each other):

| Where | Variable | Purpose |
|---|---|---|
| `~/aria/.env` | `SENTINEL_CONSULT_TOKEN` | ARIA asking SENTINEL |
| `~/atlas/.env` | `SENTINEL_CONSULT_TOKEN` | same value, SENTINEL's side |
| `~/aria/.env` | `ARIA_SENTINEL_CONSULT=true` | a switch, not a credential: lets the trading path consult automatically (off by default) |
| `~/aria/.env` | `ARIA_SERVICE_TOKEN` | SENTINEL reading ARIA (4 routes, read-mostly) |
| `~/atlas/.env` or `data/sentinel/keys.json` | `ARIA_SERVICE_TOKEN` | same value, SENTINEL's side |

`atlas/docs/AUTH_SETUP.md` explains what each can and cannot reach.

## 3. The tunnel — also yours

```bash
brew install cloudflared            # or the .pkg from Cloudflare
cloudflared tunnel login            # opens a browser: approve for your domain
cloudflared tunnel create mini
cloudflared tunnel route dns mini aria.example.com
cloudflared tunnel route dns mini sentinel.example.com
cp ~/aria/scripts/cloudflared-config.example.yml ~/.cloudflared/config.yml
#   → fill in the tunnel UUID, the credentials path and your two hostnames
cloudflared tunnel run mini         # test in the foreground; Ctrl-C when it works
sudo cloudflared service install    # then run it permanently
```

Then tell each app it is behind a proxy, in its `.env`:

```bash
# ~/aria/.env
ARIA_TRUST_PROXY=1
ARIA_BASE_URL=https://aria.example.com

# ~/atlas/.env
SENTINEL_TRUST_PROXY=1
SENTINEL_ALLOWED_ORIGINS=https://sentinel.example.com
```

Skipping these fails in ways that look like something else:

| Missing | Symptom |
|---|---|
| `*_TRUST_PROXY` | every caller looks like 127.0.0.1 — one stranger's wrong guesses lock **you** out; cookies not marked Secure |
| `SENTINEL_ALLOWED_ORIGINS` | pages load, every button fails with `403 forbidden_origin` |

`venv/bin/python scripts/mini_preflight.py` checks all of them.

**Optional, recommended: Cloudflare Access** in front of both hostnames (Zero
Trust → Access → Applications, policy "emails: you"). It adds a second, separate
door at Cloudflare's edge, so an attacker has to get through Cloudflare's login
before they can even reach the owner sign-in. It costs nothing for one user.

## 4. Install the services

```bash
cd ~/aria
chmod +x scripts/serverctl scripts/watchdog.sh
venv/bin/python scripts/mini_preflight.py     # fix every FAIL first
scripts/serverctl install                      # LaunchAgents (needs auto-login)
# or
scripts/serverctl install --boot               # LaunchDaemons: start with nobody logged in
scripts/serverctl status
```

**Agents or daemons.** Agents run only while your user is logged in, so an
unattended mini needs *Automatic login*. Daemons (`--boot`, sudo) start at boot
with nobody logged in, and still run as you, not root.

**FileVault.** With FileVault on, a reboot stops at the disk-unlock screen and
*nothing* starts until someone types the password at the machine — a power cut
takes the server down until you are physically there. Choose one:

- keep FileVault, and for planned reboots use `sudo fdesetup authrestart`;
- or turn FileVault off and accept that a stolen mini is a readable disk.

That is a genuine trade-off, and it is yours to make.

## 5. Day to day

```bash
scripts/serverctl status                 # launchd state, live, ready, tunnel, disk
scripts/serverctl health                 # exit 0 only if everything is READY
scripts/serverctl restart [aria|sentinel]
scripts/serverctl logs aria -f           # JSON lines, printed readably
scripts/serverctl logs sentinel
scripts/serverctl logs watchdog
scripts/serverctl backup
scripts/serverctl update                 # backup → pull → deps → preflight → restart → verify
```

### Health and readiness

| Endpoint | Public | Says |
|---|---|---|
| `/health` | yes | something is listening |
| `/ready` | yes | `{"ready": true/false}` and nothing more |
| ARIA `/api/system/health` | signed in | workers, activity, **readiness reasons** |
| SENTINEL `/api/health` | signed in | subsystems, degraded parts, **readiness reasons** |

Not ready means one of: no owner credential (SERVER NOT INITIALIZED), data
directory not writable, session store unreadable, disk under 512 MB (SENTINEL
also: no tools registered).

### What restarts what

- **launchd** restarts a process that exits (`KeepAlive`, at most every 15 s).
- **The watchdog** (every 10 min) restarts a service that has stopped
  *answering*, and restarts ARIA if its desk loop is stale. It does **not**
  restart for NOT READY, a dead tunnel, low disk or low memory — a restart fixes
  none of those — it notifies you instead, once per outage, via the ntfy topic
  in `data/desk_config.json`.

### Logs

| File | Rotation |
|---|---|
| `~/aria/data/aria.log` | JSON lines, 5 MB × 3 |
| `~/atlas/data/logs/sentinel.log` | JSON lines, 10 MB × 5 |
| `data/logs/launchd-*.log` | should stay near-empty; trimmed by the watchdog past 20 MB |
| `~/aria/data/logs/watchdog.log` | trimmed past 20 MB |

Credential-shaped values (`?token=`, `?api_key=`, `Bearer …`, auth headers) are
scrubbed from every log record at creation — including uvicorn's access log,
which writes the request line before either app refuses a token in a URL.

## 6. Backups and restore

```bash
scripts/serverctl backup                       # → ~/ServerBackups/server-backup-<stamp>.tar.gz
venv/bin/python scripts/backup.py --list
venv/bin/python scripts/backup.py --verify ~/ServerBackups/server-backup-<stamp>.tar.gz
```

Kept: both apps' `data/` — conversations, ratings and corrections, memory
(ChromaDB), research, the prediction ledger, desk history, configuration. SQLite
files go through SQLite's own backup API, so a database being written comes out
consistent. Every file is hashed into `MANIFEST.json`; `--verify` rechecks them.

**Never kept:** `.env`, session keys, the session store, API-key files, anything
named like a token, secret or credential. A backup is the copy most likely to
leave the machine, which makes it the wrong place for a credential.

Skipped by default: caches, raw downloads, logs, and `data/models/` (4.6 GB of
adapters on the dev box — pass `--include-models` if you want them).

Retention: newest 14. Set `SERVER_BACKUP_DIR` to put them somewhere else — an
external disk is better than the internal one. The tool refuses to write inside
either repository.

Schedule it nightly (crontab -e):

```
15 3 * * * $HOME/aria/scripts/serverctl backup >> $HOME/aria/data/logs/backup.log 2>&1
```

### Restore

```bash
scripts/serverctl stop
venv/bin/python scripts/backup.py --restore ~/ServerBackups/server-backup-<stamp>.tar.gz --to ~/restore-check
# inspect ~/restore-check, then move the pieces into place:
mv ~/aria/data  ~/aria/data.before-restore
mv ~/atlas/data ~/atlas/data.before-restore
mv ~/restore-check/aria/data     ~/aria/data
mv ~/restore-check/sentinel/data ~/atlas/data
# secrets come back from your password manager, not the archive:
#   ~/aria/.env, ~/atlas/.env (and data/sentinel/keys.json if you used it)
scripts/serverctl start && scripts/serverctl status
```

`--restore` never writes into a live data directory or a non-empty one, and
ignores absolute paths and `..` in an archive. Every device signs in again after a
restore — sessions are deliberately not backed up.

## 7. Updating, and rolling back

`serverctl update` backs up first, refuses to pull over a dirty tree, records the
current commit, installs, rebuilds the interface, runs the preflight, and **does
not restart** if the preflight fails — the old code keeps running. If the services
are not ready afterwards it prints the rollback:

```bash
cd ~/aria  && git checkout $(cat data/.last-good-aria)     && scripts/serverctl restart
cd ~/atlas && git checkout $(cat ~/aria/data/.last-good-sentinel)
```

## 8. Reaching it

| Device | How |
|---|---|
| Laptop (any OS) | `https://aria.example.com`, `https://sentinel.example.com` — sign in once per browser |
| Phone / tablet | same URLs in Safari or Chrome. ARIA can be added to the home screen (it ships a web manifest) |
| On the mini itself | `http://127.0.0.1:8000/app/` and `http://127.0.0.1:8300/` |
| Scripts | `Authorization: Bearer <owner credential>` to either app |

No native client is needed anywhere. Sessions last 7 days (2 idle); money-moving
or permission-widening actions ask for the credential again if it was not used in
the last 10 minutes. Lost a device: sign in elsewhere and use *sign out
everywhere* (`POST /api/auth/logout-everywhere`), or rotate the credential.

## 9. When something is wrong

| Symptom | Look at |
|---|---|
| `serverctl status` says not loaded | `serverctl install`; `data/logs/launchd-*.log` |
| live but NOT READY | `serverctl logs aria` — the readiness line says why |
| sign-in page, but every attempt fails | wrong credential, or locked out (429 says how long) |
| SENTINEL buttons all 403 | `SENTINEL_ALLOWED_ORIGINS` |
| locked out from everywhere | `*_TRUST_PROXY` not set; wait 15 min or restart the app |
| nothing reachable remotely | `pgrep cloudflared`; `sudo launchctl list | grep cloudflared` |
| slow / swapping | one model at a time; `ollama ps`; watchdog memory warnings |
| port 8000 serving old code | an orphan uvicorn: `lsof -i :8000`, kill it, `serverctl restart aria` |
