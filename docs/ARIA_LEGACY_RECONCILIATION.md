# Legacy reconciliation — Phase 2

Local implementation PASS; deployment migration and external broker evidence
remain BLOCKED pending Claude review and owner-approved broker access.

## Recovery protocol

Queue reads and mutations take the existing process lock. Historical rows with
no client id or durable submission protocol version are quarantined as UNKNOWN
on first access. This includes historical approved, failed/rejected, cancelled,
executed and interrupted rows: old labels do not establish whether a POST was
accepted. A genuinely pending legacy proposal with no broker/send/fill evidence
can still be approved and enters protocol version 1. Newly created rows record
version 1 before approval. Existing modern client ids remain usable.

Migration preserves the entire old row in `legacy_original`, atomically saves
the UNKNOWN snapshot and appends it to the existing audit journal. Existing
audit entries are never rewritten. A crash before replacement preserves the
old file for retry; a crash after replacement leaves durable UNKNOWN and the
original evidence, even if the audit append failed. A failed migration read
raises and cannot authorize a send. Migration is idempotent across restarts.

Quarantined orders are not approvable, rejectable as unsent, cancellable as
unsent, or resubmitted. They participate in the same alias-aware queue claim
and desk/reflex blocking checks as modern UNKNOWN orders. Timeouts, errors and
404s never mean rejection. No migration fabricates a deterministic client id
for an order that was previously submitted without one.

The reconciliation operation performs only broker reads:

| Evidence | Action |
| --- | --- |
| Persisted modern or owner-verified original client reference | Existing client-id lookup. |
| Persisted Alpaca broker order id, explicit alpaca routing | Account-scoped lookup by broker id; require exact id, matching normalized symbol, side and original quantity. |
| Duplicate persisted broker ids or mismatching broker intent | Stay UNKNOWN; record error; manual review. |
| Bare IBKR session orderId, no original orderRef | Stay UNKNOWN. Session-local IDs can be ambiguous after reconnect; do not match by ticker/quantity/time heuristics. |
| Missing IDs, unavailable history, lookup failure or 404 | Stay UNKNOWN, preserve reservations and block re-arming. |
| Broker acknowledges working or partial | Persist actual broker id, cumulative fill quantity/price and working reservation. |
| Terminal partial (cancelled/expired remainder) | Preserve actual fill; close working reservation only on terminal evidence. |
| Terminal broker refusal with no known fill | Record broker word, never inferred from timeout/absence. |

Broker updates cannot decrease cumulative fills, erase a known price on an
equal-quantity update, reopen a terminal order, change a bound broker identity,
or clear a known fill on a zero-fill cancellation callback. Duplicate identical
updates do not add state transitions. Invalid/non-finite/oversized fill evidence
is refused. This does not implement cumulative partial-exit fee/P&L accounting;
that still needs external statement evidence and integration review.

## Owner-operated recovery when evidence is missing

Keep automation blocked. Review the original snapshot, append-only queue audit,
broker order/execution statements, account/environment, original client
reference, permanent identifiers and fills. Do not identify an order by symbol
and an approximate timestamp alone. No credentials were used for this work.

If the owner finds a conclusive original identifier, the explicit maintenance
method `ApprovalQueue.bind_legacy_evidence(trade_id, by=..., note=...,
broker_order_id=..., client_order_id=...)` records the owner and evidence note.
It refuses conflicting or already-bound identities. It does **not** settle,
send, cancel or re-arm anything. The row remains UNKNOWN until a subsequent
read-only reconciliation finds the broker's answer. Use the original broker
reference, never `queue_client_order_id()` to invent one for the old send.
For IBKR, an original orderRef can use the existing lookup; a bare session ID
still cannot. Resolve account/client/permanent-ID ambiguity with the broker.

If broker investigation conclusively establishes no accepted order, the
existing owner-only, step-up-protected release endpoint records who and why.
The maintenance `release_unresolved` method likewise requires owner and note.
Release is a consequential human decision, never called by recovery or a
negative lookup. Without sufficient evidence, leave UNKNOWN and the gate
BLOCKED; do not release merely to make the dashboard green. No production
queue migration was run in this worktree.

## Verification

157 tests passed in 21.59s: legacy cases plus order settlement, independent order
safety review, final execution reliability and reduce-only regressions. Cases
cover pre-replace crashes, post-snapshot/pre-audit failure, restarts, all unsafe
historical labels, missing IDs, manual evidence, 404 then delayed acknowledgement,
conflicting identity/intent, duplicate callbacks, stale callbacks, partial fills,
terminal remainders, invalid quantities and new-claim blocking. Tests use temp
state and synthetic brokers; no external order, credential or production state.
Ruff fatal/error checks pass. Full upgraded-environment and Linux/browser
results are recorded in the Phase 2 handoff.

Claude overlap: `approval_queue.py`, `order_manager.py`, `alpaca_broker.py`.
Review against the completion branch's newer durability/launch checks before
integration; preserve its independent invariants and all existing release gates.
