"""An installed server finding its keys: the real ``web-router-mcp``, launched from outside any repo.

This is the setup a server registered from a clone lives in -- an MCP client runs
``uv run --project <clone> web-router-mcp`` in whatever directory it likes,
with no ``.env`` beside it. Each test launches the console script
this checkout installed, over stdio, from an empty temporary working directory,
with every credential stripped and ``HOME``, ``XDG_CONFIG_HOME`` and ``APPDATA``
moved to a temporary home, so where the child finds a key is decided by the
test alone. It then reads ``web-router://capabilities`` the way a client does.

The key is fake and nothing reaches a backend: reading the resource, listing
the tools and calling ``load_env`` send nothing anywhere, so these run in CI.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client.session import IncomingMessage
from mcp.client.stdio import stdio_client
from mcp_types import ToolListChangedNotification

from mcp import ClientSession, StdioServerParameters
from tests.mcp.conftest import home_variables, isolated_environment
from web_router.constants import (
    ENV_BRAVE_API_KEY,
    ENV_MCP_ENV_FILE,
    MCP_ENV_FILE,
    MCP_ENV_SOURCE_CWD,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_USED,
    MCP_RESOURCE_URI,
    MCP_TOOL_LOAD_ENV,
    MCP_TOOL_SEARCH,
    PROVIDER_AUTO,
    PROVIDER_BRAVE,
    PROVIDER_EXA_MCP,
    PROVIDER_PARALLEL_MCP,
    PROVIDER_YOU_MCP,
)
from web_router.mcp.env import user_config_dir

FAKE_KEY = "not-a-real-brave-key-0123"
"""Distinctive, so an assertion that it never reaches the model cannot pass by accident."""

SESSION_TIMEOUT = 30.0
"""Seconds one whole server session may take, start to exit. A healthy one takes about one."""

NOTIFICATION_TIMEOUT = 5.0


def _server_command() -> tuple[str, list[str]]:
    """The ``web-router-mcp`` console script beside this interpreter -- this checkout's own entry point."""
    script = shutil.which("web-router-mcp", path=str(Path(sys.executable).parent))
    # A non-editable environment may lack the script; the module runs the same main().
    return (script, []) if script else (sys.executable, ["-m", "web_router.mcp"])


class Setup:
    """One launch's working directory, home and environment."""

    def __init__(self, tmp_path: Path) -> None:
        self.work = tmp_path / "work"
        self.home = tmp_path / "home"
        self.elsewhere = tmp_path / "elsewhere"
        for directory in (self.work, self.home, self.elsewhere):
            directory.mkdir()
        self.env = isolated_environment(self.home)

    def keys(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{ENV_BRAVE_API_KEY}={FAKE_KEY}\n", encoding="utf-8")
        return path


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    """A launch setup, with this process's home moved too, so :func:`user_config_dir` agrees with the child's."""
    prepared = Setup(tmp_path)
    for name, value in home_variables(prepared.home).items():
        monkeypatch.setenv(name, value)
    return prepared


@asynccontextmanager
async def _server(setup: Setup, notices: list[IncomingMessage] | None = None) -> AsyncIterator[ClientSession]:
    command, args = _server_command()
    parameters = StdioServerParameters(command=command, args=args, env=setup.env, cwd=str(setup.work))

    async def on_message(message: IncomingMessage) -> None:
        if notices is not None:
            notices.append(message)

    with anyio.fail_after(SESSION_TIMEOUT):
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write, message_handler=on_message) as session,
        ):
            await session.initialize()
            yield session


async def _capabilities(session: ClientSession) -> tuple[dict[str, Any], str]:
    contents = await session.read_resource(MCP_RESOURCE_URI)
    text: str = contents.contents[0].text
    return json.loads(text), text


def _used(env: dict[str, Any]) -> dict[str, Any]:
    used: list[dict[str, Any]] = [place for place in env["searched"] if place["status"] == MCP_ENV_STATUS_USED]
    assert len(used) == 1, env["searched"]
    return used[0]


def _assert_found(body: dict[str, Any], text: str, path: Path, source: str) -> None:
    assert PROVIDER_BRAVE in body[MCP_TOOL_SEARCH]
    env = body["env"]
    assert env["loaded"] == {ENV_BRAVE_API_KEY: str(path)}
    assert _used(env) == {"source": source, "path": str(path), "status": MCP_ENV_STATUS_USED}
    assert "hint" not in env
    assert FAKE_KEY not in text, "a key's value must never reach the model"


async def test_an_explicit_env_file_is_found_from_any_directory(setup: Setup) -> None:
    keys = setup.keys(setup.elsewhere / "keys.env")
    setup.env[ENV_MCP_ENV_FILE] = str(keys)

    async with _server(setup) as session:
        body, text = await _capabilities(session)

    _assert_found(body, text, keys, MCP_ENV_SOURCE_EXPLICIT)


async def test_the_user_config_env_is_found_from_any_directory(setup: Setup) -> None:
    keys = setup.keys(user_config_dir() / MCP_ENV_FILE)

    async with _server(setup) as session:
        body, text = await _capabilities(session)

    _assert_found(body, text, keys, MCP_ENV_SOURCE_USER)


async def test_the_working_directory_env_is_still_read(setup: Setup) -> None:
    keys = setup.keys(setup.work / MCP_ENV_FILE)

    async with _server(setup) as session:
        body, text = await _capabilities(session)

    _assert_found(body, text, keys, MCP_ENV_SOURCE_CWD)


async def test_with_no_file_anywhere_the_model_is_told_and_can_load_one(setup: Setup) -> None:
    keys = setup.keys(setup.elsewhere / "keys.env")
    notices: list[IncomingMessage] = []

    async with _server(setup, notices) as session:
        before, _ = await _capabilities(session)
        listed = {tool.name: tool for tool in (await session.list_tools()).tools}
        loaded = await session.call_tool(MCP_TOOL_LOAD_ENV, {"path": str(keys)})
        with anyio.fail_after(NOTIFICATION_TIMEOUT):
            while not any(isinstance(notice, ToolListChangedNotification) for notice in notices):
                await anyio.sleep(0.01)
        after, after_text = await _capabilities(session)
        relisted = {tool.name: tool for tool in (await session.list_tools()).tools}

    # Keyless, and saying how to change that.
    assert before[MCP_TOOL_SEARCH] == [PROVIDER_AUTO, PROVIDER_EXA_MCP, PROVIDER_PARALLEL_MCP, PROVIDER_YOU_MCP]
    assert before["env"]["loaded"] == {}
    assert MCP_TOOL_LOAD_ENV in before["env"]["hint"]
    assert PROVIDER_BRAVE not in listed[MCP_TOOL_SEARCH].input_schema["properties"]["provider"]["enum"]

    assert not loaded.is_error, loaded.content[0].text
    result_text: str = loaded.content[0].text
    result = json.loads(result_text)
    assert (result["file"], result["loaded"]) == (str(keys), [ENV_BRAVE_API_KEY])
    assert PROVIDER_BRAVE in result["configured"][MCP_TOOL_SEARCH]
    assert FAKE_KEY not in result_text

    # Usable at once, no restart: the resource, the schemas and the descriptions all moved.
    assert PROVIDER_BRAVE in after[MCP_TOOL_SEARCH]
    assert "hint" not in after["env"]
    assert after["env"]["loaded_at_runtime"] == [{"file": str(keys), "loaded": [ENV_BRAVE_API_KEY]}]
    assert FAKE_KEY not in after_text
    assert PROVIDER_BRAVE in relisted[MCP_TOOL_SEARCH].input_schema["properties"]["provider"]["enum"]


async def test_a_remembered_file_is_read_on_the_next_start(setup: Setup) -> None:
    keys = setup.keys(setup.elsewhere / "keys.env")

    async with _server(setup) as session:
        remembered = await session.call_tool(MCP_TOOL_LOAD_ENV, {"path": str(keys), "remember": True})
    assert not remembered.is_error, remembered.content[0].text

    async with _server(setup) as session:
        body, text = await _capabilities(session)

    _assert_found(body, text, keys, MCP_ENV_SOURCE_REMEMBERED)
