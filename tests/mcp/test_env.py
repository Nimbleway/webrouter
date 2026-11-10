"""Where the server finds its keys: the startup lookup and ``load_env``'s reader, in this process.

Credentials reach the server unexported, from the first file that sets one,
and nothing else in any file gets in. ``test_env_stdio.py`` proves the same
against a real ``web-router-mcp``; these pin each rule on its own.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.mcp.env_helpers import KEY, OTHER_KEY, holds, is_set, statuses, write
from web_router.constants import (
    ENV_AGENTCORE_GATEWAY_URL,
    ENV_APPDATA,
    ENV_BRAVE_API_KEY,
    ENV_EXA_API_KEY,
    ENV_LOCAL_BROWSER_PATH,
    ENV_MCP_BROWSER_ALLOW_FILE_URLS,
    ENV_MCP_ENV_FILE,
    ENV_NIMBLE_API_KEY,
    ENV_TAVILY_API_KEY,
    ENV_XDG_CONFIG_HOME,
    MCP_ENV_FILE,
    MCP_ENV_FILE_MAX_BYTES,
    MCP_ENV_POINTER_FILE,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_MISSING,
    MCP_ENV_STATUS_NO_CREDENTIALS,
    MCP_ENV_STATUS_NOTHING_NEW,
    MCP_ENV_STATUS_USED,
    MCP_USER_CONFIG_DIR,
)
from web_router.mcp.env import EnvFileError, EnvSources, user_config_dir


def test_every_place_is_read_and_an_earlier_one_wins_per_variable(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = write(isolated_home / "elsewhere" / "keys.env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    user_env = write(user_config_dir() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={OTHER_KEY}\n{ENV_EXA_API_KEY}={KEY}\n")
    remembered = write(isolated_home / "saved.env", f"{ENV_EXA_API_KEY}={OTHER_KEY}\n{ENV_NIMBLE_API_KEY}={KEY}\n")
    write(user_config_dir() / MCP_ENV_POINTER_FILE, f"{remembered}\n")
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(explicit))

    sources = EnvSources.at_startup()

    assert holds(ENV_BRAVE_API_KEY, KEY) and holds(ENV_EXA_API_KEY, KEY) and holds(ENV_NIMBLE_API_KEY, KEY)
    assert sources.report()["loaded"] == {
        ENV_BRAVE_API_KEY: str(explicit),
        ENV_EXA_API_KEY: str(user_env),
        ENV_NIMBLE_API_KEY: str(remembered),
    }
    assert statuses(sources) == [
        (MCP_ENV_SOURCE_EXPLICIT, MCP_ENV_STATUS_USED),
        (MCP_ENV_SOURCE_USER, MCP_ENV_STATUS_USED),
        (MCP_ENV_SOURCE_REMEMBERED, MCP_ENV_STATUS_USED),
    ]


def test_a_remembered_file_only_adds_and_never_hides_the_users_keys(isolated_home: Path) -> None:
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={KEY}\n")
    remembered = write(isolated_home / "saved.env", f"{ENV_TAVILY_API_KEY}={OTHER_KEY}\n")
    write(user_config_dir() / MCP_ENV_POINTER_FILE, f"{remembered}\n")

    sources = EnvSources.at_startup()

    assert holds(ENV_TAVILY_API_KEY, KEY)
    assert statuses(sources) == [
        (MCP_ENV_SOURCE_USER, MCP_ENV_STATUS_USED),
        (MCP_ENV_SOURCE_REMEMBERED, MCP_ENV_STATUS_NOTHING_NEW),
    ]


def test_the_user_config_dir_is_read_from_any_working_directory(isolated_home: Path) -> None:
    """The case a PyPI install lives in: no repo, a client starting the server wherever it likes."""
    user_env = write(user_config_dir() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={KEY}\n")

    sources = EnvSources.at_startup()

    assert holds(ENV_BRAVE_API_KEY, KEY)
    assert sources.report()["loaded"] == {ENV_BRAVE_API_KEY: str(user_env)}
    assert statuses(sources) == [(MCP_ENV_SOURCE_USER, MCP_ENV_STATUS_USED)]


def test_a_file_that_sets_no_credential_does_not_hide_the_next_one(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A project's own .env, named by its client config, that says nothing about web-router.
    write(Path.cwd() / MCP_ENV_FILE, "DATABASE_URL=postgres://localhost/app\n")
    monkeypatch.setenv(ENV_MCP_ENV_FILE, MCP_ENV_FILE)
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={KEY}\n")

    sources = EnvSources.at_startup()

    assert holds(ENV_BRAVE_API_KEY, KEY)
    assert not sources.report()["explicit_file_missing"]
    assert statuses(sources) == [
        (MCP_ENV_SOURCE_EXPLICIT, MCP_ENV_STATUS_NO_CREDENTIALS),
        (MCP_ENV_SOURCE_USER, MCP_ENV_STATUS_USED),
    ]


def test_an_explicit_path_that_is_missing_is_reported_not_ignored(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = isolated_home / "nowhere" / ".env"
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(missing))
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={KEY}\n")

    sources = EnvSources.at_startup()
    report = sources.report()

    assert report["explicit_file_missing"] is True
    assert report["searched"][0] == {
        "source": MCP_ENV_SOURCE_EXPLICIT,
        "path": str(missing),
        "status": MCP_ENV_STATUS_MISSING,
    }
    # Reported, and the rest of the lookup still runs.
    assert report["loaded"] == {ENV_BRAVE_API_KEY: str(user_config_dir() / MCP_ENV_FILE)}


def test_a_remembered_path_is_read_on_the_next_start_and_only_adds_to_the_user_env(isolated_home: Path) -> None:
    remembered = write(isolated_home / "secrets" / "web.env", f"{ENV_BRAVE_API_KEY}={KEY}\n{ENV_NIMBLE_API_KEY}=x\n")
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_NIMBLE_API_KEY}={OTHER_KEY}\n")

    loaded = EnvSources().load(str(remembered), remember=True)
    os.environ.pop(ENV_BRAVE_API_KEY)
    os.environ.pop(ENV_NIMBLE_API_KEY)
    # Only the path is written, so rotating the key in the original is enough.
    pointer = user_config_dir() / MCP_ENV_POINTER_FILE
    assert loaded.remembered == pointer
    assert pointer.read_text(encoding="utf-8").strip() == str(remembered)
    assert KEY not in pointer.read_text(encoding="utf-8")

    sources = EnvSources.at_startup()

    assert holds(ENV_BRAVE_API_KEY, KEY)
    assert (MCP_ENV_SOURCE_REMEMBERED, MCP_ENV_STATUS_USED) in statuses(sources)
    assert holds(ENV_NIMBLE_API_KEY, OTHER_KEY), "a remembered file never replaces the user's own key"


def test_a_variable_already_exported_wins_over_the_file(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "exported")
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_NIMBLE_API_KEY}=from-file\n{ENV_BRAVE_API_KEY}={KEY}\n")

    sources = EnvSources.at_startup()

    assert holds(ENV_NIMBLE_API_KEY, "exported")
    report = sources.report()
    assert list(report["loaded"]) == [ENV_BRAVE_API_KEY]
    assert report["from_environment"] == [ENV_NIMBLE_API_KEY]


def test_only_credentials_load_so_a_foreign_env_cannot_set_policy(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remembered file is a path a conversation recorded, not the operator's own configuration.

    Flipping :data:`ENV_MCP_BROWSER_ALLOW_FILE_URLS` would open ``file:``
    navigation, and :data:`ENV_LOCAL_BROWSER_PATH` names the binary ``local``
    launches -- neither may come from a file the operator never wrote.
    """
    monkeypatch.delenv(ENV_MCP_BROWSER_ALLOW_FILE_URLS, raising=False)
    monkeypatch.delenv(ENV_LOCAL_BROWSER_PATH, raising=False)
    remembered = write(
        isolated_home / "saved.env",
        f"{ENV_MCP_BROWSER_ALLOW_FILE_URLS}=1\n{ENV_LOCAL_BROWSER_PATH}=/tmp/evil\n{ENV_MCP_ENV_FILE}=/tmp/x\n",
    )
    write(user_config_dir() / MCP_ENV_POINTER_FILE, f"{remembered}\n")

    EnvSources.at_startup()

    assert not is_set(ENV_MCP_BROWSER_ALLOW_FILE_URLS)
    assert not is_set(ENV_LOCAL_BROWSER_PATH)
    assert not is_set(ENV_MCP_ENV_FILE)


def test_nothing_is_interpolated_so_a_file_cannot_copy_another_secret(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Expanded, this would send another service's secret to Brave with every search.
    monkeypatch.setenv("SOME_OTHER_SECRET", OTHER_KEY)
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}=${{SOME_OTHER_SECRET}}\n")

    EnvSources.at_startup()

    assert holds(ENV_BRAVE_API_KEY, "${SOME_OTHER_SECRET}")


def test_nothing_found_anywhere_is_a_no_op_so_a_fresh_install_still_starts(isolated_home: Path) -> None:
    sources = EnvSources.at_startup()

    assert sources.report()["loaded"] == {}
    assert {status for _, status in statuses(sources)} == {MCP_ENV_STATUS_MISSING}


def test_an_empty_assignment_is_no_credential(isolated_home: Path) -> None:
    # A copied .env.example is every name with no value.
    write(
        user_config_dir() / MCP_ENV_FILE,
        f"{ENV_NIMBLE_API_KEY}=\n{ENV_BRAVE_API_KEY}=\n{ENV_AGENTCORE_GATEWAY_URL}=\n",
    )

    sources = EnvSources.at_startup()

    assert not is_set(ENV_NIMBLE_API_KEY)
    assert statuses(sources)[-1] == (MCP_ENV_SOURCE_USER, MCP_ENV_STATUS_NO_CREDENTIALS)
    assert "note" not in sources.report()["searched"][-1]


def test_the_report_names_variables_and_never_values(isolated_home: Path) -> None:
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={KEY}\n")
    sources = EnvSources.at_startup()
    extra = write(isolated_home / "more.env", f"{ENV_NIMBLE_API_KEY}={OTHER_KEY}\n")
    sources.load(str(extra))

    text = json.dumps(sources.report())

    assert ENV_BRAVE_API_KEY in text and ENV_NIMBLE_API_KEY in text
    assert KEY not in text and OTHER_KEY not in text


def test_load_expands_the_home_directory(isolated_home: Path) -> None:
    write(isolated_home / "keys.env", f"{ENV_BRAVE_API_KEY}={KEY}\n")

    loaded = EnvSources().load("~/keys.env")

    assert loaded.path == isolated_home / "keys.env"
    assert loaded.loaded == (ENV_BRAVE_API_KEY,)
    assert holds(ENV_BRAVE_API_KEY, KEY)


def test_load_keeps_a_value_already_set(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BRAVE_API_KEY, "exported")
    path = write(isolated_home / "keys.env", f"{ENV_BRAVE_API_KEY}={KEY}\n")

    loaded = EnvSources().load(str(path))

    assert (loaded.loaded, loaded.already_set) == ((), (ENV_BRAVE_API_KEY,))
    assert holds(ENV_BRAVE_API_KEY, "exported")


def test_load_of_a_file_that_is_not_a_dotenv_yields_nothing_and_quotes_none_of_it(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model can name any path. Whatever it points at, only credential variables are ever taken from it."""
    # OPENAI_API_KEY is not a provider credential, so no_keys leaves a developer's own in place. Setting
    # before deleting makes monkeypatch restore the variable even if the loader wrongly took it from the file.
    for name in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", ENV_LOCAL_BROWSER_PATH):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    secrets = (
        "OPENAI_API_KEY=not-real-openai-value\n"
        "AWS_SECRET_ACCESS_KEY=also-secret\n"
        f"{ENV_LOCAL_BROWSER_PATH}=/tmp/evil\n"
        'password = "hunter2"\n'
        '{"token": "json-secret"}\n'
    )
    path = write(isolated_home / "notes.txt", secrets)

    with pytest.raises(EnvFileError) as raised:
        EnvSources().load(str(path))

    assert raised.value.status == MCP_ENV_STATUS_NO_CREDENTIALS
    message = str(raised.value)
    assert str(path) in message
    for value in ("not-real-openai-value", "also-secret", "/tmp/evil", "hunter2", "json-secret"):
        assert value not in message
    for name in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", ENV_LOCAL_BROWSER_PATH):
        assert not is_set(name)


@pytest.mark.parametrize("kind", ["missing", "directory", "too large", "binary"])
def test_load_refuses_what_is_not_a_readable_dotenv_by_path_alone(isolated_home: Path, kind: str) -> None:
    path = isolated_home / "target"
    if kind == "directory":
        path.mkdir()
    elif kind == "too large":
        path.write_bytes(b"#" * (MCP_ENV_FILE_MAX_BYTES + 1))
    elif kind == "binary":
        path.write_bytes(b"\xff\xfe" + f"{ENV_BRAVE_API_KEY}={KEY}".encode("utf-16-le"))

    with pytest.raises(EnvFileError) as raised:
        EnvSources().load(str(path))

    assert str(path) in str(raised.value)
    assert KEY not in str(raised.value)
    assert not is_set(ENV_BRAVE_API_KEY)


def test_a_path_that_cannot_be_remembered_loads_nothing(isolated_home: Path) -> None:
    path = write(isolated_home / "keys.env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    # A file where the config directory should be, so it cannot be created.
    write(user_config_dir(), "in the way")

    with pytest.raises(ValueError, match="could not be recorded"):
        EnvSources().load(str(path), remember=True)

    assert not is_set(ENV_BRAVE_API_KEY)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux", ("xdg", MCP_USER_CONFIG_DIR)),
        ("darwin", ("home", "Library", "Application Support", MCP_USER_CONFIG_DIR)),
        ("win32", ("appdata", MCP_USER_CONFIG_DIR)),
    ],
)
def test_the_user_config_dir_is_where_each_platform_keeps_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, expected: tuple[str, ...]
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(ENV_XDG_CONFIG_HOME, str(tmp_path / "xdg"))
    monkeypatch.setenv(ENV_APPDATA, str(tmp_path / "appdata"))

    assert user_config_dir() == tmp_path.joinpath(*expected)


def test_a_relative_xdg_config_home_is_ignored_as_the_spec_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_XDG_CONFIG_HOME, "relative/config")

    assert user_config_dir() == tmp_path / ".config" / MCP_USER_CONFIG_DIR
