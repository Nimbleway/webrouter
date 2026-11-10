"""What a ``.env`` cannot do: add keys from the working directory unasked, or set ``AGENTCORE_GATEWAY_URL``.

The gateway is read from the environment only, so a file that sets it -- at
startup or through ``load_env`` -- is ignored for that variable, with a note.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mcp.conftest import FAKE
from tests.mcp.env_helpers import KEY, OTHER_KEY, holds, is_set, statuses, write
from web_router.constants import (
    ENV_AGENTCORE_GATEWAY_URL,
    ENV_BRAVE_API_KEY,
    ENV_MCP_ENV_FILE,
    ENV_NIMBLE_API_KEY,
    ENV_PERPLEXITY_API_KEY,
    ENV_TAVILY_API_KEY,
    MCP_ENV_FILE,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_NO_CREDENTIALS,
    MCP_ENV_STATUS_USED,
    MCP_TOOL_LOAD_ENV,
)
from web_router.constants_mcp import MCP_MSG_ENV_ONLY
from web_router.mcp.env import EnvFileError, EnvSources, user_config_dir
from web_router.mcp.env_credentials import file_variables
from web_router.mcp.server import call_tool
from web_router.mcp.tools import WebRouterTools

NOTE = MCP_MSG_ENV_ONLY.format(names=ENV_AGENTCORE_GATEWAY_URL)

GATEWAY = "https://abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
OTHER_GATEWAY = "https://gateway.example/mcp"

MCP_JSON = Path(__file__).resolve().parents[2] / ".mcp.json"


def test_a_project_env_in_the_working_directory_adds_no_key_the_user_lacks(isolated_home: Path) -> None:
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={KEY}\n")
    write(Path.cwd() / MCP_ENV_FILE, f"{ENV_PERPLEXITY_API_KEY}={OTHER_KEY}\n")

    sources = EnvSources.at_startup()

    assert holds(ENV_TAVILY_API_KEY, KEY)
    assert not is_set(ENV_PERPLEXITY_API_KEY)
    assert sources.report()["loaded"] == {ENV_TAVILY_API_KEY: str(user_config_dir() / MCP_ENV_FILE)}
    assert str(Path.cwd()) not in json.dumps(sources.report()["searched"])


def test_a_relative_env_file_path_opts_in_to_the_working_directorys_env(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How the repo's own .mcp.json reads a clone's .env: by naming it, never by default."""
    project = write(Path.cwd() / MCP_ENV_FILE, f"{ENV_NIMBLE_API_KEY}=from-file\n")
    monkeypatch.setenv(ENV_MCP_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()

    assert holds(ENV_NIMBLE_API_KEY, "from-file")
    assert sources.report()["loaded"] == {ENV_NIMBLE_API_KEY: str(project)}
    assert statuses(sources)[0] == (MCP_ENV_SOURCE_EXPLICIT, MCP_ENV_STATUS_USED)


def test_the_repos_mcp_json_opts_the_clone_in_to_its_env() -> None:
    """Without it, a clone's own .env would silently stop being read."""
    (server,) = json.loads(MCP_JSON.read_text())["mcpServers"].values()

    assert server["env"][ENV_MCP_ENV_FILE] == MCP_ENV_FILE


def test_no_file_may_set_the_agentcore_gateway() -> None:
    assert ENV_AGENTCORE_GATEWAY_URL not in file_variables()


def test_a_startup_file_that_sets_the_gateway_is_ignored_for_it_with_a_note(isolated_home: Path) -> None:
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_AGENTCORE_GATEWAY_URL}={GATEWAY}\n{ENV_BRAVE_API_KEY}={KEY}\n")

    sources = EnvSources.at_startup()

    assert not is_set(ENV_AGENTCORE_GATEWAY_URL) and holds(ENV_BRAVE_API_KEY, KEY)
    (searched,) = sources.report()["searched"]
    assert (searched["source"], searched["status"], searched["note"]) == (
        MCP_ENV_SOURCE_USER,
        MCP_ENV_STATUS_USED,
        NOTE,
    )


def test_a_startup_file_that_sets_only_the_gateway_says_so(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(isolated_home / "gateway.env", f"{ENV_AGENTCORE_GATEWAY_URL}={GATEWAY}\n")
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(path))

    sources = EnvSources.at_startup()

    assert not is_set(ENV_AGENTCORE_GATEWAY_URL)
    explicit = sources.report()["searched"][0]
    assert (explicit["source"], explicit["status"], explicit["note"]) == (
        MCP_ENV_SOURCE_EXPLICIT,
        MCP_ENV_STATUS_NO_CREDENTIALS,
        NOTE,
    )
    assert sources.report()["explicit_file_missing"] is False


async def test_load_env_ignores_the_gateway_with_a_note(isolated_home: Path) -> None:
    path = write(isolated_home / "keys.env", f"{ENV_AGENTCORE_GATEWAY_URL}={GATEWAY}\n{ENV_BRAVE_API_KEY}={KEY}\n")
    session = WebRouterTools()

    result = await call_tool(session, MCP_TOOL_LOAD_ENV, {"path": str(path)})
    assert not result.is_error, result.content[0].text
    payload = json.loads(result.content[0].text)

    assert not is_set(ENV_AGENTCORE_GATEWAY_URL)
    assert (payload["loaded"], payload["note"]) == ([ENV_BRAVE_API_KEY], NOTE)
    await session.aclose()


def test_load_env_of_a_file_that_sets_only_the_gateway_says_so(isolated_home: Path) -> None:
    path = write(isolated_home / "gateway.env", f"{ENV_AGENTCORE_GATEWAY_URL}={GATEWAY}\n")

    with pytest.raises(EnvFileError) as raised:
        EnvSources().load(str(path))

    assert not is_set(ENV_AGENTCORE_GATEWAY_URL)
    assert raised.value.status == MCP_ENV_STATUS_NO_CREDENTIALS
    assert NOTE in str(raised.value) and ENV_BRAVE_API_KEY not in str(raised.value)


def test_the_no_credentials_message_does_not_offer_the_gateway(isolated_home: Path) -> None:
    path = write(isolated_home / "empty.env", "CHROME_PATH=/bin/true\n")

    with pytest.raises(EnvFileError) as raised:
        EnvSources().load(str(path))

    assert ENV_BRAVE_API_KEY in str(raised.value) and ENV_AGENTCORE_GATEWAY_URL not in str(raised.value)


def test_an_exported_gateway_is_kept_and_reported(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_AGENTCORE_GATEWAY_URL, GATEWAY)
    write(
        user_config_dir() / MCP_ENV_FILE, f"{ENV_AGENTCORE_GATEWAY_URL}={OTHER_GATEWAY}\n{ENV_BRAVE_API_KEY}={FAKE}\n"
    )

    sources = EnvSources.at_startup()

    assert holds(ENV_AGENTCORE_GATEWAY_URL, GATEWAY)
    assert ENV_AGENTCORE_GATEWAY_URL in sources.report()["from_environment"]
    assert "note" not in sources.report()["searched"][-1]
