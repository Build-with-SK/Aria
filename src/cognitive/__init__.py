"""
src.cognitive — ARIA's cognitive architecture.
The brain, memory systems, metacognition, contradiction detection, and tool dispatch.
"""

from .working_memory import WorkingMemory
from .long_term_memory import LongTermMemory
from .metacognition import MetacognitionEngine
from .contradiction_engine import ContradictionEngine, Contradiction, ContradictionReport
from .tool_dispatcher import ToolDispatcher
from .obsidian_export import ObsidianExporter
from .aria_core import AriaCore

__all__ = [
    "WorkingMemory",
    "LongTermMemory",
    "MetacognitionEngine",
    "ContradictionEngine",
    "Contradiction",
    "ContradictionReport",
    "ToolDispatcher",
    "ObsidianExporter",
    "AriaCore",
]
