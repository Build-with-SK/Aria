# Extend ARIA with disabled multiasset research and durable review primitives

ARIA's legacy equity trading paths lack explicit FX-lot contracts, version-aware
provider rules and an atomic bound-review store. This change adds Decimal/UTC
contracts, fixture-first research and risk calculations, Prague loss/objective
rules, persistent review/intent/high-water records and a read-only MT5 SDK adapter.
New execution stays disabled; no sender or broker session is activated.

Two owner-protected routes provide disconnected status and supplied-candle
research. An inline-styled panel in the existing Portfolio workspace distinguishes
unavailable account state and POLICY_UNVERIFIED from measured values. Existing
routes, gates, desk, IBKR adapter and portfolio risk remain in place.

Also fixes a baseline test-order defect without weakening its audit assertions,
patches vulnerable frontend HTTP/build dependencies in the lockfile and supplies
a hash-pinned minimal research environment separate from the shared runtime.

Validation: TEST_EVIDENCE.md. Limitations: KNOWN_LIMITATIONS.md and implementation
matrix. No production migration/deployment/push/merge, real account, order or
independent approval is claimed. Broker-driven execution and release are blocked
on independent review, actual authorised demo evidence and runtime dependency work.
