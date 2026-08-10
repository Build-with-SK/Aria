#!/bin/bash
# ARIA launcher for macOS. Double-click this file in Finder, or run it from
# Terminal. The Windows equivalents are START_ARIA.bat / START_ARIA.ps1.
cd "$(dirname "$0")" || exit 1

OLLAMA_BIN="$HOME/ollama/ollama"

echo "◉ ARIA — starting"
echo

# Ollama is optional: without it the brain runs remote-only via the Anthropic
# API. Start it only if it is installed and not already listening.
if [ -x "$OLLAMA_BIN" ]; then
  if curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "  ollama    already running"
  else
    "$OLLAMA_BIN" serve > "$HOME/ollama/ollama.log" 2>&1 &
    echo "  ollama    started (log: ~/ollama/ollama.log)"
  fi
else
  echo "  ollama    not installed — brain will use the Anthropic API"
fi

if [ ! -x venv/bin/python ]; then
  echo "  ERROR: venv missing. Run: python3.12 -m venv venv && ./venv/bin/pip install -r requirements.txt"
  read -r -p "Press Return to close."
  exit 1
fi

echo "  backend   starting on http://localhost:8000/app/"
echo
echo "  Press Ctrl+C to stop ARIA."
echo

# Open the deck once the API answers, then hand the terminal to the server.
( for _ in $(seq 1 60); do
    sleep 2
    if curl -s -m 3 http://localhost:8000/health >/dev/null 2>&1; then
      open http://localhost:8000/app/
      break
    fi
  done ) &

exec ./venv/bin/python -m uvicorn backend.main:app --port 8000
