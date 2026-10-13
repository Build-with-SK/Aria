"""
build_ticker_map.py — generate ticker_map.json from TIS's own universe.

    python build_ticker_map.py ../../configs/universe.yaml [--only TICKERS.txt]

Aliases come from two places only:
  1. each universe entry's `name` (plus the name without a trailing share-class
     letter, "Alphabet A" -> "Alphabet");
  2. CURATED below — common headline spellings ("Footsie", "gold", "yen") —
     each kept only if its ticker is actually in the universe.
Bare tickers are never aliases: "AI", "ON", "NOW", "C", "U" and "S" are
tickers in this universe and ordinary words in a headline.

--only restricts the map to a list of tickers (one per line), for when the
user confirms the narrower set TIS actually trades.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ticker_map import DEFAULT_MAP, alias_key  # noqa: E402

# Earlier sections win a name collision.
SECTION_ORDER = ["indices", "equities", "crypto", "forex", "futures",
                 "commodities", "etfs", "fixed_income", "credit"]

CURATED = {
    # indices
    "S&P 500": "^GSPC", "S&P": "^GSPC", "S&P500": "^GSPC",
    "Dow": "^DJI", "Dow Jones": "^DJI", "Dow Jones Industrial Average": "^DJI",
    "Nasdaq": "^IXIC", "FTSE": "^FTSE", "Footsie": "^FTSE",
    "Nikkei": "^N225", "Sensex": "^BSESN", "Nifty": "^NSEI", "VIX": "^VIX",
    # companies by their usual headline name
    "Google": "GOOGL", "Alphabet": "GOOGL", "Facebook": "META",
    "Meta Platforms": "META", "JPMorgan": "JPM", "JP Morgan": "JPM",
    "Citi": "C", "BofA": "BAC", "Goldman": "GS", "Berkshire": "BRK-B",
    # commodities -> front-month futures
    "gold": "GC=F", "silver": "SI=F", "copper": "HG=F",
    "oil": "CL=F", "crude": "CL=F", "crude oil": "CL=F", "WTI": "CL=F",
    "Brent": "BZ=F", "Brent crude": "BZ=F", "natural gas": "NG=F",
    # currencies, quoted as the base leg (sentiment carries over)
    "euro": "EURUSD=X", "sterling": "GBPUSD=X", "pound": "GBPUSD=X",
    "British pound": "GBPUSD=X", "Australian dollar": "AUDUSD=X",
    "Aussie dollar": "AUDUSD=X", "New Zealand dollar": "NZDUSD=X",
    # currencies quoted as the second leg: good news for the currency moves
    # the pair DOWN, so these are also listed in INVERTED.
    "yen": "USDJPY=X", "Japanese yen": "USDJPY=X",
    "Swiss franc": "USDCHF=X", "franc": "USDCHF=X",
    "Canadian dollar": "USDCAD=X", "loonie": "USDCAD=X",
    "Bitcoin": "BTC-USD", "Ether": "ETH-USD",
}
INVERTED = ["yen", "Japanese yen", "Swiss franc", "franc", "Canadian dollar", "loonie"]

_CLASS = re.compile(r"\s+(?:Class\s+)?[A-C]$")


def build(universe_yaml: Path, only: set[str] | None = None) -> dict:
    import yaml
    uni = yaml.safe_load(universe_yaml.read_text(encoding="utf-8"))["universe"]
    unknown = set(uni) - set(SECTION_ORDER)
    if unknown:
        raise SystemExit(f"universe.yaml has sections this script does not rank: {unknown}")

    tickers: set[str] = set()
    aliases: dict[str, str] = {}     # alias text -> ticker
    owner: dict[str, str] = {}       # alias_key -> ticker
    collisions = []
    ambiguous: set[str] = set()

    def add(alias: str, ticker: str, source: str) -> None:
        # Two instruments answering to one name is ambiguity, and guessing
        # between them is how a short fund gets traded as its long twin, so
        # the name maps to nothing unless CURATED settles it.
        k = alias_key(alias)
        if not k:
            return
        if k in owner and owner[k] != ticker:
            collisions.append(f"{alias!r} ({source}): {owner[k]} vs {ticker} -> dropped")
            ambiguous.add(k)
            return
        owner.setdefault(k, ticker)
        aliases.setdefault(alias, ticker)

    for section in SECTION_ORDER:
        for entry in uni.get(section) or []:
            t, name = str(entry["ticker"]), str(entry.get("name") or "")
            if only is not None and t not in only:
                continue
            tickers.add(t)
            if name:
                add(name, t, section)
                base = _CLASS.sub("", name)
                if base != name:
                    add(base, t, section)

    aliases = {a: v for a, v in aliases.items() if alias_key(a) not in ambiguous}

    curated_kept = curated_skipped = 0
    for alias, t in CURATED.items():
        if t in tickers:
            # Curated spellings outrank auto-generated ones by design.
            k = alias_key(alias)
            if k in owner and owner[k] != t and k not in ambiguous:
                collisions.append(f"{alias!r} (curated) overrides {owner[k]} -> {t}")
            aliases = {a: v for a, v in aliases.items() if alias_key(a) != k}
            owner[k] = t
            aliases[alias] = t
            curated_kept += 1
        else:
            curated_skipped += 1

    if only is not None and only - tickers:
        raise SystemExit(f"--only names tickers not in universe.yaml: {sorted(only - tickers)}")

    return {
        "source": str(universe_yaml.name),
        "generated": date.today().isoformat(),
        "n_tickers": len(tickers),
        "aliases": dict(sorted(aliases.items(), key=lambda kv: kv[0].lower())),
        "inverted": [a for a in INVERTED if a in aliases],
        "collisions": collisions,
        "_stats": {"curated_kept": curated_kept, "curated_skipped": curated_skipped},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("universe", type=Path)
    ap.add_argument("--only", type=Path, help="file with one ticker per line")
    ap.add_argument("--out", type=Path, default=DEFAULT_MAP)
    args = ap.parse_args()
    only = None
    if args.only:
        only = {l.strip() for l in args.only.read_text().splitlines()
                if l.strip() and not l.startswith("#")}
    m = build(args.universe, only)
    args.out.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{m['n_tickers']} tickers, {len(m['aliases'])} aliases, "
          f"{len(m['collisions'])} collisions -> {args.out}")
    for c in m["collisions"]:
        print("  collision:", c)


if __name__ == "__main__":
    main()
