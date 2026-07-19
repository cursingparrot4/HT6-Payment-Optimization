from __future__ import annotations

import ast
from pathlib import Path


def test_intent_module_does_not_import_money_or_optimizer_modules() -> None:
    intent_root = Path(__file__).parents[3] / "intent"
    forbidden = {
        "engine.dates",
        "engine.feasibility",
        "engine.greedy",
        "engine.ilp",
        "engine.objective",
        "engine.optimize",
        "engine.pareto",
        "engine.recommend",
        "engine.scoring",
        "engine.what_if",
        "explain",
    }
    imported: set[str] = set()
    for path in intent_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any(
        imported_name == forbidden_name
        or imported_name.startswith(forbidden_name + ".")
        for imported_name in imported
        for forbidden_name in forbidden
    )