"""The shape rules (AGENTS.md): a file is at most 400 lines and a function at most 50.

Files and functions already over a limit are recorded in ``shape_baseline.json``
and may keep their size or shrink, never grow. ``scripts/shape.py`` measures.
"""

from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import pytest

from scripts import shape

RULE = "See 'Keep files and functions small' in AGENTS.md."
FILE_OVER = "{key} is {lines} lines; a file is at most {limit}. Move code into its own module. " + RULE
FILE_GREW = "{key} grew to {lines} lines from its baseline of {was}; a grandfathered file may only shrink. " + RULE
FUNCTION_OVER = "{key} is {lines} lines; a function is at most {limit}. Split it. " + RULE
FUNCTION_GREW = (
    "{key} grew to {lines} lines from its baseline of {was}; a grandfathered function may only shrink. " + RULE
)
STALE = (
    "{key} is in tests/shape_baseline.json but is now {lines} lines (baseline {was}). "
    "Run `make shape-baseline`: it lowers the entry, or drops it once under the limit."
)
GONE = "{key} is in tests/shape_baseline.json but no longer exists. Run `make shape-baseline` to drop it."


def _problems(
    measured: dict[str, int], baseline: dict[str, int], limit: int, over: str, grew: str
) -> tuple[list[str], list[str]]:
    """The violations, then the baseline entries to update."""
    violations: list[str] = []
    stale: list[str] = []
    for key, lines in measured.items():
        was = baseline.get(key)
        if was is None and lines > limit:
            violations.append(over.format(key=key, lines=lines, limit=limit))
        elif was is not None and lines > was:
            violations.append(grew.format(key=key, lines=lines, was=was))
        elif was is not None and lines < was:
            stale.append(STALE.format(key=key, lines=lines, was=was))
    stale.extend(GONE.format(key=key) for key in baseline if key not in measured)
    return violations, stale


@pytest.fixture(scope="module")
def measured() -> tuple[dict[str, int], dict[str, int]]:
    return shape.measure()


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict[str, int]]:
    return shape.load_baseline()


def test_files_stay_under_the_limit(
    measured: tuple[dict[str, int], dict[str, int]], baseline: dict[str, dict[str, int]]
) -> None:
    violations, _ = _problems(measured[0], baseline["files"], shape.MAX_FILE_LINES, FILE_OVER, FILE_GREW)
    assert not violations, "\n".join(violations)


def test_functions_stay_under_the_limit(
    measured: tuple[dict[str, int], dict[str, int]], baseline: dict[str, dict[str, int]]
) -> None:
    violations, _ = _problems(
        measured[1], baseline["functions"], shape.MAX_FUNCTION_LINES, FUNCTION_OVER, FUNCTION_GREW
    )
    assert not violations, "\n".join(violations)


def test_the_baseline_only_lists_what_is_still_over(
    measured: tuple[dict[str, int], dict[str, int]], baseline: dict[str, dict[str, int]]
) -> None:
    _, stale_files = _problems(measured[0], baseline["files"], shape.MAX_FILE_LINES, FILE_OVER, FILE_GREW)
    _, stale_functions = _problems(
        measured[1], baseline["functions"], shape.MAX_FUNCTION_LINES, FUNCTION_OVER, FUNCTION_GREW
    )
    stale = stale_files + stale_functions
    assert not stale, "\n".join(stale)


def test_the_baseline_file_is_written_by_the_script() -> None:
    text = shape.BASELINE.read_text(encoding="utf-8")
    hand_edited = "tests/shape_baseline.json is hand-edited: run `make shape-baseline`."
    assert shape.render(json.loads(text)) == text, hand_edited


def test_a_function_counts_from_def_to_the_end_of_its_body() -> None:
    source = textwrap.dedent(
        '''
        @decorator
        def top(a):
            """Doc."""
            return a

        class Box:
            async def method(self):
                def inner():
                    pass
                return inner
        '''
    )
    assert dict(shape.function_lines(ast.parse(source))) == {"top": 3, "Box.method": 4, "Box.method.inner": 2}


def test_the_rules_cover_the_library_scripts_and_benchmarks_but_not_tests() -> None:
    covered = {p.relative_to(shape.ROOT).parts[0] for p in shape.source_files()}
    assert covered == {"src", "scripts", "benchmarks"}
    assert Path(shape.__file__).resolve() in shape.source_files()


def test_a_missing_baseline_is_never_recreated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "shape_baseline.json"
    monkeypatch.setattr(shape, "BASELINE", missing)
    monkeypatch.setattr(shape, "ROOT", tmp_path)
    assert shape.main() == 1, "recreating it would admit every oversized file and function"
    assert not missing.exists()


def test_a_form_feed_is_not_a_line(tmp_path: Path) -> None:
    package = tmp_path / "src" / "web_router"
    package.mkdir(parents=True)
    (package / "paged.py").write_text("X = 1\n\f\nY = 2\n", encoding="utf-8")
    files, _ = shape.measure(tmp_path)
    assert files["src/web_router/paged.py"] == 3, "counted as an editor shows it, not as splitlines() does"
