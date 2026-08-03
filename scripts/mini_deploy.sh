#!/bin/bash
# ============================================================================
# scripts/mini_deploy.sh — one command to update and verify ARIA on the mini.
#
#   cd ~/aria && bash scripts/mini_deploy.sh
#
# Pulls, installs, runs the preflight, and REFUSES TO START if the preflight
# finds a blocker. That refusal is the point: this box runs unattended with a
# broker connection, so "it mostly came up" is not good enough. If something is
# wrong you want it to stop here, loudly, rather than run 24/7 in a state nobody
# checked.
#
# Safe to re-run. It does not start anything until the checks pass.
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
echo "ARIA mini deploy — $ROOT"

PY=venv/bin/python
PIP=venv/bin/pip

# ── 1. code ─────────────────────────────────────────────────────────────────
if [ -d .git ]; then
  echo
  echo "== pulling =="
  # Refuse to clobber local edits; on the mini a dirty tree usually means
  # someone hand-patched something and it should not vanish silently.
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "!! working tree is dirty. Commit or stash first — not overwriting."
    git status --short
    exit 1
  fi
  git pull --ff-only || { echo "!! pull failed"; exit 1; }
fi

# ── 2. environment ──────────────────────────────────────────────────────────
if [ ! -x "$PY" ]; then
  echo
  echo "== creating venv =="
  python3 -m venv venv || { echo "!! venv creation failed"; exit 1; }
fi

echo
echo "== dependencies =="
"$PIP" install -q --upgrade pip
# chromadb and sentence-transformers are deliberately NOT installed here; they
# are commented out in requirements.txt for low-power hosts. The vault degrades
# to empty context, which callers already handle.
"$PIP" install -q -r requirements.txt || { echo "!! pip install failed"; exit 1; }
echo "   ok"

# ── 3. preflight — the gate ─────────────────────────────────────────────────
echo
echo "== preflight =="
"$PY" scripts/mini_preflight.py
STATUS=$?

echo
if [ $STATUS -ne 0 ]; then
  cat <<'EOF'
=============================================================
PREFLIGHT FOUND BLOCKERS — not starting.
Fix the items marked FAIL above, then re-run this script.

The one that matters most: if auto_execute is true, this
machine could place orders with nobody watching. Confirm it
is deliberate AND that the account is paper before starting.
=============================================================
EOF
  exit 1
fi

echo "preflight clean."
echo
cat <<EOF
To start the backend now (LOOPBACK ONLY — read the warning below):
  cd $ROOT && venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

!! Do NOT bind --host 0.0.0.0 as things stand. None of the 106 endpoints
   require authentication, and they include /api/execute/approve/<id> and
   /api/desk/auto-execute. On 0.0.0.0 anyone on the Wi-Fi can approve a trade
   or arm auto-execution, and every chat answer is grounded in the owner's
   private Obsidian vault. The approval gate is currently enforced by the port
   being unreachable, not by a check.

   To reach the UI from another machine, tunnel instead of binding wide:
     ssh -N -L 8000:127.0.0.1:8000 <user>@<mini>   # run on the other machine

To run it permanently (survives logout/reboot), install the launchd agent
described in docs/MAC_MINI_SETUP.md.

Nightly pipeline (cron -e):
  30 7 * * 1-5 cd $ROOT && venv/bin/python main.py >> data/cron.log 2>&1
EOF
