#!/usr/bin/env bash
# overnight.sh — label → review sheet → train → evaluate, unattended, on otter65.
#
#   ssh otter65
#   tmux new -s overnight
#   read -s -p "API key: " ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY && echo
#   bash /scratch/$USER/aria/scripts/headline_v1/overnight.sh
#   # Ctrl+B D to detach. Next morning: tmux attach -t overnight, or read the log.
#
# Every step is resumable, so if anything stops it, running the same command
# again carries on: labelling skips labelled headlines, training resumes from
# its last checkpoint, and finished steps are skipped.
#
# Everything is logged to ~/overnight_<date>.log, with results in ~/RESULTS.md.
# Scores are PROVISIONAL: Checkpoint A (the human review of the test set)
# still has to happen before any score counts as final.

set -uo pipefail

USER="${USER:-$(id -un)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="/scratch/$USER/venv/bin/python"
DATA="$HOME/data/v1"
ADAPTER="$HOME/headline-v1"
LOG="$HOME/overnight_$(date +%Y%m%d_%H%M).log"

exec > >(tee -a "$LOG") 2>&1
say()  { echo; echo "=== $(date '+%H:%M:%S')  $*"; }
die()  { say "STOPPED: $*"; echo "Full log: $LOG"; exit 1; }

say "overnight run starting on $(hostname), log $LOG"

# ── preflight ──────────────────────────────────────────────────────────────
[[ "$(hostname)" == otter65* ]] || die "this is $(hostname), not otter65 — the venv lives on otter65's /scratch. Run: ssh otter65"
[[ -x "$PY" ]] || die "no venv at $PY (scratch wiped?)"
[[ -f /scratch/$USER/env.sh ]] || die "no /scratch/$USER/env.sh"
[[ -n "${TMUX:-}" ]] || die "not inside tmux — a dropped SSH connection would kill the run. Start with: tmux new -s overnight"
[[ -s "$HOME/headlines.txt" ]] || die "~/headlines.txt is missing or empty (run ~/collect_all.py)"

others=$(nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null)
[[ -z "$others" ]] || die "someone else is using the GPU: $others"

free_gb=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc 0-9)
(( free_gb >= 2 )) || die "only ${free_gb}G free in home; need ≥2G for the adapter"

for mod in anthropic datasets; do
  "$PY" -c "import $mod" 2>/dev/null || "$PY" -m pip install -q "$mod" || die "could not install $mod"
done
echo "headlines: $(sort -u "$HOME/headlines.txt" | wc -l) unique, home free: ${free_gb}G"

# ── Task 2: label ──────────────────────────────────────────────────────────
if [[ -s "$DATA/train.jsonl" && -s "$DATA/test.jsonl" ]]; then
  say "Task 2: labels already split in $DATA — skipping"
else
  [[ -n "${ANTHROPIC_API_KEY:-}" ]] || die "ANTHROPIC_API_KEY not set. Enter it with: read -s -p 'API key: ' ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY"
  say "Task 2: labelling with claude-haiku-5-5 (cap 12,000)"
  "$PY" "$HERE/label_v1.py" || die "labelling failed — rerun this script to resume"
fi
unset ANTHROPIC_API_KEY    # nothing after this needs it

n_train=$(wc -l < "$DATA/train.jsonl")
(( n_train >= 1000 )) || die "only $n_train training rows — too few to train on; check $DATA/labelled_raw.jsonl errors"

# ── Checkpoint A sheet (the review itself waits for the user) ───────────────
if [[ ! -f "$DATA/test_review.csv" ]]; then
  say "Checkpoint A: writing review sheet"
  "$PY" "$HERE/review_v1.py" || echo "(review sheet failed; training continues)"
fi

# ── Task 3: train ──────────────────────────────────────────────────────────
if [[ -d "$ADAPTER" ]]; then
  say "Task 3: $ADAPTER already exists — skipping training"
else
  # shellcheck disable=SC1090
  source "/scratch/$USER/env.sh"
  say "Task 3: training on $n_train rows (2 epochs)"
  tlog="$HOME/train_v1_$(date +%Y%m%d_%H%M).log"
  "$PY" "$HERE/train_v1.py" 2>&1 | tee "$tlog"
  if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    if grep -qi "out of memory" "$tlog"; then
      say "Task 3: out of GPU memory — retrying with batch 2 × accum 8"
      rm -rf "/scratch/$USER/out/headline-v1"/checkpoint-*   # different batch shape; start clean
      "$PY" "$HERE/train_v1.py" --batch 2 --accum 8 || die "training failed again after OOM retry"
    else
      die "training failed (see $tlog); rerunning this script resumes from the last checkpoint"
    fi
  fi
fi
[[ -d "$ADAPTER" ]] || die "training finished but $ADAPTER was not created"

# ── Task 4: evaluate (provisional until the test set is reviewed) ───────────
TEST="$DATA/test_reviewed.jsonl"; [[ -f "$TEST" ]] || TEST="$DATA/test.jsonl"
say "Task 4: evaluating $ADAPTER on $(basename "$TEST")"
"$PY" "$HERE/eval_v1.py" "$ADAPTER" "$TEST" || echo "(v1 evaluation failed — see log)"

if [[ -f "$HOME/eval_v0.py" && -d "$HOME/sentiment-v0b" ]]; then
  say "Task 4: v0b reference score"
  "$PY" "$HOME/eval_v0.py" "$HOME/sentiment-v0b" || echo "(v0b evaluation failed — see log)"
fi

say "DONE. Results: ~/RESULTS.md   Adapter: $ADAPTER   Review sheet: $DATA/test_review.csv"
echo "Still needed from you: review the flagged rows in test_review.csv (Checkpoint A)."
