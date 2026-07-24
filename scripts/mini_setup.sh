#!/bin/bash
# ============================================================================
# ARIA — Mac mini one-shot setup (macOS Monterey 12.x, late-2014 mini)
# Run on the mini from inside the repo folder:   bash scripts/mini_setup.sh
# Prereqs done by hand first (one time):
#   1. xcode-select --install          (Command Line Tools; brings git)
#   2. Install Python 3.12 from python.org (macOS 64-bit universal2 installer)
#   3. Node LTS from nodejs.org        (only needed to build the frontend once)
#   4. Copy the repo to ~/aria and .env into it (with OLLAMA_BASE and
#      ARIA_API_KEY added; ALPACA_PAPER must stay exactly "true")
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ARIA_HOME="$(pwd)"
echo "ARIA home: $ARIA_HOME"

# 1. Python venv + deps
if [ ! -d venv ]; then
  PY="$(command -v python3.12 || command -v python3.11 || command -v python3)"
  echo "using $PY ($($PY --version))"
  "$PY" -m venv venv
fi
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q
echo "✓ python deps"

# 2. Frontend one-time build (served by the backend at /app afterwards)
if [ -d frontend ] && command -v npm >/dev/null; then
  (cd frontend && npm ci --silent && npm run build --silent)
  echo "✓ frontend built → served at http://localhost:8000/app"
else
  echo "! npm not found — skip frontend build (API still fully works)"
fi

# 3. Logs dir
mkdir -p logs

# 4. launchd agents (auto-start on boot, auto-restart on crash)
mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__ARIA_HOME__|$ARIA_HOME|g" scripts/com.aria.backend.plist \
  > "$HOME/Library/LaunchAgents/com.aria.backend.plist"
sed "s|__ARIA_HOME__|$ARIA_HOME|g" scripts/com.aria.watchdog.plist \
  > "$HOME/Library/LaunchAgents/com.aria.watchdog.plist"
launchctl unload "$HOME/Library/LaunchAgents/com.aria.backend.plist" 2>/dev/null || true
launchctl unload "$HOME/Library/LaunchAgents/com.aria.watchdog.plist" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/com.aria.backend.plist"
launchctl load "$HOME/Library/LaunchAgents/com.aria.watchdog.plist"
echo "✓ launchd agents loaded (backend + watchdog)"

# 5. Wait and verify
echo "waiting for backend..."
for i in $(seq 1 30); do
  if curl -s --max-time 3 http://localhost:8000/health >/dev/null 2>&1; then
    echo "✓ backend is up: http://localhost:8000  (UI: /app)"
    curl -s http://localhost:8000/api/desk/status | head -c 300; echo
    exit 0
  fi
  sleep 2
done
echo "✗ backend did not come up — check logs/backend.err"
exit 1
