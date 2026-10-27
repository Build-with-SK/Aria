# Controlled paper-trading acceptance protocol

Status: **prepared; external execution BLOCKED pending new explicit owner authorization**. Local fake-broker fixtures do not authorize a venue order. A signed-in session or earlier permission for read-only GETs is insufficient.

## Reproducible setup and evidence

Record candidate SHA, exact interpreter/package freeze, frontend build hash, account identity and paper hostname, UTC window, session calendar, baseline order/position/cash snapshots, applicable risk limits and empty/new test intents. Use a dedicated paper account where possible. Store evidence privately with checksums and a redacted run report. Live gate must remain false; the desk remains unarmed unless separately authorized. No test should weaken approval, risk, session, reduce-only or kill-switch checks.

Owner authorization must name instrument, side, quantity or notional ceiling, order types, expiry/window, maximum total orders, permitted cancels and test stop conditions. Each new order has a human approval record; fixture code must not call the human approval HTTP handler autonomously. Simulation is preferred for cases the venue cannot deterministically produce (particularly partial fills and lost acknowledgements). Report external cases as BLOCKED if they cannot be produced safely within the approved limits.

## Scenarios — all external actions need the above authorization

| Scenario | Controlled steps | Required evidence / PASS condition |
| --- | --- | --- |
| Baseline and risk | Capture account/positions/open orders; attempt over-limit and stale/disconnected cases only in isolated fixtures | No venue mutation for refused intent, traceable refusal, no false flat/connected state |
| Approved submission | Owner creates and approves one bounded proposal after risk/session checks | Durable claim and original deterministic client ID precede a single POST; acceptance displayed as working until a fill is confirmed |
| Lost acknowledgement | With an approved test transport, allow ONE accepted submission and hide only its response; cut traffic after the send, never resubmit as fault injection | UNKNOWN/submitting survives restart; lookup finds the same broker/client identity; exactly one venue order, one claim; negative lookups never release it |
| Partial fill | Use isolated deterministic simulation first; external partial only if venue/owner can arrange it within limits | Each execution ID counted once; cumulative filled quantity and actual VWAP retained; remainder stays reserved; no fabricated full fill |
| Cancellation | Owner approves cancel of the known remaining order only | Cancel-request acceptance distinguished from terminal cancellation; verify terminal broker state plus any fills that raced the cancel; protections and remaining position remain accounted for |
| Restart and disconnect | Restart only an authorized disposable acceptance instance, disconnect its test transport while working/UNKNOWN, then reconnect | Claim/quarantine survives, startup performs reads before any new send, original IDs retained, one active writer, no new order from a missing response |
| Duplicate prevention | Concurrent approval attempts/replayed callbacks are exercised locally; approved external repetition only if expressly scoped | At most one POST per intent; no alias, lane or second-process bypass; callbacks do not double-count fills, fees or outcomes |
| Fees and P&L | Collect fill/activity/commission statements for every partial and final execution | Net P&L = signed actual proceeds minus actual cost and fees; match broker statements, currencies and documented rounding tolerance; missing fees explicitly unreconciled |
| Final reconciliation | Read all orders/activities/positions/cash after fills/cancels settle | Every created order has an accounted identity/state; cumulative fills match position deltas and cash/fees; no unmanaged remainder, duplicate, unknown residual or unexplained P&L |

For each scenario record start/end UTC, owner approval reference, original client/broker IDs, order transitions, request counts, execution IDs, quote/signal ages, risk decision IDs, alerts and reconciliation results. Keep secrets out of logs. Repeat the local scenario after process restart and compare the resulting state, including duplicate event replay.

## Abort and completion

Abort on any live endpoint/account, unexpected extra POST, breached bound, identity mismatch, unreadable risk/book, missing durable claim, unprotected/unaccounted residual or unapproved action. Do not automatically flatten: an abort does not grant permission for another order. Use read-only observation and ask the owner for any required cancellation/position action.

Each scenario receives PASS, FAIL or BLOCKED with evidence. End only after owner review of final account/position/order/fee reconciliation. A failed or unresolved scenario blocks acceptance. Keep live disabled regardless of this outcome; paper execution proves software/venue behavior, not trading profitability.
