# ARIA final integration

Candidate branch: `codex/aria-final-integration`. Baseline: designated integration branch `aria/integrity-upgrade` at `b5b47f2`, fast-forwarded in the isolated candidate to Claude completion `659a75c`. No protected branch, production checkout, service or remote was changed. Original S2.2 dirty work remains in its original checkout.

All twelve requested existing documents were found and read across the two parent branches. Historical reports remain intact; their old readiness recommendations and measurements do not describe this candidate. The owner explicitly reassigned Claude's integration responsibilities to Codex. This review is self-review, not independent external verification.

## Traceability

| Original work | Disposition in candidate |
| --- | --- |
| Claude S1.3 `e4f01ad`, `4f3b18e`, `d2f5e80`; equivalent Codex copies | Retained through completion ancestry; not applied twice. Deterministic IDs, durable claim and found-only settlement. |
| Codex `09f2158`, `0a1b21a`, `048f47b` | Retained through Claude merges `507d88a`, `4abcb84`; queue, risk/reduce-only, protective claims, durable paper exits, honest UI. |
| Claude `e7282f4` / `4abcb84` R1 | Adapted: modern acknowledged bracket status fallback retained; legacy identity failures cannot use it. Exact returned identity and finite fills checked. |
| Claude R2 `OrderRefused` | Retained; extended to paper-only live guard. Definite refusal rejects; transport ambiguity remains UNKNOWN; zero sends on refusal. |
| Claude R3 | Retained: position snapshot connected only after successful positions read; failed reads skip management. |
| Claude `f5f2bf7`, `33eb7a0`, `c49217c`, `c133e28`, `aba32e3` | Retained shared durability, desk config, one desk instance, execution backup, state-error 503. Lock-path disclosure repaired. Adapted damaged local positions/pending cancels to hold management without automatic synthetic recovery; fsync failures propagate and POSIX directory metadata is synced. |
| Claude `dde257b`, launch-sprint, sentiment merge fix | Retained: duplicate function removed, unavailable news stays unavailable, lazy Search fallback preserved, repo-anchored source scans. |
| Codex `a9a6f1d` / `7dde78a` | Retained as `839dfb8` / `64ec005`: security constraints, router 7, unused Tailwind removed, provider major caps. |
| Codex `2dd1f7c` / `2d44157` | Adapted as `b4c9942` / `2d52197` plus integration repairs: legacy quarantine, preimages, original-ID binding, identity/intent validation and monotonic fills. |
| Codex `b7b789c` | Retained as `1cec7f3`: model-free listing and single-flight semantic model load; timeout allowance removed. |
| Codex `ce54441` / `9862967` | Retained as `c2613d4` / `40d7c75`: real POSIX processes/permissions, Python 3.14 loops and scoped platform fixture; CI now builds frontend for proxy acceptance. |
| Codex `a5e4043` | Retained as `1fc1e05`: queue failure/staleness, unresolved notice and browser fixtures. |
| Codex `d07351a` runtime-ignore rules | Superseded by Claude `edf818b` broader execution/corrupt-state ignores, retained via completion ancestry. |
| Codex final evidence `043b213`, `f42e73e`, `eed0251` | Historical final reports restored; old run counts not reused as integrated evidence. |
| Later Claude `4ffda39` | Retained as `1fd6426`; batch/resume/per-row ages and sklearn compatibility. Shared lock added for batch merges. No training run started. |
| Claude TECH-09 `34576d3`, `5f43437`, `a1e313b` | Superseded by integrated `398afd8`: all six touched calendar/helper/data/test files byte-identical to `claude/work`; no reapplication. |
| Claude draft `8c234de` | Deferred: explicitly not mergeable before uncommitted S2.2; no verified function to import. Preserved original work; remaining measurement gate. |
| Other inventoried Claude branches | FX/session, sentiment argument, shell-cache, strategies, lifespan, S2.3 and reflex changes already in ancestry; no duplicate application. |
| Original uncommitted S2.2 | Preserved, not integrated or committed on its owner's behalf. Separate measurement work remains open. |

## Second review

Performed after implementation against both `659a75c` and `eed0251`, plus additional ML parent `4ffda39`. Reviewed changed-path inventories, execution call sites, wrapper read/write classification, queue claims, cancel/status fallback, legacy migration, shared locks/backup, session and risk gates, single desk instance, memory initialization, ML persistence and UI status. No wholesale divergent source replacement.

Found and repaired: legacy fallback bypass; original-client intent fields; terminal cancellation carrying new fills; stale zero-cancel downgrading FILLED; unbounded queue contention; halt removing possible protection on failed reads; ambiguous live-gate OCO refusal; ML batch lost updates; public lock path; simulated proxy state leaking into later tests; damaged position/pending-cancel recovery that could alter protections; swallowed fsync failures. Existing R1/R2/R3 tests and assertions retained.

Single full pipeline writer remains an operational requirement: atomic batch merges serialize, but end-of-pass pruning belongs to one active universe/pass. Historical missing/404 identifiers and id-less reflex UNKNOWN stay blocked. No retry/release bypass was introduced. Configuration additions: ML batch size 25, resume budget 20h (0 disables), `--force-ml`; fixture controls are test-only. No live-default or risk-threshold change.

Exact implementation changes are available with `git diff --name-status b5b47f2..HEAD`; comparison manifests and evidence are outside Git. Final source anchor and gate verdicts are in ARIA_FINAL_TEST_RESULTS and ARIA_RELEASE_CANDIDATE.

Final separate review additionally supersedes Claude's automatic synthetic adoption after local position-file damage. Missing state still permits first-run adoption, but existing unreadable state holds management and preserves broker protection until explicit repair. Pending-cancel damage also holds the tick. Shared durable writes now fail on file fsync errors and sync parent directories on POSIX. Tests strengthen the original recovery assertion to require StateUnreadable and zero protection changes.

## Candidate integration order

The candidate already contains the sequence: designated baseline → Claude completion → dependency constraints → legacy recovery → memory/Linux/UI → identity/partial-fill repairs → later Claude ML → shared-lock and state-recovery hardening. Before any owner-approved cutover, review the combined source and backup/state plan, recreate the candidate runtime, preserve all original snapshots/audits, dry-run quarantine on copies, and resolve external evidence gates. Stop all old writers before state cutover; mixed Windows queue-lock protocols and concurrent complete ML passes are unsupported. A rollback must preserve new uncertain claims and preimages; never restore availability by running legacy code over quarantined state.

## Exact changed-path manifest

Relative to `b5b47f2`, including final reports. No paths in the original dirty checkout are changed by this mission.

```text
A	.github/workflows/linux-runtime.yml
M	.github/workflows/security.yml
M	.gitignore
M	backend/main.py
M	backend/requirements_backend.txt
A	constraints-security.txt
A	docs/ARIA_ARCHITECTURE.md
A	docs/ARIA_BROWSER_VALIDATION.md
A	docs/ARIA_COLD_MEMORY.md
A	docs/ARIA_COMPLETION_AUDIT.md
A	docs/ARIA_DEPENDENCY_SECURITY.md
A	docs/ARIA_HANDOFF.md
A	docs/ARIA_LEGACY_RECONCILIATION.md
A	docs/ARIA_LINUX_VALIDATION.md
A	docs/ARIA_PAPER_BROKER_VALIDATION.md
A	docs/ARIA_PHASE2_HANDOFF.md
A	docs/ARIA_PHASE2_TEST_RESULTS.md
A	docs/ARIA_RELEASE_READINESS.md
A	docs/CODEX_ARIA_AUDIT.md
A	docs/CODEX_TEST_RESULTS.md
A	docs/CODEX_VERIFICATION.md
M	docs/DATA_LAYER.md
M	docs/collab/log-claude.md
M	docs/collab/log-codex.md
M	frontend/package-lock.json
M	frontend/package.json
M	frontend/postcss.config.js
A	frontend/scripts/verify-execution-status.mjs
A	frontend/src/components/ExecutionStatus.jsx
M	frontend/src/pages/Desk.jsx
M	frontend/src/pages/Execution.jsx
D	frontend/tailwind.config.js
M	main.py
M	requirements-ci.txt
M	requirements.txt
A	scripts/browser_pwa_acceptance.html
A	scripts/serve_browser_fixtures.py
M	src/brain/cognitive/long_term_memory.py
A	src/core/durable.py
A	src/core/launch_check.py
M	src/core/world.py
M	src/data/data_downloader.py
M	src/desk/analysts/technical_agent.py
M	src/desk/auto_executor.py
M	src/desk/config.py
M	src/desk/day_state.py
M	src/desk/desk_daemon.py
M	src/desk/position_manager.py
M	src/desk/reflex.py
M	src/execution/alpaca_broker.py
M	src/execution/approval_queue.py
M	src/execution/broker_base.py
M	src/execution/ibkr_broker.py
M	src/execution/live_guard.py
M	src/execution/order_manager.py
M	src/execution/reduce_only.py
M	src/models/ensemble_model.py
A	src/models/ml_store.py
M	src/models/model_trainer.py
M	src/risk/pretrade.py
M	src/sentiment/sentiment_analyzer.py
M	src/strategies/distressed.py
M	src/strategies/event_driven.py
M	src/trading/access.py
M	src/trading/account.py
M	src/trading/broker.py
M	src/trading/killswitch.py
M	src/trading/orders.py
M	tests/conftest.py
A	tests/test_candidate_security_audit.py
A	tests/test_cold_memory_profile.py
M	tests/test_data_freshness_api.py
M	tests/test_desk_fail_closed.py
A	tests/test_desk_single_instance.py
A	tests/test_durable_state.py
A	tests/test_final_execution_reliability.py
A	tests/test_final_profile.py
A	tests/test_final_runtime.py
A	tests/test_ibkr_loop_compat.py
A	tests/test_integrated_web_acceptance.py
M	tests/test_kill_switch_and_profiles.py
A	tests/test_launch_check.py
M	tests/test_launch_get_sweep.py
A	tests/test_legacy_reconciliation.py
A	tests/test_memory_lazy_loading.py
A	tests/test_ml_durable.py
M	tests/test_ml_persistence.py
A	tests/test_order_safety_review.py
A	tests/test_order_settlement.py
M	tests/test_path_traversal.py
M	tests/test_pipeline_sentiment.py
A	tests/test_posix_runtime.py
A	tests/test_protective_order_outcomes.py
A	tests/test_queue_durability.py
M	tests/test_reduce_only.py
M	tests/test_security_live_gate.py
M	tests/test_sentinel_bridge.py
M	tests/test_session_bypass.py
A	tests/test_snapshot_positions_unreadable.py
M	tests/test_stabilization.py
A	tests/test_state_store_durability.py
M	tests/test_step_up.py
M	tests/test_trading_pipeline.py
A	docs/ARIA_FINAL_INTEGRATION.md
A	docs/ARIA_FINAL_TEST_RESULTS.md
A	docs/ARIA_RELEASE_CANDIDATE.md
A	docs/ARIA_REMAINING_BLOCKERS.md
A	docs/ARIA_SECURITY_READINESS.md
A	docs/ARIA_SENTINEL_HANDOFF.md
```

Branch inventory covered all 16 local Claude refs. Nonancestor hashes were the cherry-picked ML commit, the unready S2.3 draft, and TECH-09 commits whose six implementation/data/test paths are already identical through398afd8. No unaccounted verified branch delta remains.
