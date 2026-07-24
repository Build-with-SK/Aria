#!/bin/bash
# ============================================================================
# ARIA watchdog — run every 10 min by com.aria.watchdog.plist.
# If /api/desk/health is down or stale, kick the backend and ntfy once.
# ============================================================================
cd "$(dirname "$0")/.."
NTFY_TOPIC="$(grep -E '^\s*"?ntfy_topic"?\s*[:=]' data/desk_config.json 2>/dev/null \
  | sed -E 's/.*[:=]\s*"?([^",}]*)"?.*/\1/' | tr -d ' ')"
STAMP=logs/watchdog.state

notify() {
  [ -n "$NTFY_TOPIC" ] && curl -s -m 8 -d "$1" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

HEALTH="$(curl -s -m 8 http://127.0.0.1:8000/api/desk/health 2>/dev/null)"
OK="$(printf '%s' "$HEALTH" | grep -o '"ok"[: ]*true')"

if [ -n "$OK" ]; then
  # healthy — clear any prior alert latch
  [ -f "$STAMP" ] && { rm -f "$STAMP"; notify "✓ ARIA desk recovered"; }
  exit 0
fi

# unhealthy: restart the backend agent
launchctl kickstart -k "gui/$(id -u)/com.aria.backend" 2>/dev/null \
  || { launchctl unload "$HOME/Library/LaunchAgents/com.aria.backend.plist" 2>/dev/null; \
       launchctl load "$HOME/Library/LaunchAgents/com.aria.backend.plist" 2>/dev/null; }

# notify once per outage (latch file), not every 10 min
if [ ! -f "$STAMP" ]; then
  touch "$STAMP"
  notify "⚠ ARIA desk unhealthy — backend restarted. health: ${HEALTH:-no response}"
fi
