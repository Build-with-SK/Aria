# Known limitations and release boundaries

This is a tested research/read-only extension, not the completed production target.

- No actual FTMO account/profile is configured or confirmed. Exact account entity,
  product/phase, restrictions and creation/merge evidence are external requirements.
- Order stages accept only `PRACTICE` accounts (own demo, FTMO Free Trial). Paid
  evaluations and funded accounts need the full gate set plus per-order human
  confirmation before any order stage may admit them; neither exists yet.
- MT5 SDK/terminal absent; only SDK fixtures ran. No connectivity, send, fill,
  partial fill, modification, cancellation or broker reconciliation is claimed.
  Mutation methods intentionally refuse rather than implement a latent live sender.
- Review library has no web approval endpoint, dedicated execution process,
  authenticated IPC or send-time worker. No broker-state transitions can be
  produced by this release. Full state vocabulary is declared; tested persisted
  paths cover review/reservation/recovery, not actual broker outcomes.
- Risk supports linear contracts with pair-quote FX conversion; inverse/nonlinear
  products require broker calculators. Book risk, spread, commission, session
  state and cooldown are derived by the engine from evidence (CR-11), but no
  adapter yet builds that evidence from a broker; financing is still a caller
  allowance. This is not yet a full currency-factor, liquidity-gap,
  consecutive-loss or real-time margin engine. No 1% exception workflow is enabled.
- News, gap and long-break rules are checked for new entries only. Positions
  already open can still meet a release or a weekend; `flatten_deadline()`
  states when a Standard FTMO Account must be flat, but nothing flattens it —
  that belongs to a future execution stage. The "major news" reading is an
  interim, stricter-than-published rule. No calendar or session feed is wired
  in; tests use fixtures.
- Policy arithmetic covers declared loss floors and objective formulas, not all
  operational/prohibited-practice rules. Known restrictions are still independently
  verified upstream. Missing cashflow/reset/high-water history cannot be guessed.
- Ten strategy definitions are research recipes. MacroDriven abstains. No strategy
  has been calibrated, promoted, backtested or paper/demo validated by this work.
  Real feeds, event citations, calendar gaps and cross-provider corroboration still
  need provider-specific integration. Capability labels describe applicability,
  not currently subscribed data.
- New backtest utilities are not a full multiasset fill simulator. No universal
  corporate-action, survivorship, partial-fill, market-hours or ruin model. The
  legacy backtester now fills stops intrabar and at gapped opens (QA-03), but
  still uses percentage costs, no per-instrument spread, swaps or lot model.
  Its calendar comes from yfinance ticker conventions; a ticker outside them is
  treated as exchange-traded unless the caller passes a calendar.
- `breach_probabilities` (QA-04) resamples the days it is given; its intervals
  do not cover regime change or a strategy that behaves differently in future.
- Observed, not changed: the root `main.py` backtest hook imports `Backtester`
  and `compute_metrics`, which `src/backtesting` does not define; the hook is
  off by default and, if enabled, logs a warning and returns no results.
- The dashboard shows an explicitly disconnected extension. Existing pages still
  show their existing legacy data. No screenshots from a deployed runtime; offline
  JSX render checks are the reproducible UI evidence. No hydration/browser event
  or real broker workflow was tested.
- Third-party plugin loading and MCP execution are disabled. Declaration checks
  are not OS isolation, a token issuer or a secure executable plugin registry.
- Shared Python venv has known advisories; it was not changed. A separate audited
  minimal research lock is supplied. The frontend router advisories were closed by the separately tested 7.18.5
  update. See DEPENDENCY_AUDIT.md.
- SQLite local storage is not encrypted. Audit chains can be keyed and anchored
  (SEC-01), but no key manager or anchor store ships: the operator holds both.
- The MT5 worker (CR-06) is tested with a fake SDK in a real spawned process.
  No genuine terminal, investor login or Windows worker host has been exercised.
  The active runtime cannot host the terminal at all (OP-01, OPERATIONS.md).
  Audit verification scans event history and is O(events); this is not suitable
  for an unbounded high-frequency event stream. Reads and indicators are bounded
  to 5,000 submitted bars but need request-size/rate/latency budgets before remote
  production use. No measured performance improvement or trading edge is claimed.
- Independent Claude review is pending; a packet is prepared, no approval invented.
  No push, PR submission, protected-branch change, integration merge or deployment.
