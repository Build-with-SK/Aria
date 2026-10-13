# Headline extraction v1: runbook for otter65

The scripts here carry out Tasks 2–6 of the headline-model handover. They were
written and unit-tested off the lab machine (`tests/test_headline_v1.py`).
Nothing in this folder has run on the GPU or against the API yet, so treat
the first run of each step as its test.

## 0. Get the files onto otter65

```bash
# on otter65; no-clobber copy, so nothing already in ~ is overwritten
git clone -b claude/aria-headline-extraction-gyt94p <repo-url> /scratch/$USER/aria
cp -n /scratch/$USER/aria/scripts/headline_v1/*.py \
      /scratch/$USER/aria/scripts/headline_v1/ticker_map.json ~/
PY=/scratch/$USER/venv/bin/python
```

None of these names collide with the existing `train_v0*.py`, `eval_v0.py`,
`infer.py` or `collect_all.py`.

## Task 1: collect (unchanged; uses the existing script)

```bash
$PY -c "import feedparser" || $PY -m pip install feedparser
$PY ~/collect_all.py && sort -u ~/headlines.txt | wc -l     # need ≥ 5,000
```

## Task 2: label

```bash
$PY -m pip install anthropic          # if missing
# the user runs this in the shell; it is never written to a file:
export ANTHROPIC_API_KEY=...
tmux new -s label
$PY ~/label_v1.py                     # resumable; rerun the same command after a drop
```

- At most 12,000 headlines are sent. More than that needs `--allow-over-cap`,
  and only after asking the user.
- Each result is appended to `~/data/v1/labelled_raw.jsonl` as it arrives.
  Failed rows are retried on a rerun.
- The run prints, and appends to `~/RESULTS.md`, the distributions, the
  agreement with the twitter human labels, the count of dropped rows, and a
  warning if one class is over 60%.
- `--split-only` rebuilds the train/test files from the raw file without any
  API calls.

## ⛔ Checkpoint A

```bash
$PY ~/review_v1.py          # writes ~/data/v1/test_review.csv and reports the flagged count
```

The user reviews the sheet. They paste fixed JSON into `corrected_label`, or
mark `drop`, then:

```bash
$PY ~/review_v1.py --apply  # writes ~/data/v1/test_reviewed.jsonl (rejects invalid fixes)
```

`eval_v1.py` marks every score PROVISIONAL unless the test file is
`test_reviewed.jsonl`.

## Task 3: train (can start before review)

**First diff the `SFTTrainer(...)`/`SFTConfig(...)` block against
`~/train_v0b.py`.** `train_v1.py` was written without v0b to hand. Where this
stack version spells an argument differently (`max_length` vs
`max_seq_length`, `processing_class` vs `tokenizer`), use v0b's spelling.

```bash
nvidia-smi                       # stop if someone else is on the GPU
source /scratch/$USER/env.sh
tmux new -s v1
$PY ~/train_v1.py                # 2 epochs, checkpoints every 100 steps, auto-resume
# OOM: close Firefox, then  $PY ~/train_v1.py --batch 2 --accum 8
```

When training finishes, the adapter is copied to `~/headline-v1`. The script
refuses to overwrite an existing adapter. For a new run, pass
`--out /scratch/$USER/out/headline-v1b --backup ~/headline-v1b`.

## Task 4: evaluate

```bash
$PY ~/eval_v1.py ~/headline-v1 ~/data/v1/test_reviewed.jsonl   # test.jsonl = provisional
$PY ~/eval_v0.py ~/sentiment-v0b                                # reference, same RESULTS.md
```

The v0b score isn't appended to `RESULTS.md` automatically. Paste its
summary in by hand.

Note: v1's test set contains twitter **train** headlines, which v0b was
trained on. Don't score v0b on the v1 test set; the comparison would be
contaminated.

## Task 5: ticker map

`ticker_map.json` is built from TIS's own `configs/universe.yaml`:
766 tickers, 814 aliases.

```bash
python build_ticker_map.py ../../configs/universe.yaml                    # full universe
python build_ticker_map.py ../../configs/universe.yaml --only tis.txt     # narrower list
cp ticker_map.json ~/                                                     # ticker_map.py reads the copy next to it
```

**Blocked on the user.** The handover expected 26–82 tickers, but the
universe file holds 766. Confirm which set the map should cover. If it's the
narrower one, provide the list as `tis.txt`, one ticker per line.

Rules the map follows (all pinned by tests):

- Bare tickers are never aliases, because `AI`, `ON`, `NOW`, `C`, `U` and `S`
  are ordinary words.
- There's no fuzzy matching. An unknown name returns `None`.
- A name shared by two instruments maps to nothing unless the curated list
  settles it. This is why "-3x" funds no longer resolve to their long twins.
- `yen`, `Swiss franc` and `Canadian dollar` are second-leg currencies. For
  these, `map_entities` sets `ticker_sentiment` opposite to the entity
  sentiment.
- Rolls-Royce is **not** in the universe, so it maps to `None`.

## Task 6: export

```bash
$PY ~/export_v1.py ~/headline-v1     # GGUF q4_k_m + Modelfile → ~/headline-v1-gguf
```

If the GGUF build fails, the script saves a merged 16-bit model, logs the
error and stops.

The Modelfile's `SYSTEM` text comes from the tokenizer's own rendering, so it
includes Llama 3.1's date preamble, exactly as in training.

Quick check of the whole path from adapter to tickers:

```bash
$PY ~/infer_v1.py ~/headline-v1 "Shell profits beat forecasts as BP slumps"
```
