"""
metrics_v1.py — scoring for eval_v1.py, kept free of torch so it can be
unit-tested anywhere.

`gold` and `preds` are parallel lists: gold labels (v1 schema) and parsed
model outputs (a dict, or None when the output was not valid JSON). A row
whose output fails the schema is scored as wrong on every field it cannot
be read for, never skipped — skipping would flatter the model.
"""
from __future__ import annotations

from collections import Counter

from headline_schema import EVENTS, SENTIMENTS, canon_name, validate

FLIP = {("positive", "negative"), ("negative", "positive")}
INVALID = "(invalid)"


def _field(pred, key):
    if isinstance(pred, dict) and isinstance(pred.get(key), str):
        return pred[key]
    return INVALID


def _entities(pred) -> dict[str, str]:
    """canonical name -> sentiment, tolerating a partly malformed list."""
    out = {}
    if isinstance(pred, dict) and isinstance(pred.get("entities"), list):
        for e in pred["entities"]:
            if isinstance(e, dict) and isinstance(e.get("name"), str):
                out.setdefault(canon_name(e["name"]), e.get("sentiment"))
    return out


def confusion(pairs, labels) -> str:
    """Markdown confusion table, rows = gold, columns = predicted."""
    c = Counter(pairs)
    cols = list(labels) + ([INVALID] if any(p == INVALID for _, p in pairs) else [])
    head = "| gold \\ pred | " + " | ".join(cols) + " |"
    sep = "|---" * (len(cols) + 1) + "|"
    body = ["| " + g + " | " + " | ".join(str(c[(g, p)]) for p in cols) + " |"
            for g in labels if any(gg == g for gg, _ in pairs)]
    return "\n".join([head, sep, *body])


def score(gold: list[dict], preds: list, json_ok: list[bool]) -> dict:
    n = len(gold)
    schema_ok = [p is not None and validate(p)[0] for p in preds]

    sent_pairs = [(g["sentiment"], _field(p, "sentiment")) for g, p in zip(gold, preds)]
    event_pairs = [(g["event"], _field(p, "event")) for g, p in zip(gold, preds)]
    majority, maj_n = Counter(g for g, _ in sent_pairs).most_common(1)[0]

    tp = n_pred = n_gold = ent_sent_ok = ent_matched = 0
    ent_flips, ent_misses = [], []
    for i, (g, p) in enumerate(zip(gold, preds)):
        ge = {canon_name(e["name"]): e["sentiment"] for e in g["entities"]}
        pe = _entities(p)
        n_gold += len(ge)
        n_pred += len(pe)
        for k in ge.keys() & pe.keys():
            tp += 1
            ent_matched += 1
            ent_sent_ok += ge[k] == pe[k]
            if (ge[k], pe[k]) in FLIP:
                ent_flips.append((i, k, ge[k], pe[k]))
        if ge.keys() ^ pe.keys():
            ent_misses.append(i)

    top_flips = [i for i, (g, p) in enumerate(sent_pairs) if (g, p) in FLIP]
    return {
        "n": n,
        "json_ok": sum(json_ok), "schema_ok": sum(schema_ok),
        "sent_acc": sum(g == p for g, p in sent_pairs) / n,
        "majority": majority, "majority_acc": maj_n / n,
        "sent_conf": confusion(sent_pairs, SENTIMENTS),
        "event_acc": sum(g == p for g, p in event_pairs) / n,
        "event_conf": confusion(event_pairs, EVENTS),
        "ent_precision": tp / n_pred if n_pred else 0.0,
        "ent_recall": tp / n_gold if n_gold else 0.0,
        "ent_tp": tp, "ent_pred": n_pred, "ent_gold": n_gold,
        "ent_sent_acc": ent_sent_ok / ent_matched if ent_matched else 0.0,
        "ent_matched": ent_matched,
        "top_flips": top_flips, "ent_flips": ent_flips,
        "ent_mismatch_rows": ent_misses,
        "sent_pairs": sent_pairs, "event_pairs": event_pairs,
    }


def pick_examples(s: dict, k: int = 20) -> list[int]:
    """Every direction flip, then other errors, then correct rows, up to k
    (more than k only if there are more than k flips — all flips are shown)."""
    flips = sorted(set(s["top_flips"]) | {i for i, *_ in s["ent_flips"]})
    errors = [i for i, ((gs, ps), (ge, pe)) in enumerate(zip(s["sent_pairs"], s["event_pairs"]))
              if (gs != ps or ge != pe) and i not in flips]
    errors += [i for i in s["ent_mismatch_rows"] if i not in flips and i not in errors]
    rest = [i for i in range(s["n"]) if i not in flips and i not in errors]
    picked = list(flips)
    for i in errors + rest:
        if len(picked) >= k:
            break
        picked.append(i)
    return picked
