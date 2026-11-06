# Oracle deployment preparation

Status on 2026-11-06: the owner reports the Oracle instance is Running and has
transferred its SSH key to Windows Downloads. No application upload has occurred.
The private/public key pair was verified locally without printing private
material. A separate local SSH copy has inheritance disabled and an owner-only
ACL; the copy was successfully parsed by OpenSSH and matches the public key.
The instance's Public IPv4 address has not been supplied, so no SSH connection
or server-side installation has been attempted.

Browser access now exposes an in-app browser, but it reaches Oracle's Cloud
Sign In page rather than an authenticated console. No OCI CLI configuration
exists on this machine. Do not create another cloud account.

## Proposed first server

Use one Always Free eligible Ubuntu ARM instance in the account's home region,
with 2 OCPUs and 12 GB RAM. The documented free-account allowance is 1,500 OCPU
hours and 9,000 GB memory hours per month, plus 200 GB combined boot/block
storage. Inspect the account's actual quotas and cost estimate before creation;
do not upgrade to paid resources to work around unavailable capacity.

Sources checked:
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm

This is a proposed target, not a measured capacity or compatibility result.

## Source inventory

ARIA's active checkout is on `aria/integrity-upgrade`, revision `58f5f25`,
ahead of its remote by three commits at inspection. Its data changes during
normal operation. The existing `codex/work` checkout has unrelated unfinished
evaluation work; do not deploy that checkout or change its unfinished files.

Sentinel's active `atlas` checkout is on `sentinel`, revision `5ea3979`, ahead
of its remote by 46 commits at inspection. It also has an uncommitted change to
`sentinel/tools.py`, untracked Anatomy integration files, and changing runtime
data. Inspect and preserve current working source changes during packaging;
cloning the remote alone will miss local work. Recheck revisions at cutover.

Sentinel's newer `deploy/mac_mini/README.md` assigns the mini monitoring and
backup roles rather than the full inference workload in ARIA's older guide.
Neither guide is evidence of a tested Oracle Linux installation. Existing
launchd jobs and Windows launchers need Linux service equivalents.

Read-only local inventory measured 18,498 ARIA data files (4.962 GiB) and 149
Sentinel data files (3.469 GiB), about 8.431 GiB combined. This excludes the
vault, model downloads, source, environments and sibling applications, and is
not a consistent backup while writers are running. Both existing local
`/ready` endpoints returned HTTP 200 with `ready: true`; neither was restarted.

## Migration sequence once the account is available

1. Complete signup or sign in to the owner's existing Oracle account. The owner
   enters new passwords, card information and verification codes as needed;
   accept signup terms only with the required action-time authorization.
2. Review the eligible instance, storage, network and cost estimate. Establish
   SSH access using a dedicated key. Keep application and model ports private.
3. Build an explicit source manifest from the current active working copies.
   Exclude Git metadata, Windows virtual environments, package caches and
   unrelated projects. Inventory sibling tools such as Anatomy and God's Eye
   View individually; preserve their upstream notices and pinned revisions.
4. Install separate Linux virtual environments for ARIA and Sentinel. Verify
   ARM wheels, required imports, frontend build and health checks before moving
   state. Install only model/runtime dependencies actually needed on this host.
5. Map configuration paths to Linux. Provision credentials through a private
   channel, outside source archives and logs. Do not copy the entire local
   environment automatically. The owner's vault remains private and requires
   an explicit transfer decision and restricted storage.
6. Stage the applications with scheduled jobs and execution disabled. Validate
   authentication, origin checks, private model access and approval gates.
7. Quiesce local writers for a consistent final state snapshot. Use SQLite's
   backup mechanism or stop database writers; do not copy a live database alone
   while ignoring its WAL. Verify snapshot integrity and file hashes.
8. Restore state on the server. Confirm PAPER mode and prevent both hosts from
   running the same scheduled work. Switch one service at a time and retain a
   verified local rollback copy. Do not enable live trading or approve actions.
9. Verify owner access over the selected private route or authenticated HTTPS,
   anonymous denial of private APIs, health/readiness and restart persistence.
   Validate off-server backups before treating the migration as complete.

## Constraints to resolve on the target

- No GPU is included in the proposed instance. Measure small-model inference;
  local inference, training and multiple browser workers cannot be assumed to
  fit beside both services.
- Windows desktop tools require a Windows-side agent; server deployment alone
  does not move the desktop or its signed-in browser sessions.
- God's Eye View currently generates loopback browser links. Remote viewing
  needs a deliberate authenticated route; exposing its provider-settings
  endpoints would violate its current local-only design.
- Keep client workloads separate from the owner's applications and private
  data. This request does not establish a client-hosting resale arrangement.
- Free instances have capacity/reclamation limits and no Free Tier SLA.

## Completion evidence still required

Oracle account identity, region, eligible instance OCID, SSH connection,
Linux/ARM import and build results, consistent state backup verification,
authenticated application checks, scheduler handover and reboot recovery.
None of these are claimed complete by this preparation note.
