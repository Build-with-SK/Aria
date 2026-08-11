"""
scripts/serve.py
================
Run the backend as a background service, with somewhere for its output to go.

WHY THIS IS NOT JUST `python -m uvicorn`
----------------------------------------
The scheduled task runs under pythonw.exe so no console window appears at
logon. pythonw has no stdout and no stderr — they are None — and uvicorn's
logging writes to a StreamHandler on stderr the moment it starts. The task
therefore exited immediately with code 1 and no explanation anywhere, which is
the worst possible failure for something meant to run unattended: silent, and
invisible until you notice weeks later that nothing has accumulated.

So the process opens its own log before anything else touches a stream, and
points both handles at it. Anything that writes to stdout or stderr from here
on — uvicorn, the scheduler, a traceback at 3am — lands in a file you can read.

    data/logs/backend.log
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "backend.log"

# Keep one previous run rather than an unbounded file. A service that fills the
# disk is a service that takes the machine with it.
MAX_BYTES = 20 * 1024 * 1024


def _open_log():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_BYTES:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
    except OSError:
        pass
    # line-buffered, utf-8, replace on undecodable: a stray character in a
    # vendor's error message must not kill the server.
    return open(LOG_FILE, "a", buffering=1, encoding="utf-8", errors="replace")


def main() -> int:
    log = _open_log()
    sys.stdout = log
    sys.stderr = log
    # Some libraries capture sys.__stdout__ instead; point those at the log too.
    try:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
    except (OSError, AttributeError):
        pass   # not fatal — the Python-level redirect above still holds

    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)

    host = os.environ.get("ARIA_HOST", "127.0.0.1")
    port = int(os.environ.get("ARIA_PORT", "8000"))

    import uvicorn
    print(f"--- ARIA backend starting on {host}:{port} ---", flush=True)
    # log_config=None: this process already owns its streams, and uvicorn's
    # default config would install handlers that fight with the redirect.
    uvicorn.run("backend.main:app", host=host, port=port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
