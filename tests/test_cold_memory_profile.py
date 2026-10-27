"""Opt-in fresh-process cold memory profile; all storage and network isolated.

ARIA_MEMORY_PROFILE=<output.json> python -m pytest tests/test_cold_memory_profile.py -s
Run in a fresh interpreter for meaningful cold-import measurements.
"""
import cProfile
import json
import os
import pstats
import sys
import time
import tracemalloc
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.getenv("ARIA_MEMORY_PROFILE"), reason="opt-in cold subprocess profile")
def test_profile_cold_listing(monkeypatch, tmp_path):
    import faulthandler
    faulthandler.dump_traceback_later(20, repeat=True)
    tracemalloc.start()
    profiler = cProfile.Profile()
    profiler.enable()
    begin = time.perf_counter()
    from test_launch_get_sweep import _owner_client
    backend, client, headers = _owner_client(monkeypatch)
    imported = time.perf_counter()
    from src.brain.cognitive.long_term_memory import LongTermMemory
    from src.brain import brain_daemon
    monkeypatch.setattr(LongTermMemory, "DB_PATH", tmp_path / "chroma")
    monkeypatch.setattr(brain_daemon, "peek_brain", lambda: None)
    responses, times = [], []
    try:
        for _ in range(2):
            start = time.perf_counter()
            responses.append(client.get("/api/brain/memories", headers=headers).json())
            times.append(round(time.perf_counter() - start, 4))
    finally:
        faulthandler.cancel_dump_traceback_later()
        profiler.disable()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    stats = pstats.Stats(profiler)
    imports = sorted([(key[0], round(value[3], 4)) for key, value in stats.stats.items()
                      if key[2] == "<module>"], key=lambda row: row[1], reverse=True)[:15]
    report = {"python": sys.version.split()[0], "backend_import_s": round(imported-begin, 4),
              "requests_s": times, "python_peak_mib": round(peak / 1024**2, 2),
              "import_cumulative_s": imports,
              "embedding_stack_loaded": any(m in sys.modules for m in ("torch", "sentence_transformers")),
              "response_shapes": [{"count": r.get("count"), "error": r.get("error", "")[:300]}
                                  for r in responses]}
    Path(os.environ["ARIA_MEMORY_PROFILE"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert all("count" in r for r in responses)
