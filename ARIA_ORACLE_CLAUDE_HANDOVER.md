# ARIA and Oracle Cloud: Technical Handover

**Prepared:** 2026-11-22  
**Audience:** Claude, taking over ARIA engineering  
**Source of truth:** `docs/collab/ORACLE_HANDOVER.md` (2026-11-06), `docs/ORACLE_DEPLOYMENT.md`, `scripts/oci_*.py`, and deployment log entries in `docs/collab/log-codex.md`.  
**Freshness warning:** This workstation has no live Oracle access profile available to this task. Instance/service observations below are the last recorded state from 2026-11-06 (unless dated otherwise); confirm current values on the host before acting. Do not expose any credential values.

## 1. Server

- **Provider / region:** Oracle Cloud Infrastructure, London region.
- **Shape:** `VM.Standard.A1.Flex`, ARM (`aarch64`), 2 OCPUs, 12 GB RAM.
- **OS:** Ubuntu Server 24.04 LTS; `oci_bootstrap.sh` explicitly refuses anything else. ARM architecture also checked by bootstrap.
- **Storage:** 100 GB balanced boot volume on the A1 instance. A prior micro instance and its 100 GB disk were retained, giving 200 GB combined. The last recorded A1 disk report was 24 GB used / 73 GB free. The micro VM's current role is not established here.
- **SSH:** Linux account `ubuntu`. Connect with the private OCI SSH profile and owner-only SSH identity `~/.ssh/aria-sentinel-oci.key` from the Windows machine; no key material is included here. OCI ingress was restricted to the Windows public IPv4 `/32`, so source-IP changes require updating the OCI security rule. The host/profile details are held in the private Windows `AriaSentinelDeploy/access` local-data directory. This handover deliberately does not give the IP, SSH alias, or private key path resolved from that profile.

## 2. Deployment

- **ARIA:** `/home/ubuntu/apps/aria`; Python environment `/home/ubuntu/apps/aria/venv`.
- **SENTINEL companion:** `/home/ubuntu/apps/sentinel`; Python environment `/home/ubuntu/apps/sentinel/venv`.
- **God's Eye View:** `/home/ubuntu/apps/gods-eye-view`.
- **Deployment helpers and operational checks:** `/home/ubuntu/deploy`.
- **Deployment mechanism:** Native Ubuntu `systemd`, no Docker. Services run as unprivileged `ubuntu`. ARIA and SENTINEL use distinct Python 3.12 ARM environments. Model/library set includes CPU PyTorch, ChromaDB 1.5.9, sentence-transformers 5.6.0, Kokoro voice, and headless Chromium. Ollama is installed as a system service. Browser Use 0.13.14 has a separate `uv` environment; Node 24.22.2 ARM is installed under `~/.local/toolchains`.
- **Application units:** `aria.service`, `sentinel.service`, `ollama.service`, `gods-eye.service` (enabled, restart automatically). Other units include `aria-pipeline.service` (oneshot) and `aria-state-backup.service` (oneshot).
- **Safe service controls** (execute over SSH):

  ```bash
  sudo systemctl status aria sentinel ollama gods-eye --no-pager
  sudo systemctl restart aria       # only when an authorized deployment/restart is intended
  sudo systemctl restart sentinel   # only when an authorized deployment/restart is intended
  sudo systemctl restart ollama
  sudo systemctl restart gods-eye
  sudo systemctl stop aria sentinel # stop app writers before restore/rollback
  ```

  The user-level deployment script `~/deploy/serverctl` is a separate Mac mini/launchd tool and is **not** the Oracle control surface. On Oracle use `systemctl` and the OCI checks below. Do not start/stop services casually: the prior handover says cloud is the active writer and rollback requires stopping the pipeline timer too.

- **Source image:** Cloud source is a transferred snapshot, not a Git clone or automatic sync. ARIA came from integration commit `58f5f25` plus a built frontend; SENTINEL from `5ea3979` plus active working tools/Anatomy changes. Later Windows source edits do not reach Oracle automatically. Deployment scripts and docs were on isolated branch `codex/oracle-deploy`, commit `bd993ff`, in the then sibling checkout `aria-oracle-deploy`. No integration merge or GitHub push was part of that migration.

## 3. Networking

- App services bind to `127.0.0.1` only. Recorded ports: ARIA `8000`, SENTINEL `8300`, God's Eye View `4174`, Ollama `11434`. These are not intended to be public ingress ports.
- SSH is the only OCI ingress described in the handover and is allowlisted to the Windows public IPv4 `/32`. Review both the OCI security list/network security group and host firewall state live before changing rules.
- No public application domain, Cloudflare Tunnel, or HTTPS endpoint was deployed for Oracle. Access was through a private Windows SSH tunnel and Windows-only hostnames:
  - `http://aria-cloud.localhost:18000/app/`
  - `http://sentinel-cloud.localhost:18300/`
  - `http://gods-eye.localhost:4174/`
- These URLs require the Windows tunnel/profile and are not accessible from iPad/client devices or when using another machine without an approved route. Do not infer public access from “Oracle server”.
- OCI load balancer, DNS, public HTTPS, and app ports exposed to the Internet were not configured according to the handover.

## 4. Configuration and secrets

Secret values are intentionally omitted. Host-generated private files are mode `0600` under `~/.config/aria-sentinel/`:

- `aria.env`
- `sentinel.env`
- `owner-access.json`

The systemd units load the two app env files. `owner-access.json` is used by the filtered smoke/status scripts to make owner-authenticated checks. Never print, paste, or copy their contents into logs, chat, tickets, or Git.

Names that may appear in source templates or local `.env.example` (template names do **not** prove a variable is populated on Oracle): `ARIA_OWNER_TOKEN`, `ARIA_OWNER_TOKEN_HASH`, `ARIA_SESSION_SECRET`, `ARIA_BASE_URL`, `ARIA_SERVICE_TOKEN`, `SENTINEL_SERVICE_TOKEN`, `ARIA_TRUST_PROXY`, `ARIA_LIVE_TRADING_ENABLED`, `ARIA_KILL_SWITCH`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER`, `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `IBKR_PAPER`, `ANTHROPIC_API_KEY`, `NEWS_API_KEY`, `FRED_API_KEY`.

Oracle deployment specifically did **not** transfer the original Windows `.env`, broker credentials, research/API credentials, or source Obsidian vault. New host authentication/service credentials were generated for the cloud. Both applications require owner auth; the Sentinel-to-ARIA bridge uses scoped service credentials. The cloud source vault path is empty and vault export is disabled. Existing app memory/index stores were transferred as owner-only application state; do not describe this as “no vault-derived data left Windows.”

Other checked-in config files include `configs/universe.yaml`, `configs/inference_tiers.json`, `configs/obsidian.yaml`, `configs/nexus_rules.yaml`, and `data/sentinel_config.json` (actual runtime paths `/home/ubuntu/apps/...`). The deployment set `pipeline_propose_trades: false` in ARIA's universe config and set Sentinel's `browser_use_repo` to the installed Browser Use path. No active Cloudflare config was deployed for Oracle.

## 5. Broker and execution mode

- **Broker:** disconnected; no Alpaca keys or IBKR gateway credentials were migrated. Do not assume a paper broker connection is currently available.
- **Recorded controls:** mode `PAPER`; live gate closed; live broker access denied; `auto_execute=false`; reflex disabled; no order was approved or placed during deployment. No call to `approve_and_execute` was made.
- **Inference:** self-hosted only. Ollama and both app routers were configured for `qwen2.5:7b-instruct-q4_K_M`, one loaded model and one concurrent request. A 3B model exists but was explicitly not configured as silent fallback.
- A live trading switch remains an out-of-process control and must not be enabled without explicit owner authorization. Never weaken the approval/risk gates while troubleshooting.

## 6. Databases and data

- The application uses SQLite stores plus JSON/JSONL application state and ChromaDB 1.5.9 indexes. The 2026-11-06 final-state transfer included 7,401 state files and 20 SQLite snapshots; all 20 passed `quick_check`. A separate backup count was 7,402 files.
- State included conversations, application memory/index stores, research/ledger/config state, and the latest 1,545 ticker/horizon ensembles plus metadata (3,090 verified files). Kokoro voice weights and Ollama 7B weights were also installed. Historical model versions remain local.
- No source Obsidian vault was copied. Application memory/index stores were copied; cloud source-vault editing/export is unavailable and the vault export pipeline is disabled.
- No schema migration details are recorded as having run during the Oracle cutover. The deployment included identity/safety and application code snapshots, but do not infer that a database migration ran or is pending without checking migration tooling and live DB schemas.
- Backup location: `~/ServerBackups` (per `oci_aux_services.py`). `aria-state-backup.timer` runs daily around 03:15 Europe/London, randomized by up to 300 seconds, retaining 14 archives. Recurring backup excludes secrets, sessions, and model downloads. Windows downloads at 04:00/sign-in when available, verifies SHA-256, and retains 14 local copies. Verified originals/archives are retained privately on Windows for rollback.

## 7. Scheduled jobs and persistent processes

- Always-on enabled services: `aria`, `sentinel`, `ollama`, `gods-eye`.
- `aria-pipeline.timer` launches `aria-pipeline.service` daily at **06:30 UTC**, low priority (`Nice=15`, `CPUQuota=100%`, one-core quota). It runs `python main.py`; full daily training on Oracle has **not** completed validation.
- `aria-state-backup.timer` launches `aria-state-backup.service` daily at approximately 03:15 London and keeps 14 archives.
- Cloudflare tunnel: none on Oracle. The Windows SSH tunnel reconnect task is local to Windows.
- Prevent duplicate writers: Windows `ARIA Backend` and `TradingIntelligenceSystem` scheduled tasks were exported and disabled during cutover. Do not restart/re-enable them while Oracle writers/timer are active. For rollback, stop Oracle apps **and** `aria-pipeline.timer` first, reconcile newer cloud state from verified backups, then restore one Windows writer only.

## 8. Changes absent from GitHub repository

The migration and deployment contain operational state not represented by the normal GitHub source repository:

- Oracle host bootstrap and service configuration, units, host-generated credentials, virtual environments, Ollama model store, Kokoro weights, Browser Use runtime/Chromium, Node ARM toolchain, tunnel/SSH profile, and Windows scheduled task changes.
- Transferred application state: owner conversations/memory/indexes, SQLite/JSON research and ledger state, trained ensembles and metadata, and private backups.
- Deployed source is a one-time snapshot; there is no live clone or automatic deployment from GitHub. Thus changes made in Windows checkout after the snapshot are absent on Oracle until an explicit reviewed deployment.
- Deployment helpers/docs were maintained in isolated `codex/oracle-deploy` at `bd993ff`, in a separate local checkout at the time. Confirm whether that branch/checkout has since been pushed or integrated; the 2026-11-06 record says it had not been merged/pushed. The existence of the local checkout today does not prove GitHub has it.
- The main Windows checkout intentionally kept `docs/collab/ORACLE_HANDOVER.md` untracked and private. Treat it as an operational note, not as a committed repo artifact.

## 9. Known problems and unfinished work

**Last recorded runtime check at 2026-11-06 19:19 UTC:** health/readiness/UI returned 200 for both apps; anonymous private requests returned 401 and owner requests 200; execution controls and scoped bridge passed. However, worker health was **not green**: 8 workers OK, Cognitive brain failing, Quant lab researcher stalled. At 19:08:27 the brain's explicit local 7B request timed out and raised `BrainUnreachable`; measured CPU generation was only 3.63 tokens/sec on 2 cores. Do not downgrade model silently, change provider, reset status, or claim all functions work.

Other open items/limits:

- Review the brain timeout and stalled Quant lab researcher.
- Validate a complete scheduled cloud training run; it has not been validated.
- Two OCPUs make research/training responses slow; feeds needing credentials remain unavailable.
- No full-market-coverage, profitability, live broker, cloud-training-complete, or client-hosting readiness claim is supported.
- No Windows desktop/browser session or iPad access on the Oracle host.
- ChromaDB 1.5.9 had a startup race between the vault warmer and brain. Launcher `oci_serve.py` retains an initialized shared client before Uvicorn/worker startup; this avoided backend source changes. A related upstream cache issue is documented as Chroma issue 7253. One post-fix cycle completed at 16:15:39 UTC with all 10 workers healthy, but the later 19:19 UTC check superseded that result.
- Confirm that the last recorded 2026-11-06 state remains current before any deployment/restart or trading-related action.

## 10. Health checks

Run from SSH on the Oracle host as `ubuntu`. These checks report filtered health/auth/control data and avoid returning secrets or private response contents:

```bash
# Unit and timer state
systemctl is-active aria sentinel ollama gods-eye
systemctl is-enabled aria sentinel ollama gods-eye
systemctl list-timers --all 'aria-*'
systemctl status aria sentinel ollama gods-eye aria-pipeline.timer aria-state-backup.timer --no-pager

# Filtered HTTP, owner-auth, execution-gate, bridge, brain and worker checks
/home/ubuntu/apps/aria/venv/bin/python /home/ubuntu/deploy/oci_smoke.py
/home/ubuntu/apps/aria/venv/bin/python /home/ubuntu/deploy/oci_status.py

# Confirm listener scope (all application/model ports should be loopback only)
sudo ss -ltnp

# Recent service errors (inspect locally; redact before sharing)
journalctl -u aria -u sentinel -u ollama -u gods-eye --since '2 hours ago' --no-pager
journalctl -u aria-pipeline.service --since '7 days ago' --no-pager
```

`oci_smoke.py` expects `/home/ubuntu/.config/aria-sentinel/owner-access.json`; it checks `/health`, `/ready`, UI, anonymous private `401/403`, and authenticated owner `200` for both apps. `oci_status.py` prints filtered PAPER/live/auto/reflex/inference/bridge/voice/worker/brain status and exits nonzero if critical execution or bridge controls fail. Do not use `env`, `cat` on secret files, shell tracing, or commands that dump environment files.

### First actions for the incoming engineer

1. Read repository `AGENTS.md`, `docs/collab/WORK_PLAN.md`, and current `docs/collab/log-claude.md` / `log-codex.md` before edits.
2. Establish live Oracle status with the private profile and commands in section 10; confirm current branch/snapshot separately.
3. Investigate the recorded brain timeout and Quant lab stall under an authorized task. Preserve the paper-only, no-auto-execution, owner-approval model.
4. For any source changes, follow the integrator/review process and perform an explicit, reviewed deployment; a Windows edit is not a server update.
