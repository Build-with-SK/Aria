"""
Ollama provider adapter — urllib only, per project convention.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from src.inference.errors import FatalError, RetryableError, classify_http
from src.inference.providers import Provider

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"


class OllamaProvider(Provider):
    name = "ollama"
    local = True

    def __init__(self, base_url: str = OLLAMA_BASE):
        self.base_url = base_url

    def complete(self, model: str, messages: list[dict], *,
                 system: str = "", max_tokens: int = 400,
                 temperature: float = 0.4, timeout: float = 120.0) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        payload = json.dumps({
            "model": model, "stream": False, "messages": msgs,
            "options": {"temperature": temperature, "top_p": 0.9,
                        "num_predict": max_tokens},
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
