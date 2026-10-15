# Claude Code log

Only Claude Code edits this file. Newest entry at the top.

## 2026-10-15 — CI fixture: full suite deps, hermetic test isolation, FX fixture narrowed, universe-audit split — candidate, not merged
Files: `requirements-ci.txt`, `tests/conftest.py`, `tests/fixtures/universe_fixture.py`,
`tests/test_quote_capabilities.py`, `tests/test_identity_namespaces.py`,
`tests/test_identity_safety.py`, `tests/test_price_unit_audit.py`,
`tests/test_stabilization.py`, own log.
Changed: in the isolated worktree `.claude/worktrees/deploy-fixture` (branch
`claude/work` at 993bec8), continued a prior CLI run that had drafted but not
committed: `requirements-ci.txt` gains scikit-learn/joblib (recalibration's
fallback path is a materially different decision than production's, so
leaving the ML stack out of CI would test the wrong behaviour);
`tests/conftest.py` redirects every writable store under `data/` to a
throwaway tree for the whole session (closes a lazy-import race and an
instance-attribute leak that signed the owner out of his own session store)
and falls back to a small, named-instrument `universe.db`
(`tests/fixtures/universe_fixture.py`, 16 real rows) when CI has none;
`tests/test_quote_capabilities.py` stands in `StockLatestQuoteRequest`/
`CryptoLatestQuoteRequest` so the reflex-lane quote tests exercise the real
mocked path instead of silently no-opping on a `NameError` alpaca-py's
absence from CI was causing.
Addressed Codex's review (`ci-fixture-review.md`) on that draft, three fixes:
(1) `_deterministic_fx_rates` was autouse for the whole suite; it is now
opt-in, requested by exactly the 9 tests across `test_identity_namespaces.py`,
`test_price_unit_audit.py` and `test_identity_safety.py` whose own assertions
go through `cur.to_base`/`cur.convert` — every other test keeps running
against the real `src.data.currency.rates`. (2) Split the universe-audit
test: `test_the_universe_audit_reports_without_repairing` now reads a new
`universe_reference` fixture to assert `>20000` only when a real
`universe.db` was actually copied in, and `== 16` against the named-instrument
fallback otherwise — same contract, honest counts either way — plus a
no-mutation byte-for-byte check, and a new
`test_the_audit_is_correct_against_the_fallback_fixture_with_no_real_db`
regression that builds the fixture directly with no real DB in play at all.
(3) Corrected `universe_fixture.py`'s docstring, which previously said the
production-size assertion would be "left failing" — it no longer is.
Measured with the clean CI venv Codex pointed at (sklearn/joblib present, no
broker/Chroma SDK): `tests/test_currency.py tests/test_identity_safety.py
tests/test_identity_namespaces.py tests/test_price_unit_audit.py
tests/test_stabilization.py tests/test_quote_capabilities.py
tests/test_portfolio_risk.py` — 201 passed, 2 skipped (pre-existing, no
production ledger in this checkout), 0 failed. Also ran `idt.audit()` by hand
against Codex's retained 22,023-row production-size reference to confirm the
`>20000` branch (`ambiguous_roots=177`, `known_bad_names` non-empty) — that
branch is untested by the focused suite in this checkout, since this worktree
has no real `data/universe.db`.
Open: not merged to `aria/integrity-upgrade`. Per the task, Codex reviews this
candidate and runs the full clean suite next, then integration follows.
For Codex: candidate at this commit on `claude/work`; nothing else in the
worktree was touched (primary dirty files, keys/vault/data untouched, no
push, no restart).
Correction (prose-only, same task): `tests/conftest.py`'s comment above
`real_universe` still said the production-size tests "stay failing,
honestly" — stale since the `universe_reference` split in this same
candidate. Fixed to say they branch on `universe_reference["kind"]` instead.

## 2026-10-15 — CI fix: pyyaml missing from requirements-ci.txt (run 38813015922) — done, isolated worktree
Files: `requirements-ci.txt`.
Changed: in the isolated worktree `.claude/worktrees/deploy-fixture` (branch
`claude/work`), merged `aria/integrity-upgrade` (fast-forward bcdce55→a80ae6b,
no conflicts), then added `pyyaml>=6.0.1` to `requirements-ci.txt`, matching
`requirements.txt`'s existing pin. `main.py:16` does a module-scope
`import yaml`; `tests/test_ml_persistence.py` imports `main`, so collection
failed in CI with `ModuleNotFoundError: yaml` — `requirements.txt` already had
the dependency, `requirements-ci.txt` did not.
Measured: primary venv already has pyyaml 6.0.3 installed (satisfies the new
pin). Ran `tests/test_ml_persistence.py` from the worktree using the primary
venv's interpreter: 26 passed.
Open: Codex owns pushing this fix and rerunning CI (run 38813015922 failed on
full-suite COLLECTION; protected-paths and security-regression already
passed). No runtime cutover needed — CI-only dependency fix.
For Codex: nothing else changed; this is the one-line addition you asked for.

## 2026-10-11 — review of codex/work 50c8e5f (S1.6 + S1.7) — done
Files: docs/collab/log-claude.md
Changed: merged 50c8e5f into `aria/integrity-upgrade` (log-only, fast-forward).
Measured:
- S1.7 confirmed. `subscribe()` has one definition (`src/core/bus.py:243`) and
  two test callers (`tests/test_core_spine.py`); production reads the log via
  `bus.recent()` in `backend/main.py` and `src/core/world.py`.
- S1.6 process was right: own branch, own log, nothing removed, main folder
  untouched.
- S1.6 has three errors to correct:
  1. `src/scheduler/scheduler.py` is NOT absent. It is tracked and present in
     the Codex folder.
  2. "No filename references found" is wrong for
     `SENTINEL_RUNTIME_COMPLETION_REPORT.md`:
     `docs/ARIA_SENTINEL_CONTRACT.md` lines 9 and 300 point to it, and the two
     SENTINEL reports cite each other. Keep both, or move them and fix the
     links in the same commit.
  3. The inventory is incomplete. Tracked root files with no recommendation:
     `alerts_engine.py`, `backtester.py`, `fix_sentiment.py`, `macro_data.py`,
     `ml_model.py`, `model_trainer.py`, `portfolio_analyzer.py`,
     `sentiment_analyzer.py`, `test_political_imports.py`, `run_political.py`,
     `ARCHITECTURE_AUDIT.md`, `HANDOFF_TOMORROW.md`.
Open: owner decision on S1.7 (Codex recommends removing `subscribe()`; I agree)
and on which root prompts/reports to archive.
For Codex: the `*.pyc` files and `main_backup_*.py` exist only in the main
folder because they are gitignored; you were right that your folder has none,
and that part of S1.6 is now mine. Nine of the root `.py` files in item 3 are
already deleted in the uncommitted S0 backlog, so only classify
`run_political.py`, `ARCHITECTURE_AUDIT.md` and `HANDOFF_TOMORROW.md`, and
correct items 1 and 2 in your entry. Merge `aria/integrity-upgrade` first.

## 2026-10-11 — setup — done
Files: docs/collab/*, AGENTS.md, CLAUDE.md
Changed: created `claude/work` and `codex/work` at 95358a2; Codex worktree at
`Documents\aria-codex`; work plan and Codex handover written.
Measured: from the Codex folder, `tests/test_calibration.py` and
`tests/test_self_state.py` pass (44) using the main folder's interpreter;
imports resolve to the Codex copy.
Open: S0.1 (commit the 2026-10-09 backlog) waits for the owner's yes. Stage 5
is not written down yet.
For Codex: start with S1.6 and S1.7; they do not depend on S0.
