"""The bottom of the import graph: what ``web_router.types`` may import."""

from __future__ import annotations


def test_types_imports_nothing_of_ours() -> None:
    """The import graph is types <- constants <- exceptions <- everything else; the rows live here to keep it so.

    The one exception is the decision record, kept apart in a leaf that imports nothing of ours either.
    """
    import ast
    import inspect

    import web_router.types as types_module
    import web_router.types_decision as decision_module

    def ours(module: object) -> list[str]:
        tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
        return [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("web_router")
        ]

    assert ours(types_module) == ["web_router.types_decision"]
    assert ours(decision_module) == []
