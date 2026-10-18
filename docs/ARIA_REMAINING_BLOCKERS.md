# ARIA remaining release blockers — 2026-10-18

**Overall release/deployment: BLOCKED.** Locally authorized hardening, tests and procedures are complete subject to the final evidence recorded in ARIA_FINAL_TEST_RESULTS. No production action is implied by a local PASS.

| Release gate | Verdict | Evidence required to close |
| --- | --- | --- |
| Raw source-security scanner | FAIL | Bandit still exits 1 with 43 findings; review inventory retains every original finding and all remaining patterns |
| Manual source-security acceptance | BLOCKED | Enforce/test connection-time public-fetch egress (DNS rebinding), validate nine further-review items in security/BANDIT_TRIAGE.md; no generic waiver |
| Historical legacy reconciliation | BLOCKED | Authorized original account/environment evidence, complete orders/fills/fee statements and identities. Follow ARIA_HISTORICAL_RECONCILIATION.md including AAPL 2026-08-06 21:02 UTC, local dfe2a885. Missing/404 never proves no submission |
| Controlled external paper acceptance | BLOCKED | New explicit owner order/cancel authorization and actual venue evidence per ARIA_PAPER_ACCEPTANCE.md. No external reads or orders in this mission |
| IBKR gateway acceptance | BLOCKED | Authorized paper gateway/session/permanent order identity evidence; local adapter mocks do not certify a gateway |
| Fees / cumulative partial-exit net P&L | BLOCKED | Actual execution/commission/activity statements, residual reconciliation and idempotent replay; unknown fees stay unreconciled |
| Production cutover/runtime | BLOCKED | Owner-approved state backup/restore plan, exact deployment runtime/dependency audit and single-version cutover. Stop old queue/desk/pipeline writers before candidate opens real state |
| Hosted browser/proxy/TLS and supervision | BLOCKED | Approved target-host acceptance, startup supervision and sustained uptime; isolated loopback fixture PASS does not establish hosted availability |
| Optional heavy package/model deployment | BLOCKED | Exact consumer audits, trusted model/cache provenance and ACLs; base dependency audit excludes these; verify patched Linux distro build/backports |
| Unlinked reflex UNKNOWN recovery | BLOCKED | Original queue/order binding or separately reviewed owner recovery evidence; no ticker-guess release, expiry or automatic retry |
| Remaining per-row signal/ML perception age contracts | BLOCKED | Explicit data-age policy for remaining consumers. Dataset/file freshness does not prove every underlying bar is current. No trading thresholds guessed |
| S2.2/S2.3 source-level measurement integration | BLOCKED | Finish and review original unfinished evaluation work; preserved in its checkout, no pooled performance claim |
| External independent review | BLOCKED | Separate qualified reviewer of combined changes. Sole lead's review is not an independent approval |
| Measured trading edge | BLOCKED | Independent point-in-time cost-adjusted out-of-sample evidence; software regression does not demonstrate profitability |
| Production/live release | BLOCKED | All applicable release gates and explicit owner authority. Live disabled throughout local validation |

Backup hardening, ML timestamp correction, XML hardening and public-URL preflight/redirect checks are implemented in `edfb5e6`; their local regressions are no longer deferred. Two original SSRF findings remain mitigated rather than closed until connection-time egress is verified. No source-security failure is concealed by the passing regression suite.

Operate only one complete ML pass at a time. Batch writes are locked, but concurrent end-of-pass pruning with different universes is unsupported. Preserve new claim/quarantine/audit schemas during rollback; never run legacy code over uncertain rows to restore availability. Restore first into a fresh disposable directory and verify archive manifests/databases before any owner-approved state move.
