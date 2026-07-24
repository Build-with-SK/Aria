"""
src/inference/base.py
=====================
One source of truth for where Ollama lives. The split deployment (Mac mini
runs the desk, the laptop runs Ollama) points OLLAMA_BASE at the laptop's
LAN address; everything defaults to localhost as before.
"""
from __future__ import annotations

import os


def ollama_base() -> str:
    return os.environ.get("OLLAMA_BASE", "http://localhost:11434").rstrip("/")
