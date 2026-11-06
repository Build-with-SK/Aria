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

`ReviewStore(Path(...))` creates only its explicit-path SQLite database. Schema 1
is additive and separate from every existing production store. Every review/state
or rule-boundary write uses BEGIN IMMEDIATE; exceptions roll back. Keep the file
owner-only. An approval token is returned once and never logged/stored in plaintext.

Use `store.backup(new_path)` (SQLite backup API), not a mid-write file copy. It
refuses overwrite/self-copy. Restore by opening an independently copied backup,
running `verify_audit()`, checking account/profile identity and calling
`recover(now=aware_utc)` before any future reviewed worker is attached. Recovery
holds pending/unknown intents at RECONCILIATION_REQUIRED; it does not resend.
Fixture tests prove backup restore, interrupted transaction and repeated recovery.

Do not mark unknown orders rejected or filled just to clear a queue. A future
broker worker must compare client correlation, broker tickets/deals, quantities,
account/environment and positions. No reconciliation worker is deployed here.

## Rollback and incidents

Revert the feature commits in the isolated branch or discard the worktree after
preserving review artifacts. No database down migration or production restoration
is needed because none was changed. Keep review stores/backup files if they hold
real owner reviews; do not delete them as a code rollback.

For a legacy runtime incident, use the existing documented authenticated halt and
kill-switch procedures. This branch does not change the running legacy state,
auto-execute toggles, owner sessions or broker credentials.
