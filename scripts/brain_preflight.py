"""
scripts/brain_preflight.py
==========================
Can this machine actually serve her brain? (docs/ARIA_NEXT_SESSION.md step 4.)

    venv/Scripts/python.exe scripts/brain_preflight.py

Reports, and downloads nothing. Pulling a 5 GB model is the owner's decision
and his disk; this prints the command and stops.

Checks:
  - Ollama reachable, and which models are present
  - whether each tier's PRIMARY candidate is one of them (a tier pointing at a
    model that is not installed fails at the first real question, not here)
  - the policy in force: self-hosted only, abstain vs disclose
  - the context window actually being sent to Ollama
  - GPU memory, when nvidia-smi is available, against what a 7B at Q4_K_M needs
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inference import policy
from src.inference.base import ollama_base
from src.inference.providers.ollama import DEFAULT_NUM_CTX
from src.inference.router import load_tier_config

#: What the brief recommends for an 8 GB card, and roughly what it costs.
RECOMMENDED = [
    ("qwen2.5:7b-instruct-q4_K_M", 4.7, "general instruct — analyst prose"),
    ("llama3.1:8b-instruct-q4_K_M", 4.9, "the alternative of the same size"),
]
VRAM_NEEDED_GB = 6.5     # 7B at Q4_K_M plus an 8K KV cache, with headroom


def installed_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(f"{ollama_base()}/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def gpu_memory_gb() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return round(int(out.stdout.strip().splitlines()[0]) / 1024, 1)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def main() -> int:
    problems: list[str] = []
    print("ARIA brain preflight\n" + "=" * 60)

    pol = policy.describe()
    print(f"\nPolicy      self-hosted only: {pol['self_hosted_only']}   "
          f"degrade: {pol['degrade']}")
    print(f"            {pol['explanation']}")
    if not pol["self_hosted_only"]:
        problems.append("ARIA_ALLOW_VENDOR_BRAIN is set — a vendor API is "
                        "permitted in the reasoning path (decision 1 overridden)")

    base = ollama_base()
    models = installed_models()
    print(f"\nOllama      {base}")
    if models is None:
        print("            UNREACHABLE")
        problems.append(f"Ollama is not answering at {base} — with a "
                        f"self-hosted-only policy she has no brain at all, and "
                        f"every reasoning call will abstain")
        models = []
    else:
        print(f"            {len(models)} model(s): {', '.join(models) or 'none'}")

    print(f"\nContext     num_ctx {DEFAULT_NUM_CTX} tokens (OLLAMA_NUM_CTX)")
    print("            Ollama truncates silently past this; "
          "src/inference/context.py budgets against the same number.")

    print("\nTiers")
    tiers = load_tier_config()
    for tier, candidates in tiers.items():
        if not candidates:
            problems.append(f"tier {tier} has no candidates")
            continue
        primary_provider, primary_model = candidates[0][0], candidates[0][1]
        present = primary_model in models
        mark = "ok " if present else "MISSING"
        print(f"  {tier:<9} {primary_provider}/{primary_model:<28} {mark}")
        if not present and models:
            problems.append(
                f"tier {tier} routes to {primary_model}, which is not "
                f"installed — this fails at the first real question, not here")
        for spare in candidates[1:]:
            print(f"            (spare, only under ARIA_DEGRADE=disclose: "
                  f"{spare[0]}/{spare[1]})")

    vram = gpu_memory_gb()
    print(f"\nGPU         {f'{vram} GB' if vram else 'nvidia-smi unavailable'}")
    if vram and vram < VRAM_NEEDED_GB:
        problems.append(
            f"{vram} GB of VRAM against ~{VRAM_NEEDED_GB} GB for a 7B at "
            f"Q4_K_M with an {DEFAULT_NUM_CTX}-token window — expect a spill to "
            f"CPU and a debate that no longer fits the desk tick")

    print("\nRecommended for this card (not installed automatically):")
    for name, size, why in RECOMMENDED:
        state = "installed" if name in models else f"~{size} GB — ollama pull {name}"
        print(f"  {name:<32} {why}\n      {state}")

    print("\n" + "=" * 60)
    if problems:
        print(f"{len(problems)} thing(s) to fix:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Ready: her brain is reachable, self-hosted, and routed to a model "
          "that exists.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
