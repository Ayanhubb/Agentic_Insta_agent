"""The generic runtime package does not import product publishers or execute code."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "agent_runtime"
FORBIDDEN_MODULES = {"agent", "tools", "festivals", "services", "scheduler", "instagram", "importlib", "subprocess"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_TEXT = (
    "instagram",
    "graph.facebook",
    "graph.instagram",
    "meta_access_token",
    "enqueue_instagram",
)


def _module_root(name: str | None) -> str:
    if not name:
        return ""
    return name.split(".", 1)[0]


def test_runtime_package_has_no_product_or_dynamic_execution_imports() -> None:
    files = list(ROOT.glob("*.py"))
    assert files
    for path in files:
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        for needle in FORBIDDEN_TEXT:
            assert needle not in lowered, f"{path.name} contains {needle}"
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert _module_root(alias.name) not in FORBIDDEN_MODULES
            elif isinstance(node, ast.ImportFrom):
                assert _module_root(node.module) not in FORBIDDEN_MODULES
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS
