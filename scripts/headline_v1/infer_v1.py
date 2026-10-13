"""
infer_v1.py — run a v1 adapter on headlines and map entities to TIS tickers.

    /scratch/$USER/venv/bin/python infer_v1.py ~/headline-v1 "Shell beats, BP misses"

Prints one JSON object per headline: the model's extraction plus a `tickers`
list from ticker_map.map_entities (unmatched names are dropped, never guessed).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_v1 import generate_all  # noqa: E402
from headline_schema import parse_output  # noqa: E402
from ticker_map import map_entities  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    adapter, heads = Path(sys.argv[1]).expanduser(), sys.argv[2:]
    for text, raw in zip(heads, generate_all(adapter, heads)):
        obj, json_ok, schema_ok = parse_output(raw)
        out = {"text": text, "raw": raw.strip(), "schema_ok": schema_ok}
        if schema_ok:
            out["extraction"] = obj
            out["tickers"] = map_entities(obj["entities"])
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
