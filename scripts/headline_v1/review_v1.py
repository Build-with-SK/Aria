"""
review_v1.py — Checkpoint A: human review of the v1 test set.

    python review_v1.py            # test.jsonl -> test_review.csv (+ flag count)
    python review_v1.py --apply    # reviewed csv -> test_reviewed.jsonl

The reviewer opens test_review.csv (Excel / LibreOffice is fine), looks at
every flagged row at least, and for any wrong label pastes the corrected JSON
into `corrected_label`. Rows with `drop` set to y/yes/1/x are removed (an
unusable headline). --apply then writes test_reviewed.jsonl, the only file
eval_v1.py treats as giving final scores.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from headline_schema import append_results, canon_name, validate  # noqa: E402

OPPOSITE = {("positive", "negative"), ("negative", "positive")}
FLAG_COLS = ["flag_entity_not_in_text", "flag_event_other",
             "flag_entity_opposite", "flag_gold_disagree"]


def flags(row: dict) -> dict[str, str]:
    """The four automatic flags from the handover; value is a short reason."""
    text, label = row["text"], row["label"]
    low = text.lower()
    missing = [e["name"] for e in label["entities"]
               if e["name"].lower() not in low and canon_name(e["name"]) not in low]
    opposite = [e["name"] for e in label["entities"]
                if (e["sentiment"], label["sentiment"]) in OPPOSITE]
    gold = row.get("gold_sentiment")
    return {
        "flag_entity_not_in_text": "; ".join(missing),
        "flag_event_other": "other" if label["event"] == "other" else "",
        "flag_entity_opposite": "; ".join(opposite),
        "flag_gold_disagree": (f"gold={gold}" if gold and gold != label["sentiment"] else ""),
    }


def write_review(test_path: Path, csv_path: Path) -> tuple[int, int]:
    rows = [json.loads(l) for l in test_path.read_text(encoding="utf-8").splitlines()]
    n_flagged = 0
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel reads UTF-8
        w = csv.writer(f)
        w.writerow(["id", "headline", "label", "gold_sentiment", *FLAG_COLS,
                    "flagged", "corrected_label", "drop", "notes"])
        for i, r in enumerate(rows):
            fl = flags(r)
            hit = any(fl.values())
            n_flagged += hit
            w.writerow([i, r["text"], json.dumps(r["label"], ensure_ascii=False),
                        r.get("gold_sentiment", ""), *(fl[c] for c in FLAG_COLS),
                        "Y" if hit else "", "", "", ""])
    return len(rows), n_flagged


def apply_review(test_path: Path, csv_path: Path, out_path: Path) -> str:
    rows = [json.loads(l) for l in test_path.read_text(encoding="utf-8").splitlines()]
    corrected = dropped = 0
    errors = []
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        sheet = {int(r["id"]): r for r in csv.DictReader(f)}
    if len(sheet) != len(rows):
        raise SystemExit(f"{csv_path} has {len(sheet)} rows, test set has {len(rows)}")
    out = []
    for i, r in enumerate(rows):
        s = sheet[i]
        if s["headline"].strip() != r["text"].strip():
            raise SystemExit(f"row {i}: headline in the sheet does not match test.jsonl")
        if s["drop"].strip().lower() in {"y", "yes", "1", "x", "true"}:
            dropped += 1
            continue
        fix = s["corrected_label"].strip()
        if fix:
            try:
                obj = json.loads(fix)
            except json.JSONDecodeError as e:
                errors.append(f"row {i}: corrected_label is not JSON ({e.msg})")
                continue
            ok, why = validate(obj)
            if not ok:
                errors.append(f"row {i}: corrected_label fails the schema ({why})")
                continue
            r = {**r, "label": obj}
            corrected += 1
        out.append(r)
    if errors:
        raise SystemExit("Fix these in the sheet, then rerun --apply:\n  " + "\n  ".join(errors))
    with out_path.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return (f"## Checkpoint A — test set reviewed\n"
            f"- {len(rows)} rows reviewed: {corrected} corrected, {dropped} dropped, "
            f"{len(out)} kept → `{out_path}`")


def main() -> None:
    d = Path.home() / "data" / "v1"
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=Path, default=d / "test.jsonl")
    ap.add_argument("--csv", type=Path, default=d / "test_review.csv")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.apply:
        md = apply_review(args.test, args.csv, args.test.with_name("test_reviewed.jsonl"))
    else:
        if args.csv.exists():
            raise SystemExit(f"{args.csv} already exists; it may hold the user's "
                             "review. Move it aside first.")
        n, k = write_review(args.test, args.csv)
        md = (f"## Checkpoint A — review sheet written\n"
              f"- `{args.csv}`: {n} rows, **{k} flagged** for review. "
              "Final scores wait for the user's confirmation.")
    print(md)
    append_results(md)


if __name__ == "__main__":
    main()
