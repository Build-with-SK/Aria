"""
scripts/stamp_signal_identity.py
================================
Attach provider identity to every row of data/signals.json. Additive only.

WHY
---
`export_rich_signals()` in main.py now stamps `provider_symbol`, `venue` and
`price_unit` onto each signal as it is written, so identity travels with the
price instead of being re-derived downstream. The file on disk predates that
change, and regenerating it means a full market pipeline run — which would also
change every price, so it cannot be used to prove that only identity changed.

So this backfills the identity fields onto the EXISTING file and proves it
touched nothing else: a digest over every pre-existing key is taken before and
after, and the script refuses to write if it moved.

WHAT IT IS ALLOWED TO WRITE
---------------------------
    provider_symbol   the string the price was actually fetched with
    venue             where that instrument trades
    price_unit        the unit the price is quoted in (USD, GBp, INR, ...)
    identity_basis    how those were established

No price, score, action, confidence or explanation is touched. A signal key IS
its provider symbol — configs/universe.yaml holds the exact strings
`data_downloader` hands to the provider — so nothing here is a guess.

USAGE
-----
    python scripts/stamp_signal_identity.py            # dry run
    python scripts/stamp_signal_identity.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.identity import signal_identity          # noqa: E402

SIGNALS = ROOT / "data" / "signals.json"

ADDITIVE_FIELDS = ("provider_symbol", "venue", "price_unit", "identity_basis")


def _digest(data: dict) -> str:
    """A digest over everything EXCEPT the fields this script may add.

    Anything else moving means the script did something it was not asked to.
    """
    trimmed = {
        k: {kk: vv for kk, vv in v.items() if kk not in ADDITIVE_FIELDS}
        for k, v in data.items() if isinstance(v, dict)
    }
    return hashlib.sha256(
        json.dumps(trimmed, sort_keys=True, default=str).encode()).hexdigest()


def build(data: dict) -> tuple[dict, list[dict]]:
    out, plan = {}, []
    for ticker, sig in data.items():
        if not isinstance(sig, dict):
            out[ticker] = sig
            continue
        ident = signal_identity(ticker)
        row = dict(sig)
        row["provider_symbol"] = ident.provider_symbol
        row["venue"] = ident.exchange
        row["price_unit"] = ident.currency
        row["identity_basis"] = ident.reason
        out[ticker] = row
        plan.append({"ticker": ticker, "provider_symbol": ident.provider_symbol,
                     "venue": ident.exchange, "price_unit": ident.currency,
                     "price": sig.get("current_price"),
                     "status": ident.status})
    return out, plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    data = json.loads(SIGNALS.read_text(encoding="utf-8"))
    before = _digest(data)
    stamped, plan = build(data)
    after = _digest(stamped)

    unresolved = [p for p in plan if p["status"] != "RESOLVED"]
    by_unit: dict[str, int] = {}
    for p in plan:
        by_unit[str(p["price_unit"])] = by_unit.get(str(p["price_unit"]), 0) + 1

    print("=" * 74)
    print("SIGNAL IDENTITY STAMP")
    print("=" * 74)
    print(f"file              : {SIGNALS}")
    print(f"rows              : {len(plan)}")
    print(f"unresolved        : {len(unresolved)}")
    print(f"digest unchanged  : {before == after}")
    print()
    print("price units:")
    for unit, n in sorted(by_unit.items(), key=lambda kv: -kv[1]):
        print(f"   {unit:<8} {n:>5}")
    print()
    print("the four colliding tickers:")
    for t in ("BA", "AAL", "JD", "EDV"):
        row = next((p for p in plan if p["ticker"] == t), None)
        if row:
            print(f"   {t:<5} -> provider {row['provider_symbol']:<8} "
                  f"{row['venue']:<6} {row['price_unit']:<5} price {row['price']}")
    print()
    if unresolved:
        print(f"UNRESOLVED ({len(unresolved)}) — stamped as null, never guessed:")
        for p in unresolved[:15]:
            print(f"   {p['ticker']}")
        print()

    if before != after:
        print("REFUSING: a non-identity field changed. Nothing written.")
        return 1
    if not args.apply:
        print("DRY RUN - nothing written. Re-run with --apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ROOT / "data" / "backups" / f"signals-identity-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SIGNALS, backup / SIGNALS.name)
    SIGNALS.write_text(json.dumps(stamped, indent=1, default=str), encoding="utf-8")
    print(f"APPLIED — {len(plan)} rows stamped")
    print(f"   backup  : {backup}")
    print(f"   restore : copy \"{backup / SIGNALS.name}\" \"{SIGNALS}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
