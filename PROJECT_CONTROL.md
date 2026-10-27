# PROJECT_CONTROL — ARIA and SENTINEL

**Date:** 2026-10-27 (session opened 2026-10-26)
**Status:** Canonical control document. Authored by Claude (independent review), sole editor as assigned by the Project Director.
**Release recommendation:** **BLOCKED** (see Pending Gates at the end).

---

## 1. Roles

| Role | Holder |
|---|---|
| Project Director (final authority, sole approver) | Owner |
| Engineering Coordinator | ChatGPT |
| Principal Engineering & Release Lead (implementation, reconciliation, evidence) | Codex |
| Architecture and Independent Review | Claude |

## 2. File and lane ownership

| Artifact / lane | Sole editor |
|---|---|
| `PROJECT_CONTROL.md` (this file, canonical) | Claude |
| `PROJECT_CONTROL_CODEX.md` | Codex |
| Each agent's own log | That agent only |
| Reconciliation records, failure/skip/evidence records | Codex |
| Implementation and isolated integration branches | Codex |
| Architecture review, independent review findings and verdicts | Claude |
| Frozen schema, catalog and dataset contracts | Claude (reviews them; changes only with owner approval) |

- Each file has exactly one editor. **No simultaneous edits.** An agent that needs a change in a file it does not own asks the owner of that file to make it.
- Each agent alone edits its own log. No agent edits another agent's log.
- Codex integrates this file's bytes exactly as authored. Codex does not edit them.
- Codex records every failure, skip and piece of evidence in its own lane. Claude records review findings and verdicts, and reviews only exact, identified source snapshots.

## 3. Arbitration and interfaces

- Disputes go to the **Project Director**. Neither agent settles a dispute by acting first.
- **No silent interface overrides.** Any change to a shared interface, schema, endpoint or contract is stated in writing, reviewed and recorded before it is integrated.
- **Frozen contracts cannot be superseded by this document.** If this file and a frozen schema, catalog or dataset contract conflict, the frozen contract wins until the owner explicitly changes it.

## 4. Supersession of WORK_PLAN

The older `docs/collab/WORK_PLAN.md` ownership is superseded **only to the extent of** the owner's latest appointment of Codex as engineering lead and the explicit, reviewed assignments in this document. Everything else in WORK_PLAN still applies. Its invariants and the runtime isolation between agents, branches, folders and environments stay fully in force.

## 5. Actions that need explicit owner approval

None of the following happens without **explicit, recorded owner approval** given for that specific action:

- merging to a protected branch;
- a production deploy or restart;
- live orders or any financial execution;
- replacing a production model.

**Status as of 2026-10-27: none of these approvals has been granted.** Elapsed time, deadlines, mission statements and other task text do not grant any of them.

## 6. Standing safety invariants (cannot be relaxed)

- Human approvals stay mandatory. The brain proposes and the human approves. Nothing calls `approve_and_execute` autonomously.
- PyAutoGUI `FAILSAFE` stays enabled.
- LLMs cannot execute arbitrary commands or financial actions.
- An approval given in one context does not carry over to another.

## 7. University training and resources

- Training runs only with **verified authorization** and access to **permitted resources** only.
- **No Bubblewrap bypass**, and no sandbox or access-control circumvention of any kind.
- Datasets, environments and artifacts stay **separate** from each other and from ARIA/SENTINEL production data.
- Desktop-session and university-session details that are pending or announced **are not authorizations**. Authorization exists only once it is verified and recorded.
- **Status as of 2026-10-27:** desktop and university authorization and resources are **unverified**.

## 8. Current state and decisions

- **Verified source anchors:**
  - ARIA `6c30df588b552e21bf8166f81f5963ca799b7d2f`
  - SENTINEL `f3a0a8d278d6fc112f4809d09989ec984509673f`
- **Final local suites (on the anchors above):**
  - 3585 passed, 12 skipped
  - 2243 passed, 16 skipped
  - Skips are recorded as skips. They are **not** counted as passes.
- **Independent review:** both pinned Claude reviews of the exact anchors are **APPROVE WITH LIMITATIONS**. They cover scoped local checks only. They are **not** whole-project acceptance.
- **Change rule:**
  - Any implementation change needs relevant re-verification and a new review against a new pinned anchor.
  - Docs-only handoffs keep the anchors above unchanged.
- Nothing in this document claims 100% completion, full verification or release readiness.
- **Decisions:**
  1. Keep fixing and verifying in isolation. Critical defects come first.
  2. The release recommendation stays **BLOCKED** until every gate below is recorded as passed.

## 9. Pending gates

| Gate | Owner of evidence | Status |
|---|---|---|
| Local test suites on both pinned anchors | Codex | Complete, with skips recorded (12 and 16) |
| Independent review of the pinned anchors | Claude | Complete: APPROVE WITH LIMITATIONS (scoped local checks only) |
| Whole-project acceptance | Codex → Owner | Pending |
| Model qualification | Codex / Claude review | Pending |
| Security, host and dependency remediation | Codex → Owner | Pending |
| External venue state and fees | Codex → Owner | Pending |
| Desktop / university authorization and resources | Owner | Unverified, not authorized |
| Protected merge / production deploy or restart / live execution / production model replacement | Owner | Not granted |
