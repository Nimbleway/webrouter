"""Changelog fragments (``scripts/changelog.py``): the repo's own are valid, and collecting them works.

The first test runs in ``make check`` on every pull request, whatever its base, so a
malformed fragment fails CI before it reaches a release.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import changelog

BASE = """# Changelog

## [Unreleased]

### Changed

- Old change.

## [0.1.0] - 2026-01-01

### Added

- Released entry.
"""


def _write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_repos_fragments_are_valid() -> None:
    assert changelog.problems() == []


def test_a_missing_directory_has_no_fragments(tmp_path: Path) -> None:
    assert changelog.fragment_paths(tmp_path / "changelog.d") == []
    assert changelog.problems(tmp_path / "changelog.d") == []


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("Bad Name.added.md", "Text.", "name it <name>.<type>.md"),
        ("fix.improved.md", "Text.", "'improved' is not a changelog type"),
        ("fix.fixed.txt", "Text.", "name it <name>.<type>.md"),
        ("fix.fixed.md", "  \n", "is empty"),
        ("fix.fixed.md", "x" * (changelog.MAX_CHARS + 1), f"at most {changelog.MAX_CHARS}"),
        ("fix.fixed.md", "- Text.", "starts a bullet or a heading"),
        ("fix.fixed.md", "Fix A.\n- Fix B.", "starts a bullet or a heading"),
        ("fix.fixed.md", "Fix A.\n## [0.14.0]", "starts a bullet or a heading"),
        ("fix.fixed.md", "One.\n\nTwo.", "more than one paragraph"),
    ],
)
def test_a_malformed_fragment_is_named(tmp_path: Path, name: str, text: str, expected: str) -> None:
    _write(tmp_path, name, text)
    found = changelog.problems(tmp_path)
    assert len(found) == 1
    assert expected in found[0]
    assert name in found[0]


def test_the_readme_is_not_a_fragment(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "How to write a fragment.\n\nMore.")
    assert changelog.problems(tmp_path) == []


def test_an_entry_may_start_with_a_pull_request_number(tmp_path: Path) -> None:
    _write(tmp_path, "num.fixed.md", "#123 no longer drops the path.")
    assert changelog.problems(tmp_path) == []


def _fragment(type_: str, text: str) -> changelog.Fragment:
    return changelog.Fragment(path=Path(f"x.{type_}.md"), type=type_, text=text)


def test_collect_puts_entries_at_the_top_of_an_existing_section() -> None:
    out = changelog.collect(BASE, [_fragment("changed", "New change."), _fragment("changed", "Older new change.")])
    assert "### Changed\n\n- New change.\n- Older new change.\n- Old change.\n" in out


def test_collect_creates_sections_in_keep_a_changelog_order() -> None:
    out = changelog.collect(BASE, [_fragment("fixed", "A fix."), _fragment("added", "A feature.")])
    unreleased = out.split("## [0.1.0]")[0]
    assert unreleased.index("### Added") < unreleased.index("### Changed") < unreleased.index("### Fixed")
    assert "### Added\n\n- A feature.\n" in unreleased
    assert "### Fixed\n\n- A fix.\n" in unreleased
    assert out.endswith("- Released entry.\n")


def test_collect_needs_an_unreleased_heading() -> None:
    with pytest.raises(ValueError, match="Unreleased"):
        changelog.collect("# Changelog\n", [_fragment("added", "A feature.")])


def test_main_collects_and_deletes_the_fragments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "changelog.d"
    fragment = _write(directory, "feature.added.md", "A feature.\n")
    _write(directory, "README.md", "Kept.")
    target = tmp_path / "CHANGELOG.md"
    target.write_text(BASE, encoding="utf-8")
    monkeypatch.setattr(changelog, "FRAGMENT_DIR", directory)
    monkeypatch.setattr(changelog, "CHANGELOG", target)

    assert changelog.main([]) == 0
    assert "### Added\n\n- A feature.\n" in target.read_text(encoding="utf-8")
    assert not fragment.exists()
    assert (directory / "README.md").exists()


def test_main_refuses_a_malformed_fragment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "changelog.d"
    _write(directory, "bad.nope.md", "Text.")
    target = tmp_path / "CHANGELOG.md"
    target.write_text(BASE, encoding="utf-8")
    monkeypatch.setattr(changelog, "FRAGMENT_DIR", directory)
    monkeypatch.setattr(changelog, "CHANGELOG", target)

    assert changelog.main([]) == 1
    assert target.read_text(encoding="utf-8") == BASE


def test_an_undatable_fragment_is_ordered_by_name_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, "feature.added.md", "A feature.")
    assert changelog.added_at(path) == 0
    assert "ordered by name" in capsys.readouterr().err
