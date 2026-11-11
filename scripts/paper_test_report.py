"""Read persisted paper-test files only; no application imports or network calls.

Usage (from the Codex checkout):
    python scripts/paper_test_report.py --data-dir ../trading-intelligence-system/data
    python scripts/paper_test_report.py --json ../reports/paper-test.json

Timestamps without an offset use the baseline's offset (BST for this test).
Closed exit records, including partial exits, are counted separately. Executions
are an activity count, never added to closed-trade P&L a second time. Open P&L
requires an explicit persisted mark; a high-water mark is not a current price.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "trading-intelligence-system" / "data"
CAVEAT = "Two days cannot separate skill from luck. This is a descriptive paper-test report, not evidence of a trading edge."


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def timestamp(value, local_tz=None):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            return result.replace(tzinfo=local_tz) if local_tz else None
        return result
    except ValueError:
        return None


def read_json(path, warnings, required=False):
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        if required:
            raise ValueError(f"{path.name}: missing or invalid JSON") from None
        warnings.append(f"{path.name}: missing or invalid; its measurements are unavailable.")
        return None


def read_jsonl(path, warnings):
    rows = []
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError
                    rows.append(row)
                except ValueError:
                    warnings.append(f"{path.name}:{line_no}: invalid record excluded (possibly an incomplete append).")
    except OSError:
        warnings.append(f"{path.name}: unavailable; counts and totals from this log are withheld.")
        return None
    return rows


def in_window(rows, start, cutoff, warnings, name, time_key="at"):
    out = []
    for row in rows or []:
        at = timestamp(row.get(time_key), start.tzinfo)
        if at is None:
            warnings.append(f"{name}: record without a usable {time_key} excluded.")
        elif start < at <= cutoff:
            out.append((at, row))
    return sorted(out, key=lambda pair: pair[0])


def side(row, closed=False):
    value = row.get("entry_side") if closed else row.get("side")
    if value in ("buy", "long"):
        return "long"
    if value in ("sell", "short"):
        return "short"
    value = row.get("side")
    if value in ("long", "short"):
        return value
    return {"sell": "long", "buy": "short"}.get(value, "unknown") if closed else "unknown"


def asset_class(row):
    explicit = row.get("asset_class")
    if explicit:
        return explicit if explicit in ("crypto", "equity") else "unknown"
    symbol = row.get("ticker") or row.get("symbol") or ""
    if not isinstance(symbol, str) or not symbol:
        return "unknown"
    # The desk's persisted convention is crypto pairs with -USD or /USD;
    # no broker lookup or claim of security-master verification is made.
    if symbol.endswith(("-USD", "/USD")):
        return "crypto"
    return "equity" if re.fullmatch(r"[A-Z][A-Z0-9.\-]*", symbol) else "unknown"


def total(values):
    values = list(values)
    return round(sum(values), 6) if all(value is not None for value in values) else None


def drawdown(points):
    if len(points) < 2:
        return {"max_drawdown_usd": None, "max_drawdown_pct": None}
    peak = points[0][1]
    dollars = percent = 0.0
    for _, equity in points[1:]:
        peak = max(peak, equity)
        dollars = max(dollars, peak - equity)
        if peak > 0:
            percent = max(percent, (peak - equity) / peak * 100)
    return {"max_drawdown_usd": round(dollars, 6), "max_drawdown_pct": round(percent, 6)}


def build_report(data_dir, now=None):
    desk = Path(data_dir).resolve() / "desk"
    warnings = []
    baseline = read_json(desk / "two_day_test.json", warnings, required=True)
    if not isinstance(baseline, dict):
        raise ValueError("two_day_test.json: expected a baseline object")
    start = timestamp(baseline.get("started_at"))
    end = timestamp(baseline.get("ends_at"))
    start_equity = number(baseline.get("start_equity_usd"))
    if start is None or end is None or end <= start or start_equity is None or start_equity <= 0:
        raise ValueError("Baseline needs offset-aware started_at/ends_at and positive start_equity_usd")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Report time must include a UTC offset")
    cutoff = min(now, end)
    status = "not started" if now < start else "in progress" if now < end else "completed"
    raw_closed = read_jsonl(desk / "closed_trades.jsonl", warnings)
    raw_exec = read_jsonl(desk / "executions.jsonl", warnings)
    raw_curve = read_jsonl(desk / "equity_curve.jsonl", warnings)
    trades = []
    for at, row in in_window(raw_closed, start, cutoff, warnings, "closed_trades.jsonl"):
        entry_at = timestamp(row.get("entry_at"), start.tzinfo)
        pnl = number(row.get("pnl"))
        if row.get("estimated") and not number(row.get("exit_price")):
            pnl = None  # an unpriced estimated close is not a measured zero
        trades.append({"symbol": row.get("ticker") or row.get("symbol") or "unknown",
                       "side": side(row, closed=True), "asset_class": asset_class(row),
                       "entry_price": number(row.get("entry_price")), "exit_price": number(row.get("exit_price")),
                       "qty": number(row.get("qty")), "pnl_usd": pnl,
                       "entry_at": entry_at.isoformat() if entry_at else None, "closed_at": at.isoformat(),
                       "hold_seconds": (at - entry_at).total_seconds() if entry_at and at >= entry_at else None,
                       "partial_exit": number(row.get("fraction")) not in (None, 1.0),
                       "estimated": bool(row.get("estimated"))})
    executions = in_window(raw_exec, start, cutoff, warnings, "executions.jsonl")
    curve = []
    for at, row in in_window(raw_curve, start, cutoff, warnings, "equity_curve.jsonl"):
        equity = number(row.get("equity"))
        if equity is None:
            warnings.append("equity_curve.jsonl: non-finite/missing equity excluded.")
        else:
            curve.append((at, equity))
    end_equity = curve[-1][1] if curve else None
    pnl = end_equity - start_equity if end_equity is not None else None
    realised = total(trade["pnl_usd"] for trade in trades) if raw_closed is not None else None
    if any(trade["pnl_usd"] is None for trade in trades):
        warnings.append("Some closed exits have no usable P&L; realised total is withheld and win/loss statistics use priced exits only.")
    if baseline.get("open_positions_at_start") not in (None, 0):
        warnings.append("The baseline includes carry-in positions; account equity change is not attributable solely to trades opened during this test.")
    positions_file = desk / "positions.json"
    positions = read_json(positions_file, warnings)
    positions_usable = isinstance(positions, (dict, list))
    if positions_usable and positions_file.stat().st_mtime > cutoff.timestamp():
        warnings.append("positions.json: snapshot was written after the report cutoff; historical open P&L is unavailable.")
        positions_usable = False
    entries = list(positions.values()) if isinstance(positions, dict) else positions if isinstance(positions, list) else []
    open_positions = []
    for row in entries if positions_usable else []:
        if not isinstance(row, dict):
            warnings.append("positions.json: non-object position excluded.")
            continue
        entry_at = timestamp(row.get("entry_at"), start.tzinfo)
        if entry_at is None:
            warnings.append("positions.json: undated position excluded from test attribution.")
            continue
        if not start < entry_at <= cutoff:
            continue
        mark = next((number(row.get(key)) for key in ("current_price", "last_price", "market_price")
                     if number(row.get(key)) is not None), None)
        unrealised = number(row.get("unrealized_pl"))
        entry, qty, direction = number(row.get("entry_price")), number(row.get("qty")), side(row)
        if unrealised is None and mark is not None and entry is not None and qty is not None and direction != "unknown":
            unrealised = (mark - entry) * abs(qty) * (1 if direction == "long" else -1)
        open_positions.append({"symbol": row.get("ticker") or row.get("symbol") or "unknown", "side": direction,
                               "asset_class": asset_class(row), "entry_price": entry, "mark_price": mark,
                               "qty": qty, "unrealised_pnl_usd": unrealised})
    unrealised = total(row["unrealised_pnl_usd"] for row in open_positions) if positions_usable else None
    if any(row["unrealised_pnl_usd"] is None for row in open_positions):
        warnings.append("Open positions lack persisted current marks; unrealised P&L is withheld, not estimated from entry/high-water prices.")
    if any(trade["estimated"] for trade in trades):
        warnings.append("Some closed-trade prices/P&L are marked estimated by the source log.")
    if any(timestamp(row["entry_at"]) < start for row in trades if row["entry_at"]):
        warnings.append("Some exits close carry-in positions: their full logged trade P&L is not solely post-start performance.")
    split = {}
    for kind in ("crypto", "equity", "unknown"):
        closed = [trade for trade in trades if trade["asset_class"] == kind]
        opened = [row for row in open_positions if row["asset_class"] == kind]
        split[kind] = {"closed_trades": len(closed) if raw_closed is not None else None,
                       "realised_pnl_usd": total(row["pnl_usd"] for row in closed) if raw_closed is not None else None,
                       "open_positions": len(opened) if positions_usable else None,
                       "unrealised_pnl_usd": total(row["unrealised_pnl_usd"] for row in opened) if positions_usable else None}
    scored = [trade["pnl_usd"] for trade in trades if trade["pnl_usd"] is not None]
    wins, losses = [v for v in scored if v > 0], [v for v in scored if v < 0]
    return {"status": status, "started_at": start.isoformat(), "ends_at": end.isoformat(), "as_of": now.isoformat(),
            "cutoff": cutoff.isoformat(), "start_equity_usd": start_equity, "end_equity_usd": end_equity,
            "end_equity_at": curve[-1][0].isoformat() if curve else None,
            "equity_pnl_usd": round(pnl, 6) if pnl is not None else None,
            "equity_pnl_pct": round(pnl / start_equity * 100, 6) if pnl is not None else None,
            "realised_pnl_usd": realised, "unrealised_pnl_usd": unrealised,
            "reconciliation_residual_usd": round(pnl - realised - unrealised, 6)
                if pnl is not None and realised is not None and unrealised is not None else None,
            "closed_trades": trades, "closed_trade_count": len(trades) if raw_closed is not None else None,
            "scored_exit_count": len(scored), "win_rate_pct": len(wins) / len(scored) * 100 if scored else None,
            "average_win_usd": sum(wins) / len(wins) if wins else None,
            "average_loss_usd": sum(losses) / len(losses) if losses else None,
            "execution_record_count": len(executions) if raw_exec is not None else None,
            "execution_modes": dict(Counter(row.get("mode", "unknown") for _, row in executions)),
            "equity_snapshot_count": len(curve), "open_positions": open_positions,
            "open_position_count": len(open_positions) if positions_usable else None,
            **drawdown([(start, start_equity)] + curve), "asset_split": split, "warnings": warnings,
            "caveat": CAVEAT,
            "method": ["Only records strictly after started_at and at/before min(report time, ends_at) are counted.",
                       f"Naive desk timestamps use the baseline offset {start.strftime('%z')}.",
                       "Closed exit records include partial exits; win rate includes breakevens in its denominator and excludes unpriced exits.",
                       "Positions are the persisted snapshot, not a live broker valuation; post-cutoff snapshots are withheld.",
                       "Crypto/equity split uses explicit asset_class, else the desk's -USD or /USD pair convention; other tickers default to equity.",
                       "Drawdown includes baseline equity; dollar and percentage maxima are measured separately on recorded marks only.",
                       "Equity change may include cash flows, fees or unlogged activity; any reconciliation residual is not assigned to unrealised P&L."]}


def render_report(report):
    def fmt(value, unit="$"):
        return "unavailable" if value is None else f"{unit}{value:,.2f}" if unit else f"{value:,.2f}"
    def count(value):
        return "unavailable" if value is None else str(value)
    lines = [f"PAPER TEST — {report['status']}", f"Window: {report['started_at']} to {report['ends_at']}",
             f"Cutoff: {report['cutoff']} | last equity mark: {report['end_equity_at'] or 'unavailable'}",
             f"Equity: start {fmt(report['start_equity_usd'])}; end/latest {fmt(report['end_equity_usd'])}",
             f"Equity P&L: {fmt(report['equity_pnl_usd'])} ({fmt(report['equity_pnl_pct'], '')}%)",
             f"Realised: {fmt(report['realised_pnl_usd'])}; unrealised: {fmt(report['unrealised_pnl_usd'])}; residual: {fmt(report['reconciliation_residual_usd'])}",
             f"Closed exit records: {count(report['closed_trade_count'])}; open positions: {count(report['open_position_count'])}; execution records: {count(report['execution_record_count'])}",
             f"Win rate: {fmt(report['win_rate_pct'], '')}% over {report['scored_exit_count']} priced exits; average win {fmt(report['average_win_usd'])}; average loss {fmt(report['average_loss_usd'])}",
             f"Max drawdown: {fmt(report['max_drawdown_usd'])}; {fmt(report['max_drawdown_pct'], '')}%",
             "Closed exits (side = entry exposure; partial exits counted separately):"]
    for trade in report["closed_trades"]:
        hold = f"{trade['hold_seconds'] / 3600:.2f}h" if trade["hold_seconds"] is not None else "unavailable"
        lines.append(f"  {trade['closed_at']} {trade['symbol']} {trade['side']} entry {fmt(trade['entry_price'])} exit {fmt(trade['exit_price'])} P&L {fmt(trade['pnl_usd'])} hold {hold}"
                     + (" [partial]" if trade["partial_exit"] else "") + (" [estimated]" if trade["estimated"] else ""))
    if not report["closed_trades"]:
        lines.append("  No closed exits recorded in the test window (or log unavailable; see warnings).")
    lines.append("Asset split:")
    for kind, block in report["asset_split"].items():
        lines.append(f"  {kind}: {count(block['closed_trades'])} closed / {count(block['open_positions'])} open; realised {fmt(block['realised_pnl_usd'])}; unrealised {fmt(block['unrealised_pnl_usd'])}")
    lines.extend(f"Warning: {warning}" for warning in report["warnings"])
    lines.extend(f"Method: {method}" for method in report["method"])
    lines.append(report["caveat"])
    return "\n".join(lines)


def export_path(path, data_dir):
    destination = Path(path).resolve()
    data_dir = Path(data_dir).resolve()
    if destination.is_relative_to(data_dir) or any(part.casefold() == "data" for part in destination.parts):
        raise ValueError("--json must be outside every data/ directory and outside --data-dir")
    if destination.suffix.lower() != ".json":
        raise ValueError("--json output must have a .json extension")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="data root containing desk/ (read only)")
    parser.add_argument("--json", type=Path, help="optional NEW JSON file outside data/; existing files are never overwritten")
    parser.add_argument("--as-of", help="offset-aware report time for reproducible cutoffs; default is now")
    args = parser.parse_args(argv)
    try:
        destination = export_path(args.json, args.data_dir) if args.json else None
        now = timestamp(args.as_of) if args.as_of else None
        if args.as_of and now is None:
            raise ValueError("--as-of needs an ISO timestamp with UTC offset")
        report = build_report(args.data_dir, now=now)
        if destination:
            # Validation above precedes all writes; parents must already exist.
            with destination.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, allow_nan=False)
                stream.write("\n")
        print(render_report(report))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Report unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
