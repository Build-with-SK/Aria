# Browser validation — Phase 2

PASS for local fixture-backed browser workflows. This is **real Chromium
interaction through Codex's browser Playwright/AX APIs**, running the actual
React/Vite build with effects, client routing and network requests. It is not
an SSR/static-build claim and not a deployed/live-broker acceptance test.

`scripts/serve_browser_fixtures.py` serves the staged app on ephemeral loopback
with synthetic owner/read API responses and no backend/broker imports. All
state-changing HTTP methods are refused. No key, account, vault, production
data, real sign-in, trading approval or order was used. The browser request
journal contains only GETs (501 at the evidence checkpoint). Fixture controls
toggle queue/signals/health failures without contacting an external service.

| Workflow | Observed result |
| --- | --- |
| Dashboard | Brain shell, unavailable-model state and data sections render; client navigation works after router security upgrade. |
| Scanner | AAPL bullish and TSLA bearish fixtures render; BEAR filter changes book from 2/2 to 1/2. |
| Approval display | Pending AAPL quantity, risk, broker and approval/reject/cancel controls render. Controls were inspected, never clicked. |
| UNKNOWN warning | Explicit CHECK BROKER label, original client id and warning that the broker may hold the order; no resubmit controls. |
| Risk rejection | REJECTED history shows concentration-limit reason and "nothing sent". This verifies display of a persisted refusal, not a real submission. |
| Execution history | MSFT filled: actual quantity 2 at 101; NVDA partial: actual quantity 1 at 100. No acknowledgement mislabeled as fill. |
| API failure | Scanner explicitly reports 503. Health failure shows SERVER UNREACHABLE. Queue failure now reports unavailable, not "No pending trades". |
| Reconnection/refresh | Restored health removes connection banner; queue polling restores rows; reload restores scanner results. Cached queue failures retain records but remove approval controls. |

The browser exposed an actual defect: useApprovalQueue already returned an error
but Execution ignored it. A first failed read rendered an empty pending queue;
a later failed refresh retained actionable stale records. Fixed with an explicit
alert/retry state and unavailable actions while stale. Added an always-visible
unresolved count/review button and an UNRESOLVED filter so UNKNOWN records are
discoverable from the default pending view.

The first fixture server returned a malformed empty successful derivatives
response. The page's error boundary caught it. Corrected the fixture contract to
an explicit 503 for unavailable derivatives, then reloaded and repeated the
workflow; pricing failures render inline. No product assertion was removed.
Expected injected 503 console messages are not counted as unexpected errors.

Evidence outside Git: browser request JSONL, DOM snapshots for UNKNOWN,
scanner failure/recovery and risk/fill history, plus browser screenshots for
stale state and UNKNOWN. Capture required showing the browser pane; its native
narrow viewport was retained. No invented desktop-size or responsive claim.

Offline checks additionally pass: route/Research/freshness SSR, seven execution
receipt states plus three new queue notice cases, and staging Vite build.
Full technical/shell checks are in the final handoff. These supplement the actual
browser run. Deployment cookies, reverse proxy/SSE, real account data, installed
PWA cache upgrade and broker-mediated UI remain separate acceptance gates.

Reproduce: build to a staging dist, start the fixture server with private
`--control` JSON and `--log` paths, open its printed /app/brain URL in the real
browser, and follow the workflows above. Restore control JSON to `{}` when done.
