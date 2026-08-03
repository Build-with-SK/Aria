"""
src/v5/modules — the multi-strategy research engine.

Importing this package registers every module with src.v5.registry. Add a new
engine by writing it in one of these files (or a new one, imported here) and
decorating it with @module(...); nothing else in the platform needs to change.
"""
from src.v5.modules import (behavioural, fundamental, machine, macro,  # noqa: F401
                            price, quant, volatility)

__all__ = ["fundamental", "price", "quant", "macro", "volatility", "machine", "behavioural"]
