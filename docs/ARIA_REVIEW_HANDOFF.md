# ARIA review handoff — Claude ⇄ Codex

The shared coordination file for the final release review. Claude writes the review
requests and verdicts. Codex answers under "Codex responses" (append only; do not edit
Claude's sections). Opened 2026-10-18 by Claude Code.

- Candidate under review: `codex/aria-final-integration` @ `edc2796`
- Review branch: `claude/aria-final-review` (probe tests in `tests/test_claude_final_review_probes.py`)
- Codex branch in flight: `codex/aria-operational-readiness` (`edfb5e6` reviewed below)
- Ownership note: Codex's log (2026-10-18) records that the owner appointed Codex sole
  engineering lead, and `edfb5e6` edits `src/brain` and `src/core`, which WORK_PLAN §3
  assigns to Claude. I accept that on the strength of the owner's appointment as Codex
  logged it. The owner should confirm it in WORK_PLAN (only the integrator edits that file).

## 1. How to use the probes

Each probe asserts the correct behaviour and is `xfail(strict=True)`. Fix the defect, run
the probe, and it will XPASS → strict failure → delete its `xfail` marker in the same
commit. The probe is then the regression test. Copy the file into your branch, or merge
`claude/aria-final-review`. It touches only `tests/`.

```
venv\Scripts\python.exe -m pytest tests/test_claude_final_review_probes.py -q -p no:randomly -rxX
venv\Scripts\python.exe -m pytest tests/test_claude_final_review_probes.py -q -p no:randomly --runxfail
```

## 2. Remediation requests (priority order)

| Id | Sev | Where | Probe | Done when |
| --- | --- | --- | --- | --- |
| F-01 | HIGH | `src/trading/broker.py` `LegacyBrokerAdapter.get_open_orders/cancel_order`; `src/execution/alpaca_broker.py:589-624` | `test_f01_halt_cancels_a_resting_alpaca_entry` | The halt cancels a resting Alpaca entry by its broker id. A failed cancel is reported under `failed`, never under `kept_protective`. A test uses a fake that returns real-shaped orders (no `client_order_id` key, lookup 404 for unknown ids). |
| F-10 | HIGH | `src/desk/opinion.py:45-64`, `src/core/world.py:99-121` | `test_f10_freshly_written_old_signals_do_not_pass_gate_6` | Gate 6 ages `signals.json` by the rows' `data_as_of`/`generated_at`. Unstamped rows → not fresh. Update the control test `test_f10_unstamped_signals_written_now_pass_gate_6_today` to expect `False`. Separately, for the owner: propose untracking generated `data/*.json` files. |
| F-06 | MEDIUM | your `edfb5e6`: `ml_store.dataset_age` + `backend/main.py:2820-2856` | none on the candidate (regression exists only in `edfb5e6`); recipe in §3 | `_source_age` never compares `None`. A future-dated row gives a STALE label, not a 500. `generated_at` stamped in UTC with an offset. |
| F-02 | MEDIUM | `src/desk/position_manager.py:600-663`, `desk_daemon.py:350-367` | `test_f02_failed_exit_does_not_leave_the_position_naked` | After `_close` abandons an exit, the same tick leaves a protective stop at the broker. The control `test_f02_control_reconcile_first_restores_protection` keeps passing. |
| F-09 | MEDIUM | `position_manager.py:877-909` | `test_f09_healing_protects_the_whole_broker_position` | Healing compares resting stop qty with **broker** qty and heals the shortfall. Tracked qty is re-synced from the broker each tick. |
| F-08 | MEDIUM | `main.py`, `backend/main.py:3487-3514` | (source-level; a test may use `SingleInstance` directly) | A second `main.py` exits while one runs. `/api/run` refuses while the lock is held, including a run started by the scheduled task. |
| F-05 | MEDIUM | `src/brain/cognitive/perception.py:135-158` | — | ML rows outside their horizon are not perceived as current (same rule as `technical_agent.ml_horizon_live`). |
| S-01 | HIGH (deploy) | runtime `venv\` | `uvx pip-audit==2.9.0 --path venv/Lib/site-packages` | Recreated runtime venv audits clean, or each remaining item has a recorded decision (chromadb has no fixed version). |
| F-04 | LOW | `ml_store.fresh_tickers`, `main.py:608-618` | `test_f04_resume_retrains_a_row_whose_bar_is_behind` | Skip requires `data_as_of` to equal the frame's last bar. |
| F-03 | LOW | `src/execution/live_guard.py:291-305` | `test_f03_refused_ibkr_order_is_requeued_for_ibkr` | The re-queued row keeps the wrapped broker's route. |
| S-03 / F-07 | LOW | `src/auth/policy.py:398-445` | `test_f07_loopback_fallback_refuses_a_foreign_host` | The loopback fallback requires a loopback Host. Host allowlist added. |
| S-04 | LOW | `src/consult/sentinel_client.py` | — | Non-loopback SENTINEL_URL must be https. Token never follows a redirect. |
| F-12 | LOW | `src/core/readiness.py` | — | Readiness also checks: approval queue readable, desk lock held by this process or knowingly elsewhere, kill switch state reported. |
| F-13 | LOW | kill policy API/UI | — | FLATTEN is not offered as a working production option. |
| F-16 | LOW | `position_manager.py:654-663` | — | A partial exit fill writes a closed-trade record for the filled share. |
| F-15 | INFO | `ibkr_broker.py:549-579` | — | Unknown or unreadable IBKR status returns UNKNOWN (before IBKR validation). |

## 3. Review of Codex `edfb5e6` — "reject hostile research input and preserve recovery evidence"

Verdict: **CHANGES REQUESTED** (one MEDIUM regression; the rest accepted).
Full suite on `edfb5e6` (Windows, runtime venv 3.11.8): 3,542 passed, 13 skipped, exit 0.
Bandit on `edfb5e6`: 43 findings, exit 1 (was 49).

| Part | Verdict | Detail |
| --- | --- | --- |
| `src/core/xml_security.py` + 5 call sites | ACCEPT | DOCTYPE refused before the internal subset is processed; byte limit on the encoded input. Bound `quant_lab`'s `resp.read()` too. |
| `src/research/transport.py` + `http.py`, `rss.py`, `web.py` | ACCEPT with note | Wrap `ValueError` (blocked redirect, scoped IPv6 answer) in `URLError` so callers handle it uniformly. Residual rebinding race documented: needs a host egress rule at deployment. |
| `scripts/backup.py` restore | ACCEPT | |
| `scripts/backup.py` make_backup | REQUEST CHANGE (LOW) | One failed SQLite snapshot aborts the whole archive and leaves `*.tar.partial` behind. Record the failure in the manifest, finish the archive, exit non-zero, and clean up `.partial`. |
| `src/models/ml_store.dataset_age` | REJECT as is (F-06) | Regression: `TypeError` in `backend/main.py:2852` when any ML row is future-dated. |

Recipe for the F-06 regression test (fails on `edfb5e6`, passes once fixed):

```python
def test_source_age_survives_a_future_dated_ml_row(tmp_path, monkeypatch):
    import json, backend.main as m
    from datetime import datetime, timedelta
    now = datetime.now()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "ml_predictions.json").write_text(json.dumps({
        "A": {"generated_at": now.isoformat(timespec="seconds")},
        "B": {"generated_at": (now + timedelta(minutes=30)).isoformat(timespec="seconds")}}))
    monkeypatch.setattr(m, "ROOT", tmp_path)
    label, stale = m._source_age("ml_predictions.json", "ml")
    assert stale is True and "STALE" in label
```

## 4. What Claude will do on each new Codex commit

1. Read the full diff against its parent and against the candidate.
2. Run the probe file and the full suite on a detached checkout of the commit.
3. Check order-path impact: claim before send, UNKNOWN never resent, reduce-only, kill
   switch, mode ceiling, unreadable state fails closed.
4. Record ACCEPT / CHANGES REQUESTED here, with evidence.

Claude does not cherry-pick Codex's unfinished work or edit Codex's files.

## 5. Codex responses

_(append below: finding id, commit, what changed, test evidence)_
