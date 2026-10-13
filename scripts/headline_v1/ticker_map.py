"""
ticker_map.py — Task 5: entity name -> TIS ticker, deterministically.

The model never emits tickers (decision 3). After inference each entity name
goes through normalise(); a name that is not an alias of a ticker in the TIS
universe returns None and is dropped. Nothing is guessed: no fuzzy matching,
no prefix matching, because a near-miss here becomes a trade on the wrong
instrument.

    from ticker_map import normalise
    normalise("Rolls Royce plc")   # -> the mapped ticker, or None

Build ticker_map.json from TIS's configs/universe.yaml with
build_ticker_map.py; this module only reads it.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_MAP = Path(__file__).resolve().parent / "ticker_map.json"

# Corporate-form words that carry no identity. Wider than the eval-matching
# rule in headline_schema.canon_name, on purpose: here we want "Shell plc",
# "Shell", "Shell PLC." and "Shell Group" all to reach the same key.
_FORM = {"plc", "inc", "ltd", "limited", "group", "corp", "corporation",
         "co", "company", "holdings", "holding", "sa", "se", "ag", "nv", "the"}


def alias_key(name: str) -> str:
    s = name.lower().replace("&", " and ").replace("’", "'")
    # A minus before a number is meaning, not punctuation: "-2x Crude Oil"
    # is the short fund, "2x Crude Oil" the long one.
    s = re.sub(r"(?<![a-z0-9])[-−–](?=\d)", " minus ", s)
    s = re.sub(r"'s\b", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = s.split()
    # Strip form words from the ends only, never from the middle
    # ("The Trade Desk" -> "trade desk", "Bank of New York Mellon" intact).
    while words and words[0] in _FORM:
        words.pop(0)
    while words and words[-1] in _FORM:
        words.pop()
    return " ".join(words)


FLIP = {"positive": "negative", "negative": "positive", "neutral": "neutral"}


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[dict[str, str], frozenset[str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    aliases = {alias_key(k): v for k, v in data["aliases"].items()}
    inverted = frozenset(alias_key(k) for k in data.get("inverted", []))
    return aliases, inverted


def normalise(name: str, path: Path | str = DEFAULT_MAP) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    return _load(str(path))[0].get(alias_key(name))


def is_inverted(name: str, path: Path | str = DEFAULT_MAP) -> bool:
    """True when good news for `name` is bad news for its ticker — a currency
    quoted as the second leg of its pair ("yen" -> USDJPY=X)."""
    return alias_key(name) in _load(str(path))[1]


def map_entities(entities: list[dict], path: Path | str = DEFAULT_MAP) -> list[dict]:
    """Model entities -> [{name, sentiment, ticker, ticker_sentiment}],
    unmatched names dropped. `ticker_sentiment` is the direction for the
    ticker itself, which differs from `sentiment` for inverted currency legs.
    Two names reaching the same ticker keep the first."""
    out, seen = [], set()
    for e in entities:
        name = e.get("name", "")
        t = normalise(name, path)
        if t and t not in seen:
            seen.add(t)
            s = e.get("sentiment")
            out.append({**e, "ticker": t,
                        "ticker_sentiment": FLIP.get(s, s) if is_inverted(name, path) else s})
    return out
