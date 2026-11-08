"""The bottom of the import graph: what ``web_router.types`` may import."""

from __future__ import annotations


def test_types_imports_nothing_of_ours() -> None:
    """The import graph is types <- constants <- exceptions <- everything else; the rows live here to keep it so.

    The exceptions are the decision record, the page types, a response's preset meta and a browser field table's rows,
    each kept apart in a leaf that imports nothing of ours either.
    """
    import ast
    import inspect

    import web_router.types as types_module
    import web_router.types_browser as browser_module
    import web_router.types_decision as decision_module
    import web_router.types_meta as meta_module
    import web_router.types_page as page_module

    def ours(module: object) -> list[str]:
        tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
        return [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("web_router")
        ]

    leaves = {"web_router.types_decision", "web_router.types_page", "web_router.types_meta", "web_router.types_browser"}
    assert set(ours(types_module)) == leaves
    assert ours(decision_module) == ours(page_module) == ours(meta_module) == ours(browser_module) == []


def test_the_record_and_page_types_import_from_types_as_from_the_package() -> None:
    import web_router
    from web_router import types

    for name in ("DecisionRecord", "DecisionHop", "DecisionAttempt", "PageState", "PageSnapshot", "Screenshot"):
        assert getattr(types, name) is getattr(web_router, name)
