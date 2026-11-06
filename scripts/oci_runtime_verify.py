"""Read-only cross-platform model, policy and local inference checks."""
import json
from pathlib import Path
import time
import urllib.request


def main():
    from src.models.model_trainer import ModelRegistry
    registry = ModelRegistry()
    checked = 0
    for symbol in ("AAPL", "BTC-USD", "RELIANCE.NS", "SPY", "^FTSE"):
        if list(registry.base_dir.glob(f"{symbol}_1d_*.joblib")):
            model, _ = registry.load_latest(symbol, 1)
            if model is None:
                raise RuntimeError("A restored model failed to load")
            checked += 1
    if not checked:
        raise RuntimeError("No restored models could be tested")
    print(json.dumps({"restored_ensembles_loaded": checked}), flush=True)
    request = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=json.dumps({
        "model": "qwen2.5:7b-instruct-q4_K_M", "prompt": "Reply with the single word ready.",
        "stream": False, "options": {"num_predict": 8, "num_ctx": 1024, "num_thread": 2},
    }).encode(), headers={"Content-Type": "application/json"})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=240) as response:
        payload = json.load(response)
    if not payload.get("done") or not payload.get("response", "").strip():
        raise RuntimeError("Local model returned no completed answer")
    duration = payload.get("eval_duration", 0) / 1e9
    print(json.dumps({"ollama_model": payload["model"], "completed": True,
                      "elapsed_seconds": round(time.monotonic() - started, 2),
                      "tokens_per_second": round(payload.get("eval_count", 0) / duration, 2) if duration else None}), flush=True)


if __name__ == "__main__":
    main()
