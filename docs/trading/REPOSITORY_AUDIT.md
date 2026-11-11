# Repository audit — 2026-11-06

Baseline revision: `58f5f25`. Discovery preceded product edits. Main checkout is
`aria/integrity-upgrade`, with unrelated runtime JSON changes; those are untouched.
Implementation is isolated in worktree `aria-multiasset-trading`, branch
`feature/aria-multiasset-trading`, created from and merged with integration HEAD.
The requested branch name overrides the usual `codex/` prefix. No push or merge.

## Applications and persistence

- `backend/main.py`: one FastAPI app, 228 decorated routes at baseline (157 GET,
  67 POST, 3 PATCH, 1 DELETE). Full inventory: `API_INVENTORY.md`.
- `frontend/`: React 18/Vite dashboard, axios hooks, auth and currency contexts,
  eight workspaces; legacy destinations redirect. `frontend/src/App.jsx:189`.
- `main.py`: batch research pipeline; `chat.py`: alternate CLI;
  `src/dashboard/dashboard.py`: Streamlit alternate entry point. These prevent
  declaring modules dead merely because FastAPI does not import them.
- `src/database/database.py:71`: SQLite history tables (signals, prices,
  backtests, alerts, macro, ML, outcomes); `src/core/bus.py:136`: SQLite events.
  `src/trading/orders.py`, `src/execution/approval_queue.py`: separate JSONL/JSON
  order and approval persistence. No central migration framework found.
- 317 Python source modules and 151 test modules at baseline. Dependencies:
  `requirements.txt`, `requirements-ci.txt`, `backend/requirements_backend.txt`,
  `frontend/package.json` and lockfile. No Docker/compose files found.
- Deployment: `scripts/serve.py`, `scripts/serverctl`, launchd plists,
  `scripts/watchdog.sh`, Windows task installer, Cloudflare example.
  `.github/workflows/security.yml` runs security and full Python suites;
  `.github/workflows/site.yml` concerns the separate public `site/` app.

## Existing boundaries and conflicts

- `src/trading/`: deterministic modes, hard limits, account snapshots, costs,
  risk, validator, simulated/legacy broker adapter, idempotent journal, lifecycle,
  outcomes and scorecards. Reuse; do not create a second trading app root.
- `src/execution/`: older Alpaca/IBKR path with approval queue, broker gate and
  kill switch. `src/desk/` still calls this path. Both paths remain operational.
- `src/risk/`: independent portfolio preflight, FX, factors, covariance and
  profiles. Legacy financial arithmetic uses floats; precise multiasset contracts
  must not silently reinterpret existing equity quantities as FX lots.
- `src/auth/policy.py:474`: default deny; owner-only private data; service peer
  has explicit method/path allowlist. Session/step-up/origin guards already exist.
  Preserve protected auth, approval and live-gate files.
- `src/trading/modes.py:57`: RESEARCH default, PAPER and gated LIVE_AUTONOMOUS.
  Existing paper autonomy is not the new manual-only multiasset workflow. New
  functionality must default DISABLED and cannot inherit legacy PAPER permission.
- `src/execution/ibkr_broker.py:293`: real ib_insync implementation, contracts,
  quotes, orders, reconciliation and status mapping. Constructor attempts a TWS
  connection; inspection did not instantiate it. Paper identity uses destination
  port and operator intent; that is not proof of a healthy paper session.
- `src/data/technical_engine.py`, `technical_summary.py`, `technical_feeds.py`:
  existing indicators and completed-candle/session checks. `ta` already installed.
- `src/v5/`, `src/intelligence/`, `src/research/`, `src/macro/`, `src/backtesting/`:
  evidence, provenance, PIT snapshots, statistical correction, feeds, replay,
  evaluation and strategy research. Preserve promotion/calibration gates.
- `src/brain/daemon.py`, desk analysts and inference router handle models;
  `src/trading` cannot import them (`tests/test_trading_structure.py`).
- FastAPI startup uses multiple daemon threads; APScheduler also exists in
  `src/scheduler/scheduler.py`. No general durable job queue found.
- No trading plugin/MCP registry or MT5 adapter found. Vault mentions MCP only to
  explain that vault access is plain files. Do not introduce arbitrary code loading.

## Findings and limitations

1. New FTMO execution cannot use generic equity sizing: lots, tick value,
   settlement currencies, account identity and provider profile must be explicit.
2. Legacy order journal is useful but not an atomic multi-process approval outbox;
   new approval binding needs transactional persistence and restart recovery.
3. Live-capable legacy mode exists in source. No attempt was made to enable it,
   inspect secrets or change running account settings.
4. Broad dependency ranges are not reproducible supply-chain pins. Existing
   security workflow is regression testing, not a complete vulnerability scan.
5. No actual MT5 account, terminal or FTMO profile is configured by this task.
   No broker connectivity, profitable edge or policy compliance is inferred.
6. Unused/duplicate routes are compatibility redirects, not removal candidates.
   No dead module deletion is proposed without hidden-entry-point checks.
7. Vault resolved through `get_vault_path()`; private note content is excluded
   from committed artifacts. No credentials or `.env` values were printed.

Baseline command and final results are recorded in `TEST_EVIDENCE.md`.
