"""Real loopback reverse proxy, signed login, built SPA and streamed chat.
No external broker/provider; test isolation owns every state file.
"""
import json
import os
import threading
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from test_final_runtime import listening


def test_proxy_login_built_shell_and_incremental_sse(monkeypatch):
    dist = os.environ.get('ARIA_ACCEPTANCE_DIST')
    if not dist:
        pytest.skip('set ARIA_ACCEPTANCE_DIST to the staged production build')
    from backend import main as backend
    from src.auth import policy, hardening
    from src.inference import router
    from starlette.routing import Mount
    # Proxy discovery is intentionally process-persistent in the application.
    # This simulated deployment must not retire loopback inference in later tests.
    monkeypatch.setattr(policy, '_PROXY_SEEN', dict(policy._PROXY_SEEN))
    monkeypatch.setenv('ARIA_TESTING', '1')
    monkeypatch.setenv('ARIA_OWNER_TOKEN', 'integration-fixture-owner')
    monkeypatch.delenv('ARIA_OWNER_TOKEN_HASH', raising=False)
    monkeypatch.setenv('ARIA_TRUST_PROXY', 'true')
    monkeypatch.setattr(policy, 'owner_token_hash', lambda: '')
    hardening._limiter._hits.clear()
    monkeypatch.setattr(backend, '_vault_context', lambda *a, **k: '')
    monkeypatch.setattr(backend, '_ondemand_ticker_context', lambda *a, **k: '')
    monkeypatch.setattr(backend.app.router, 'routes', [Mount('/app', app=backend._SPAStatics(
        directory=dist, html=True))] + list(backend.app.router.routes))
    release = threading.Event()
    class Result:
        text, model, provider, degraded, primary = 'Fixture answer.', 'stub', 'stub', False, ''
    class Router:
        def complete(self, *a, on_token=None, **kw):
            assert release.wait(8), 'proxy buffered the initial SSE event'
            if on_token:
                on_token('Fixture answer.')
            return Result()
    monkeypatch.setattr(router, 'get_router', lambda: Router())
    with listening(backend.app) as upstream:
        upstream_port = int(upstream.rsplit(':', 1)[1])
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def forward(self):
                headers = {'X-Forwarded-For': '203.0.113.77', 'X-Forwarded-Proto': 'http'}
                for name in ('Content-Type', 'Cookie', 'Authorization'):
                    if self.headers.get(name): headers[name] = self.headers[name]
                size = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(size) if size else None
                conn = http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=10)
                try:
                    conn.request(self.command, self.path, body=body, headers=headers)
                    response = conn.getresponse()
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                            self.send_header(name, value)
                    self.end_headers()
                    while chunk := response.read1(4096):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                finally: conn.close()
            do_GET = do_POST = forward
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True); thread.start()
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{proxy.server_port}', trust_env=False, timeout=12) as client:
                assert client.get('/health').status_code == 200
                denied = client.get('/api/execute/queue')
                assert denied.status_code == 401, denied.text
                shell = client.get('/app/portfolio')
                assert shell.status_code == 200 and 'no-cache' == shell.headers['cache-control']
                import re
                asset = re.search(r'src="([^"]+\.js)"', shell.text).group(1)
                assert client.get(asset).status_code == 200
                login = client.post('/api/auth/login', json={'token': 'integration-fixture-owner'})
                assert login.status_code == 200, login.text
                assert 'HttpOnly' in login.headers['set-cookie']
                assert client.get('/api/execute/queue').status_code == 200
                with client.stream('POST', '/api/chat/stream', json={'messages':[
                        {'role':'user','content':'Describe this fixture.'}]}) as stream:
                    assert stream.status_code == 200
                    assert stream.headers['x-accel-buffering'] == 'no'
                    assert stream.headers['content-type'].startswith('text/event-stream')
                    lines = stream.iter_lines()
                    assert next(lines) == 'event: ack'
                    release.set()
                    rest = '\n'.join(lines)
                    assert 'event: token' in rest and 'event: done' in rest
                    assert 'Fixture answer.' in rest
        finally:
            release.set(); proxy.shutdown(); proxy.server_close(); thread.join(5)
