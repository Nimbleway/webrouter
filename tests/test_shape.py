"""The file-length rule (AGENTS.md): a Python file is at most 400 lines.

Files already over it are recorded in ``shape_baseline.json`` and may keep
their size or shrink, never grow. ``scripts/shape.py`` measures. Functions are
bounded by ruff (``C901``, ``PLR0915``), not here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import shape

RULE = "See 'Keep files and functions small' in AGENTS.md."
FILE_OVER = "{key} is {lines} lines; a file is at most {limit}. Move code into its own module. " + RULE
FILE_GREW = "{key} grew to {lines} lines from its baseline of {was}; a grandfathered file may only shrink. " + RULE
STALE = (
    "{key} is in tests/shape_baseline.json but is now {lines} lines (baseline {was}). "
    "Run `make shape-baseline`: it lowers the entry, or drops it once under the limit."
)
GONE = "{key} is in tests/shape_baseline.json but no longer exists. Run `make shape-baseline` to drop it."


def _problems(measured: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """The violations, then the baseline entries to update."""
    limit = shape.MAX_FILE_LINES
    violations: list[str] = []
    stale: list[str] = []
    for key, lines in measured.items():
        was = baseline.get(key)
        if was is None and lines > limit:
            violations.append(FILE_OVER.format(key=key, lines=lines, limit=limit))
        elif was is not None and lines > was:
            violations.append(FILE_GREW.format(key=key, lines=lines, was=was))
        elif was is not None and lines < was:
            stale.append(STALE.format(key=key, lines=lines, was=was))
    stale.extend(GONE.format(key=key) for key in baseline if key not in measured)
    return violations, stale


@pytest.fixture(scope="module")
def measured() -> dict[str, int]:
    return shape.measure()


@pytest.fixture(scope="module")
def baseline() -> dict[str, int]:
    return shape.load_baseline()


def test_files_stay_under_the_limit(measured: dict[str, int], baseline: dict[str, int]) -> None:
    violations, _ = _problems(measured, baseline)
    assert not violations, "\n".join(violations)


def test_the_baseline_only_lists_what_is_still_over(measured: dict[str, int], baseline: dict[str, int]) -> None:
    _, stale = _problems(measured, baseline)
    assert not stale, "\n".join(stale)


def test_the_baseline_file_is_written_by_the_script() -> None:
    text = shape.BASELINE.read_text(encoding="utf-8")
    hand_edited = "tests/shape_baseline.json is hand-edited: run `make shape-baseline`."
    assert shape.render(json.loads(text)) == text, hand_edited


def test_the_rule_covers_the_library_scripts_benchmarks_and_tests(measured: dict[str, int]) -> None:
    assert {key.split("/", 1)[0] for key in measured} == {"src", "scripts", "benchmarks", "tests"}
    assert Path(shape.__file__).resolve() in shape.source_files()


def test_a_missing_baseline_is_never_recreated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "shape_baseline.json"
    monkeypatch.setattr(shape, "BASELINE", missing)
    monkeypatch.setattr(shape, "ROOT", tmp_path)
    assert shape.main() == 1, "recreating it would admit every oversized file"
    assert not missing.exists()


def test_a_form_feed_is_not_a_line(tmp_path: Path) -> None:
    package = tmp_path / "src" / "web_router"
    package.mkdir(parents=True)
    (package / "paged.py").write_text("X = 1\n\f\nY = 2\n", encoding="utf-8")
    assert shape.measure(tmp_path)["src/web_router/paged.py"] == 3, "counted as an editor shows it"


def _oversized_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, int]) -> Path:
    """A tree holding one 401-line file, and a baseline that lists ``files``; returns the baseline path."""
    package = tmp_path / "src" / "web_router"
    package.mkdir(parents=True)
    (package / "big.py").write_text("X = 1\n" * (shape.MAX_FILE_LINES + 1), encoding="utf-8")
    baseline = tmp_path / "shape_baseline.json"
    baseline.write_text(shape.render(files), encoding="utf-8")
    monkeypatch.setattr(shape, "BASELINE", baseline)
    monkeypatch.setattr(shape, "ROOT", tmp_path)
    return baseline


@pytest.mark.parametrize(
    "files",
    [pytest.param({}, id="new"), pytest.param({"src/web_router/big.py": shape.MAX_FILE_LINES}, id="grown")],
)
def test_the_baseline_is_not_rewritten_when_something_grew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, int]
) -> None:
    baseline = _oversized_tree(tmp_path, monkeypatch, files)
    before = baseline.read_text(encoding="utf-8")
    assert shape.main() == 1, "make shape-baseline must refuse a new or grown entry"
    assert baseline.read_text(encoding="utf-8") == before


def test_the_baseline_records_a_shrink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = _oversized_tree(tmp_path, monkeypatch, {"src/web_router/big.py": 500})
    assert shape.main() == 0
    assert shape.load_baseline(baseline) == {"src/web_router/big.py": shape.MAX_FILE_LINES + 1}
