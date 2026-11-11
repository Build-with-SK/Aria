"""
Ollama provider adapter — urllib only, per project convention.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from src.inference.errors import FatalError, RetryableError, classify_http
from src.inference.providers import Provider

logger = logging.getLogger(__name__)

from src.inference.base import ollama_base
OLLAMA_BASE = ollama_base()   # env OLLAMA_BASE overrides (mini -> laptop)

#: Ollama's own default context is small (2K on older builds, 4K on newer),
#: and when a prompt exceeds it the server TRUNCATES and answers anyway — a
#: normal-looking reply from a model that never saw the end of the prompt.
#: With vault injection plus a debate transcript, that is the ordinary case,
#: not an edge one. So the window is set explicitly here and the same number
#: is what src/inference/context.py budgets against.
#:
#: 8192 fits a 7B at Q4_K_M inside 8 GB of VRAM with room for the KV cache.
#: Raising it is a VRAM decision, not a config preference: past roughly 16K on
#: this GPU the model spills to CPU and the desk tick stops being met.
DEFAULT_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))


def _read_stream(resp, on_token) -> dict:
    """Ollama's streaming reply: one JSON object per line, the last carrying
    `done: true`. Each content delta goes to `on_token`; the whole text is
    returned in the same shape as the non-streaming call, so everything after
    this — the empty-content check, the error handling — is unchanged.

    `thinking` deltas are never passed on: they are the model's working, and
    ARIA does not publish her chain of thought."""
    content = []
    for raw in resp:
        line = raw.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except ValueError:
            continue
        delta = ((chunk.get("message") or {}).get("content")) or ""
        if delta:
            content.append(delta)
            try:
                on_token(delta)
            except Exception:           # a dead listener never costs the answer
                pass
        if chunk.get("done"):
            break
    return {"message": {"content": "".join(content)}}


class OllamaProvider(Provider):
    name = "ollama"
    local = True
    #: Can hand each piece of a reply to a callback as it is generated. The
    #: router checks this rather than assuming, so a provider that cannot
    #: stream is still called correctly — it just delivers in one piece.
    streams = True

    def __init__(self, base_url: str = OLLAMA_BASE, num_ctx: int = DEFAULT_NUM_CTX):
        self.base_url = base_url
        self.num_ctx = int(num_ctx)

    def complete(self, model: str, messages: list[dict], *,
                 system: str = "", max_tokens: int = 400,
                 temperature: float = 0.4, timeout: float = 120.0,
                 on_token=None) -> str:
        # A message may carry `images`: a list of base64 PNG/JPEG strings, which
        # Ollama's chat API accepts on any vision-capable model. This is what
        # lets her LOOK at something — a chart, a screenshot, a filing page —
        # rather than only read numbers about it. Text-only models ignore the
        # field, so passing it is safe; whether the model can actually see is
        # checked in src/brain/cognitive/sight.py, not guessed at here.
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {k: v for k, v in m.items() if k in ("role", "content", "images")}
            for m in messages]
        payload = json.dumps({
            "model": model, "stream": bool(on_token), "messages": msgs,
            "options": {"temperature": temperature, "top_p": 0.9,
                        "num_predict": max_tokens, "num_ctx": self.num_ctx},
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = (_read_stream(r, on_token) if on_token
                       else json.loads(r.read()))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            # Ollama 404s an unknown model — fatal for this candidate
            raise classify_http(e.code, body or str(e), self.name, model) from e
        except urllib.error.URLError as e:
            raise RetryableError(f"connection failed: {e.reason}",
                                 self.name, model) from e
        except TimeoutError as e:
            raise RetryableError("timeout", self.name, model) from e
        except OSError as e:
            raise RetryableError(f"socket error: {e}", self.name, model) from e
        try:
            return out["message"]["content"].strip()
        except (KeyError, TypeError) as e:
            raise FatalError(f"malformed response: {out}", self.name, model) from e
