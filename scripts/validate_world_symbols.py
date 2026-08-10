"""
scripts/validate_world_symbols.py
=================================
Prove that every curated world symbol is real before it reaches the universe.

`src/data/world_symbols.py` is hand-maintained, and its module docstring sets
the rule the rest of the system depends on: "real, verifiable names only; no
fabricated tickers." A hand-typed ticker list is exactly the kind of thing that
looks authoritative and quietly is not — a transposed digit in a Tokyo or Riyadh
code produces a symbol that is syntactically perfect, silently unpriceable, and
indistinguishable from a real one by reading the file.

The failure mode matters more than it looks. A bad symbol does not crash
anything: the research modules abstain when they cannot load history, which is
correct behaviour, so a dead ticker shows up as a permanently abstaining
instrument that nobody investigates. Ten of those across a market make that
market look thin rather than mistyped.

So: ask the vendor. Every symbol is fetched in batches; anything that returns no
usable price history is reported for removal. Duplicates within an exchange are
reported too, because a name listed twice is silently double-weighted in any
breadth or index-level calculation built on the list.

    venv/bin/python scripts/validate_world_symbols.py            # report only
    venv/bin/python scripts/validate_world_symbols.py --json out.json

This is a maintenance script, not a runtime dependency: nothing imports it, and
the universe never calls it. It exists so that a human editing the curated lists
can check their work in one command.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

BATCH = 40


def _collect() -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """Symbols grouped by exchange, plus any duplicate report."""
    from src.data import world_symbols as ws

    by_exchange: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen: dict[str, str] = {}
    duplicates: list[str] = []

    for display, yahoo, name, exchange, currency, asset_class in ws.all_world_symbols():
        if yahoo in seen:
            duplicates.append(f"{yahoo} ({name}) — already listed as '{seen[yahoo]}'")
            continue
        seen[yahoo] = name
        by_exchange[exchange].append((yahoo, name))
    return by_exchange, duplicates


def _price_check(symbols: list[str]) -> set[str]:
    """Symbols the vendor could actually price. Batched to keep the request
    count sane; a batch that fails wholesale is retried one symbol at a time so
    a single bad ticker cannot condemn the thirty-nine next to it."""
    import warnings

    import yfinance as yf

    ok: set[str] = set()
    warnings.filterwarnings("ignore")

    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        try:
            df = yf.download(chunk, period="1mo", interval="1d",
                             progress=False, threads=True, auto_adjust=True)
        except Exception:
            df = None

        if df is None or df.empty:
            for s in chunk:                     # fall back to one at a time
                ok |= _price_check_single(s)
            continue

        for s in chunk:
            try:
                col = df["Close"][s] if len(chunk) > 1 else df["Close"]
                if col.notna().sum() > 0:
                    ok.add(s)
            except Exception:
                pass
        print(f"  … checked {min(i + BATCH, len(symbols))}/{len(symbols)}", flush=True)
    return ok


def _price_check_single(symbol: str) -> set[str]:
    import yfinance as yf
    try:
        df = yf.download(symbol, period="1mo", interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and not df.empty and df["Close"].notna().sum().sum() > 0:
            return {symbol}
    except Exception:
        pass
    return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the full result to this path")
    ap.add_argument("--exchange", help="validate only this exchange label")
    args = ap.parse_args()

    by_exchange, duplicates = _collect()
    if args.exchange:
        by_exchange = {k: v for k, v in by_exchange.items() if k == args.exchange}

    all_symbols = [s for rows in by_exchange.values() for s, _ in rows]
    print(f"validating {len(all_symbols)} curated world symbols "
          f"across {len(by_exchange)} exchanges\n")

    ok = _price_check(all_symbols)

    result, total_bad = {}, 0
    for exchange in sorted(by_exchange):
        rows = by_exchange[exchange]
        bad = [(s, n) for s, n in rows if s not in ok]
        total_bad += len(bad)
        result[exchange] = {
            "n": len(rows), "n_ok": len(rows) - len(bad),
            "unpriceable": [{"symbol": s, "name": n} for s, n in bad],
        }
        flag = "OK " if not bad else "!! "
        print(f"{flag}{exchange:<14} {len(rows) - len(bad):>3}/{len(rows):<3} priced"
              + (f"   → drop: {', '.join(s for s, _ in bad)}" if bad else ""))

    if duplicates:
        print("\nDUPLICATES (listed more than once — silently double-weighted):")
        for d in duplicates:
            print(f"  {d}")

    print(f"\n{len(ok)}/{len(all_symbols)} priced · {total_bad} unpriceable "
          f"· {len(duplicates)} duplicate")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"exchanges": result, "duplicates": duplicates}, indent=2), encoding="utf-8")
        print(f"written to {args.json}")

    return 1 if (total_bad or duplicates) else 0


if __name__ == "__main__":
    raise SystemExit(main())
