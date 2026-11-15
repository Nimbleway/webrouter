"""The bottom of the import graph: what ``web_router.types`` may import."""

from __future__ import annotations


def test_types_imports_nothing_of_ours() -> None:
    """The import graph is types <- constants <- exceptions <- everything else; the rows live here to keep it so.

    The exceptions are the decision record, the page types and the option levels, each kept apart in a leaf that
    imports nothing of ours either. A module-level ``__getattr__`` runs on attribute access, never at import time,
    so its import -- ``types``'s, of the benchmark models' old path -- is no edge in that graph.
    """
    import ast
    import inspect

    import web_router.types as types_module
    import web_router.types_decision as decision_module
    import web_router.types_errors as errors_module
    import web_router.types_options as options_module
    import web_router.types_page as page_module

    def ours(module: object) -> list[str]:
        tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
        lazy = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"]
        skipped = {id(inner) for node in lazy for inner in ast.walk(node)}
        return [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("web_router")
            and id(node) not in skipped
        ]

    leaves = {
        "web_router.types_decision",
        "web_router.types_page",
        "web_router.types_options",
        "web_router.types_errors",
    }
    assert set(ours(types_module)) == leaves
    assert ours(decision_module) == ours(page_module) == ours(options_module) == ours(errors_module) == []


def test_the_record_and_page_types_import_from_types_as_from_the_package() -> None:
    import web_router
    from web_router import types

    names = ("DecisionRecord", "DecisionHop", "DecisionAttempt", "PageState", "PageSnapshot", "Screenshot", "Recency")
    for name in names:
        assert getattr(types, name) is getattr(web_router, name)
