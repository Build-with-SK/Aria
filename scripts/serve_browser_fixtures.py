"""Read-only local fixture origin for real-browser checks of the actual build.

No backend/broker imports, credentials, trading calls, or writable API routes.
Usage: python scripts/serve_browser_fixtures.py --dist <staging-dist>
       --control <json-state> --log <private-jsonl> [--port 0]
Control keys: health_up (default true), queue_error, signals_error.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def fixtures():
    base = dict(created_at="2026-10-17T12:00:00Z", side="buy", qty=2, order_type="market",
                asset_class="equity", est_value=202, risk_amount=10, signal_score=70,
                confidence="HIGH", broker="alpaca", stop_loss=96, take_profit=111)
    trades = [
        {**base, "id": "fixture-pending", "ticker": "AAPL", "status": "pending",
         "thesis_summary": "Synthetic approval display — no order can be submitted"},
        {**base, "id": "fixture-unknown", "ticker": "BTC-USD", "status": "unknown",
         "client_order_id": "fixture-uncertain-id", "order_status": "unknown"},
        {**base, "id": "fixture-filled", "ticker": "MSFT", "status": "executed",
         "order_status": "filled", "filled_qty": 2, "fill_price": 101,
         "broker_order_id": "fixture-confirmed", "order_terminal": True},
        {**base, "id": "fixture-partial", "ticker": "NVDA", "status": "executed",
         "order_status": "partial", "filled_qty": 1, "fill_price": 100,
         "broker_order_id": "fixture-partial", "order_terminal": True},
        {**base, "id": "fixture-risk", "ticker": "TSLA", "status": "rejected",
         "rejection_reason": "RISK REJECTED: concentration limit exceeded; nothing sent"},
    ]
    signals = {ticker: dict(ticker=ticker, name=f"Synthetic {ticker}", action=action,
                            composite_score=score, current_price=101, asset_class="equity")
               for ticker, score, action in [("AAPL", 70, "BUY"), ("TSLA", -60, "SELL")]}
    return {
        "/health": {"status": "ok"},
        "/api/auth/me": {"authenticated": True, "owner": True, "role": "owner"},
        "/api/auth/status": {"authentication_configured": True},
        "/api/signals": {"signals": signals, "count": 2},
        "/api/summary": {"top_bullish": [{"ticker": "AAPL", "score": 70}], "top_bearish": []},
        "/api/execute/queue": {"trades": trades, "count": len(trades),
                               "stats": {"pending": 1, "executed": 2, "rejected": 1, "unknown": 1, "total": 5}},
        "/api/execute/brokers": {"alpaca": {"connected": False, "paper": True}, "ibkr": {"connected": False, "paper": True}},
        "/api/execute/positions": {"positions": []},
        "/api/portfolio/holdings": {"positions": [], "holdings": []},
        "/api/brain": {"status": "stopped", "status_reason": "Browser fixture; no workers or broker"},
        "/api/brain/activity": {"events": [], "status": "stopped"},
        "/api/news/feed": {"items": [], "articles": []},
        "/api/alerts": {"alerts": [], "count": 0},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    dist = args.dist.resolve(strict=True)
    responses, lock = fixtures(), threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, status, body, mime="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def record(self):
            with lock, args.log.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"method": self.command, "path": self.path}) + "\n")

        def do_GET(self):
            self.record()
            path = urlsplit(self.path).path
            state = json.loads(args.control.read_text()) if args.control.exists() else {}
            failed = (path == "/health" and not state.get("health_up", True) or
                      path == "/api/execute/queue" and state.get("queue_error") or
                      path == "/api/signals" and state.get("signals_error"))
            if path.startswith("/api/") or path == "/health":
                # Unprovisioned derivatives return an explicit failure, not a
                # malformed successful pricing response.
                if path.startswith("/api/quant/"):
                    self.send(503, b'{"detail":"Synthetic derivatives unavailable"}')
                    return
                body = {"detail": "Synthetic API failure"} if failed else responses.get(path, {})
                status = parse_qs(urlsplit(self.path).query).get("status", [None])[0]
                if path == "/api/execute/queue" and not failed and status:
                    trades = [row for row in body["trades"] if row["status"] == status]
                    body = {**body, "trades": trades, "count": len(trades)}
                self.send(503 if failed else 200, json.dumps(body).encode())
                return
            relative = path.removeprefix("/app/")
            file = (dist / relative).resolve()
            if not file.is_relative_to(dist):
                self.send(403, b"{}")
                return
            if not file.is_file():
                file = dist / "index.html"
            self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")

        def do_POST(self):
            self.record()
            self.send(405, b'{"detail":"Browser fixture refuses every state-changing operation"}')

        do_PATCH = do_PUT = do_DELETE = do_POST

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{server.server_port}/app/brain", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
