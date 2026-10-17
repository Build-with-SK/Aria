# ARIA integration handoff — 2026-10-17

Branch: `codex/aria-final-verification`; baseline: `b5b47f2`.
Recommendation: **BLOCKED for release**, pending independent review, security
remediation and external PAPER-only validation. No merge/push/deploy, production
restart, real order or live enablement. Original Codex unfinished work preserved.

## Commit provenance

| Candidate | Source / purpose |
| --- | --- |
| c9cf484 | Claude e4f01ad: deterministic ids and initial settlement |
| 2aad950 | Claude 4c0a172: pristine universe fixture |
| 209a1b2 | Claude 4f3b18e: found-only settlement, claims, reconciliation routes |
| 0c965d2 | Claude d2f5e80: atomic claim recheck |
| 09f2158 | Codex: durable queue, adapter/OCO/risk/chat reliability |
| 0a1b21a | Codex: accurate status UI and compatible lock updates |
| 048f47b | Codex: desk exit/journal recovery, terminal partial state, lazy sentiment dependency, CI and regression compatibility |
| d07351a | Codex: exclude private execution audits/locks from Git |

Reused Claude commits retain attribution. Do not cherry-pick equivalent commits
twice if S1.3 is integrated separately. Review the complete diff from b5b47f2 and
the Codex additions after the follow-up; integration remains Claude's decision.

## Contracts requiring review

- Queue schema accepts existing rows through dataclass defaults; new ids are
  full UUIDs. Sibling .lock and .audit.jsonl files provide OS exclusion and
  persistent transitions. Claims are durable before POST. Corruption/audit write
  failure blocks sends; never delete an uncertain claim to recover availability.
- UNKNOWN never clears on a remote 404. Owner release stays step-up gated,
  requires a note, and has no automatic caller. Check the broker first.
- in_flight() includes working acknowledgements; reconciliation refreshes these
  too. order_terminal distinguishes cancelled partial fills from working
  remainders without dropping confirmed quantity/price.
- Protections carry situation=protective_exit and durable ids/claims. Ambiguous
  OCO has no separate-stop fallback. Hidden reducing claims block another exit.
  Adapter read failures now raise; do not turn them back into an empty account.
- PositionManager paper exits reuse OrderManager and re-read reduce-only after
  claiming. No automatic call of the human approval HTTP handler. Live accounts
  still queue for human approval. Auth/live/session/risk invariants retained.
- IBKR orderRef supports lookup, not venue-enforced uniqueness. Software claims
  prevent resends; missing gateway history remains UNKNOWN. Windows OS locking
  is tested locally; Linux awaits CI.
- TradingPipeline stays non-production (JOURNAL_SCOPE unchanged). Its journal is
  locked/durable; startup derives a halt from unsettled records. Only the
  synchronous in-process simulator may declare absence authoritative.
- Pending entries conservatively DEFER new exposure rather than contribute
  zero risk. Quote/snapshot skew tolerance is explicitly bounded at five seconds.
- No endpoints removed/renamed. Reused owner reconciliation/release routes keep
  their guards. Frontend CI builds/checks only; no publication step added.

## Remaining integrator gates

1. Independently review execution changes; run the complete suite on the final
   integration commit and remote Linux CI. No local result authorises cutover.
2. Review remaining npm major upgrades and triage/rebuild the installed Python
   runtime. No forced upgrade/shared-venv mutation was performed here.
3. Validate actual PAPER-only delayed acknowledgement/visibility, partial cancel,
   gateway restart, protective reservation and real fill/fee reconciliation.
   Before cutover, compare pre-S1.3 queue/execution records with the broker.
   Legacy approved/rejected rows without a recorded client id may predate the
   new durable claim protocol: their local label alone cannot prove nothing
   was sent. Quarantine ambiguous legacy rows and verify them externally;
   never replay them automatically or treat an empty new journal as evidence.
4. Complete cumulative partial-exit/fee accounting evidence before relying on
   trade-level net P&L. Preserve reconciliation residuals and estimated flags;
   the mock results do not prove complete external accounting.
5. Profile cold memory access (25-second timeout), and verify real UI hydration
   and action status against the integrated backend. Staging build not deployed.
6. Preserve point-in-time news/fundamental limitations; no edge/live-money claim.

See the audit, verification, test-results and Codex collaboration log for the
evidence and intermediate failures. The chat report identifies the final docs
commit; the implementation commit above anchors the source review.
