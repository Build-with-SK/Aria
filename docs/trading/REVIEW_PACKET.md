# Claude independent review packet

Branch: `feature/aria-multiasset-trading`; baseline `58f5f25`. No push or merge.
Review read-only; do not edit the main runtime or active feature files. The latest
owner brief assigns Codex implementation and Claude independent review. This file
does not claim a Claude review has occurred.

Read ARCHITECTURE.md (diagram), ARCHITECTURE_DECISIONS.md, RISK_POLICY.md,
SECURITY_MODEL.md, IMPLEMENTATION_MATRIX.md, DEPENDENCY_AUDIT.md and TEST_EVIDENCE.md.
Changed files: `git diff --name-status 58f5f25 HEAD`; implementation history:
`git log --oneline 58f5f25..HEAD`. Baseline test-order fix is separate and preserves
the exact 16-row/no-write assertions.

## Highest-priority questions

1. Are any model/plugin/MCP/network paths able to obtain execution authority?
   There is no new sender, mutation endpoint, startup worker or terminal connection.
2. Can exact money/quantity/FX/tick rules produce a permissive result with missing
   evidence? Nonlinear contracts and incomplete book risk must remain UNVERIFIED.
3. Does approval bind every substituted field, expire and consume once across
   concurrent connections? Verify atomic rollback/outbox and restart holds.
4. Do Prague boundaries and monotone balance highs survive DST/restart? Can
   incomplete history, withdrawals or row/audit tampering lower a limit?
5. Are legacy/new permissions and units kept separate? Legacy modes, share-sized
   OrderSpec, gates, kill switch, pretrade and reduce-only controls are preserved.
6. Are research trigger, calibration, OOS and actual demo evidence distinguished?
   There is no validated edge, live macro feed or 25% performance target.
7. Verify remaining dependency findings and that isolated lock updates do not
   imply the vulnerable shared runtime has been remediated.

## Reproduction / coverage

Use the isolated hash-pinned research environment in OPERATIONS.md. Run
the explicit seven-file PowerShell test list in OPERATIONS.md. Run the full legacy
suite with the full-stack test environment, not the minimal research lock. TestClient uses
remote fixture identity without lifespan startup; do not start backend daemons.
Run frontend build and all four `frontend/scripts/verify-*.mjs` commands.

Coverage is by deterministic/adversarial scenarios, not a claimed line-percentage:
money/FX/lot/margin/costs; malformed/stale inputs; account/quote/profile substitution;
midnight/DST; historical/new consistency; snapshot lineage; replay/race/crash;
backup/audit tamper; terminal account changes; forbidden writes; API access;
completed-candle/identity/provider checks; embargo, costs and seeded bootstrap;
loading/error/disconnected UI. Exact executed results are in TEST_EVIDENCE.md.

External evidence: official public FTMO/MQL5/cTrader documentation only. No actual
terminal, account session, order, fill or compliance sign-off. Gates 4/5/6 are open.
Full broker-driven lifecycle, operational-rule automation, realistic multiasset
replay, population of the dashboard and process sandbox remain incomplete.

Return severity, exact file/line, reproducible failure and required change. Record
disagreements/evidence/resolution here before advancing execution; no automatic
approval from another model. No independent findings or resolutions yet.
