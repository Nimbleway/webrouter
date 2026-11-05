"""Measure the file-length rule: a Python file is at most 400 lines.

Covers ``src/web_router/``, ``scripts/``, ``benchmarks/`` and ``tests/``.
``tests/test_shape.py`` holds every file to the limit, except those recorded in
``tests/shape_baseline.json``, which may keep their size or shrink but never
grow. Ruff has no file-length rule, so this one lives here; functions are
bounded by ruff instead (``C901`` and ``PLR0915`` in ``pyproject.toml``).

    make shape-baseline    # rewrite the baseline after a grandfathered file shrank

The baseline only gets shorter: regenerate it after shrinking a file, never to
admit a new file over the limit -- split that one instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("src/web_router", "scripts", "benchmarks", "tests")
BASELINE = ROOT / "tests" / "shape_baseline.json"
MAX_FILE_LINES = 400


def source_files(root: Path = ROOT) -> list[Path]:
    """Every ``.py`` file the rule covers, in a stable order."""
    files: list[Path] = []
    for directory in SOURCE_DIRS:
        files.extend(p for p in (root / directory).rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def measure(root: Path = ROOT) -> dict[str, int]:
    """Every covered file's line count, keyed by its path from ``root``."""
    files: dict[str, int] = {}
    for path in source_files(root):
        text = path.read_text(encoding="utf-8")
        # Newlines only, as editors count: splitlines() also breaks on form feeds and other separators.
        files[path.relative_to(root).as_posix()] = text.count("\n") + (bool(text) and not text.endswith("\n"))
    return files


def violators(root: Path = ROOT) -> dict[str, int]:
    """Today's files over the limit -- what the baseline records."""
    return {key: lines for key, lines in measure(root).items() if lines > MAX_FILE_LINES}


def load_baseline(path: Path = BASELINE) -> dict[str, int]:
    data: dict[str, int] = json.loads(path.read_text(encoding="utf-8"))
    return data


def render(baseline: dict[str, int]) -> str:
    return json.dumps(dict(sorted(baseline.items())), indent=2) + "\n"


def main() -> int:
    """Rewrite the baseline, refusing to add an entry or grow one."""
    current = violators(ROOT)
    if not BASELINE.exists():
        # Recreating it would admit every oversized file at once: restore it from git instead.
        print(
            f"{BASELINE.relative_to(ROOT)} is missing; restore it with `git checkout -- {BASELINE.relative_to(ROOT)}`.",
            file=sys.stderr,
        )
        return 1
    old = load_baseline(BASELINE)
    grown = [f"{key} ({lines} lines)" for key, lines in current.items() if lines > old.get(key, 0)]
    if grown:
        print("Not regenerating: these are new or grew past the baseline. Split them instead:", file=sys.stderr)
        print("\n".join(f"  {g}" for g in grown), file=sys.stderr)
        return 1
    BASELINE.write_text(render(current), encoding="utf-8")
    print(f"Wrote {BASELINE.relative_to(ROOT)}: {len(current)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
