#!/bin/bash
# ============================================================================
# ARIA — Mac mini one-shot setup (macOS Monterey 12.x, late-2014 mini)
# Run on the mini from inside the repo folder:   bash scripts/mini_setup.sh
# Prereqs done by hand first (one time):
#   1. xcode-select --install          (Command Line Tools; brings git)
#   2. Install Python 3.12 from python.org (macOS 64-bit universal2 installer)
#   3. Node LTS from nodejs.org        (only needed to build the frontend once)
#   4. Copy the repo to ~/aria and .env into it (with OLLAMA_BASE and
#      ARIA_OWNER_TOKEN from scripts/init_owner_auth.py; ALPACA_PAPER must
#      stay exactly "true")
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

# 3. launchd jobs for ARIA, SENTINEL (if it sits beside this repo) and the
#    watchdog. serverctl owns this so there is exactly one way to install them.
chmod +x scripts/serverctl scripts/watchdog.sh
scripts/serverctl install
echo "✓ launchd jobs installed"

# 4. Wait and verify — READY, not merely alive.
echo "waiting for the services..."
for i in $(seq 1 30); do
  if scripts/serverctl health >/dev/null 2>&1; then
    scripts/serverctl status
    exit 0
  fi
  sleep 2
done
scripts/serverctl status
echo "✗ not ready — scripts/serverctl logs aria   (and: scripts/mini_preflight.py)"
exit 1
