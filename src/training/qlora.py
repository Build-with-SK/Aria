"""
src/training/qlora.py
=====================
THE TRAINING RUN — config, preflight, and the cluster job
(docs/ARIA_NEXT_SESSION.md step 5).

Two machines, two jobs: the laptop SERVES, the university cluster TRAINS.
Nothing in this file trains anything on the laptop by accident, and nothing in
it launches a job — it renders one and hands it to the owner, who is the
person the acceptable-use policy applies to.

THE ONE REFUSAL THAT MATTERS
----------------------------
A corpus with `contains_personal_data` NEVER gets a cluster job rendered for
it. The brief's data-governance point is not advisory: the vault holds his
personal notes, `data/auth/users.jsonl` holds other people's sign-in data, and
neither goes onto university storage — including inside a training set, and
including inside the weights that come back. The voice corpus therefore trains
locally or not at all. Outcomes and domain corpora carry no personal data and
are what the cluster is for.

HYPERPARAMETERS
---------------
The defaults are for a 7B at 4-bit on one 40 GB card, which is what a shared
cluster typically hands out. They are a starting point, not a result: nobody
has run this yet, because `training_export()` is still empty. When the first
run happens, what it produced belongs in the adapter's registry entry so the
next one can be compared against it rather than guessed at again.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = ROOT / "data" / "training" / "runs"

#: What a QLoRA run needs on the machine that actually trains. Absent here on
#: purpose — the laptop serves; it does not need a trainer installed.
REQUIRED_PACKAGES = ("torch", "transformers", "peft", "bitsandbytes",
                     "trl", "datasets", "accelerate")


@dataclass
class TrainConfig:
    base_model: str = "Qwen/Qwen2.5-7B-Instruct"
    dataset_dir: str = ""
    output_dir: str = ""
    # QLoRA
    load_in_4bit: bool = True
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple[str, ...] = ("q_proj", "k_proj", "v_proj", "o_proj",
                                       "gate_proj", "up_proj", "down_proj")
    # schedule
    epochs: float = 2.0
    learning_rate: float = 1e-4
    batch_size: int = 4
    grad_accum: int = 4
    max_seq_len: int = 4096
    warmup_ratio: float = 0.03
    seed: int = 20260812
    # cluster
    partition: str = "gpu"
    gpus: int = 1
    time_limit: str = "08:00:00"
    cpus: int = 8
    memory: str = "64G"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["target_modules"] = list(self.target_modules)
        return d


@dataclass
class Preflight:
    ok: bool = False
    refusals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        head = "READY to render a training job" if self.ok else "REFUSED:"
        return "\n".join([head, *(f"  - {r}" for r in self.refusals),
                          *(f"  note: {n}" for n in self.notes)])


def missing_packages() -> list[str]:
    import importlib.util
    return [p for p in REQUIRED_PACKAGES if importlib.util.find_spec(p) is None]


def preflight(manifest: dict, *, target: str = "cluster") -> Preflight:
    """Can this corpus be trained on, and on which machine?

    `target` is "cluster" or "local". The distinction is not about capacity —
    it is about whose disk the data ends up on.
    """
    p = Preflight()

    if not manifest:
        p.refusals.append("no dataset manifest — build one first with "
                          "scripts/build_training_set.py")
        return p

    splits = manifest.get("splits") or {}
    train_rows = (splits.get("train") or {}).get("rows", 0)
    if not train_rows:
        p.refusals.append("the dataset has no training rows")

    if manifest.get("contains_personal_data") and target == "cluster":
        p.refusals.append(
            "this corpus contains the owner's personal notes. It does not go "
            "onto university storage, and neither do weights trained on it — "
            "train it locally or not at all (brief, step 8: data governance)")

    if target == "local":
        p.notes.append(
            "training locally on an 8 GB card means a 7B at 4-bit with a short "
            "sequence length and a slow epoch. It is viable for the voice "
            "corpus, which is small; it is not where the outcomes run belongs.")

    if manifest.get("corpus") == "outcomes" and not (splits.get("holdout") or {}).get("rows"):
        p.refusals.append(
            "no held-out split — an adapter that cannot be evaluated against "
            "the incumbent cannot be promoted, so training it is wasted time")

    if target == "cluster":
        p.notes.append(
            "a batch cluster cannot host a 24/7 service: SLURM wall clocks run "
            "12-48h. Train here, serve on the laptop.")
        p.notes.append(
            "confirm the acceptable-use policy with the administrators before "
            "the first submission. A personal trading system on research "
            "compute is the owner's conversation to have, and the consequence "
            "of skipping it is a suspended account.")

    missing = missing_packages()
    if missing and target == "local":
        p.refusals.append(f"not installed here: {', '.join(missing)}")
    elif missing:
        p.notes.append(f"absent locally ({', '.join(missing)}) — expected; the "
                       f"cluster job loads its own environment")

    p.ok = not p.refusals
    return p


def render_sbatch(config: TrainConfig, *, job_name: str = "aria-qlora") -> str:
    """A SLURM script the owner can read before he submits it."""
    return f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --partition={config.partition}
#SBATCH --gres=gpu:{config.gpus}
#SBATCH --cpus-per-task={config.cpus}
#SBATCH --mem={config.memory}
#SBATCH --time={config.time_limit}
#SBATCH --output=%x-%j.out

# Generated by src/training/qlora.py. Read it before submitting: it runs under
# your account, under your institution's acceptable-use policy.
#
# The dataset copied here carries NO personal data — that is enforced in
# preflight(), not by convention. If you are about to copy a corpus marked
# SENSITIVE onto this filesystem, stop.

set -euo pipefail

module load cuda || true
source "${{ARIA_VENV:-$HOME/aria-venv}}/bin/activate"

python train_qlora.py \\
  --base-model "{config.base_model}" \\
  --dataset-dir "{config.dataset_dir}" \\
  --output-dir "{config.output_dir}" \\
  --lora-r {config.lora_r} \\
  --lora-alpha {config.lora_alpha} \\
  --lora-dropout {config.lora_dropout} \\
  --epochs {config.epochs} \\
  --learning-rate {config.learning_rate} \\
  --batch-size {config.batch_size} \\
  --grad-accum {config.grad_accum} \\
  --max-seq-len {config.max_seq_len} \\
  --seed {config.seed}

# The adapter that comes back is a CANDIDATE. It is registered with
# src/training/adapters.py and promoted only if it beats the incumbent on the
# held-out split — see scripts/promote_adapter.py.
"""


def prepare_run(manifest: dict, config: TrainConfig | None = None, *,
                target: str = "cluster", directory: Path = RUNS_DIR) -> dict:
    """Preflight, then write the run directory. Renders; never submits."""
    config = config or TrainConfig()
    config.dataset_dir = config.dataset_dir or (manifest.get("dir") or "")

    check = preflight(manifest, target=target)
    if not check.ok:
        raise PermissionError(check.describe())

    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(directory) / f"{manifest.get('corpus', 'run')}_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir = config.output_dir or str(run_dir / "adapter")

    (run_dir / "config.json").write_text(
        json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    (run_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    if target == "cluster":
        (run_dir / "submit.sbatch").write_text(render_sbatch(config),
                                               encoding="utf-8")
    trainer = Path(__file__).parent / "train_qlora.py"
    if trainer.exists():
        shutil.copy(trainer, run_dir / "train_qlora.py")

    return {"dir": str(run_dir), "target": target, "config": config.to_dict(),
            "notes": check.notes,
            "next": ("copy this directory to the cluster and `sbatch "
                     "submit.sbatch`" if target == "cluster" else
                     "run train_qlora.py in this directory")}
