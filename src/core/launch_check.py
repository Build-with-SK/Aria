"""
src/core/launch_check.py
========================
The launch-verification checklist, MEASURED. Every line is a reading taken now,
with its evidence; nothing is a PASS because it passed last time.

    venv\\Scripts\\python.exe -m src.core.launch_check --root <repo> \\
        --base-url http://localhost:8000 [--json PATH]

Read-only by construction: it reads files under <root>/data and the pipeline
log, and makes unauthenticated GET requests. It never writes into <root>, never
signs in, never calls the broker and never starts or stops anything — so it
can run during the paper test.

What it cannot see is said, not guessed: a page rendering the owner's data and
the content of an owner-only endpoint need the owner's signed-in browser, and
the test suite is its own command. Those report NOT_MEASURED with the way to
measure them.

Statuses: PASS (measured, as required) · WARN (measured, needs a look) ·
FAIL (measured, not as required) · NOT_MEASURED (this tool cannot see it).
Exit code 1 when any check FAILs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PASS, WARN, FAIL, NOT_MEASURED = "PASS", "WARN", "FAIL", "NOT_MEASURED"

#: The eight pages of WORK_PLAN §5 gate L2.
ROUTES = ("brain", "research", "market", "portfolio", "strategies",
          "daily-report", "track-record", "system")
#: Owner-only reads (require_owner in backend/main.py). Without a session each
#: must refuse; a 200 here is owner data served to anyone.
OWNER_ENDPOINTS = ("/api/desk/status", "/api/desk/executions", "/api/desk/playbooks",
                   "/api/execute/queue", "/api/trading/status", "/api/world")
#: src/core/world.STALE_AFTER_HOURS — the budgets the auto-execution gate uses.
SIGNALS_BUDGET_H, ML_BUDGET_H = 30, 48
HEARTBEAT_BUDGET_MIN, EQUITY_MARK_BUDGET_MIN = 15, 30
#: A signal row whose last bar is this much older than the newest is stale
#: inside a fresh file (the file is aged as a whole by the desk's gate).
STALE_ROW_DAYS = 5
#: The pipeline's scheduled-task execution limit (install_scheduler.ps1).
PIPELINE_LIMIT_H = 8


def _check(name: str, status: str, evidence: str, **detail) -> dict:
    return {"check": name, "status": status, "evidence": evidence, **detail}


def _when(value) -> Optional[datetime]:
    """An ISO time as an aware datetime; a naive one is local time."""
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def _hours(t: Optional[datetime], now: datetime) -> Optional[float]:
    return None if t is None else round((now - t).total_seconds() / 3600, 2)


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _jsonl(path: Path) -> list:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _get(url: str, timeout: float = 6.0) -> tuple[Optional[int], str, int, str]:
    """(status, body text, milliseconds, error) for an unauthenticated GET."""
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"),
                                    timeout=timeout) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            return r.status, body, int((time.monotonic() - t0) * 1000), ""
    except urllib.error.HTTPError as e:
        return e.code, "", int((time.monotonic() - t0) * 1000), ""
    except Exception as e:
        return None, "", int((time.monotonic() - t0) * 1000), f"{type(e).__name__}: {e}"


# ── runtime, pages, auth ────────────────────────────────────────────────────

def check_runtime(base: Optional[str]) -> dict:
    if not base:
        return _check("runtime", NOT_MEASURED, "no --base-url given")
    got = {p: _get(base + p) for p in ("/health", "/ready")}
    ok = all(s == 200 for s, *_ in got.values())
    ev = "; ".join(f"{p} {s or e} in {ms} ms" for p, (s, _, ms, e) in got.items())
    return _check("runtime", PASS if ok else FAIL, ev)


def check_frontend(root: Path, base: Optional[str]) -> dict:
    index = root / "frontend" / "dist" / "index.html"
    if not index.exists():
        return _check("frontend build", FAIL, f"{index} missing")
    html = index.read_text(encoding="utf-8", errors="replace")
    assets = sorted({a.split('"')[0] for a in html.split("assets/")[1:]
                     if a.split('"')[0].endswith(".js")})
    missing = [a for a in assets if not (index.parent / "assets" / a).exists()]
    if missing or not assets:
        return _check("frontend build", FAIL, f"index.html names {assets}; missing {missing}")
    ev = f"dist/index.html -> {', '.join(assets)} (present)"
    if not base:
        return _check("frontend build", PASS, ev + "; routes not requested (no --base-url)")
    served = {r: _get(f"{base}/app/{r}") for r in ROUTES}
    bad = [r for r, (s, body, _, e) in served.items()
           if s != 200 or not any(a in body for a in assets)]
    ev += f"; /app/<route> served the shell for {len(ROUTES) - len(bad)}/{len(ROUTES)} routes"
    return _check("frontend build", FAIL if bad else PASS,
                  ev + (f" — not: {bad}" if bad else ""))


def check_owner_pages() -> dict:
    return _check("pages render owner data", NOT_MEASURED,
                  "needs the owner's signed-in browser: open the 8 routes on "
                  "localhost:8000 (launch night: Claude in Chrome), confirm each renders "
                  "with 0 console errors and 0 failed /api calls")


def check_auth(base: Optional[str]) -> dict:
    if not base:
        return _check("owner endpoints refuse without a session", NOT_MEASURED,
                      "no --base-url given")
    got = {p: _get(base + p)[0] for p in OWNER_ENDPOINTS}
    leaked = [p for p, s in got.items() if s == 200]
    unreachable = [p for p, s in got.items() if s is None]
    status = FAIL if leaked else (WARN if unreachable else PASS)
    return _check("owner endpoints refuse without a session", status,
                  ", ".join(f"{p} {s}" for p, s in got.items()))


# ── data freshness ──────────────────────────────────────────────────────────

def check_signals(root: Path, now: datetime) -> dict:
    rows = _json(root / "data" / "signals.json")
    if not isinstance(rows, dict) or not rows:
        return _check("signals freshness", FAIL, "data/signals.json missing or empty")
    rows = {k: v for k, v in rows.items() if isinstance(v, dict)}
    gen = max((_when(v.get("generated_at")) for v in rows.values()
               if _when(v.get("generated_at"))), default=None)
    if gen is None:
        gen = datetime.fromtimestamp((root / "data" / "signals.json").stat().st_mtime).astimezone()
    age = _hours(gen, now)
    def bar(v):
        try:
            return datetime.fromisoformat(str(v.get("data_as_of"))[:10])
        except ValueError:
            return None
    bars = Counter(str(v.get("data_as_of"))[:10] for v in rows.values())
    newest = max((b for b in map(bar, rows.values()) if b), default=None)
    stale_rows = sorted(f"{k} ({str(v.get('data_as_of'))[:10]})" for k, v in rows.items()
                        if newest and bar(v) and (newest - bar(v)).days > STALE_ROW_DAYS)
    undated = sum(1 for v in rows.values() if bar(v) is None)
    ev = (f"{len(rows)} rows, generated {gen.isoformat(timespec='minutes')} ({age} h, budget "
          f"{SIGNALS_BUDGET_H} h); last bars {dict(bars.most_common(4))}")
    if stale_rows or undated:
        ev += f"; rows > {STALE_ROW_DAYS} d behind the newest bar: {stale_rows}; undated: {undated}"
    status = FAIL if age > SIGNALS_BUDGET_H else (WARN if stale_rows or undated else PASS)
    return _check("signals freshness", status, ev, age_hours=age, stale_rows=stale_rows)


def check_ml(root: Path, now: datetime) -> dict:
    path = root / "data" / "ml_predictions.json"
    rows = _json(path)
    if not isinstance(rows, dict) or not rows:
        return _check("ML predictions", FAIL, "data/ml_predictions.json missing or empty")
    gens = [_when(v.get("generated_at")) for v in rows.values() if isinstance(v, dict)]
    gen = max((g for g in gens if g), default=None)
    basis = "generated_at"
    if gen is None:
        gen, basis = datetime.fromtimestamp(path.stat().st_mtime).astimezone(), "file mtime (rows undated)"
    age = _hours(gen, now)
    return _check("ML predictions", FAIL if age > ML_BUDGET_H else PASS,
                  f"{len(rows)} tickers, {basis} {gen.isoformat(timespec='minutes')} "
                  f"({age} h, budget {ML_BUDGET_H} h)", age_hours=age)


def _last_run_lines(root: Path, max_bytes: int = 8_000_000) -> list[str]:
    log = root / "tis_run.log"
    try:
        with log.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    starts = [i for i, l in enumerate(lines) if "TRADING INTELLIGENCE SYSTEM" in l]
    return lines[starts[-1]:] if starts else []


def check_sentiment(root: Path) -> dict:
    lines = _last_run_lines(root)
    if not lines:
        return _check("sentiment", NOT_MEASURED, "no pipeline run found in tis_run.log")
    started = lines[0][:19]
    for l in reversed(lines):
        if "Sentiment analysis failed" in l:
            return _check("sentiment", FAIL, f"run {started}: {l[20:].strip()[:160]}")
        if "NO ticker had any news" in l:
            return _check("sentiment", FAIL, f"run {started}: {l[20:].strip()[:160]}")
        if "Sentiment analysis complete" in l:
            ok = "unavailable" in l          # the run reports unavailable apart
            return _check("sentiment", PASS if ok else WARN,
                          f"run {started}: {l[20:].strip()[:200]}"
                          + ("" if ok else " (code before unavailable-labelling)"))
    return _check("sentiment", NOT_MEASURED, f"run {started} has not reached the sentiment step")


def _pid_alive(pid) -> Optional[bool]:
    """Is this process running? Read-only. On Windows `os.kill(pid, 0)` is NOT
    a probe — it terminates the process — so the handle is opened for query
    only. None when it cannot be determined."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)        # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            try:
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            finally:
                k32.CloseHandle(h)
            return bool(ok) and code.value == 259          # STILL_ACTIVE
        except Exception:
            return None
    import os
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return None


def check_pipeline(root: Path, now: datetime) -> dict:
    run = _json(root / "data" / "pipeline_run.json")
    if not isinstance(run, dict):
        return _check("pipeline run", FAIL, "data/pipeline_run.json missing — no run recorded")
    started, step_at = _when(run.get("started_at")), _when(run.get("step_at"))
    if run.get("completed"):
        return _check("pipeline run", PASS, f"completed {run.get('finished_at')} "
                      f"(started {run.get('started_at')})")
    since = _hours(started, now)
    ev = (f"started {run.get('started_at')}, step '{run.get('step')}' since "
          f"{run.get('step_at')}, not completed")
    last = _last_run_lines(root)
    if last:
        ev += f"; log's last line {last[-1][:19]}"
    alive = _pid_alive(run.get("pid"))
    if alive is False:
        return _check("pipeline run", FAIL, ev + f" — pid {run.get('pid')} is not running: "
                      f"this run died without finishing")
    if since is not None and since > PIPELINE_LIMIT_H:
        return _check("pipeline run", FAIL, ev + f" — {since} h after start, past the "
                      f"{PIPELINE_LIMIT_H} h task limit: this run is dead")
    return _check("pipeline run", WARN, ev + f" — {since} h after start, pid "
                  f"{run.get('pid')} {'running' if alive else 'state unknown'}")


# ── trading safety and the paper test ───────────────────────────────────────

def check_sentinel(url: Optional[str]) -> dict:
    if not url:
        return _check("SENTINEL", NOT_MEASURED, "no --sentinel-url given")
    s, body, ms, e = _get(url.rstrip("/") + "/health", timeout=4)
    return _check("SENTINEL", PASS if s == 200 else FAIL,
                  f"{url}/health {s or e} in {ms} ms {body[:80]}".strip())


def check_desk_and_broker(root: Path, now: datetime) -> dict:
    hb = _json(root / "data" / "desk" / "heartbeat.json") or {}
    marks = _jsonl(root / "data" / "desk" / "equity_curve.jsonl")
    hb_age = _hours(_when(hb.get("at")), now)
    last = marks[-1] if marks else {}
    mark_age = _hours(_when(last.get("at")), now)
    ev = (f"heartbeat {hb.get('at')} (tick {hb.get('tick_count')}, errors {hb.get('errors')}); "
          f"last equity mark {last.get('at')} = {last.get('equity')} — written only after a "
          f"successful broker account read")
    ok = (hb_age is not None and hb_age * 60 <= HEARTBEAT_BUDGET_MIN and not hb.get("errors")
          and mark_age is not None and mark_age * 60 <= EQUITY_MARK_BUDGET_MIN
          and float(last.get("equity") or 0) > 0)
    return _check("desk + broker connectivity", PASS if ok else FAIL, ev)


def check_paper_mode(root: Path) -> dict:
    mode = _json(root / "data" / "trading" / "mode.json") or {}
    m = mode.get("mode")
    ev = (f"data/trading/mode.json mode={m} since {mode.get('since')}; the live gate "
          f"(ARIA_LIVE_TRADING_ENABLED) lives in the backend process environment and is "
          f"read via /api/trading/status with the owner's session")
    if m in ("PAPER", "RESEARCH"):
        return _check("PAPER-mode enforcement", PASS, ev)
    return _check("PAPER-mode enforcement", FAIL, ev)


def check_kill_switch_and_risk(root: Path) -> dict:
    ks = _json(root / "data" / "kill_switch.json")
    cfg = _json(root / "data" / "desk_config.json") or {}
    if ks is None:
        return _check("kill switch + risk controls", FAIL, "data/kill_switch.json unreadable")
    ev = (f"kill switch engaged={ks.get('engaged')} (last change {ks.get('released_at') or ks.get('engaged_at')}); "
          f"desk risk_profile={cfg.get('risk_profile')}, auto_execute={cfg.get('auto_execute')}, "
          f"daily_notional_budget={cfg.get('daily_notional_budget')}, "
          f"max_trades_per_day={cfg.get('max_trades_per_day')}")
    return _check("kill switch + risk controls", WARN if ks.get("engaged") else PASS, ev)


def check_reflex(root: Path) -> dict:
    pbs = _json(root / "data" / "desk" / "playbooks.json")
    if not isinstance(pbs, list):
        return _check("reflex observability", FAIL, "data/desk/playbooks.json unreadable")
    by = Counter(p.get("status") for p in pbs)
    attempted = sum(1 for p in pbs if p.get("attempts"))
    ev = f"{len(pbs)} playbooks {dict(by)}; {attempted} carry attempt records"
    return _check("reflex observability", WARN if by.get("unknown") else PASS, ev)


def check_orders(root: Path, since: Optional[datetime]) -> dict:
    q = _json(root / "data" / "execution" / "approval_queue.json") or []
    by = Counter(t.get("status") for t in q if isinstance(t, dict))
    ex = [r for r in _jsonl(root / "data" / "desk" / "executions.jsonl")
          if since is None or (_when(r.get("at")) or since) >= since]
    modes = Counter(r.get("mode") for r in ex)
    halt = _json(root / "data" / "trading" / "order_halt.json") or {}
    unsettled = by.get("approved", 0) + by.get("unknown", 0) + modes.get("unknown", 0)
    ev = (f"approval queue {dict(by)}; desk executions since test start {dict(modes) or 0}; "
          f"order engine halted={bool(halt.get('halted'))}")
    return _check("order reconciliation", WARN if unsettled or halt.get("halted") else PASS, ev)


def check_paper_test(root: Path, now: datetime) -> tuple[dict, Optional[datetime]]:
    t = _json(root / "data" / "desk" / "two_day_test.json")
    if not isinstance(t, dict):
        return _check("paper-test continuity", NOT_MEASURED, "no data/desk/two_day_test.json"), None
    start, end = _when(t.get("started_at")), _when(t.get("ends_at"))
    marks = [m for m in _jsonl(root / "data" / "desk" / "equity_curve.jsonl")
             if start and (_when(m.get("at")) or start) >= start]
    gaps = []
    for a, b in zip(marks, marks[1:]):
        ta, tb = _when(a.get("at")), _when(b.get("at"))
        if ta and tb and (tb - ta).total_seconds() > 3600:
            gaps.append(f"{a['at'][:16]}->{b['at'][:16]}")
    state = "IN PROGRESS" if end and now < end else "ENDED"
    last = marks[-1] if marks else {}
    ev = (f"{state}: started {t.get('started_at')}, ends {t.get('ends_at')}, start equity "
          f"{t.get('start_equity_usd')}; {len(marks)} equity marks since start, last "
          f"{last.get('at')} = {last.get('equity')}; gaps > 1 h: {gaps or 'none'}")
    return _check("paper-test continuity", WARN if gaps or not marks else PASS, ev), start


def check_suite() -> dict:
    return _check("full-suite regression", NOT_MEASURED,
                  "its own command: venv\\Scripts\\python.exe -m pytest tests -q "
                  "(EXIT=0, floor in docs/collab/WORK_PLAN.md)")


def run(root: Path, base: Optional[str], sentinel: Optional[str],
        now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now(timezone.utc).astimezone()
    paper, test_start = check_paper_test(root, now)
    return [
        check_runtime(base), check_frontend(root, base), check_owner_pages(),
        check_auth(base), check_signals(root, now), check_ml(root, now),
        check_sentiment(root), check_pipeline(root, now), check_sentinel(sentinel),
        check_desk_and_broker(root, now), check_paper_mode(root),
        check_kill_switch_and_risk(root), check_reflex(root),
        check_orders(root, test_start), paper, check_suite(),
    ]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Measured, read-only launch verification.")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]),
                    help="the repository whose data/ the runtime uses")
    ap.add_argument("--base-url", default="", help="e.g. http://localhost:8000")
    ap.add_argument("--sentinel-url", default="", help="e.g. http://127.0.0.1:8300")
    ap.add_argument("--json", default="", help="also write the results here (outside --root)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    results = run(root, args.base_url.rstrip("/") or None, args.sentinel_url or None)
    for r in results:
        print(f"{r['status']:<13} {r['check']:<42} {r['evidence']}")
    if args.json:
        out = Path(args.json).resolve()
        if root in out.parents:
            print("refusing to write inside --root: this tool is read-only there", file=sys.stderr)
            return 2
        out.write_text(json.dumps({"measured_at": datetime.now(timezone.utc).isoformat(),
                                   "root": str(root), "results": results}, indent=1),
                       encoding="utf-8")
    return 1 if any(r["status"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
