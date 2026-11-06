"""Start one private app, initializing ARIA's shared Chroma client serially.

ARIA's vault warmer and brain start concurrently and share a Chroma directory.
Chroma 1.5.9 publishes a partially initialized system in its process cache.
Holding a fully initialized client before either thread starts prevents that
startup race without changing application code or its authorization rules.
"""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", choices=("aria", "sentinel"))
    args = parser.parse_args()
    root = Path.home() / "apps" / args.app
    os.chdir(root)
    sys.path.insert(0, str(root))
    retained_client = None
    if args.app == "aria":
        import chromadb
        retained_client = chromadb.PersistentClient(path=str(root / "data/brain_memory/chromadb"))
        retained_client.heartbeat()
        print("ARIA shared memory store initialized before worker startup", flush=True)
    import uvicorn
    module, port = ("backend.main:app", 8000) if args.app == "aria" else ("sentinel.server:app", 8300)
    uvicorn.run(module, host="127.0.0.1", port=port, workers=1,
                server_header=False, proxy_headers=False)
    # Explicitly retain it through the complete lifetime of Uvicorn.
    if retained_client is not None:
        retained_client.close()


if __name__ == "__main__":
    main()
