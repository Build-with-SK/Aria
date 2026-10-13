"""
headline_schema.py
==================
The v1 headline-extraction contract, in one place.

Every other script in this folder imports from here: the labeller, the review
sheet, training, evaluation, inference and the Ollama export. That is the
point of the module — the system prompt and the serialised answer format must
be byte-identical across all of them, and the only way to guarantee that is
to have exactly one copy.

Pure standard library, so it imports on any machine (no torch, no SDK).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

SENTIMENTS = ("positive", "negative", "neutral")

EVENTS = (
    "earnings", "guidance", "mna", "central_bank", "macro_data",
    "commodity_move", "regulation", "legal", "management", "product",
    "rating_change", "other",
)

# Twitter-financial-news-sentiment: 0=bearish, 1=bullish, 2=neutral.
TWITTER_LABELS = {0: "negative", 1: "positive", 2: "neutral"}

SYSTEM_PROMPT = (
    "Extract structured data from a financial news headline. "
    "Reply with one JSON object and nothing else: "
    '{"sentiment": "positive|negative|neutral", '
    '"entities": [{"name": "...", "sentiment": "positive|negative|neutral"}], '
    '"event": "' + "|".join(EVENTS) + '"}. '
    "entities lists companies, indices, commodities or currencies explicitly "
    "named in the headline, by name exactly as written, never as tickers; use [] "
    "if none. Top-level sentiment is the likely effect on the named entities, or "
    "on the broad market if there are none."
)

# JSON Schema for Claude structured outputs (label_v1.py). Enums are enforced
# server-side; validate() below re-checks everything regardless.
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "sentiment": {"type": "string", "enum": list(SENTIMENTS)},
                },
                "required": ["name", "sentiment"],
                "additionalProperties": False,
            },
        },
        "event": {"type": "string", "enum": list(EVENTS)},
    },
    "required": ["sentiment", "entities", "event"],
    "additionalProperties": False,
}

# A name that is only a ticker-shaped token ("RR.", "AAPL", "$TSLA") is the
# failure decision 3 exists to prevent. Upper-case words of 2+ letters are
# also real names (BP, HSBC, GSK), so only flag the clearly ticker-like forms.
_TICKERISH = re.compile(r"^\$[A-Za-z]+$|^[A-Z]{1,5}\.[A-Z]{0,2}$|^[A-Z]+[=^-][A-Z]*$|^\^")


def validate(obj) -> tuple[bool, str]:
    """Strict check against the v1 schema. Returns (ok, reason)."""
    if not isinstance(obj, dict):
        return False, "not an object"
    if set(obj) != {"sentiment", "entities", "event"}:
        return False, f"keys {sorted(obj)}"
    if obj["sentiment"] not in SENTIMENTS:
        return False, f"sentiment {obj['sentiment']!r}"
    if obj["event"] not in EVENTS:
        return False, f"event {obj['event']!r}"
    ents = obj["entities"]
    if not isinstance(ents, list):
        return False, "entities not a list"
    seen = set()
    for e in ents:
        if not isinstance(e, dict) or set(e) != {"name", "sentiment"}:
            return False, "malformed entity"
        name = e["name"]
        if not isinstance(name, str) or not name.strip():
            return False, "empty entity name"
        if e["sentiment"] not in SENTIMENTS:
            return False, f"entity sentiment {e['sentiment']!r}"
        if _TICKERISH.match(name.strip()):
            return False, f"ticker-like entity {name!r}"
        key = canon_name(name)
        if key in seen:
            return False, f"duplicate entity {name!r}"
        seen.add(key)
    return True, ""


def format_label(label: dict) -> str:
    """The exact assistant text the model is trained to emit."""
    return json.dumps(
        {"sentiment": label["sentiment"],
         "entities": [{"name": e["name"], "sentiment": e["sentiment"]}
                      for e in label["entities"]],
         "event": label["event"]},
        ensure_ascii=False,
    )


def messages(text: str, label: dict | None = None) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}]
    if label is not None:
        msgs.append({"role": "assistant", "content": format_label(label)})
    return msgs


def parse_output(raw: str):
    """Model text -> (obj | None, json_ok, schema_ok).

    Takes the first {...} span so a stray trailing token does not count as
    invalid JSON, but anything that does not parse is reported as such."""
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None, False, False
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None, False, False
    ok, _ = validate(obj)
    return obj, True, ok


_SUFFIX = re.compile(r"(?:[\s,]+(?:plc|inc|ltd|group))+\.?$", re.I)


def canon_name(name: str) -> str:
    """Entity-matching key for evaluation: case-insensitive, ignoring
    plc/Inc/Ltd/Group suffixes (the rule the handover specifies)."""
    s = re.sub(r"\s+", " ", name.strip().rstrip(".")).lower()
    s = _SUFFIX.sub("", s).strip()
    return s


def norm_text(text: str) -> str:
    """Key used to deduplicate headlines and join them to gold labels."""
    return re.sub(r"\s+", " ", text).strip().lower()


def append_results(markdown: str, path: Path | None = None) -> Path:
    """Append a dated block to ~/RESULTS.md, the single run log."""
    path = path or Path.home() / "RESULTS.md"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n<!-- {stamp} -->\n{markdown.rstrip()}\n")
    return path
