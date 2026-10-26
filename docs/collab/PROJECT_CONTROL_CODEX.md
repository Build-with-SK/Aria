# ARIA / SENTINEL release coordination — Codex lane

Opened 2026-10-26 by the owner's Principal Engineering & Release Lead mission.
This file and `RELEASE_RECONCILIATION_CODEX.md` are Codex-owned. Claude retains
`WORK_PLAN.md`, its logs, schema/data contracts and review verdicts. A shared
`PROJECT_CONTROL.md` must have one agreed editor; no ownership acknowledgment
from Claude is assumed. Codex proposes Claude as its editor, with Codex updates
submitted in this lane. No simultaneous edits to shared source are authorized.

Isolated branches: `codex/aria-release-reconcile-20261026` and
`codex/sentinel-release-reconcile-20261026`. Protected/runtime branches stay put.
ARIA starts from e4e4de1 plus Claude review 5270df6; SENTINEL starts at 22646cf.
The owner mission authorizes integration repairs; existing Claude requests in
ARIA_REVIEW_HANDOFF explicitly assign the reviewed defects to Codex.

Priority: reproduce and repair ARIA F-01/F-10, then F-02/F-09/F-06; reconcile
SENTINEL's newly fetched Claude cognition changes and rerun isolated gates.
Training cannot proceed on an unverified university host. Do not repeat the
failed full-context 8 GiB experiment, enable candidate routing, modify datasets,
read sealed benchmark items, or represent infrastructure smoke as qualification.

No production merge, push, deployment, live order, external paper order, model
replacement or financial transaction is authorized. Owner approval remains a
release gate. PyAutoGUI FAILSAFE, exact approvals and all trading guards remain.

## Ownership acknowledgment
Claude CLI review session cf670847-6379-488f-99f6-c2459a8a4add explicitly accepted
sole edit ownership of future canonical PROJECT_CONTROL.md on 2026-10-26.
Codex owns this lane, its logs/reconciliation and isolated branches. Neither
file can override frozen schema/catalog/dataset contracts. No concurrent source
edits occurred: the review used read-only tools and made no changes.
