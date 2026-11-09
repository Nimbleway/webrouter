"""The bottom of the import graph: what ``web_router.types`` may import."""

from __future__ import annotations


def test_types_imports_nothing_of_ours() -> None:
    """The import graph is types <- constants <- exceptions <- everything else; the rows live here to keep it so.

    The exceptions are the decision record, the page types, the option levels, a response's preset meta and a
    browser field table's rows, each kept apart in a leaf that imports nothing of ours either. A module-level
    ``__getattr__`` runs on attribute access, never at import time, so its import -- ``types``'s, of the benchmark
    models' old path -- is no edge in that graph.
    """
    import ast
    import inspect

    import web_router.types as types_module
    import web_router.types_browser as browser_module
    import web_router.types_decision as decision_module
    import web_router.types_meta as meta_module
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
        "web_router.types_meta",
        "web_router.types_browser",
    }
    assert set(ours(types_module)) == leaves
    leaf_modules = (decision_module, page_module, options_module, meta_module, browser_module)
    assert {module.__name__: ours(module) for module in leaf_modules if ours(module)} == {}


def test_the_record_and_page_types_import_from_types_as_from_the_package() -> None:
    import web_router
    from web_router import types

    names = ("DecisionRecord", "DecisionHop", "DecisionAttempt", "PageState", "PageSnapshot", "Screenshot", "Recency")
    for name in names:
        assert getattr(types, name) is getattr(web_router, name)


def test_every_class_types_takes_from_a_leaf_is_re_exported() -> None:
    """``from web_router.types import X`` must pass ``mypy --strict``, which only an ``X as X`` import allows."""
    import ast
    import importlib
    import inspect

    from web_router import types

    plain = [
        alias.name
        for node in ast.walk(ast.parse(inspect.getsource(types)))
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("web_router.types_")
        for alias in node.names
        if isinstance(getattr(importlib.import_module(node.module), alias.name), type) and alias.asname != alias.name
    ]
    assert plain == [], f"import these as `X as X` in types.py: {plain}"
