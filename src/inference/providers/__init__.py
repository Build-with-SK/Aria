"""Provider adapters for the inference router."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Provider(ABC):
    """One inference backend. complete() returns the response text or raises
    a typed error from src.inference.errors."""

    name: str = "provider"
    local: bool = False     # True for on-box backends (Ollama)

    @abstractmethod
    def complete(self, model: str, messages: list[dict], *,
                 system: str = "", max_tokens: int = 400,
                 temperature: float = 0.4, timeout: float = 120.0) -> str: ...
