"""
scripts/backfill_outcomes.py
============================
Turn the desk's debate archive into a labelled outcome set.

WHY THIS EXISTS
---------------
The teacher grades CLOSED trades, and only 3 have closed. That is not a training
set — it is below the Track Record module's own floor of 20 resolved calls, and
training on it would manufacture exactly the false confidence the V5 work was
built to refuse.

But the judge records a directional `view` and a `conviction` on every debate,
not only on the ones that became trades. A view is a falsifiable claim: it can be
checked against what the price actually did next, whether or not anyone acted on
it. That is the signal this script harvests.

WHAT IT REFUSES TO PRETEND
--------------------------
The archive is 434 debates over 16 days across 20 tickers. Those are NOT 434
independent observations, and reporting them as such would produce a beautifully
narrow confidence interval that means nothing:

  * 44 debates on the same ticker inside 16 days overlap almost completely. The
    5-day forward return starting Monday and the one starting Tuesday share four
    days of the same price path. Counting both as independent is the classic way
    to fake statistical power.
  * 20 tickers, over half of them crypto, in a single regime. Crypto co-moves;
    on a risk-off day every one of them is wrong together. That is one bet, not
    twenty.

So the script reports BOTH the naive count and a cluster-aware effective sample
size, and it computes its Wilson interval on the effective n. If the honest
interval is too wide to conclude anything, that is the finding.

USAGE
-----
    venv\\Scripts\\python.exe scripts\\backfill_outcomes.py                 # report only
    venv\\Scripts\\python.exe scripts\\backfill_outcomes.py --write         # write the JSONL
    venv\\Scripts\\python.exe scripts\\backfill_outcomes.py --horizon 5     # pick a horizon
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DEBATES = ROOT / "data" / "desk" / "debates"
OUT_FILE = ROOT / "data" / "desk" / "backfilled_outcomes.jsonl"

# A view only counts as directional if it commits to a side.
BULL = {"bull", "bullish", "long", "buy"}
BEAR = {"bear", "bearish", "short", "sell"}

# Horizons in trading days. Anything longer than the archive's own span cannot
# resolve for most of the corpus, which is why 1/5/10 and not 252.
HORIZONS = (1, 5, 10)


# ── loading ──────────────────────────────────────────────────────────────────

def load_debates() -> list[dict]:
    out = []
    for p in sorted(DEBATES.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        judge = d.get("judge") or {}
        view = str(judge.get("view") or "").strip().lower()
        at = d.get("at")
        tic = d.get("ticker")
        if not (at and tic and view):
            continue
        out.append({
            "id": d.get("id") or p.stem,
            "ticker": tic,
            "at": at,
            "view": view,
            "conviction": judge.get("conviction"),
            "conviction_bar": judge.get("conviction_bar"),
            "verdict": judge.get("verdict"),
            "regime": judge.get("regime"),
            "invalidation_level": judge.get("invalidation_level"),
        })
    return out


def direction_of(view: str) -> str | None:
    if view in BULL:
        return "bull"
    if view in BEAR:
        return "bear"
    return None      # neutral / hold / unknown — not a falsifiable claim


# ── prices ───────────────────────────────────────────────────────────────────

def price_frames(tickers: list[str]) -> dict:
    """One history fetch per ticker, reusing V5's own cached market data so this
    does not hammer the vendor and sees exactly what the modules see."""
    from src.v5 import marketdata
    frames = {}
    for t in sorted(set(tickers)):
        try:
            df = marketdata.history(t, period="1y", interval="1d")
        except Exception as e:
            print(f"  ! {t}: history failed ({e})")
            df = None
        if df is not None and not df.empty and "Close" in df:
            # tz-naive index so comparisons against parsed timestamps are safe
            try:
                df = df.tz_localize(None)
            except (TypeError, AttributeError):
                try:
                    df.index = df.index.tz_localize(None)
                except Exception:
                    pass
            frames[t] = df
        else:
            print(f"  ! {t}: no usable history — its debates will be skipped")
    return frames


def forward_return(df, when: datetime, horizon: int):
    """Return (entry_close, exit_close, pct) for the first bar STRICTLY AFTER the
    debate's calendar day, and the bar `horizon` trading days later.

    Entering on the next bar, not the same one, is the whole ballgame. A debate
    timestamped 15:41 is inside the session; grading it against that same day's
    close would let the judge's view be scored on a move that had already partly
    happened when it spoke. That is look-ahead bias, and it flatters every number
    downstream. Next-bar entry is the conservative, defensible choice: you learn
    of the view, you act at the next close.

    None if the window is incomplete — an unresolved outcome must never be
    silently treated as a resolved one.
    """
    import pandas as pd

    idx = df.index
    # Debate timestamps carry microseconds; a daily index may be a coarser unit
    # and pandas refuses the lossy comparison. Normalise to the calendar day.
    ts = pd.Timestamp(when)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    ts = ts.normalize()
    try:
        ts = ts.as_unit(idx.unit)
    except (AttributeError, ValueError):
        pass

    # side='right' → first bar after that whole day, never the same one.
    pos = idx.searchsorted(ts, side="right")
    if pos >= len(idx):
        return None                      # debate is newer than the price data
    exit_pos = pos + horizon
    if exit_pos >= len(idx):
        return None                      # horizon has not elapsed yet
    try:
        entry = float(df["Close"].iloc[pos])
        exit_ = float(df["Close"].iloc[exit_pos])
    except Exception:
        return None
    if not (math.isfinite(entry) and math.isfinite(exit_)) or entry == 0:
        return None                      # never divide by a zero/NaN entry
    return entry, exit_, (exit_ - entry) / entry * 100.0


# ── honest statistics ────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96):
    """Wilson score interval. Used on the EFFECTIVE n, not the raw count."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(max(p * (1 - p) / n + z * z / (4 * n * n), 0.0)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def effective_n(rows: list[dict], horizon: int) -> float:
    """Cluster-aware sample size.

    Two corrections, both deliberately conservative:

    1. Overlap. Within one ticker, calls closer together than the horizon share
       most of their price path. Count a ticker's calls as roughly
       span_days / horizon independent windows, never more than the raw count.
    2. Cross-ticker correlation. Crypto moves as a bloc; treating each coin as an
       independent bet overstates power. Crypto tickers are pooled and counted as
       sqrt(k) independent names rather than k.
    """
    by_ticker: dict[str, list[datetime]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(datetime.fromisoformat(r["at"]))

    per_ticker = {}
    for t, times in by_ticker.items():
        if not times:
            continue
        span = (max(times) - min(times)).days + 1
        windows = max(1.0, span / max(horizon, 1))
        per_ticker[t] = min(float(len(times)), windows)

    crypto = {t: v for t, v in per_ticker.items() if t.upper().endswith("-USD")}
    other = {t: v for t, v in per_ticker.items() if t not in crypto}

    eff = sum(other.values())
    if crypto:
        # k correlated names contribute ~sqrt(k) names' worth of independence.
        mean_per_name = sum(crypto.values()) / len(crypto)
        eff += mean_per_name * math.sqrt(len(crypto))
    return eff


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, help="trading days forward")
    ap.add_argument("--write", action="store_true", help="write the JSONL")
    args = ap.parse_args()

    debates = load_debates()
    print(f"debates with a judge view : {len(debates)}")

    directional = [d for d in debates if direction_of(d["view"])]
    print(f"directional (bull/bear)   : {len(directional)}")
    print(f"non-directional, skipped  : {len(debates) - len(directional)}")
    if not directional:
        print("\nNothing falsifiable to grade. Stopping.")
        return

    print(f"\nfetching prices for {len(set(d['ticker'] for d in directional))} tickers…")
    frames = price_frames([d["ticker"] for d in directional])

    rows, unresolved = [], 0
    for d in directional:
        df = frames.get(d["ticker"])
        if df is None:
            continue
        when = datetime.fromisoformat(d["at"])
        fr = forward_return(df, when, args.horizon)
        if fr is None:
            unresolved += 1
            continue
        entry, exit_, pct = fr
        want = direction_of(d["view"])
        correct = (pct > 0) if want == "bull" else (pct < 0)
        rows.append({**d,
                     "horizon_days": args.horizon,
                     "entry": round(entry, 6),
                     "exit": round(exit_, 6),
                     "return_pct": round(pct, 4),
                     "predicted": want,
                     "correct": bool(correct)})

    print(f"resolved                  : {len(rows)}")
    print(f"not yet resolvable        : {unresolved}")
    if not rows:
        print("\nNothing resolved at this horizon. Stopping.")
        return

    k = sum(1 for r in rows if r["correct"])
    n = len(rows)
    eff = effective_n(rows, args.horizon)
    eff_k = k * (eff / n)

    print("\n" + "=" * 62)
    print(f"HORIZON {args.horizon}d")
    print("=" * 62)
    print(f"naive hit rate      : {k}/{n} = {k/n:6.1%}")
    lo, hi = wilson(k, n)
    print(f"  naive Wilson 95%  : [{lo:.1%}, {hi:.1%}]   <- OVERSTATES certainty")
    print(f"effective sample n  : {eff:.1f}  (from {n} raw calls)")
    lo_e, hi_e = wilson(round(eff_k), round(eff))
    print(f"  honest Wilson 95% : [{lo_e:.1%}, {hi_e:.1%}]")

    beats = lo_e > 0.5
    print(f"\nbeats a coin flip at 95%? {'YES' if beats else 'NO — interval includes 50%'}")

    by_t = Counter(r["ticker"] for r in rows)
    print(f"\nticker concentration: {len(by_t)} names, top: {by_t.most_common(5)}")
    vd = Counter(r["verdict"] for r in rows)
    print(f"verdicts            : {dict(vd)}")

    floor = 20
    if eff < floor:
        print(f"\n!! effective n {eff:.1f} is below the project's own floor of {floor}.")
        print("   This is a pipeline-validation set, NOT a basis for retraining weights.")

    if args.write:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with OUT_FILE.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        print(f"\nwrote {len(rows)} labelled rows -> {OUT_FILE.relative_to(ROOT)}")
    else:
        print("\n(dry run — pass --write to save)")


if __name__ == "__main__":
    main()
