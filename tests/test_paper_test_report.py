"""Synthetic files only. No application import, broker, API or primary data."""
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "paper_test_report.py"
spec = importlib.util.spec_from_file_location("paper_test_report", SCRIPT)
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)
START = datetime.fromisoformat("2026-10-16T17:13:55+01:00")
END = START + timedelta(days=2)
NOW = START + timedelta(hours=24)


def at(hours):
    return (START + timedelta(hours=hours)).isoformat()


@pytest.fixture
def files(tmp_path):
    root = tmp_path / "input"
    desk = root / "desk"
    desk.mkdir(parents=True)
    (desk / "two_day_test.json").write_text(json.dumps({
        "started_at": START.isoformat(), "ends_at": END.isoformat(), "start_equity_usd": 1000,
        "open_positions_at_start": 0, "closed_trades_before_start": 900, "executions_before_start": 999,
    }), encoding="utf-8")

    def write(*, closed=(), executions=(), equity=(), positions=None):
        for name, rows in [("closed_trades.jsonl", closed), ("executions.jsonl", executions), ("equity_curve.jsonl", equity)]:
            (desk / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        (desk / "positions.json").write_text(json.dumps(positions if positions is not None else {}), encoding="utf-8")
        for path in desk.iterdir():
            os.utime(path, (NOW.timestamp() - 1, NOW.timestamp() - 1))
        return root

    write()
    return write


def closed(hours=1, pnl=20, **extra):
    return {"at": at(hours), "entry_at": at(0.5), "ticker": "TEST", "entry_side": "buy", "side": "sell",
            "entry_price": 100, "exit_price": 102, "pnl": pnl, "qty": 10, **extra}


def test_only_strictly_post_start_records_are_counted_and_logs_not_double_counted(files):
    old, boundary, new = closed(-1, -999), closed(0, -999), closed(2, 20)
    root = files(closed=[old, boundary, new, closed(30, 999)],
                 executions=[old, boundary, new, closed(30, 999)],
                 equity=[{"at": at(-1), "equity": 50000}, {"at": at(0), "equity": 50000},
                         {"at": at(2), "equity": 1020}, {"at": at(30), "equity": 9}])
    result = reporter.build_report(root, NOW)
    assert result["closed_trade_count"] == result["execution_record_count"] == 1
    assert result["realised_pnl_usd"] == result["equity_pnl_usd"] == 20
    assert result["equity_pnl_pct"] == 2
    assert result["max_drawdown_usd"] == 0
    assert result["status"] == "in progress"
    assert result["closed_trades"][0]["hold_seconds"] == 5400


def test_open_position_is_unrealised_not_a_closed_win(files):
    root = files(equity=[{"at": at(2), "equity": 1030}], positions={"BTC-USD": {
        "ticker": "BTC-USD", "asset_class": "crypto", "side": "long", "entry_at": at(1),
        "entry_price": 100, "current_price": 115, "qty": 2,
    }})
    result = reporter.build_report(root, NOW)
    assert result["closed_trade_count"] == 0
    assert result["realised_pnl_usd"] == 0
    assert result["unrealised_pnl_usd"] == 30
    assert result["win_rate_pct"] is None
    assert result["reconciliation_residual_usd"] == 0
    assert result["asset_split"]["crypto"]["open_positions"] == 1


def test_short_open_position_and_prestart_positions(files):
    root = files(positions={
        "SHORT": {"ticker": "SHORT", "side": "short", "entry_at": at(1), "entry_price": 100, "last_price": 90, "qty": 2},
        "OLD": {"ticker": "OLD", "side": "long", "entry_at": at(-1), "unrealized_pl": 999},
    })
    result = reporter.build_report(root, NOW)
    assert result["open_position_count"] == 1
    assert result["unrealised_pnl_usd"] == 20


def test_missing_position_marks_are_not_entry_or_high_water_prices(files):
    root = files(equity=[{"at": at(2), "equity": 1050}], positions={"TEST": {
        "ticker": "TEST", "side": "long", "entry_at": at(1), "entry_price": 100, "high_water": 150, "qty": 1,
    }})
    result = reporter.build_report(root, NOW)
    assert result["unrealised_pnl_usd"] is None
    assert result["reconciliation_residual_usd"] is None
    assert any("lack persisted current marks" in warning for warning in result["warnings"])


def test_empty_test_has_no_fabricated_end_equity_or_win_rate(files):
    result = reporter.build_report(files(), NOW)
    assert result["start_equity_usd"] == 1000
    assert result["end_equity_usd"] is result["equity_pnl_usd"] is result["equity_pnl_pct"] is None
    assert result["realised_pnl_usd"] == result["unrealised_pnl_usd"] == 0
    assert result["win_rate_pct"] is result["average_win_usd"] is result["average_loss_usd"] is None
    assert result["max_drawdown_usd"] is result["max_drawdown_pct"] is None
    assert "Two days cannot separate skill from luck" in reporter.render_report(result)


def test_drawdown_uses_chronological_peaks_and_baseline_not_old_curve(files):
    root = files(equity=[{"at": at(-1), "equity": 100000}, {"at": at(3), "equity": 900},
                         {"at": at(1), "equity": 1200}, {"at": at(4), "equity": 1250},
                         {"at": at(2), "equity": 1100}])
    result = reporter.build_report(root, NOW)
    assert result["max_drawdown_usd"] == 300
    assert result["max_drawdown_pct"] == 25
    assert result["end_equity_usd"] == 1250
    assert result["equity_snapshot_count"] == 4


def test_baseline_to_first_mark_loss_is_in_drawdown(files):
    result = reporter.build_report(files(equity=[{"at": at(1), "equity": 800}]), NOW)
    assert result["max_drawdown_usd"] == 200
    assert result["max_drawdown_pct"] == 20


def test_after_end_uses_only_test_window_and_does_not_backdate_current_positions(files):
    root = files(closed=[closed(47, 10), closed(49, 500)],
                 equity=[{"at": at(47), "equity": 1010}, {"at": at(49), "equity": 1510}])
    os.utime(root / "desk" / "positions.json", ((END + timedelta(hours=2)).timestamp(),) * 2)
    result = reporter.build_report(root, END + timedelta(hours=3))
    assert result["status"] == "completed"
    assert result["closed_trade_count"] == 1
    assert result["end_equity_usd"] == 1010
    assert result["open_position_count"] is result["unrealised_pnl_usd"] is None
    assert any("after the report cutoff" in warning for warning in result["warnings"])


def test_win_loss_stats_asset_split_and_entry_exposure_side(files):
    root = files(closed=[closed(1, 30, ticker="BTC-USD"), closed(2, 10), closed(3, -20, entry_side="sell", side="buy"), closed(4, 0)])
    result = reporter.build_report(root, NOW)
    assert result["win_rate_pct"] == 50
    assert result["average_win_usd"] == 20
    assert result["average_loss_usd"] == -20
    assert result["asset_split"]["crypto"]["realised_pnl_usd"] == 30
    assert result["asset_split"]["equity"]["realised_pnl_usd"] == -10
    assert result["closed_trades"][2]["side"] == "short"


def test_naive_desk_timestamps_use_baseline_offset(files):
    root = files(closed=[closed(1, at=(START + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
                                entry_at=(START + timedelta(minutes=30)).replace(tzinfo=None).isoformat())])
    trade = reporter.build_report(root, NOW)["closed_trades"][0]
    assert trade["closed_at"] == at(1)
    assert trade["hold_seconds"] == 1800


def test_malformed_and_unpriced_records_are_disclosed(files):
    root = files(closed=[closed(1, estimated=True, exit_price=0), {"at": "invalid"}])
    with (root / "desk" / "closed_trades.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"incomplete":')
    result = reporter.build_report(root, NOW)
    assert result["closed_trade_count"] == 1
    assert result["realised_pnl_usd"] is None
    assert result["win_rate_pct"] is None
    assert len(result["warnings"]) >= 3


def test_missing_logs_do_not_masquerade_as_zero(files):
    root = files()
    for name in ("closed_trades.jsonl", "executions.jsonl", "equity_curve.jsonl", "positions.json"):
        (root / "desk" / name).unlink()
    result = reporter.build_report(root, NOW)
    assert result["closed_trade_count"] is result["execution_record_count"] is None
    assert result["realised_pnl_usd"] is result["unrealised_pnl_usd"] is None


def test_cli_read_only_and_optional_json_outside_inputs(files, tmp_path, capsys, monkeypatch):
    root = files(closed=[closed()])
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (root / "desk").iterdir()}
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("network call from offline report"))
    output = tmp_path / "report.json"
    assert reporter.main(["--data-dir", str(root), "--as-of", NOW.isoformat(), "--json", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["closed_trade_count"] == 1
    assert "in progress" in capsys.readouterr().out
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (root / "desk").iterdir()}
    assert reporter.main(["--data-dir", str(root), "--json", str(output)]) == 2
    assert "Report unavailable" in capsys.readouterr().err  # never overwrite an export


@pytest.mark.parametrize("relative", ["desk/report.json", "report.json"])
def test_json_export_cannot_write_under_input_root(files, relative):
    root = files()
    destination = root / relative
    assert reporter.main(["--data-dir", str(root), "--json", str(destination)]) == 2
    assert not destination.exists()


def test_json_export_blocks_other_data_directories(files, tmp_path):
    root = files()
    other = tmp_path / "OtherCheckout" / "DaTa"
    other.mkdir(parents=True)
    assert reporter.main(["--data-dir", str(root), "--json", str(other / "report.json")]) == 2
    assert not (other / "report.json").exists()
    assert reporter.export_path(tmp_path / "elsewhere.json", root).parent == tmp_path


def test_invalid_baseline_and_as_of_fail_without_creating_export(files, tmp_path):
    root = files()
    output = tmp_path / "invalid.json"
    assert reporter.main(["--data-dir", str(root), "--json", str(output), "--as-of", "2026-10-17T12:00:00"]) == 2
    (root / "desk" / "two_day_test.json").write_text('{"started_at": null}', encoding="utf-8")
    assert reporter.main(["--data-dir", str(root), "--json", str(output)]) == 2
    assert not output.exists()
