"""
train_v1.py — Task 3: LoRA fine-tune Llama 3.1 8B on the v1 headline labels.

    source /scratch/$USER/env.sh
    tmux new -s v1
    /scratch/$USER/venv/bin/python train_v1.py
    # Ctrl+B D to detach; tmux attach -t v1 to come back

Same base model, LoRA settings and train_on_responses_only as v0b. Checkpoints
every 100 steps; rerunning the same command resumes from the latest one.
When it finishes the adapter is copied to ~/headline-v1 (scratch can be wiped).

Written without train_v0b.py to hand — diff the SFTTrainer/SFTConfig block
against v0b before the first run, and prefer v0b's spelling of any argument
this stack version renamed.
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from headline_schema import append_results, messages, validate  # noqa: E402

BASE = "unsloth/Llama-3.1-8B-Instruct-bnb-4bit"
MAX_SEQ = 512
EPOCHS = 2
BATCH, ACCUM = 4, 4          # OOM: close Firefox, then --batch 2 --accum 8
LR = 2e-4
SEED = 3407


def main() -> None:
    user = getpass.getuser()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path.home() / "data" / "v1" / "train.jsonl")
    ap.add_argument("--out", type=Path, default=Path(f"/scratch/{user}/out/headline-v1"))
    ap.add_argument("--backup", type=Path, default=Path.home() / "headline-v1")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--accum", type=int, default=ACCUM)
    args = ap.parse_args()

    if args.backup.exists():
        raise SystemExit(f"{args.backup} already exists. Never overwrite an adapter: "
                         "pass --out/--backup with a new version name (headline-v1b, ...).")

    rows = [json.loads(l) for l in args.data.read_text(encoding="utf-8").splitlines()]
    bad = [i for i, r in enumerate(rows) if not validate(r["label"])[0]]
    if bad:
        raise SystemExit(f"{len(bad)} training rows fail the v1 schema (first: {bad[:5]})")

    # Heavy imports only after the cheap checks pass. unsloth must come first.
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template, train_on_responses_only
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    model, tok = FastLanguageModel.from_pretrained(
        model_name=BASE, max_seq_length=MAX_SEQ, load_in_4bit=True, dtype=None)
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=SEED)
    tok = get_chat_template(tok, chat_template="llama-3.1")

    texts = [tok.apply_chat_template(messages(r["text"], r["label"]), tokenize=False)
             for r in rows]
    longest = max(len(tok(t)["input_ids"]) for t in texts)
    if longest > MAX_SEQ:
        raise SystemExit(f"longest example is {longest} tokens > {MAX_SEQ}; "
                         "it would be truncated mid-answer")
    ds = Dataset.from_dict({"text": texts}).shuffle(seed=SEED)

    steps_per_epoch = math.ceil(len(ds) / (args.batch * args.accum))
    total = steps_per_epoch * EPOCHS
    warmup = max(1, round(0.05 * total))
    print(f"{len(ds)} rows, longest {longest} tokens, {total} steps, warmup {warmup}")

    trainer = SFTTrainer(
        model=model,
        processing_class=tok,
        train_dataset=ds,
        args=SFTConfig(
            dataset_text_field="text",
            max_length=MAX_SEQ,
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.accum,
            num_train_epochs=EPOCHS,
            warmup_steps=warmup,
            learning_rate=LR,
            lr_scheduler_type="linear",
            optim="adamw_8bit",
            weight_decay=0.01,
            logging_steps=10,
            save_strategy="steps",
            save_steps=100,
            save_total_limit=3,
            output_dir=str(args.out),
            seed=SEED,
            report_to="none",
        ),
    )
    trainer = train_on_responses_only(
        trainer,
        instruction_part="<|start_header_id|>user<|end_header_id|>\n\n",
        response_part="<|start_header_id|>assistant<|end_header_id|>\n\n")

    resume = any(args.out.glob("checkpoint-*"))
    if resume:
        print("resuming from the latest checkpoint in", args.out)
    stats = trainer.train(resume_from_checkpoint=True if resume else None)

    model.save_pretrained(str(args.out))
    tok.save_pretrained(str(args.out))
    shutil.copytree(args.out, args.backup,
                    ignore=shutil.ignore_patterns("checkpoint-*"))

    loss = stats.metrics.get("train_loss")
    md = (f"## Task 3 — train {args.out.name}\n"
          f"- data: `{args.data}` ({len(ds)} rows), base `{BASE}`, LoRA r=16 α=16 "
          f"all attn+MLP, responses-only loss\n"
          f"- {EPOCHS} epochs, batch {args.batch}×{args.accum}, lr {LR}, "
          f"warmup {warmup}/{total} steps, max_seq {MAX_SEQ}\n"
          f"- final train loss: {loss:.4f}" + (" (resumed)" if resume else "") + "\n"
          f"- adapter: `{args.out}` → backed up to `{args.backup}`")
    print(md)
    append_results(md)


if __name__ == "__main__":
    main()
