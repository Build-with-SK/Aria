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


class OllamaProvider(Provider):
    name = "ollama"
    local = True

    def __init__(self, base_url: str = OLLAMA_BASE, num_ctx: int = DEFAULT_NUM_CTX):
        self.base_url = base_url
        self.num_ctx = int(num_ctx)

    def complete(self, model: str, messages: list[dict], *,
                 system: str = "", max_tokens: int = 400,
                 temperature: float = 0.4, timeout: float = 120.0) -> str:
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
            "model": model, "stream": False, "messages": msgs,
            "options": {"temperature": temperature, "top_p": 0.9,
                        "num_predict": max_tokens, "num_ctx": self.num_ctx},
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
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
