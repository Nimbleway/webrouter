"""Release collection regressions for reused names, wrapped entries and missing headings."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import changelog


def test_reused_fragment_names_sort_by_the_current_addition(tmp_path: Path) -> None:
    def git(*args: str, date: str = "2000-01-01T00:00:00Z") -> None:
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
        )

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    reused = tmp_path / "reused.fixed.md"
    other = tmp_path / "other.fixed.md"
    reused.write_text("First fix.")
    git("add", ".")
    git("commit", "-m", "first addition")
    reused.unlink()
    git("add", ".")
    git("commit", "-m", "collect release")
    other.write_text("Other fix.")
    git("add", ".")
    git("commit", "-m", "other addition", date="2001-01-01T00:00:00Z")
    reused.write_text("Current fix.")
    git("add", ".")
    git("commit", "-m", "reuse name", date="2002-01-01T00:00:00Z")
    assert [fragment.text for fragment in changelog.load(tmp_path)] == ["Current fix.", "Other fix."]


def test_wrapped_ordered_markers_stay_in_one_changelog_bullet(tmp_path: Path) -> None:
    path = tmp_path / "wrap.fixed.md"
    path.write_text("Fix A.\n1. Fix B.")
    assert changelog.problems(tmp_path) == []
    assert changelog.collect("## [Unreleased]\n", changelog.load(tmp_path)).endswith("- Fix A. 1. Fix B.\n")


def test_missing_heading_reports_an_error_without_changing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "changelog.d"
    directory.mkdir()
    fragment = directory / "fix.fixed.md"
    fragment.write_text("A fix.")
    target = tmp_path / "CHANGELOG.md"
    target.write_text("# Changelog\n")
    monkeypatch.setattr(changelog, "FRAGMENT_DIR", directory)
    monkeypatch.setattr(changelog, "CHANGELOG", target)
    assert changelog.main([]) == 1
    error = capsys.readouterr().err
    assert "has no '## [Unreleased]' heading" in error
    assert "Traceback" not in error
    assert target.read_text() == "# Changelog\n"
    assert fragment.read_text() == "A fix."
