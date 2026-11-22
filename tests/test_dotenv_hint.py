"""A configuration error points at a ``.env`` the library did not read."""

from __future__ import annotations

import pickle
import shlex
from pathlib import Path

import pytest

from web_router import WebRouter
from web_router._dotenv import dotenv_hint, nearest_dotenv
from web_router.constants import ANSWER_ENV_BY_PROVIDER
from web_router.exceptions import ConfigurationError, NoAnswerProviderError, ProviderNotFoundError

KEY = "WR_TEST_DOTENV_KEY"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working directory, one level below a ``.env`` that sets :data:`KEY`, with the environment lacking it."""
    (tmp_path / ".env").write_text(
        f"# keys\nexport {KEY}='abc'\nEMPTY_ONE=\nQUOTED_EMPTY=\"\" # unset\nCOMMENTED= # unset\n", encoding="utf-8"
    )
    work = tmp_path / "src"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.delenv(KEY, raising=False)
    return tmp_path


def test_a_missing_key_set_in_a_parent_dotenv_names_the_file(project: Path) -> None:
    error = ConfigurationError.missing_api_key("nimble", KEY)
    assert f"A .env exists at {(project / '.env').resolve()} and sets {KEY}" in str(error)
    assert f"uv run --env-file {(project / '.env').resolve()}" in str(error)


def test_no_hint_once_the_environment_holds_the_key(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY, "abc")
    assert ".env exists" not in str(ConfigurationError.missing_api_key("nimble", KEY))


@pytest.mark.parametrize("name", ["EMPTY_ONE", "QUOTED_EMPTY", "COMMENTED"])
def test_no_hint_for_a_variable_the_dotenv_leaves_empty(project: Path, name: str) -> None:
    assert dotenv_hint(f"Set the {name} environment variable.") == ""


def test_a_dotenv_saved_with_a_byte_order_mark_still_names_its_first_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PowerShell 5's `Set-Content -Encoding utf8` and some Windows editors write one; python-dotenv skips it.
    (tmp_path / ".env").write_text(f"{KEY}=abc\n", encoding="utf-8-sig")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(KEY, raising=False)
    assert f"sets {KEY}" in str(ConfigurationError.missing_api_key("nimble", KEY))


def test_no_hint_without_a_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY, raising=False)
    assert nearest_dotenv(tmp_path) is None
    assert dotenv_hint(f"Set the {KEY} environment variable.", start=tmp_path) == ""


def test_the_hint_is_added_once_through_pickling(project: Path) -> None:
    error = ConfigurationError.missing_api_key("nimble", KEY)
    assert str(pickle.loads(pickle.dumps(error))).count(".env exists") == 1


def test_no_hint_from_an_oversized_dotenv(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web_router._dotenv.MCP_ENV_FILE_MAX_BYTES", 8)
    assert dotenv_hint(f"Set the {KEY} environment variable.") == ""


def test_the_command_keeps_a_path_with_a_space_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spaced = tmp_path / "My Project"
    spaced.mkdir()
    (spaced / ".env").write_text(f"{KEY}=abc\n", encoding="utf-8")
    monkeypatch.chdir(spaced)
    monkeypatch.delenv(KEY, raising=False)
    command = str(ConfigurationError.missing_api_key("nimble", KEY)).split("`")[1]
    args = shlex.split(command)
    assert args[args.index("--env-file") + 1] == str((spaced / ".env").resolve())


def test_a_word_in_the_message_is_not_taken_for_a_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("API=foo\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(KEY, raising=False)
    assert ".env exists" not in str(ConfigurationError.missing_api_key("nimble", KEY))


def test_answer_with_no_backend_names_the_unread_dotenv(project: Path) -> None:
    error = NoAnswerProviderError("auto", ["perplexity"], configured=[], env_vars=[KEY])
    assert f"A .env exists at {(project / '.env').resolve()} and sets {KEY}" in str(error)
    assert isinstance(error, ProviderNotFoundError)


def test_answer_auto_with_its_key_only_in_a_dotenv_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("PERPLEXITY_API_KEY=abc\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for env_var in {var for names in ANSWER_ENV_BY_PROVIDER.values() for var in names}:
        monkeypatch.delenv(env_var, raising=False)
    with WebRouter() as router, pytest.raises(NoAnswerProviderError, match="sets PERPLEXITY_API_KEY"):
        router.answer("q")
