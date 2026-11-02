"""
Both projects at once against the one shared Ollama, each through its own code.

ARIA side: this process (ARIA venv), N threads through OllamaProvider.complete.
SENTINEL side: a child process (SENTINEL venv) with isolated data dirs, M
threads through mind.router._local_reason. Same model, same load options.

Prints one JSON line per request (who, start, end, outcome) plus Ollama's
/api/ps before and after, so a reload or a second copy of the model shows.

usage: python concurrency.py ARIA_ROOT SENTINEL_ROOT SENTINEL_PY MODEL N_ARIA N_SENTINEL OUT
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ARIA_ROOT, SENT_ROOT, SENT_PY, MODEL, N_ARIA, N_SENT, OUT = sys.argv[1:8]
N_ARIA, N_SENT = int(N_ARIA), int(N_SENT)
sys.path.insert(0, ARIA_ROOT)
os.chdir(ARIA_ROOT)
from src.inference.providers.ollama import OllamaProvider  # noqa: E402

PROMPT = "In exactly three short sentences, explain what a bond's duration measures."
T0 = time.time()


def ps():
    with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as r:
        return [{k: m.get(k) for k in ("name", "size", "size_vram", "context_length")}
                for m in json.loads(r.read()).get("models", [])]


SENT_CHILD = r'''
import json, os, sys, tempfile, threading, time
d = tempfile.mkdtemp(prefix="sentinel-conc-")
for k, v in {"SENTINEL_DATA_DIR": d + "/s", "MIND_DATA_DIR": d + "/m",
             "SENTINEL_MEMORY_DB": d + "/mem.db", "ASSISTANT_CHAT_DB": d + "/chat.db"}.items():
    os.environ[k] = v
os.makedirs(d + "/m", exist_ok=True)
json.dump({"local_backend": "ollama", "ollama_url": "http://127.0.0.1:11434",
           "local_model": sys.argv[2], "consult_enabled": False},
          open(d + "/m/router_config.json", "w"))
sys.path.insert(0, sys.argv[1])
from mind import router
cfg = router.get_config()
t0 = float(sys.argv[4])
def go(i):
    s = time.time()
    try:
        text = router._local_reason(cfg, "", [{"role": "user", "content": sys.argv[5]}], 160,
                                    model=sys.argv[2])
        out = {"who": "sentinel", "i": i, "ok": True, "chars": len(text)}
    except Exception as e:
        out = {"who": "sentinel", "i": i, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    out.update(start=round(s - t0, 2), end=round(time.time() - t0, 2))
    print(json.dumps(out), flush=True)
ts = [threading.Thread(target=go, args=(i,)) for i in range(int(sys.argv[3]))]
for t in ts: t.start(); time.sleep(0.05)
for t in ts: t.join()
'''


def aria(i, rows):
    s = time.time()
    try:
        text = OllamaProvider().complete(MODEL, [{"role": "user", "content": PROMPT}],
                                         max_tokens=160, timeout=90)
        out = {"who": "aria", "i": i, "ok": True, "chars": len(text)}
    except Exception as e:                                # noqa: BLE001
        out = {"who": "aria", "i": i, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    out.update(start=round(s - T0, 2), end=round(time.time() - T0, 2))
    rows.append(out)
    print(json.dumps(out), flush=True)


def main():
    before = ps()
    child = subprocess.Popen([SENT_PY, "-c", SENT_CHILD, SENT_ROOT, MODEL, str(N_SENT), str(T0), PROMPT],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", cwd=SENT_ROOT)
    rows = []
    ts = [threading.Thread(target=aria, args=(i, rows)) for i in range(N_ARIA)]
    for t in ts:
        t.start()
        time.sleep(0.05)
    for t in ts:
        t.join()
    so, se = child.communicate(timeout=900)
    for line in so.splitlines():
        if line.startswith("{"):
            rows.append(json.loads(line))
            print(line, flush=True)
    if child.returncode:
        print("sentinel child stderr:", se[-1500:], flush=True)
    after = ps()
    summary = {"summary": True, "model": MODEL, "n_aria": N_ARIA, "n_sentinel": N_SENT,
               "ok": sum(r["ok"] for r in rows), "busy": sum("ModelBusy" in r.get("error", "") for r in rows),
               "other_errors": [r for r in rows if not r["ok"] and "ModelBusy" not in r.get("error", "")],
               "wall_s": round(time.time() - T0, 1), "resident_before": before, "resident_after": after}
    with open(OUT, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"model": MODEL, **r}) + "\n")
        f.write(json.dumps(summary) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
