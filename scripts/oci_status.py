"""Report deployment controls and bridge health without private data contents."""
import json
from pathlib import Path
import urllib.request


def main():
    credentials = json.loads((Path.home() / ".config/aria-sentinel/owner-access.json").read_text())

    def get(port, route, key):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{route}",
            headers={"Authorization": "Bearer " + credentials[key]})
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)

    mode = get(8000, "/api/trading/status", "ARIA_OWNER_TOKEN")["mode"]
    desk = get(8000, "/api/desk/status", "ARIA_OWNER_TOKEN")
    inference = get(8000, "/api/inference/status", "ARIA_OWNER_TOKEN")
    bridge = get(8300, "/api/aria", "SENTINEL_TOKEN")
    voice = get(8300, "/api/voice", "SENTINEL_TOKEN")
    system = get(8000, "/api/system/health", "ARIA_OWNER_TOKEN")
    brain = get(8000, "/api/brain/status", "ARIA_OWNER_TOKEN").get("daemon", {})
    report = {
        "execution_mode": mode["effective_mode"], "live_gate_open": mode["live_gate_open"],
        "live_broker_access": mode["can_access_live_broker"],
        "auto_execute": desk["config"]["auto_execute"], "reflex_enabled": desk["config"]["reflex_enabled"],
        "desk_running": desk["running"], "broker_connected": desk["account"]["connected"],
        "self_hosted_only": inference["policy"]["self_hosted_only"],
        "routing": {k: v["primary"] for k, v in inference["routing"].items()},
        "sentinel_aria_bridge": {k: bridge.get(k) for k in ("up", "authorized", "token_configured", "credential")},
        "sentinel_voice": {k: voice.get(k) for k in ("available", "voice", "loaded", "accent")},
        "workers": system.get("workers"),
        "brain": {k: brain.get(k) for k in ("running", "thinking", "cycle_count", "model", "step", "memory_count", "last_cycle_at")},
    }
    print(json.dumps(report, indent=2))
    if mode["live_gate_open"] or mode["can_access_live_broker"] or desk["config"]["auto_execute"] or desk["config"]["reflex_enabled"]:
        raise SystemExit("Execution gate check failed")
    if not bridge.get("authorized") or not inference["policy"]["self_hosted_only"]:
        raise SystemExit("Bridge or inference policy check failed")


if __name__ == "__main__":
    main()
