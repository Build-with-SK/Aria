# External PAPER broker — Phase 2

Read-only Alpaca connectivity/account/history/positive recovery PASS. Historical
local-to-broker reconciliation remains BLOCKED where evidence is insufficient.
No order submission, cancellation, replacement, live account request, production
queue mutation or service restart was performed.

The isolated worktree has no broker credentials. The primary application's
configuration contains keys and explicit paper intent. The owner expressly
approved use **only** against `https://paper-api.alpaca.markets` for connectivity,
account status and read-only order/reconciliation queries. That approval was
received before external credential use. IBKR infrastructure/credential use was
not approved; configured paper ports had no local listener at inspection.

## Evidence

- TLS-verified GET /v2/account: HTTP 200, ACTIVE, account/trading not blocked.
- GET /v2/orders with status=all and bounded history: HTTP 200, terminal history
  readable. No account number, balance, ticker, real order identifier or secret
  is copied into this public handoff. Exact private evidence is outside Git.
- A real historical filled order was read through the candidate Alpaca adapter
  by broker id and original client id. Identity and cumulative fill agree;
  positive quantity/price and terminal state are retained; intent metadata exists.
- Historical local queue references were read without constructing a production
  OrderManager or invoking the migration. A bounded sample of available broker
  identifiers returned 404; other rows lacked original identifiers. None of the
  persisted broker ids matched the bounded retrieved history. These results
  establish **insufficient evidence**, not non-execution or permission to resend.
- 27 successful/attempted external request operations in the completed checks,
  all GET. Direct HTTP refused redirects and bypassed proxies; the SDK transport
  was wrapped to refuse non-GET, non-HTTPS or unapproved-host requests and redirects.
  Initial SDK checks stopped before network use because its base URL is an enum;
  the allowlist correctly uses the enum value. No product guard was changed.

## Remaining prerequisites

1. Claude reviews/integrates the candidate before any production migration.
2. The owner/broker establishes which historical paper account/environment each
   unresolved row belongs to, including account resets/key rotations and broker
   retention limits. Obtain original order/execution statements and permanent
   references; retain the current audit and original snapshot.
3. Bind only conclusive identifiers with an owner/evidence note, then perform
   read-only reconciliation. Unmatched/404/missing-reference orders stay UNKNOWN.
   Owner release requires evidence and the existing step-up-protected decision;
   never mass-release because current history did not contain the old ids.
4. A separate, explicit order-level approval and controlled paper test plan are
   required for new submit/lost-ack/partial-fill/restart/fee acceptance scenarios.
   Read-only historical confirmation cannot prove those new-order scenarios or
   cumulative partial-exit accounting. No such approval was requested or assumed.
5. IBKR needs an approved paper gateway/session and evidence tying original
   orderRef/permanent identity to the account/client. Bare session orderId is
   insufficient. Do not infer paper from a configured port alone.

The official [paper trading documentation](https://docs.alpaca.markets/us/docs/paper-trading)
describes a simulation with separate credentials/endpoints, and the official
[client-id lookup](https://docs.alpaca.markets/us/reference/getorderbyclientorderid)
is a GET. A passing read-only paper check is not a production-readiness claim.
