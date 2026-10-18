# Historical Alpaca reconciliation — read-only procedure

Status: **BLOCKED for actual account reconciliation** until the owner supplies an authorized account/environment and exports or approves authenticated GET-only access. This mission performs no broker calls and changes no production queue. Execute the procedure against private copies; publish only redacted conclusions.

## 1. Preserve evidence and define scope

1. Record the deployed commit, account/environment identity (paper/live), statement timezone, retrieval UTC time and operator. Confirm hostname and account together; a paper hostname alone does not identify the historical account. Do not copy credentials into a report.
2. Obtain read-only exports or owner-approved GETs for orders, executions/fills, account activities/fees and statements. Preserve original bytes, retrieval filters, all page tokens, status codes and SHA-256 hashes outside Git. Keep a separate redacted report. Do not reuse a trading adapter that can submit or auto-reconcile the production queue.
3. Copy the queue and its audit/reconciliation journals using an existing backup or authorized read. Record checksums and never edit the originals. Obtain archived queues near the event if the current copy has changed.
4. Establish an inventory of all legacy rows lacking deterministic client IDs, including executed/cancelled/rejected labels. A local rejection can have been written after a lost acknowledgement. Labels alone never establish venue state.

## 2. AAPL / queue reference dfe2a885

The owner-specified target is **6 August 2026, 21:02 UTC**, AAPL, local queue reference **`dfe2a885`**. Preserve the original timestamp string; a naive `created_at` has no timezone proof. Do not silently assign it UTC or confuse 21:02 UTC with exchange local time (17:02 New York on that date). Search a wider UTC interval and the full surrounding session/day, recording both conversions and the inclusive/exclusive query boundaries.

- The old local record described buy 1 market and a test-artifact rejection. Treat that description as a lead, not proof that it was a test or that nothing was sent.
- `dfe2a885` is a local queue reference, not an Alpaca UUID or proven client-order ID. Do not send it as a broker order ID or create a new deterministic ID retroactively and claim it identifies the original submission.
- Query/export all order statuses, including canceled, rejected, expired, replaced and filled, in ascending time with nested bracket legs where available. Exhaust pagination and split date intervals if the provider caps results. Retain replace/parent/child chains and original IDs.
- Retrieve an original known broker UUID and/or original recorded client ID individually if available. Capture positive order and execution evidence. Verify account, symbol, side, quantity, timestamps and replacement relationships; a symbol/time/quantity match by itself is only a candidate.
- Cross-check fills/activities and daily/monthly statements, not just the orders endpoint. A subsequently canceled order may have partial fills. Account position today cannot prove that a historical order never existed.

For approved REST reads use only the provider's orders, individual order/by-client-ID and account-activities GET resources. Verify current pagination and historical retention behavior before the session. Disable HTTP retries that change methods, refuse unexpected redirects, record errors, and use no POST/PATCH/DELETE/cancel endpoint. No commands here contain credentials or default to a live account.

## 3. Reconciliation outcomes

| Evidence | Record in the disposable report | Trading authority |
| --- | --- | --- |
| Original identity plus matching filled executions | CONFIRMED FILLED, cumulative fill quantity/VWAP and evidence references | None |
| Original identity plus working/submitting/partial order | CONFIRMED WORKING/PARTIAL, residual quantity and protections | None |
| Original identity plus terminal order and all execution evidence | CONFIRMED TERMINAL, retain any actual partial fills and fees | None |
| Similar symbol/time/size without original identity | CANDIDATE MATCH — needs further evidence | None |
| 404, empty list, incomplete pages, wrong/missing account, missing retention period, read outage | UNRESOLVED / evidence incomplete | Never retry, re-arm or release |
| No result even after complete available export | NO MATCH IN AVAILABLE EVIDENCE; list coverage limits | Never treat as proof of no submission |

Compare totals per account/instrument against executions, statements, positions and cash movements. List every unexplained residual and its evidence gap. Final report must explicitly address `dfe2a885`; acceptable conclusions include unresolved. Fees unavailable remain unknown, never zero.

## 4. Stop and handoff

Stop if account/environment identity differs, pagination is incomplete, statement coverage is missing, an identifier points to another instrument/order, or any tool proposes a mutation. Preserve evidence and request owner review. No reconciliation result in this procedure changes a queue row. Releasing an UNKNOWN row is a separate owner decision through the existing step-up/note workflow after sufficient evidence; this procedure grants no release/resend authority.
