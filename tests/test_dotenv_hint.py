"""A configuration error points at a ``.env`` the library did not read."""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from web_router._dotenv import dotenv_hint, nearest_dotenv
from web_router.exceptions import ConfigurationError

KEY = "WR_TEST_DOTENV_KEY"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory, one level below a ``.env`` that sets :data:`KEY`, with the environment lacking it."""
    (tmp_path / ".env").write_text(f"# keys\nexport {KEY}='abc'\nEMPTY_ONE=\n", encoding="utf-8")
    work = tmp_path / "src"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.delenv(KEY, raising=False)
    return tmp_path


def test_a_missing_key_set_in_a_parent_dotenv_names_the_file(project: Path) -> None:
    error = ConfigurationError.missing_api_key("nimble", KEY)
    assert f"A .env exists at {(project / '.env').resolve()} and sets {KEY}" in str(error)
    assert "uv run --env-file .env" in str(error)


def test_no_hint_once_the_environment_holds_the_key(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY, "abc")
    assert ".env exists" not in str(ConfigurationError.missing_api_key("nimble", KEY))


def test_no_hint_for_a_variable_the_dotenv_leaves_empty(project: Path) -> None:
    assert dotenv_hint("Set the EMPTY_ONE environment variable.") == ""


def test_no_hint_without_a_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY, raising=False)
    assert nearest_dotenv(tmp_path) is None
    assert dotenv_hint(f"Set the {KEY} environment variable.", start=tmp_path) == ""


def test_the_hint_is_added_once_through_pickling(project: Path) -> None:
    error = ConfigurationError.missing_api_key("nimble", KEY)
    assert str(pickle.loads(pickle.dumps(error))).count(".env exists") == 1
