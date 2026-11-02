"""Measured single request against Ollama with GPU/RAM sampling.

python -I probe.py MODEL [--num-gpu N] [--think true|false] [--ctx 4096] [--predict 64]
                   [--prompt TEXT] [--unload-first] [--out results.jsonl] [--label X]

Prints one JSON line: timings from Ollama (load/prompt/eval), wall-clock TTFT,
peak GPU memory (nvidia-smi), minimum free RAM, Ollama /api/ps placement.
"""
import argparse
import ctypes
import json
import subprocess
import sys
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:11434"


class MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def free_ram_mb():
    m = MEMSTATUS()
    m.dwLength = ctypes.sizeof(MEMSTATUS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**20, m.ullAvailPageFile / 2**20


def gpu_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=5).stdout.strip().split(",")
        return float(out[0]), float(out[1])
    except Exception:
        return float("nan"), float("nan")


class Sampler(threading.Thread):
    def __init__(self, every=0.5):
        super().__init__(daemon=True)
        self.every = every
        self.stop = threading.Event()
        self.peak_gpu = 0.0
        self.min_ram = 1e12
        self.min_commit = 1e12
        self.max_util = 0.0

    def run(self):
        while not self.stop.is_set():
            g, u = gpu_used_mb()
            r, c = free_ram_mb()
            self.peak_gpu = max(self.peak_gpu, g)
            self.max_util = max(self.max_util, u)
            self.min_ram = min(self.min_ram, r)
            self.min_commit = min(self.min_commit, c)
            time.sleep(self.every)


def post(path, body, timeout=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def ps():
    with urllib.request.urlopen(BASE + "/api/ps", timeout=5) as r:
        return json.loads(r.read()).get("models", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--num-gpu", type=int, default=None)
    ap.add_argument("--think", default="false")
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--predict", type=int, default=64)
    ap.add_argument("--prompt", default="Reply with exactly one word: ready")
    ap.add_argument("--unload-first", action="store_true")
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    if a.prompt_file:
        a.prompt = open(a.prompt_file, encoding="utf-8").read()
    if a.unload_first:
        for m in ps():
            if m.get("name") == a.model:
                post("/api/generate", {"model": a.model, "keep_alive": 0}).read()
        time.sleep(2)

    g0, _ = gpu_used_mb()
    r0, c0 = free_ram_mb()
    loaded_before = [m.get("name") for m in ps()]
    opts = {"num_ctx": a.ctx, "num_predict": a.predict, "temperature": 0}
    if a.num_gpu is not None:
        opts["num_gpu"] = a.num_gpu
    body = {"model": a.model, "messages": [{"role": "user", "content": a.prompt}],
            "stream": True, "options": opts, "keep_alive": "10m"}
    if a.think in ("true", "false"):
        body["think"] = a.think == "true"

    s = Sampler()
    s.start()
    t0 = time.monotonic()
    ttft = None
    content, thinking, final, err = [], [], {}, ""
    try:
        with post("/api/chat", body) as resp:
            for raw in resp:
                line = raw.strip()
                if not line:
                    continue
                ch = json.loads(line)
                msg = ch.get("message") or {}
                if (msg.get("content") or msg.get("thinking")) and ttft is None:
                    ttft = time.monotonic() - t0
                content.append(msg.get("content") or "")
                thinking.append(msg.get("thinking") or "")
                if ch.get("done"):
                    final = ch
                    break
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    wall = time.monotonic() - t0
    time.sleep(1.0)
    s.stop.set()
    s.join()
    placement = [{k: m.get(k) for k in ("name", "size", "size_vram", "context_length")}
                 for m in ps()]
    ns = 1e9
    res = {
        "label": a.label, "model": a.model, "num_gpu": a.num_gpu, "think": a.think,
        "ctx": a.ctx, "error": err, "loaded_before": loaded_before,
        "wall_s": round(wall, 2), "ttft_s": round(ttft, 2) if ttft else None,
        "load_s": round(final.get("load_duration", 0) / ns, 2),
        "prompt_tokens": final.get("prompt_eval_count"),
        "prompt_tok_s": round(final.get("prompt_eval_count", 0) / (final.get("prompt_eval_duration", 1) / ns), 1) if final.get("prompt_eval_duration") else None,
        "eval_tokens": final.get("eval_count"),
        "eval_tok_s": round(final.get("eval_count", 0) / (final.get("eval_duration", 1) / ns), 2) if final.get("eval_duration") else None,
        "done_reason": final.get("done_reason"),
        "content": "".join(content)[:300], "thinking_chars": len("".join(thinking)),
        "gpu_before_mb": g0, "gpu_peak_mb": s.peak_gpu, "gpu_util_max": s.max_util,
        "ram_free_before_mb": round(r0), "ram_free_min_mb": round(s.min_ram),
        "commit_free_min_mb": round(s.min_commit), "placement": placement,
    }
    line = json.dumps(res)
    print(line)
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(line + "\n")


main()
