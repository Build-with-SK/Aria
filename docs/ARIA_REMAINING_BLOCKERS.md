# ARIA remaining blockers

Release/deployment recommendation: **BLOCKED**. Development can continue on SENTINEL using the limited contract; live-money authority is not granted.

| Gate | Status | Evidence needed to close |
| --- | --- | --- |
| Production cutover/runtime | BLOCKED | Owner-approved backup, private runtime/package migration, state migration review, single-instance restart and post-cutover checks. No production change performed. |
| Historical legacy reconciliation | BLOCKED | Original account/environment, statements, permanent/original IDs and execution evidence. Prior bounded reads had missing IDs/404s; absence never authorizes release/resend. Current positive historical adapter test does not identify those old rows. |
| New paper-order venue acceptance | BLOCKED | Separate explicit order-level approval, controlled session and actual lost-ack/partial/cancel/restart/fees evidence. Read-only permission does not cover these writes. |
| IBKR | BLOCKED | Approved paper gateway/account/client and permanent identity evidence. No gateway session validated; bare session orderId remains insufficient. |
| Fees and cumulative partial-exit net P&L | BLOCKED | Broker execution/fee statements, multi-fill/cancel residual reconciliation, no duplicated fees/outcomes after replay. Mock gross fills do not prove external net accounting. |
| Deployed host/browser/proxy/TLS/supervision | BLOCKED | Actual target and approved cutover. Local reverse proxy, browser, SSE, cache and restart evidence is not a hosted acceptance result. Seven-day uptime/pipeline continuity unmeasured. |
| Optional heavy deployment dependencies | BLOCKED | Audit/remediate exact Chroma/Torch/OAuth consumers or remove unused packages; zero base audit is not coverage. Patched Linux distro security build also required. |
| Unlinked reflex UNKNOWN | BLOCKED | Original evidence/queue binding or explicitly reviewed owner recovery procedure. Never clear by ticker guesses or automatic expiry. |
| S2.2/S2.3 measurement integration | BLOCKED | Finish/review the original uncommitted source-level evaluation work without overwriting it; no pooled performance claim. |
| Per-row signal age / ML perception consumers | BLOCKED | Approved data-age contract across remaining consumers. ML batch readers are improved; existing signal file age is not proof every bar is current. No threshold guessed here. |
| Measured trading edge | BLOCKED | Sufficient independent, point-in-time, cost-adjusted out-of-sample evidence. Passing software tests establishes no profitable edge. |
| External independent review | BLOCKED | Another qualified reviewer; Codex's separate second pass is self-review. |

Account-wide pending-entry DEFER remains deliberately conservative. Batch merges are locked, but run only one complete ML pass at a time; multiple concurrent pruning universes are unsupported. Source lint/type debt and scoped Bandit patterns remain documented security/maintenance limitations. None is hidden by an all-green release claim.
