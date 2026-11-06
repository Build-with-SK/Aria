"""Check cloud services without exposing credentials or response contents."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def probe(port, path, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(2_000_000)
            return response.status, json.loads(body) if path == "/ready" else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError):
        return 0, None


def main():
    credentials = json.loads((Path.home() / ".config/aria-sentinel/owner-access.json").read_text())
    failures = []
    for app, port, private, ui, key in (
        ("aria", 8000, "/api/portfolio", "/app/", "ARIA_OWNER_TOKEN"),
        ("sentinel", 8300, "/api/health", "/", "SENTINEL_TOKEN"),
    ):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            code, payload = probe(port, "/ready")
            if code == 200 and payload and payload.get("ready"):
                break
            time.sleep(2)
        row = {"app": app}
        for path in ("/health", "/ready", ui):
            code, payload = probe(port, path)
            row[path] = code
            if code != 200 or (path == "/ready" and not payload.get("ready")):
                failures.append(f"{app} {path}")
        row["anonymous_private"] = probe(port, private)[0]
        row["owner_private"] = probe(port, private, credentials[key])[0]
        if row["anonymous_private"] not in (401, 403) or row["owner_private"] != 200:
            failures.append(f"{app} authentication")
        print(json.dumps(row))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
