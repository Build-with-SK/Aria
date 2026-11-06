# Oracle owner deployment

Deployed 2026-11-06 with owner authorization: Always Free limits, no paid upgrade.
London Ubuntu 24.04 ARM, A1.Flex, 2 OCPUs, 12 GB RAM, 100 GB Balanced boot disk.
The earlier micro-instance and its 100 GB disk remain intact: 200 GB combined.
Oracle's estimate showed GBP 3.40/month before tier pricing; no paid upgrade was
selected. Eligibility remains subject to [Oracle's free limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

## Windows access

| App | Private address |
| --- | --- |
| ARIA | http://aria-cloud.localhost:18000/app/ |
| Sentinel | http://sentinel-cloud.localhost:18300/ |
| God's Eye View | http://gods-eye.localhost:4174/ |

The ARIA Sentinel Cloud Tunnel Windows task starts at sign-in and reconnects
after interruption. The server runs while Windows is off; these addresses need
this Windows tunnel. They do not provide iPad or client access.

Private connection settings and owner-login.json live outside Git, under the
Windows app's local data in AriaSentinelDeploy/access. The owner's handover gives
the resolved path. Copy the relevant token into each app's sign-in form; never
put it in a URL or chat. Separate hostnames isolate host-only session cookies.

SSH uses an owner-only key in ~/.ssh and ingress restricted to the current
Windows public IPv4 /32. If that address changes, update the Oracle SSH rule.
Ports 8000, 8300, 4174 and Ollama 11434 bind only to server loopback.

## Installed and transferred

ARIA source is integration 58f5f25, with the built frontend. Sentinel is 5ea3979
plus active working tool/Anatomy changes. No Git history was uploaded.
Separate Python 3.12 ARM environments include matching ML libraries, CPU PyTorch,
ChromaDB 1.5.9, sentence-transformers 5.6.0, Kokoro and headless Chromium.
Anatomy resources, the locally patched globe, Node 24.22.2 ARM and Browser Use
0.13.14 in its own uv environment are installed. The browser planner proposes
one step; existing approval and host restrictions remain.

Ollama and both routers use qwen2.5:7b-instruct-q4_K_M, with one loaded model and
one concurrent request. A 3B model is installed but not a silent fallback.
Latest checkpoints for 1,545 ticker/horizon pairs plus metadata were verified
(3,090 files); older versions remain on Windows.

Final state was backed up after both local writers stopped: 7,401 verified files,
20 SQLite quick_checks passed. Prior staged cloud state is retained under
~/deploy/pre-cutover-*. Windows app data remains in place for rollback.

No local .env, broker/research credentials, browser profile or source Obsidian
vault was copied. Application memory/index stores are part of the owner state
backup and remain behind owner authentication. Source-vault editing/export is
unavailable; the pipeline's vault export is disabled. Windows desktop control
and signed-in sessions still require Windows components.

## Services and controls

aria, sentinel, ollama and gods-eye are enabled systemd services with automatic
restart, running as unprivileged users. Host-generated credentials stay in
mode 600 files under ~/.config/aria-sentinel.

The deployment launcher initializes ARIA's shared Chroma client before its vault
warmer and brain start concurrently. This avoids the observed 1.5.9 startup race
without backend edits. Chroma has an open
[related cache issue](https://github.com/chroma-core/chroma/issues/7253).

Measured controls: PAPER mode, live gate closed, live broker access denied,
auto-execute off, reflex off, self-hosted inference only. The Sentinel/ARIA
bridge uses a scoped service credential. No order or approval call was made.
Brokers are disconnected because credentials and gateways were not migrated.

Windows ARIA Backend and TradingIntelligenceSystem tasks were exported and
disabled to prevent duplicates. The cloud aria-pipeline.timer preserves the
daily 06:30 UTC schedule, with low priority and a one-core quota. A full daily
training run on this host has not yet been validated.

aria-state-backup.timer runs around 03:15 London daily and retains 14 state
archives, excluding secrets, sessions and model downloads. Windows ARIA Sentinel
Cloud Backup downloads the newest archive at 04:00 or sign-in when Windows is
available, verifies SHA256 and retains 14 local copies. Initial cloud backup:
7,402 files, downloaded and independently verified with no problems.

## Verification and limits

- Frontend production build passed; six focused deployment tests passed.
- Both health/ready/UI 200, private anonymous 401, authenticated owner 200.
- Both sign-in pages and globe UI loaded through the tunnel.
- Two restored ensembles loaded on ARM; both main environments pass pip check.
- Local 7B short generation completed in 30.83 s including load, 3.63 tokens/second.
- British voice synthesized 1.69 seconds at 24 kHz; fresh Chromium started/closed.
- Reboot recovery passed: all four services returned automatically; health,
  readiness, owner authentication, private binding and execution gates passed.
  The Windows tunnel also recovered after a forced SSH-child disconnect.
- A complete post-fix brain cycle finished at 16:15:39 UTC; all ten workers
  then reported healthy (no failing, stalled or late workers).

Two CPU cores give slow replies during research/training. Do not assume client
workloads or more large models fit. Some feeds need credentials still on
Windows. No full market-coverage, profitability, completed cloud training or
client-hosting readiness claim is made.

## Operations and rollback

Use the private SSH profile. Inspect systemctl status for aria/sentinel/ollama/
gods-eye. Run ~/apps/aria/venv/bin/python ~/deploy/oci_smoke.py and oci_status.py
for health/auth and filtered controls/bridge status. Never paste environment or
owner-access files into logs/support messages.

To revert, first stop both cloud apps and aria-pipeline.timer. Re-enable/start
the preserved Windows ARIA task and Sentinel launcher, then the daily task.
Windows retains cutover state; reconcile newer cloud state from a verified
backup before resuming writes if cloud operation has continued. One host writes.

For cloud restore, stop both apps, retain current data, verify the backup and
unpack into an empty staging folder. Preserve host credentials/model weights,
apply Linux paths and execution gates, then restart and smoke-test. The initial
restore/cutover scripts intentionally refuse a second migration over live state.
