# Decisions before implementation

1. Extend `src/trading/` with explicit multiasset contracts, provider policy and
   durable review state. Preserve legacy interfaces. Latest owner brief assigns
   this implementation branch to Codex and Claude to read-only review, superseding
   the older area ownership for these additions. Do not edit `WORK_PLAN.md`.
2. Add routes only to the existing FastAPI app with lazy imports and existing
   owner dependencies. Expose research/read-only operations; no direct order-send
   API and no real terminal connection triggered by HTTP requests.
3. Decimal arithmetic and timezone-aware UTC contracts; provider-symbol identity
   is mandatory, prices are never joined on ambiguous display tickers.
4. FTMO examples are discovery-only, unconfirmed and non-executable. Account,
   product, creation date, phase and entity are part of profile identity. Unknown
   restrictions or snapshot methodology mean POLICY_UNVERIFIED.
5. New workflow execution is DISABLED. MT5 is read-only in this release; mutation
   methods refuse even when legacy PAPER mode or permissive environment flags
   are set. No public service, worker deployment or real order is authorised.
6. Additive SQLite tables in a separate explicit-path review store, schema version
   recorded; no changes to production databases. Approval consumption, intent and
   hash-chained event share a transaction. No default database creation on import.
7. Analysis returns proposals or ABSTAIN, never broker orders or authorisation.
   Reuse indicator/PIT/backtest implementations rather than rebuilding each.
8. No arbitrary plugin imports. Without an OS sandbox third-party code remains
   disabled. Capability manifests and MCP policy checks alone are not a sandbox.

## Threat model and acceptance

Threats: stale/malformed quotes, symbol substitution, missing FX rates, account
switch, reordered approval payload, replay, racing processes, crash during send,
ambiguous acknowledgement, malicious MCP data, secret leakage and stolen files.
Acceptance: deterministic refusals with reasons; profile/content hashes; unit
and adversarial tests without credentials; no extra npm packages; existing routes
remain; disconnected UI reports unknown values rather than fabricated balances.
SQLite integrity chains detect edits but cannot prevent a privileged attacker
rewriting the entire chain; external anchoring and hardened workers remain open.

## Dependency-ordered slices and rollback

| Phase | Deliverable | Gate / rollback |
|---|---|---|
| P0 discovery | Audit, API/UI inventories, isolated baseline | Record failures before changes |
| P0 contracts / rules | Decimal contracts, identity, freshness, Prague loss calculations | Fixture and boundary tests; revert additive modules |
| P0 durable review | Bound approvals, atomic intent/outbox, transition validation, audit | Replay/race/restart tests; archive store, revert additions |
| P1 research | Fixture providers, strategy proposals, experiment manifests | Completed candles, evidence, deterministic outputs |
| P2 connectivity | Read-only lazy MT5 adapter, capability refusals | Mock tests; real session BLOCKED |
| P3 UI/API | Existing app read-only workspace; disabled execution | Auth/API tests, build and offline render checks |
| P4 extensions | Default-deny manifest / MCP policy | No third-party code activation without sandbox |

No destructive migrations, push, deployment or branch merge. Future real demo
order needs a genuine authorised demo account, verified policy and independent
review before an authenticated time-limited approval. No live execution planned.
