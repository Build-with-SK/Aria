# Oracle migration handover for Claude
Date: 2026-11-06. Owner explicitly asked Codex to tell Claude the migration state.
This is an operational handover, not authorization to restart, trade, merge or
publish. Read docs/collab/WORK_PLAN.md and keep the existing approval/risk rules.

## Active runtime and source
Oracle is now the active ARIA/Sentinel host. Do not restart Windows ARIA Backend
or Sentinel, or re-enable TradingIntelligenceSystem, while cloud writers/timer
are active. Local data and task exports remain for rollback.
Cloud: Ubuntu24.04 ARM A1.Flex,2OCPU/12GB/100GB, London. Earlier micro-instance
and100GB disk retained;200GB combined. No paid upgrade selected.
Cloud source is a snapshot, not a live Git clone or automatic sync:
ARIA integration58f5f25 plus built frontend; Sentinel5ea3979 plus its active
working tools/Anatomy changes. Later Windows edits are NOT automatically deployed.
Deployment scripts/docs are on codex/oracle-deploy in ../aria-oracle-deploy,
commit bd993ff. No integration merge, GitHub push, backend edits, or work in the
unrelated unfinished ../aria-codex evaluation change was performed.

## What transferred
-687 application source/build files, hash-verified.
-7401 final state files, including conversations, application memory/index
 stores, research/ledger/configuration;20 SQLite snapshots quick_checked.
 The source Obsidian vault was not copied. Its already-existing application
 memory/index stores were included with owner-only state; do not describe this
 as no vault-derived data having left Windows.
-Latest1545 trained ticker/horizon ensembles plus metadata (3090 verified files).
-Kokoro British voice weights; Ollama7B; separate ARM Python environments.
-Anatomy resources, locally patched Gods Eye View, Node24.22.2 ARM,
 isolated Browser Use0.13.14 and headless Chromium.
-Linux paths, host-generated credentials, loopback services, daily timers.

## What stayed local or is not connected
-Original .env and broker/research/API credentials were not transferred.
 Original Obsidian files, signed-in Windows browser profiles/cookies, desktop
 tooling, historical model versions and original source/data copies remain local.
-Broker is disconnected. Live gate false; PAPER; auto_execute false;
 reflex false; self-hosted-only inference; scoped Sentinel-to-ARIA read bridge.
 Nothing called approve_and_execute or placed/approved an order during deployment.
-Server does not automatically gain Windows desktop access, iPad access, a public
 domain, client accounts, or publicly exposed application ports.
-Full daily model-training pipeline on this host has not completed validation.
 Timer preserves06:30UTC; low priority/one-core quota. Vault export disabled.

## Services, access and backups
systemd services aria,sentinel,ollama,gods-eye are enabled/restart automatically.
ARIA/Sentinel run from /home/ubuntu/apps/{aria,sentinel}; deployment helpers in
/home/ubuntu/deploy. Use oci_smoke.py and oci_status.py for filtered checks.
Chroma1.5.9 startup race was observed between vault warmer and brain; oci_serve.py
retains an initialized shared client before Uvicorn/worker startup. App source
was not patched. First complete post-fix cycle16:15:39UTC succeeded/all10workers
healthy; subsequent server reboot and tunnel reconnect tests passed.
Daily backup around03:15London,14 archives. Windows Cloud Backup task downloads
latest at04:00/sign-in when available, hashes and retains14 copies.
A server backup7402files was downloaded and independently verified on Windows.
Models/secrets/sessions are excluded from recurring state backup; originals and
model/source migration archives remain in private local storage.

Windows private links:
http://aria-cloud.localhost:18000/app/
http://sentinel-cloud.localhost:18300/
http://gods-eye.localhost:4174/
These rely on the Windows SSH tunnel. Server runs while Windows is off but
these links are not accessible on iPad/client machines without another route.
Connection profile and owner login files are private under the Windows app
local-data AriaSentinelDeploy/access directory; use the existing SSH identity
~/.ssh/aria-sentinel-oci.key. Do NOT print/copy auth files, tokens, or local.env.
SSH ingress is restricted to the Windows public IPv4/32; update OCI rule if it
changes. All app/model ports bind127.0.0.1. No paid upgrade/public app exposure.

## Fresh check at19:19UTC (supersedes initial all-green worker report)
Both apps health/readiness/UI200; anonymous private401; owner private200.
PAPER/no-live/no-auto/reflex-off controls and scoped read bridge still pass.
British voice loaded. Brain running,1893cycles,6457memories; it was thinking
at ORIENT. Worker registry currently8ok,1failing(Cognitive brain),1stalled
(Quant lab researcher), so overall worker health is NOT all green.
Latest brain error at19:08:27: explicit7B local-model request timed out and
raised BrainUnreachable. CPU benchmark was only3.63tokens/sec with7B on2cores.
Report this as an open runtime/capacity issue. Do not silently choose a weaker
model or vendor, reset health, enable execution, or claim every function works.
Investigate under a separately authorized task; this handover requests
acknowledgment/awareness only. Deployment scripts are reviewable on the isolated
branch; any integration must follow the existing integrator/review process.

## Safe next work
Review the timeout/stalled researcher; validate the next full scheduled
training run; provision individually authorized provider credentials/private
device access only if requested. Any future source update needs explicit
deployment to Oracle; modifying Windows source alone does not update the server.
For rollback, stop cloud services AND aria-pipeline.timer first, reconcile newer
cloud state from verified backups, then restore one Windows writer only.

## Delivery to Claude
The owner-requested handover was queued in the existing Claude Desktop Code
session "ARIA trading system architecture review" using Ctrl+Enter so its
ongoing read-only review was not interrupted. The UI confirmed "1 message
queued". Receipt/acknowledgment has not yet been observed. The same document
and own Codex log pointer are present in the active primary checkout.
