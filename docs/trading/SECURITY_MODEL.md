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

Tests cover remote anonymous/service denial, invalid input, stale/missing evidence,
account switch, all MT5 mutations, concurrency, rollback, replay and audit edits.
Protected auth/live gate/approval/workflow/fence files were not edited.

Limits: SQLite is local-owner storage, not a hardened remote service or secret
vault. A privileged attacker can inspect Python memory, replace code, rewrite the
whole database/chain or truncate an unanchored suffix. Chained hashes detect
ordinary event changes, not a compromised host. External anchoring, filesystem
ACLs, encrypted backups, process identity, vulnerability scans and worker isolation
must be verified before any demo-send release. No sandbox claim is made.
