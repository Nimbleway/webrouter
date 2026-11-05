"""Measure the shape rules: a file is at most 400 lines and a function at most 50.

Files are measured under ``src/web_router/``, ``scripts/``, ``benchmarks/`` and
``tests/``; functions under all but ``tests/``. ``tests/test_shape.py`` holds
every file and function to the limits, except those recorded in
``tests/shape_baseline.json``, which may keep their size or shrink but never grow.

    make shape-baseline    # rewrite the baseline after a grandfathered file or function shrank

The baseline only gets shorter: regenerate it after shrinking something, never
to admit a new file or function over a limit -- split that one instead.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("src/web_router", "scripts", "benchmarks", "tests")
# Test functions are long by nature (setup, call, asserts in one place); only their files are held to a limit.
FILES_ONLY_DIRS = ("tests",)
BASELINE = ROOT / "tests" / "shape_baseline.json"
MAX_FILE_LINES = 400
MAX_FUNCTION_LINES = 50


def source_files(root: Path = ROOT) -> list[Path]:
    """Every ``.py`` file the rules cover, in a stable order."""
    files: list[Path] = []
    for directory in SOURCE_DIRS:
        files.extend(p for p in (root / directory).rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def function_lines(tree: ast.AST) -> Iterator[tuple[str, int]]:
    """Each function's qualified name and its length, ``def`` line to the end of its body, docstring included.

    Decorators are not counted. A name defined twice in one scope (a property's
    getter and setter) reports each; the caller keeps the larger.
    """

    def walk(node: ast.AST, prefix: str) -> Iterator[tuple[str, int]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                assert child.end_lineno is not None
                yield name, child.end_lineno - child.lineno + 1
                yield from walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def measure(root: Path = ROOT) -> tuple[dict[str, int], dict[str, int]]:
    """Every covered file's line count and every function's, keyed ``path`` and ``path::qualname``."""
    files: dict[str, int] = {}
    functions: dict[str, int] = {}
    for path in source_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        # Newlines only, as editors count: splitlines() also breaks on form feeds and other separators.
        files[rel] = text.count("\n") + (bool(text) and not text.endswith("\n"))
        if rel.split("/", 1)[0] in FILES_ONLY_DIRS:
            continue
        for qualname, lines in function_lines(ast.parse(text, filename=rel)):
            key = f"{rel}::{qualname}"
            functions[key] = max(lines, functions.get(key, 0))
    return files, functions


def violators(root: Path = ROOT) -> dict[str, dict[str, int]]:
    """Today's files and functions over a limit -- what the baseline records."""
    files, functions = measure(root)
    return {
        "files": {k: v for k, v in files.items() if v > MAX_FILE_LINES},
        "functions": {k: v for k, v in functions.items() if v > MAX_FUNCTION_LINES},
    }


def load_baseline(path: Path = BASELINE) -> dict[str, dict[str, int]]:
    data: dict[str, dict[str, int]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def render(baseline: dict[str, dict[str, int]]) -> str:
    return json.dumps({k: dict(sorted(v.items())) for k, v in baseline.items()}, indent=2) + "\n"


def main() -> int:
    """Rewrite the baseline, refusing to add an entry or grow one."""
    current = violators(ROOT)
    if not BASELINE.exists():
        # Recreating it would admit every oversized file and function at once: restore it from git instead.
        print(
            f"{BASELINE.relative_to(ROOT)} is missing; restore it with `git checkout -- {BASELINE.relative_to(ROOT)}`.",
            file=sys.stderr,
        )
        return 1
    old = load_baseline(BASELINE)
    grown = [
        f"{key} ({lines} lines)"
        for kind in ("files", "functions")
        for key, lines in current[kind].items()
        if lines > old[kind].get(key, 0)
    ]
    if grown:
        print("Not regenerating: these are new or grew past the baseline. Split them instead:", file=sys.stderr)
        print("\n".join(f"  {g}" for g in grown), file=sys.stderr)
        return 1
    BASELINE.write_text(render(current), encoding="utf-8")
    print(f"Wrote {BASELINE.relative_to(ROOT)}: {len(current['files'])} files, {len(current['functions'])} functions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
