"""The operator's switches over where keys come from, and the limits on what ``load_env`` will open.

Each switch defaults to what the server did before it, so these set one at a
time and check the place it governs -- and only that place -- changes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tests.mcp.conftest import outcome
from tests.mcp.env_helpers import KEY, holds, is_set, statuses, write
from web_router.client import AsyncWebRouter
from web_router.constants import (
    ENV_BRAVE_API_KEY,
    MCP_ENV_FILE,
    MCP_ENV_SOURCE_CWD,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_USED,
    MCP_TOOL_LOAD_ENV,
)
from web_router.constants_mcp_env import (
    ENV_MCP_CWD_ENV,
    ENV_MCP_LOAD_ENV,
    ENV_MCP_REMEMBERED_ENV,
    MCP_ENV_STATUS_REFUSED,
    MCP_ENV_STATUS_SKIPPED,
)
from web_router.exceptions import ConfigurationError
from web_router.mcp.env import EnvFileError, EnvSources
from web_router.mcp.env_pointer import pointer_file
from web_router.mcp.tools import WebRouterTools

posix_only = pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")


def _remember(path: Path) -> None:
    EnvSources().load(str(path), remember=True)
    os.environ.pop(ENV_BRAVE_API_KEY)


def _refusal(path: Path) -> EnvFileError:
    with pytest.raises(EnvFileError) as raised:
        EnvSources().load(str(path))
    return raised.value


def test_load_env_opens_only_a_file_named_as_a_dotenv_with_one_refusal_for_all(isolated_home: Path) -> None:
    """``/etc/passwd`` and a path with no file read the same, so a steered model cannot map the machine."""
    present = write(isolated_home / "passwd", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    absent = isolated_home / "nothing-here"

    refusals = [_refusal(present), _refusal(absent)]

    assert [refusal.status for refusal in refusals] == [MCP_ENV_STATUS_REFUSED] * 2
    assert str(refusals[0]).replace(str(present), "<path>") == str(refusals[1]).replace(str(absent), "<path>")
    assert not is_set(ENV_BRAVE_API_KEY)


@pytest.mark.parametrize("name", [".environment", ".envrc", "env"])
def test_a_name_that_only_starts_like_a_dotenv_is_refused(isolated_home: Path, name: str) -> None:
    assert _refusal(write(isolated_home / name, f"{ENV_BRAVE_API_KEY}={KEY}\n")).status == MCP_ENV_STATUS_REFUSED


@pytest.mark.parametrize("name", [".env", ".env.local", "work.env"])
def test_every_usual_dotenv_name_loads(isolated_home: Path, name: str) -> None:
    path = write(isolated_home / name, f"{ENV_BRAVE_API_KEY}={KEY}\n")

    assert EnvSources().load(str(path)).loaded == (ENV_BRAVE_API_KEY,)


def test_a_dotenv_named_link_to_another_kind_of_file_is_refused(isolated_home: Path) -> None:
    target = write(isolated_home / "secrets.txt", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    link = isolated_home / ".env"
    link.symlink_to(target)

    refusal = _refusal(link)

    assert refusal.status == MCP_ENV_STATUS_REFUSED
    assert str(target) not in str(refusal), "never says where the link leads"
    assert not is_set(ENV_BRAVE_API_KEY)


@posix_only
def test_the_remembered_record_is_owner_only_even_over_an_older_one(isolated_home: Path) -> None:
    write(pointer_file(), "/old/.env\n").chmod(0o644)
    path = write(isolated_home / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")

    EnvSources().load(str(path), remember=True)

    assert stat.S_IMODE(pointer_file().stat().st_mode) == 0o600
    assert pointer_file().read_text(encoding="utf-8").strip() == str(path)


def test_forget_drops_the_record_so_the_next_start_reads_nothing_from_it(isolated_home: Path) -> None:
    remembered = write(isolated_home / "keys" / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    _remember(remembered)

    assert EnvSources.forget() == remembered
    assert EnvSources.forget() is None, "nothing left to forget"
    assert MCP_ENV_SOURCE_REMEMBERED not in dict(statuses(EnvSources.at_startup()))
    assert not is_set(ENV_BRAVE_API_KEY)


def test_the_working_directory_env_can_be_switched_off(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(Path.cwd() / MCP_ENV_FILE, f"{ENV_BRAVE_API_KEY}={KEY}\n")
    monkeypatch.setenv(ENV_MCP_CWD_ENV, "off")

    sources = EnvSources.at_startup()

    assert (MCP_ENV_SOURCE_CWD, MCP_ENV_STATUS_SKIPPED) in statuses(sources)
    assert not is_set(ENV_BRAVE_API_KEY)
    assert ENV_MCP_CWD_ENV in str(sources.report()["searched"]), "the report says which switch skipped it"


@pytest.mark.parametrize(
    ("scope", "inside", "read"),
    [("project", False, False), ("project", True, True), ("off", True, False), ("anywhere", False, True)],
)
def test_a_remembered_file_is_read_where_the_operator_scopes_it(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch, scope: str, inside: bool, read: bool
) -> None:
    folder = Path.cwd() / "config" if inside else isolated_home / "other-project"
    _remember(write(folder / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n"))
    monkeypatch.setenv(ENV_MCP_REMEMBERED_ENV, scope)

    found = dict(statuses(EnvSources.at_startup()))

    assert found[MCP_ENV_SOURCE_REMEMBERED] == (MCP_ENV_STATUS_USED if read else MCP_ENV_STATUS_SKIPPED)
    assert holds(ENV_BRAVE_API_KEY, KEY) is read
    assert found[MCP_ENV_SOURCE_USER] != MCP_ENV_STATUS_SKIPPED, "only the remembered place is governed"


@pytest.mark.parametrize("name", [ENV_MCP_CWD_ENV, ENV_MCP_REMEMBERED_ENV])
def test_a_startup_switch_value_it_does_not_take_stops_the_server(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "sometimes")

    with pytest.raises(ConfigurationError, match=name):
        EnvSources.at_startup()


def _tools() -> WebRouterTools:
    return WebRouterTools(AsyncWebRouter("auto"))


async def test_with_load_env_off_the_tool_is_neither_listed_nor_callable(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_MCP_LOAD_ENV, "off")
    tools = _tools()

    assert MCP_TOOL_LOAD_ENV not in {tool.name for tool in tools.list_tools()}
    failed, text = await outcome(tools, MCP_TOOL_LOAD_ENV, path=str(isolated_home / ".env"))
    assert failed and text.startswith(f"Unknown tool {MCP_TOOL_LOAD_ENV!r}")


async def test_with_load_only_remember_is_refused_before_anything_loads(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_MCP_LOAD_ENV, "load")
    path = write(isolated_home / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    tools = _tools()

    failed, text = await outcome(tools, MCP_TOOL_LOAD_ENV, path=str(path), remember=True)

    assert failed and ENV_MCP_LOAD_ENV in text
    assert not is_set(ENV_BRAVE_API_KEY)
    assert not pointer_file().exists()
    failed, payload = await outcome(tools, MCP_TOOL_LOAD_ENV, path=str(path))
    assert not failed and payload["loaded"] == [ENV_BRAVE_API_KEY], "loading for the session still works"


async def test_the_forget_argument_removes_the_record_and_takes_no_path(isolated_home: Path) -> None:
    tools = _tools()
    path = write(isolated_home / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    _remember(path)

    failed, payload = await outcome(tools, MCP_TOOL_LOAD_ENV, forget=True)
    assert not failed and payload["forgotten"] == str(path)
    assert not pointer_file().exists()

    failed, text = await outcome(tools, MCP_TOOL_LOAD_ENV, forget=True, path=str(path))
    assert failed and "forget" in text


@posix_only
async def test_load_env_says_when_others_can_read_the_file(isolated_home: Path) -> None:
    path = write(isolated_home / ".env", f"{ENV_BRAVE_API_KEY}={KEY}\n")
    path.chmod(0o644)

    failed, payload = await outcome(_tools(), MCP_TOOL_LOAD_ENV, path=str(path))

    assert not failed and f"chmod 600 {path}" in payload["note"]
