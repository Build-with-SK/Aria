#!/bin/bash
# ============================================================================
# scripts/watchdog.sh — run every 10 min by com.aria.watchdog.plist.
#
# WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT
# -----------------------------------------------
#   RESTARTS a service only when it has stopped ANSWERING (/health fails): that
#   is the one failure a restart reliably fixes — a crash KeepAlive missed, a
#   hung event loop.
#
#   NOTIFIES, once per outage, when a service answers but is NOT READY, when
#   the tunnel is down, when the disk is nearly full or memory is critically
#   short. Restarting does not fix "no owner credential configured" or "disk
#   full", and a watchdog that restarts in a loop over those makes the machine
#   worse, not better.
#
# THE BUG THIS REPLACES
# ---------------------
# The previous version polled /api/desk/health with no credential. That route
# is owner-only, so once ARIA_OWNER_TOKEN was set every probe got 401, read as
# "unhealthy", and the backend was restarted every ten minutes, forever. The
# desk-staleness check survives, but authenticated — and it passes the token to
# curl on stdin, not argv, so it never appears in `ps` output.
# ============================================================================
cd "$(dirname "$0")/.." || exit 1
ARIA_HOME="$(pwd)"
SENTINEL_HOME="${SENTINEL_HOME:-}"
[ -z "$SENTINEL_HOME" ] && [ -d "$ARIA_HOME/../atlas/sentinel" ] && SENTINEL_HOME="$(cd "$ARIA_HOME/../atlas" && pwd)"

LOG_DIR="$ARIA_HOME/data/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/watchdog.log"
STATE="$LOG_DIR/watchdog.state"; mkdir -p "$STATE"
CTL="$ARIA_HOME/scripts/serverctl"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >> "$LOG"; }

NTFY_TOPIC="$(grep -E '^\s*"?ntfy_topic"?\s*[:=]' data/desk_config.json 2>/dev/null \
  | sed -E 's/.*[:=]\s*"?([^",}]*)"?.*/\1/' | tr -d ' ')"
notify() { [ -n "$NTFY_TOPIC" ] && curl -s -m 8 -d "$1" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1; log "notify: $1"; }

# Latch per condition: tell the owner once when it starts and once when it ends,
# not every ten minutes in between.
raise() {   # name message
  if [ ! -f "$STATE/$1" ]; then touch "$STATE/$1"; notify "⚠ $2"; else log "still: $2"; fi
}
clear_() {  # name message
  if [ -f "$STATE/$1" ]; then rm -f "$STATE/$1"; notify "✓ $2"; fi
}

code() { curl -s -o /dev/null -m 8 -w '%{http_code}' "$1"; }

check_service() {   # name url target
  local name="$1" url="$2" target="$3"
  if [ "$(code "$url/health")" != 200 ]; then
    log "$name not answering — restarting"
    "$CTL" restart "$target" >> "$LOG" 2>&1
    raise "$target-down" "$name was not answering and has been restarted"
    return
  fi
  clear_ "$target-down" "$name is answering again"
  if [ "$(code "$url/ready")" != 200 ]; then
    raise "$target-unready" "$name is running but NOT READY (see its log — e.g. no owner credential, disk, data dir)"
  else
    clear_ "$target-unready" "$name is ready"
  fi
}

check_service ARIA http://127.0.0.1:8000 aria
[ -n "$SENTINEL_HOME" ] && check_service SENTINEL http://127.0.0.1:8300 sentinel

# The desk loop, which can wedge while the web server stays up. Needs the owner
# token; skipped (not failed) when the credential is stored only as a hash.
TOKEN="$(grep -E '^ARIA_OWNER_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2-)"
if [ -n "$TOKEN" ]; then
  DESK="$(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" \
          | curl -s -m 8 -K - http://127.0.0.1:8000/api/desk/health)"
  if printf '%s' "$DESK" | grep -q '"ok"[: ]*true'; then
    clear_ desk "ARIA desk loop recovered"
  elif [ -n "$DESK" ]; then
    log "desk unhealthy — restarting ARIA"
    "$CTL" restart aria >> "$LOG" 2>&1
    raise desk "ARIA desk loop was stale; backend restarted"
  fi
fi
unset TOKEN

# The tunnel: without it nothing is reachable from outside.
if pgrep -x cloudflared >/dev/null; then clear_ tunnel "tunnel is back"
else raise tunnel "cloudflared is not running — remote access is down"; fi

# Disk: the slow failure that takes a server with it.
FREE_MB="$(df -m "$ARIA_HOME" | awk 'NR==2 {print $4}')"
if [ "${FREE_MB:-0}" -lt 1024 ]; then raise disk "only ${FREE_MB} MB of disk left"
else clear_ disk "disk has room again (${FREE_MB} MB)"; fi

# Memory: an 8 GB mini running a local model can be pushed into swap death.
if command -v memory_pressure >/dev/null; then
  PCT="$(memory_pressure 2>/dev/null | awk -F': ' '/free percentage/ {gsub("%","",$2); print int($2)}')"
  if [ -n "$PCT" ] && [ "$PCT" -lt 10 ]; then raise memory "memory critically short (${PCT}% free)"
  elif [ -n "$PCT" ]; then clear_ memory "memory pressure normal (${PCT}% free)"; fi
fi

# launchd's captured stdout/stderr files are not rotated by anything. They should
# hold almost nothing (the apps log to rotating files), but a crash loop can fill
# them, so keep the last 2,000 lines of any that pass 20 MB.
for f in "$LOG_DIR"/launchd-*.log "$SENTINEL_HOME/data/logs"/launchd-*.log "$LOG"; do
  [ -f "$f" ] || continue
  if [ "$(wc -c < "$f")" -gt 20000000 ]; then
    tail -n 2000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    log "trimmed $f"
  fi
done
