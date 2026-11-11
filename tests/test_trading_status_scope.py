"""
tests/test_trading_status_scope.py
==================================
/api/trading/status reported an `order_engine` with zero orders and
halted=false, and /api/trading/orders returned an empty journal — while the
desk and the approval queue were sending real paper orders. Both views read
src/trading/orders.OrderEngine, which only TradingPipeline.run feeds, and no
production caller runs the pipeline. The numbers were true and the picture
was wrong.

The fix labels the journal's scope (`orders.JOURNAL_SCOPE`) rather than
rewiring execution. This file pins the claim inside that label — "no
production caller runs the pipeline" — so the day someone wires the pipeline
in, the test fails and the label has to change with it.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _imports_pipeline(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "src.trading.pipeline" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "src.trading.pipeline":
                return True
            if mod == "src.trading" and any(a.name == "pipeline" for a in node.names):
                return True
    return False


def test_no_production_code_runs_the_trading_pipeline():
    callers = []
    candidates = [ROOT / "main.py"]
    for base in ("src", "backend", "scripts"):
        candidates += [p for p in (ROOT / base).rglob("*.py") if "__pycache__" not in p.parts]
    for p in candidates:
        if p.parts[-2:] == ("trading", "pipeline.py"):
            continue
        if _imports_pipeline(p):
            callers.append(p.relative_to(ROOT).as_posix())
    assert callers == [], (
        f"{callers} now import src.trading.pipeline. If production orders go "
        f"through TradingPipeline, update src/trading/orders.JOURNAL_SCOPE "
        f"(production_path, note) and the src/trading docstring — they say it "
        f"does not.")


def test_scope_points_at_endpoints_that_exist():
    from backend.main import app
    from src.trading.orders import JOURNAL_SCOPE
    paths = {getattr(r, "path", None) for r in app.routes}
    assert JOURNAL_SCOPE["production_path"] is False
    for p in JOURNAL_SCOPE["production_orders_at"]:
        assert p in paths, f"JOURNAL_SCOPE sends readers to {p}, which is not a route"


# The label being SERVED is asserted in tests/test_trading_api.py, which has a
# real owner session.
