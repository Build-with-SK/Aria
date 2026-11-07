# Security model

New HTTP routes use the existing structural role guard plus `require_owner`.
Invalid research input returns a generic 422, not a validation echo containing
private data. No credentials are accepted, returned or discovered. Research
content and MCP/plugin text cannot select an operation or execution transport.

New execution posture is enforced by **absence of a sender**, not just flags.
There is no new live/paper activation endpoint, plugin loader, model tool or MT5
mutation path. Permissive illustrative environment flags do not enable writes.
The actual legacy configuration system remains unchanged; no duplicate `.env`
switches were added. Future activation must be reviewed against existing modes,
kill switch, preflight, reduce-only controls and authenticated step-up.

Review binding includes the full proposal (account, instrument, side, type,
quantity, prices/tolerance, expiry, snapshot and profile), risk/policy hashes,
bounded TTL and one secret capability token stored hashed. A transaction consumes
it and reserves exactly one intent. Changed profile/risk/payload and replay fail.
The store never accepts a risk verdict: `queue` and `reserve_intent` take the
evidence (`RiskInputs`) and run `assess()` themselves under the store's fixed
policy (`ReviewStore.POLICY`), the second time inside the reserving transaction.
A hand-built ALLOW is refused (review CR-02). The stored assessment must match its
audit record, and a caller clock more than 5 s from the store's clock is refused.
No public API exposes library review methods. The `authenticated_owner` library
argument is NOT an authenticator: only a trusted server handler may supply it.

Order stages are PRACTICE-only in this release (review CR-07): the store refuses
to queue or reserve a proposal whose rule profile is `PAID_EVALUATION`,
`FUNDED`, `LIVE` or undeclared, re-checking at reservation. The server's DEMO
label is not used as the boundary, because every FTMO account is a demo account.
Contracts reaching the risk engine, rule evaluation or the store are rebuilt
through validation first, so `model_copy(update=...)` cannot bypass a validator.

Tests cover remote anonymous/service denial, invalid input, stale/missing evidence,
account switch, all MT5 mutations, concurrency, rollback, replay and audit edits.
Protected auth/live gate/approval/workflow/fence files were not edited.

MT5 (review CR-06): the SDK is imported only inside a separate worker process;
ARIA holds a pipe-and-process client with no SDK object, the worker refuses
every message but allowlisted reads before the SDK sees it, and it connects only
with an investor login (`trade_allowed` False on the account and the terminal).

The audit chain is keyed (review SEC-01): with an audit key held outside the
database, each event is an HMAC-SHA256 link, so writing the file is not enough
to rebuild a valid chain. Intent reservation requires a keyed chain. An external
anchor (`anchor()` / `verify_audit(anchor)`) detects truncation and rewriting
through the anchored event.

Limits: SQLite is local-owner storage, not a hardened remote service or secret
vault. A privileged attacker who can read the key (process memory, the key file)
or replace code can still forge the chain; one who can also replace the anchor
can hide truncation. The key and anchor stores are the operator's: this release
ships neither. Filesystem ACLs, encrypted backups, process identity,
vulnerability scans and the MT5 worker's host must be verified before any
demo-send release. No sandbox claim is made.

## Remediation review follow-up: materialized authority

A valid HMAC chain alone does not authorize SQLite rows. Transactions now
reconstruct proposal states, approval identity/token/actor/expiry, terminal token
consumption, intent identity/state and the complete boundary key set from audit
events. They compare the reconstruction with the materialized tables before any
operation and again before commit. Approval events now include all authority
fields. Older stores whose approval events lack these fields fail closed and
require a separately reviewed rebuild; no automatic trust upgrade is made.
Deleting boundary rows or inventing approval rows cannot be repaired by appending
new public-method events. External anchoring remains necessary to detect removal
of an entire unanchored audit suffix. These checks do not establish authenticity
of caller-supplied market/account evidence; the trusted acquisition boundary is
still an open blocker (CR-03/05/07/09/11).
