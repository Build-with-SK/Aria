# Operations

The main runtime and private data stores remain untouched. This worktree must not
run ARIA's daemons, use live credentials or listen on ARIA's ports. Review using
offline CLI, TestClient tests without lifespan startup and static UI verifiers.

## Independent Python environment

The shared production venv has known advisories. Do not upgrade it in place as a
side effect of this branch. A minimal hash-pinned research/test environment is
provided; it includes no broker SDK, torch, ChromaDB or credential configuration.

```powershell
uv venv --python 3.11 .venv-research
uv pip sync --python .venv-research\Scripts\python.exe --require-hashes requirements-trading-research.lock
.venv-research\Scripts\python.exe scripts\multiasset_research.py
$researchTests = Get-ChildItem tests/test_multiasset_*.py | ForEach-Object FullName
.venv-research\Scripts\python.exe -m pytest -q $researchTests
```

Use a temporary directory instead of `.venv-research` if desired. Do not commit an
environment directory. Reaudit the lock before use; a clean advisory result is a
point-in-time registry check, not proof of supply-chain security.

The minimal environment runs the seven new research test modules. The complete
legacy suite additionally imports yfinance and optionally broker SDKs at
collection; run it with the existing full-stack test environment. `-k multiasset`
still collects unrelated modules, so use the explicit file list above.

## Review store / restoration

`ReviewStore(Path(...), audit_key=key)` creates only its explicit-path SQLite
database (schema 3), separate from every existing production store. The audit
key (32+ bytes) is kept **outside** the database: every audit event is then an
HMAC-SHA256 link that someone who can write the file cannot rebuild (review
SEC-01). A store opened without a key works as a review queue but cannot reserve
an intent; a chain is keyed or unkeyed from its first event and never changes,
and opening a keyed chain without its key, or with another key, is refused.
After each review session, store `store.anchor()` (the chain head) somewhere
the database's writer cannot change; `verify_audit(anchor)` then also detects a
chain truncated below, or rewritten through, that event. This release ships no
key manager or anchor store: both are the operator's. Every review/state
or rule-boundary write uses BEGIN IMMEDIATE; exceptions roll back. Keep the file
owner-only. An approval token is returned once and never logged/stored in plaintext.

Use `store.backup(new_path)` (SQLite backup API), not a mid-write file copy. It
refuses overwrite/self-copy and closes both connections before returning, so the
backup file is not left locked on Windows (review CR-14). Open a backup with the
same audit key. Restore by opening an independently copied backup,
running `verify_audit()`, checking account/profile identity and calling
`recover(now=aware_utc)` before any future reviewed worker is attached. Recovery
holds pending/unknown intents at RECONCILIATION_REQUIRED; it does not resend.
Fixture tests prove backup restore, interrupted transaction and repeated recovery.

Do not mark unknown orders rejected or filled just to clear a queue. A future
broker worker must compare client correlation, broker tickets/deals, quantities,
account/environment and positions. No reconciliation worker is deployed here.

## MT5 worker host (review OP-01)

The `MetaTrader5` Python package is published for Windows only and drives a
terminal on the same machine. ARIA's active runtime is a Linux ARM host, which
cannot run that terminal, and ARIA's Windows writer tasks are deliberately
disabled. So no MT5 worker can run on the current setup: started there, the
worker refuses with "needs a Windows host". The UI shows MT5 as not configured.

A future MT5 worker needs a **separate, always-on Windows host** holding the
terminal and the investor (read-only) login, bridged to the runtime. That is a
new trust boundary: account access on one machine, decisions on another. Before
any such host is attached, design and review:

- how the runtime authenticates to the worker host and the reverse, over a
  private link only (no public listener);
- that the worker host holds only the investor login — never a master password,
  never broker or ARIA owner credentials beyond what reads need;
- what the runtime does when the link or host is down (reads become
  UNAVAILABLE; nothing falls back to another account or a cached figure);
- who patches and monitors the host, and where its logs go.

Latency: the runtime's local model is slow on the current 2-core host, so no
time-sensitive path (news windows, pre-break flattening) may depend on it; those
checks are deterministic and run in the risk engine. Choosing and providing the
Windows host is the owner's decision; this release adds none.

## Rollback and incidents

Revert the feature commits in the isolated branch or discard the worktree after
preserving review artifacts. No database down migration or production restoration
is needed because none was changed. Keep review stores/backup files if they hold
real owner reviews; do not delete them as a code rollback.

For a legacy runtime incident, use the existing documented authenticated halt and
kill-switch procedures. This branch does not change the running legacy state,
auto-execute toggles, owner sessions or broker credentials.

## External checkpoint lifecycle

A keyed review store now requires a separate `anchor_path` to reserve an intent.
Keep the key and checkpoint outside broker-writable/database-writable state.
The application checkpoints before SQLite commit and fails closed on an advanced,
missing or corrupted checkpoint. A failed commit may therefore require manual
recovery verification. Database backups alone do not establish current approval
consumption: reconcile them with the newest independently held checkpoint. Existing
stores without a checkpoint require a reviewed baseline/migration; never attach a
new empty checkpoint to already-populated history and assume it is authenticated.
