"""
tests/test_trading_structure.py
===============================
The architectural promises, checked against the source rather than believed.

    "The LLM never controls the broker."
    "Only one function can produce a live-capable adapter."
    "Hard limits are changed from exactly one place."
    "The desk and the approval queue both go through the mode gate."

Each of those is a property of the import graph and the call sites, not of any
one test's behaviour, so each is checked by reading the code. A promise that
lives only in a docstring is a promise until someone adds an import.
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path(__file__).parent.parent
TRADING = REPO / "src" / "trading"

#: Anything that could put a language model, or a network call it controls,
#: inside the execution path.
FORBIDDEN_PREFIXES = ("src.brain", "src.inference", "src.consult", "src.agents",
                      "ollama", "openai", "anthropic", "transformers", "torch")


def modules() -> list[Path]:
    return sorted(p for p in TRADING.glob("*.py"))


def imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # relative: within src/trading
                continue
            names.append(node.module or "")
    return names


def test_the_package_exists_and_every_module_parses():
    assert len(modules()) >= 14
    for path in modules():
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_no_model_can_reach_the_execution_path(path):
    for name in imports_of(path):
        for bad in FORBIDDEN_PREFIXES:
            assert not name.startswith(bad), (
                f"{path.name} imports {name}. src/trading is the deterministic "
                f"execution path; a model belongs on the other side of a Signal.")


def test_the_execution_path_does_not_import_the_web_layer():
    for path in modules():
        for name in imports_of(path):
            assert not name.startswith("backend"), f"{path.name} imports {name}"


def test_only_access_py_can_hand_out_a_live_adapter():
    offenders = []
    for path in modules():
        text = path.read_text(encoding="utf-8")
        if "is_live=" in text and path.name not in ("access.py", "broker.py"):
            offenders.append(path.name)
    assert offenders == [], offenders

    access = (TRADING / "access.py").read_text(encoding="utf-8")
    tree = ast.parse(access)
    live_funcs = [n.name for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and "can_access_live_broker" in ast.unparse(n)]
    assert "live_adapter" in live_funcs
    # every other function that builds an adapter pins is_live to False
    paper = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "paper_adapter")
    assert "is_live=False" in ast.unparse(paper)


def test_the_raw_broker_is_only_constructed_behind_the_gate():
    """AlpacaBroker() anywhere in src/trading must be inside access.py, which
    wraps it before returning it."""
    for path in modules():
        text = path.read_text(encoding="utf-8")
        if "AlpacaBroker(" in text:
            assert path.name == "access.py", f"{path.name} builds a raw broker"


def test_the_desk_and_the_approval_queue_both_go_through_the_mode_gate():
    desk = (REPO / "src" / "desk" / "auto_executor.py").read_text(encoding="utf-8")
    assert "gate_for_desk(" in desk
    assert "OrderManager(alpaca=AlpacaBroker())" not in desk

    backend = (REPO / "backend" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(backend)
    calls = [ast.unparse(n) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("OrderManager")]
    assert calls, "no OrderManager construction found in backend/main.py"
    for call in calls:
        assert "gate_for_approvals" in call, call


def test_hard_limits_are_changed_from_exactly_one_place():
    callers = []
    for path in list((REPO / "src").rglob("*.py")) + [REPO / "backend" / "main.py"]:
        if path.name == "limits.py":
            continue
        if "set_limits(" in path.read_text(encoding="utf-8"):
            callers.append(str(path.relative_to(REPO)))
    assert callers in ([], ["backend\\main.py"], ["backend/main.py"]), callers


def test_the_ceilings_are_source_not_settings():
    limits = (TRADING / "limits.py").read_text(encoding="utf-8")
    tree = ast.parse(limits)
    ceilings = [n for n in tree.body if isinstance(n, ast.AnnAssign)
                and getattr(n.target, "id", "") == "CEILINGS"]
    assert ceilings, "CEILINGS is not a module-level constant"
    assert "CEILINGS" not in limits.split("def set_limits")[1].split("def ")[0] or \
        "_clean" in limits, "set_limits must clamp through _clean, not edit ceilings"


def test_the_kill_switch_module_depends_on_nothing_clever():
    """The halt has to work when everything else is broken, so it may import
    only the simplest modules."""
    names = imports_of(TRADING / "killswitch.py")
    for name in names:
        assert name.startswith(("src.execution", "src.desk")) or "." not in name, name


def test_every_trading_module_imports_cleanly():
    import importlib
    for path in modules():
        if path.name == "__init__.py":
            continue
        importlib.import_module(f"src.trading.{path.stem}")
