"""
Anthropic (Claude API) provider adapter — urllib, key from ANTHROPIC_API_KEY.
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

API_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(Provider):
    name = "anthropic"
    local = False

    def complete(self, model: str, messages: list[dict], *,
                 system: str = "", max_tokens: int = 400,
                 temperature: float = 0.4, timeout: float = 60.0) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise FatalError("ANTHROPIC_API_KEY not set", self.name, model)
        body: dict = {"model": model, "max_tokens": max_tokens,
                      "temperature": temperature, "messages": messages}
        if system:
            body["system"] = system
        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            raise classify_http(e.code, detail or str(e), self.name, model) from e
        except urllib.error.URLError as e:
            raise RetryableError(f"connection failed: {e.reason}",
                                 self.name, model) from e
        except TimeoutError as e:
            raise RetryableError("timeout", self.name, model) from e
        except OSError as e:
            raise RetryableError(f"socket error: {e}", self.name, model) from e
        text = "".join(b.get("text", "") for b in out.get("content", [])).strip()
        if not text:
            raise FatalError(f"empty response: {out}", self.name, model)
        return text
