"""
eval_v1.py — Task 4: score a v1 adapter on a labelled test file.

    /scratch/$USER/venv/bin/python eval_v1.py ~/headline-v1 ~/data/v1/test_reviewed.jsonl

Scores are marked PROVISIONAL unless the test file is test_reviewed.jsonl,
i.e. the set the user signed off at Checkpoint A. Raw predictions go to
~/data/v1/preds_<adapter>.jsonl so a result can be re-examined without the GPU.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from headline_schema import append_results, messages, parse_output  # noqa: E402
from metrics_v1 import pick_examples, score  # noqa: E402

MAX_NEW = 200


def generate_all(adapter: Path, texts: list[str]) -> list[str]:
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template
    import torch

    model, tok = FastLanguageModel.from_pretrained(
        model_name=str(adapter), max_seq_length=512, load_in_4bit=True, dtype=None)
    tok = get_chat_template(tok, chat_template="llama-3.1")
    FastLanguageModel.for_inference(model)

    outs = []
    for i, text in enumerate(texts):
        # Render to text, then tokenise without special tokens: the template
        # already starts with <|begin_of_text|>, and this sidesteps the
        # return-type change of apply_chat_template across transformers versions.
        prompt = tok.apply_chat_template(messages(text), add_generation_prompt=True,
                                         tokenize=False)
        ids = tok(prompt, return_tensors="pt", add_special_tokens=False)["input_ids"].to(model.device)
        with torch.no_grad():
            gen = model.generate(input_ids=ids, max_new_tokens=MAX_NEW, do_sample=False,
                                 pad_token_id=tok.eos_token_id)
        outs.append(tok.decode(gen[0, ids.shape[1]:], skip_special_tokens=True))
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(texts)}", flush=True)
    return outs


def report(name: str, test_path: Path, rows: list[dict], raws: list[str]) -> str:
    parsed = [parse_output(r) for r in raws]
    preds = [p for p, _, _ in parsed]
    s = score([r["label"] for r in rows], preds, [j for _, j, _ in parsed])
    n = s["n"]
    final = test_path.name == "test_reviewed.jsonl"
    f = lambda x: f"{100 * x:.1f}%"   # noqa: E731

    out = [
        f"## Task 4 — eval {name} on `{test_path.name}` ({n} rows)"
        + ("" if final else " — **PROVISIONAL: test set not yet reviewed**"),
        f"- valid JSON: {s['json_ok']}/{n} ({f(s['json_ok'] / n)}); "
        f"schema-valid: {s['schema_ok']}/{n} ({f(s['schema_ok'] / n)})",
        f"- top-level sentiment accuracy: **{f(s['sent_acc'])}** "
        f"(always-{s['majority']} baseline {f(s['majority_acc'])})",
        "", s["sent_conf"], "",
        f"- event accuracy: **{f(s['event_acc'])}**", "", s["event_conf"], "",
        f"- entities: precision **{f(s['ent_precision'])}** ({s['ent_tp']}/{s['ent_pred']}), "
        f"recall **{f(s['ent_recall'])}** ({s['ent_tp']}/{s['ent_gold']})",
        f"- per-entity sentiment accuracy on matched entities: "
        f"{f(s['ent_sent_acc'])} ({s['ent_matched']} matched)",
        f"- direction flips (positive↔negative): top-level **{len(s['top_flips'])}**, "
        f"per-entity **{len(s['ent_flips'])}**",
        "", "### Examples (every flip first)", "",
        "| # | headline | gold | model output |", "|---|---|---|---|",
    ]
    cell = lambda x: str(x).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    for i in pick_examples(s):
        out.append(f"| {i} | {cell(rows[i]['text'])} | "
                   f"{cell(json.dumps(rows[i]['label'], ensure_ascii=False))} | {cell(raws[i].strip())} |")
    return "\n".join(out)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    adapter, test_path = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    rows = [json.loads(l) for l in test_path.read_text(encoding="utf-8").splitlines()]
    raws = generate_all(adapter, [r["text"] for r in rows])

    preds_path = Path.home() / "data" / "v1" / f"preds_{adapter.name}_{test_path.stem}.jsonl"
    with preds_path.open("w", encoding="utf-8") as fh:
        for r, raw in zip(rows, raws):
            fh.write(json.dumps({"text": r["text"], "gold": r["label"], "raw": raw},
                                ensure_ascii=False) + "\n")

    md = report(adapter.name, test_path, rows, raws) + f"\n\nraw predictions: `{preds_path}`"
    print(md)
    append_results(md)


if __name__ == "__main__":
    main()
