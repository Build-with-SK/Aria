"""
export_v1.py — Task 6: merge the adapter, export GGUF q4_k_m + Ollama Modelfile.

    /scratch/$USER/venv/bin/python export_v1.py ~/headline-v1

Writes /scratch/$USER/out/headline-v1-gguf/{*.gguf, Modelfile} and copies the
folder to ~/headline-v1-gguf. If the GGUF build fails (llama.cpp needs to
compile, and there is no sudo here), it saves a merged 16-bit model instead,
logs the error and stops — per the handover.

The Modelfile's SYSTEM text is taken from the tokenizer's own rendering of
the training prompt, not retyped: Llama 3.1's template adds a "Cutting
Knowledge Date / Today Date" preamble to the system block, and the model was
trained with it, so Ollama has to send it too.
"""
from __future__ import annotations

import getpass
import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from headline_schema import append_results, messages  # noqa: E402

SYS_OPEN = "<|start_header_id|>system<|end_header_id|>\n\n"
EOT = "<|eot_id|>"

TEMPLATE = (
    '{{ if .System }}<|start_header_id|>system<|end_header_id|>\n\n'
    '{{ .System }}<|eot_id|>{{ end }}'
    '{{ if .Prompt }}<|start_header_id|>user<|end_header_id|>\n\n'
    '{{ .Prompt }}<|eot_id|>{{ end }}'
    '<|start_header_id|>assistant<|end_header_id|>\n\n'
    '{{ .Response }}<|eot_id|>'
)


def rendered_system(tok) -> str:
    """The exact system-block text the model saw in training."""
    text = tok.apply_chat_template(messages("x"), tokenize=False, add_generation_prompt=True)
    start = text.index(SYS_OPEN) + len(SYS_OPEN)
    return text[start:text.index(EOT, start)]


def modelfile(gguf_name: str, system: str) -> str:
    if '"""' in system:
        raise ValueError("system prompt contains a triple quote")
    return (f"FROM ./{gguf_name}\n\n"
            f'TEMPLATE """{TEMPLATE}"""\n\n'
            f'SYSTEM """{system}"""\n\n'
            "PARAMETER temperature 0\n"
            "PARAMETER num_predict 200\n"
            'PARAMETER stop "<|eot_id|>"\n'
            'PARAMETER stop "<|start_header_id|>"\n'
            'PARAMETER stop "<|end_header_id|>"\n')


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    adapter = Path(sys.argv[1]).expanduser()
    user = getpass.getuser()
    out = Path(f"/scratch/{user}/out/{adapter.name}-gguf")
    backup = Path.home() / out.name
    if backup.exists():
        raise SystemExit(f"{backup} exists; never overwrite an export")

    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template

    model, tok = FastLanguageModel.from_pretrained(
        model_name=str(adapter), max_seq_length=512, load_in_4bit=True, dtype=None)
    tok = get_chat_template(tok, chat_template="llama-3.1")
    system = rendered_system(tok)

    try:
        model.save_pretrained_gguf(str(out), tok, quantization_method="q4_k_m")
    except Exception as e:  # noqa: BLE001 — any build failure takes the fallback
        merged = Path(f"/scratch/{user}/out/{adapter.name}-merged16")
        err = "".join(traceback.format_exception_only(type(e), e)).strip()
        model.save_pretrained_merged(str(merged), tok, save_method="merged_16bit")
        shutil.copytree(merged, Path.home() / merged.name)
        md = (f"## Task 6 — export {adapter.name}: GGUF FAILED\n"
              f"- error: `{err[:500]}`\n"
              f"- saved merged 16-bit instead: `{merged}` → `~/{merged.name}`. Task stopped.")
        print(md)
        append_results(md)
        raise SystemExit(1)

    ggufs = sorted(out.rglob("*.gguf"), key=lambda p: p.stat().st_size)
    q4 = [p for p in ggufs if "q4_k_m" in p.name.lower()] or ggufs
    if not q4:
        raise SystemExit(f"save_pretrained_gguf returned but no .gguf under {out}")
    gguf = q4[-1]
    if gguf.parent != out:
        shutil.move(str(gguf), out / gguf.name)
        gguf = out / gguf.name
    (out / "Modelfile").write_text(modelfile(gguf.name, system), encoding="utf-8")
    shutil.copytree(out, backup)

    md = (f"## Task 6 — export {adapter.name}\n"
          f"- `{gguf.name}` ({gguf.stat().st_size / 2**30:.2f} GiB) + `Modelfile` in `{out}` "
          f"→ `{backup}`\n"
          f"- load: `cd {backup} && ollama create {adapter.name} -f Modelfile`")
    print(md)
    append_results(md)


if __name__ == "__main__":
    main()
