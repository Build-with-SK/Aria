"""
tests/test_ml_durable.py
========================
The pipeline's ML step survives being stopped.

It trains ~2.2 tickers a minute, ~4 h for the universe, and used to write
data/ml_predictions.json once, after the last ticker. A closed console, a
battery stop or the old 2 h task limit lost the whole step: 1 run in 10
completed between 2026-07-22 and 2026-10-17, and the file stayed dated
2026-05-31 (docs/ARIA_COMPLETION_AUDIT.md, O-2).

WHAT IS PINNED
--------------
  * a run killed after batch 2 leaves batches 1-2 on disk, readable
  * a rerun trains only what is missing; --force-ml retrains everything
  * the file is only ever replaced whole: never torn, never opened for writing
  * the pass that finishes drops rows from passes that did not
  * readers age rows by their own stamp, not by the file the batches touch
  * the meta-learner fits the same model without the deprecated `penalty`

Every test runs in tmp_path with a fake trainer. No network, no live data/.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import main as pipeline
from src.models import ml_store

REPO = Path(__file__).resolve().parent.parent
TICKERS = [f"T{i:03d}" for i in range(60)]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Nothing in the ML step's persistence may reach the network."""
    def refuse(*a, **k):
        raise AssertionError("the ML persistence path attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@dataclass
class _Result:
    """Stands in for EnsembleResult — same attribute names."""
    ticker: str = "AAPL"
    horizon: int = 5
    prediction: int = 1
    probability_up: float = 0.62
    confidence: float = 71.0
    base_learner_probs: dict = field(default_factory=lambda: {"xgboost": 0.61})
    meta_score: float = 0.3
    regime: str = "bull"
    regime_adjusted_prob: float = 0.64
    cv_mean_score: float = 0.55
    feature_importances: dict = field(default_factory=dict)
    model_version: str = "v6.0"


def _frame(last: str = "2026-10-16") -> pd.DataFrame:
    idx = pd.date_range(end=last, periods=5, freq="B")
    return pd.DataFrame({"Close": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=idx)


def _featured(tickers=TICKERS) -> dict:
    return {t: _frame() for t in tickers}


def _file(root: Path) -> Path:
    return root / "data" / "ml_predictions.json"


def _read(root: Path) -> dict:
    return json.loads(_file(root).read_text(encoding="utf-8"))


def _stamp(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


class _Run:
    """A fake trainer with the real one's contract: honours `skip`, reports
    each ticker through `on_ticker` once its horizons are done, and can be
    stopped after N tickers the way a closed console stops it."""

    def __init__(self, root: Path, stop_after=None):
        self.root, self.stop_after = root, stop_after
        self.trained, self.skip_seen, self.left_on_disk = [], None, None

    def install(self, monkeypatch):
        run = self

        class _Trainer:
            def __init__(self, cfg):
                pass

            def train_and_predict_all(self, featured, *, skip=(), on_ticker=None):
                run.skip_seen = set(skip)
                out = {}
                for t in featured:
                    if t in skip:
                        continue
                    if run.stop_after is not None and len(run.trained) == run.stop_after:
                        # What a hard kill leaves: the file as it is right now.
                        run.left_on_disk = _read(run.root) if _file(run.root).exists() else {}
                        raise KeyboardInterrupt
                    out[t] = {5: _Result(ticker=t)}
                    run.trained.append(t)
                    on_ticker(t, out[t])
                return out

        import src.models.model_trainer as mt
        monkeypatch.setattr(mt, "ModelTrainer", _Trainer)
        return self


@pytest.fixture
def ledger(monkeypatch):
    """Captures what reaches the prediction ledger, batch by batch."""
    batches = []
    monkeypatch.setattr("src.intelligence.recorders.record_ml_predictions",
                        lambda payload, **k: batches.append(sorted(payload)) or {"recorded": 0})
    return batches


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    return tmp_path


CFG = {"ml": {"persist_every_tickers": 25}}


# ── interruption ────────────────────────────────────────────────────────────

def test_a_run_stopped_after_batch_two_leaves_batches_one_and_two_on_disk(root, monkeypatch, ledger):
    run = _Run(root, stop_after=55).install(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_ml(_featured(), CFG)

    # The instant it stopped: exactly the two completed batches, each row whole.
    assert sorted(run.left_on_disk) == TICKERS[:50]
    for t, row in run.left_on_disk.items():
        assert row["generated_at"] and row["data_as_of"] == "2026-10-16"
        assert row["horizons"]["5"]["bullish_prob"] == pytest.approx(0.64)
    assert ledger[:2] == [TICKERS[:25], TICKERS[25:50]], "the ledger lags the file"

    # Ctrl+C runs the finally: the five finished tickers of batch 3 land too.
    assert sorted(_read(root)) == TICKERS[:55]


def test_a_hard_kill_keeps_the_batches_already_merged(tmp_path):
    """os._exit runs no finally, no atexit, no flush — the closest a test gets
    to a closed console or TerminateProcess. Run in its own interpreter."""
    (tmp_path / "data").mkdir()
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(REPO)!r})
        import pandas as pd
        import main as pipeline
        import src.models.model_trainer as mt
        import src.intelligence.recorders as rec
        rec.record_ml_predictions = lambda payload, **k: {{}}
        from dataclasses import dataclass

        @dataclass
        class R:
            horizon: int = 5
            regime_adjusted_prob: float = 0.6
            confidence: float = 50.0
            cv_mean_score: float = 0.5
            regime: str = "bull"
            model_version: str = "v6.0"

        class T:
            def __init__(self, cfg): pass
            def train_and_predict_all(self, featured, *, skip=(), on_ticker=None):
                for i, t in enumerate(featured):
                    if i == 55:
                        os._exit(3)
                    on_ticker(t, {{5: R()}})

        mt.ModelTrainer = T
        idx = pd.date_range(end="2026-10-16", periods=3, freq="B")
        featured = {{f"T{{i:03d}}": pd.DataFrame({{"Close": [1.0, 2.0, 3.0]}}, index=idx)
                    for i in range(60)}}
        pipeline.run_ml(featured, {{"ml": {{"persist_every_tickers": 25}}}})
    """)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 3, done.stderr[-2000:]
    rows = _read(tmp_path)
    assert sorted(rows) == TICKERS[:50]
    assert all(r["generated_at"] for r in rows.values())
    assert not list((tmp_path / "data").glob(".*.tmp")), "a temp file was left behind"


# ── resume ──────────────────────────────────────────────────────────────────

def test_a_rerun_trains_only_the_tickers_still_missing(root, monkeypatch, ledger):
    _Run(root, stop_after=55).install(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_ml(_featured(), CFG)

    second = _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(), CFG)
    assert second.skip_seen == set(TICKERS[:55])
    assert second.trained == TICKERS[55:]
    rows = _read(root)
    assert sorted(rows) == TICKERS
    assert all(ml_store.stamped(r) for r in rows.values())


def test_force_retrains_every_ticker(root, monkeypatch, ledger):
    _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(), CFG)
    again = _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(), CFG, force=True)
    assert again.skip_seen == set() and again.trained == TICKERS


def test_rows_older_than_the_resume_budget_are_retrained(root, monkeypatch, ledger):
    _file(root).write_text(json.dumps({
        "T000": {"ticker": "T000", "generated_at": _stamp(19), "data_as_of": "2026-10-16"},
        "T001": {"ticker": "T001", "generated_at": _stamp(21)},
        "T002": {"ticker": "T002"},                               # unstamped
        "T003": {"ticker": "T003", "generated_at": _stamp(-2)},   # in the future
    }), encoding="utf-8")
    run = _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(TICKERS[:4]), CFG)
    assert run.skip_seen == {"T000"}, "only a row inside the 20 h default is reused"
    assert run.trained == ["T001", "T002", "T003"]


def test_the_resume_budget_is_configurable_and_zero_disables_it(root, monkeypatch, ledger):
    _file(root).write_text(json.dumps({"T000": {"generated_at": _stamp(1)}}), encoding="utf-8")
    run = _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(TICKERS[:2]), {"ml": {"resume_max_age_hours": 0}})
    assert run.skip_seen == set() and run.trained == TICKERS[:2]


# ── the file is never torn ──────────────────────────────────────────────────

def test_the_file_is_only_ever_replaced_whole(root, monkeypatch, ledger):
    """Every version a reader could see parses, and only grows during a run;
    the live path is never opened for writing — writes go to a temp file that
    replaces it."""
    target = _file(root).resolve()
    seen, opened = [], []
    real_replace, real_open = os.replace, open

    def watch_replace(src, dst, *a, **k):
        real_replace(src, dst, *a, **k)
        if Path(dst).resolve() == target:
            seen.append(json.loads(target.read_text(encoding="utf-8")))

    def watch_open(file, mode="r", *a, **k):
        if any(m in str(mode) for m in "wax+") and not isinstance(file, int):
            if Path(file).resolve() == target:
                opened.append(mode)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(os, "replace", watch_replace)
    monkeypatch.setattr("builtins.open", watch_open)
    _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(), CFG)

    assert opened == [], "the live file was opened for writing — a crash would tear it"
    assert [len(v) for v in seen] == [25, 50, 60]
    for before, after in zip(seen, seen[1:]):
        assert set(before) <= set(after)


def test_a_write_that_fails_leaves_the_old_file_and_retries_the_batch(root, monkeypatch, ledger):
    _file(root).write_text(json.dumps({"OLD": {"ticker": "OLD"}}), encoding="utf-8")
    real_replace, failures = os.replace, []

    def fail_once(src, dst, *a, **k):
        if not failures and Path(dst).name == "ml_predictions.json":
            failures.append(dst)
            # Not PermissionError: durable retries that one (a reader's handle).
            raise OSError("the disk refused the rename")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", fail_once)
    run = _Run(root, stop_after=30).install(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_ml(_featured(), CFG)

    assert run.left_on_disk == {"OLD": {"ticker": "OLD"}}, "a failed write changed the file"
    assert not list((root / "data").glob(".*.tmp"))
    # The batch stayed in memory and went out with the next write.
    assert sorted(_read(root)) == ["OLD"] + TICKERS[:30]


def test_a_file_that_cannot_be_opened_is_never_written_over(root, monkeypatch, ledger):
    """A sharing violation says nothing about the content; reading it as empty
    would drop every row with the next merge."""
    before = json.dumps({"KEEP": {"generated_at": _stamp(100)}})
    _file(root).write_text(before, encoding="utf-8")

    def locked(path, *a, **k):
        raise ml_store.StateUnreadable(Path(path), "could not open: sharing violation")

    monkeypatch.setattr(ml_store, "read_json_state", locked)
    _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(TICKERS[:3]), CFG)
    assert _file(root).read_text(encoding="utf-8") == before


def test_a_damaged_file_is_copied_aside_and_replaced(root, monkeypatch, ledger):
    _file(root).write_text('{"T000": {"ticker": "T0', encoding="utf-8")
    _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(TICKERS[:3]), CFG)
    assert sorted(_read(root)) == TICKERS[:3]
    kept = list((root / "data").glob("ml_predictions.json.corrupt-*"))
    assert kept and kept[0].read_text(encoding="utf-8") == '{"T000": {"ticker": "T0'


# ── the end of a pass ───────────────────────────────────────────────────────

def test_a_finished_pass_drops_rows_no_pass_produced_and_an_interrupted_one_does_not(
        root, monkeypatch, ledger):
    _file(root).write_text(json.dumps({"GONE": {"ticker": "GONE", "generated_at": _stamp(72)},
                                       "LEGACY": {"ticker": "LEGACY"}}), encoding="utf-8")
    _Run(root, stop_after=30).install(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_ml(_featured(), CFG)
    assert {"GONE", "LEGACY"} <= set(_read(root)), "an unfinished pass pruned rows"

    _Run(root).install(monkeypatch)
    pipeline.run_ml(_featured(), CFG)
    assert sorted(_read(root)) == TICKERS


def test_a_run_that_produced_nothing_leaves_the_file_alone(root, monkeypatch, ledger):
    before = json.dumps({"OLD": {"ticker": "OLD", "generated_at": _stamp(500)}})
    _file(root).write_text(before, encoding="utf-8")
    _Run(root).install(monkeypatch)
    pipeline.run_ml({}, CFG)
    assert _file(root).read_text(encoding="utf-8") == before


# ── the real trainer honours skip and reports as it goes ───────────────────

def test_the_trainer_skips_and_reports_each_ticker_without_changing_its_inputs(monkeypatch):
    from src.models import model_trainer as mt
    feats_for = {}

    def build_features(ticker, df, all_data, **k):
        feats_for[ticker] = sorted(all_data)
        return pd.DataFrame({"f": np.arange(10.0)}, index=df.index)

    monkeypatch.setattr("src.models.feature_engine.build_features", build_features)
    monkeypatch.setattr("src.models.targets.build_targets", lambda df, **k: df)
    monkeypatch.setattr(mt.ModelTrainer, "_train_one",
                        lambda self, ticker, horizon, **k: _Result(ticker=ticker, horizon=horizon))
    monkeypatch.setattr(mt, "ModelRegistry", lambda **k: None)

    idx = pd.date_range(end="2026-10-16", periods=10, freq="B")
    featured = {t: pd.DataFrame({"Close": np.arange(10.0)}, index=idx) for t in ("A", "B", "C")}
    trainer = mt.ModelTrainer({"ml": {"min_training_rows": 5, "prediction_horizons": [1, 5]}})
    reported = []
    out = trainer.train_and_predict_all(featured, skip={"B"},
                                        on_ticker=lambda t, h: reported.append((t, sorted(h))))
    assert reported == [("A", [1, 5]), ("C", [1, 5])]
    assert set(out) == {"A", "C"} and "B" not in feats_for
    assert feats_for["A"] == ["A", "B", "C"], "a skipped ticker vanished from cross-asset inputs"


# ── readers age rows, not the file the batches touch ───────────────────────

def _mixed_file(data: Path, *, old_hours: float = 130 * 24) -> None:
    """A pass that has written one fresh batch next to the previous pass's rows:
    the file was modified seconds ago, AAPL's row is months old."""
    (data / "signals.json").write_text(json.dumps({"AAPL": {
        "composite_score": 25.0, "bullish_prob": 0.6, "trend_score": 70, "momentum_score": 65,
        "realised_vol": 0.2, "current_price": 150.0, "action": "Buy", "regime": "Bull",
        "confidence": "Medium"}}), encoding="utf-8")
    horizons = {"1": {"horizon_days": 1, "bullish_prob": 0.3, "direction": "Bearish",
                      "model_agreement": 0.7},
                "20": {"horizon_days": 20, "bullish_prob": 0.2, "direction": "Bearish",
                       "model_agreement": 0.5}}
    (data / "ml_predictions.json").write_text(json.dumps({
        "AAPL": {"horizons": horizons, "overall_signal": "Bearish",
                 "generated_at": _stamp(old_hours)},
        "MSFT": {"horizons": horizons, "overall_signal": "Bearish",
                 "generated_at": _stamp(0.1)},
    }), encoding="utf-8")


def test_the_technical_analyst_ages_each_ml_row_by_its_own_stamp(tmp_path, monkeypatch):
    from src.desk import opinion
    from src.desk.analysts import technical_agent as TA
    monkeypatch.setattr(opinion, "DATA", tmp_path)
    monkeypatch.setattr(TA, "DATA", tmp_path)
    _mixed_file(tmp_path)
    assert not [e for e in TA.opine("AAPL").evidence if "ML ensemble" in e["claim"]], (
        "a 130-day-old row was cited because the file had just been touched")

    _mixed_file(tmp_path, old_hours=3 * 24)
    claims = [e["claim"] for e in TA.opine("AAPL").evidence if "ML ensemble" in e["claim"]]
    assert claims == ["ML ensemble 20d horizon: Bearish (20% bullish, agreement 50%)"]


def test_an_unstamped_row_beside_stamped_ones_has_no_age_to_cite():
    body = {"OLD": {"horizons": {}}, "NEW": {"generated_at": _stamp(1)}}
    assert ml_store.made_at(body, "OLD", datetime.now()) is None
    only_old = {"OLD": {"horizons": {}}}
    mtime = datetime(2026, 5, 31, 1, 18)
    assert ml_store.made_at(only_old, "OLD", mtime) == mtime, (
        "a file written in one piece is still aged by its mtime")


def test_the_world_model_calls_a_half_done_pass_stale(tmp_path, monkeypatch):
    from src.core import world
    monkeypatch.setattr(world, "DATA", tmp_path)
    _mixed_file(tmp_path)
    f = world.dataset_freshness("ml_predictions.json")
    assert f["status"] == "STALE" and f["basis"] == "row_generated_at"
    assert "1 of 2 rows older than 48h" in f["why"]
    assert world._market()["ml"]["stale"] is True

    _mixed_file(tmp_path, old_hours=5)
    f = world.dataset_freshness("ml_predictions.json")
    assert f["status"] == "FRESH" and f["age_hours"] == pytest.approx(5, abs=0.1)


def test_the_chat_context_ages_ml_by_its_oldest_row(tmp_path, monkeypatch):
    import backend.main as B
    monkeypatch.setattr(B, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    _mixed_file(tmp_path / "data")
    label, stale = B._source_age("ml_predictions.json", "ml")
    assert stale is True and "by its oldest row" in label and "1 of 2 rows" in label


# ── the meta-learner without the deprecated `penalty` ──────────────────────

def test_the_meta_learner_fits_without_a_penalty_futurewarning():
    from src.models import ensemble_model as em
    rng = np.random.default_rng(0)
    oof = pd.DataFrame(rng.uniform(size=(200, 4)), columns=list("abcd"))
    y = pd.Series((oof["a"] + rng.normal(scale=0.3, size=200) > 0.5).astype(int))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        meta = em._train_meta_learner(oof, y)
    assert meta is not None
    assert not [w for w in caught if "penalty" in str(w.message)], caught


def test_concurrent_batch_merges_do_not_drop_finished_tickers(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    path = tmp_path / "ml.json"
    original = ml_store.read_rows
    def slow_read(p):
        rows = original(p)
        time.sleep(0.01)
        return rows
    monkeypatch.setattr(ml_store, "read_rows", slow_read)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: ml_store.merge_rows(path, {f"T{n}": {"value": n}}), range(32)))
    assert original(path) == {f"T{n}": {"value": n} for n in range(32)}


def test_the_elastic_net_arguments_fit_the_same_model_on_either_sklearn(monkeypatch):
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from src.models import ensemble_model as em
    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, 5))
    y = (X[:, 0] + rng.normal(size=300) > 0).astype(int)
    common = dict(C=1.0, solver="saga", max_iter=300, random_state=42)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old = LogisticRegression(penalty="elasticnet", l1_ratio=0.5, **common).fit(X, y)
    new = LogisticRegression(**common, **em._elasticnet(0.5)).fit(X, y)
    assert np.array_equal(old.coef_, new.coef_) and np.array_equal(old.intercept_, new.intercept_)

    # Before 1.8, l1_ratio alone is ignored: the penalty must still be named.
    monkeypatch.setattr(sklearn, "__version__", "1.7.2")
    assert em._elasticnet(0.5) == {"penalty": "elasticnet", "l1_ratio": 0.5}
    monkeypatch.setattr(sklearn, "__version__", "1.10.0")
    assert em._elasticnet(0.5) == {"l1_ratio": 0.5}
