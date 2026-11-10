"""What a ``.env`` cannot do: add keys from the working directory unasked, replace the user's or set the gateway.

A project's ``.env`` is read only when ``WEB_ROUTER_PROJECT_ENV_FILE`` names it,
and last, so it only adds keys. ``AGENTCORE_GATEWAY_URL`` is read from the
environment only, so a file that sets it -- at startup or through ``load_env``
-- is ignored for that variable, with a note.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mcp.conftest import FAKE
from tests.mcp.env_helpers import KEY, OTHER_KEY, holds, is_set, statuses, write
from web_router.constants import (
    AUTO_CHAIN_SEARCH,
    ENV_AGENTCORE_GATEWAY_URL,
    ENV_BRAVE_API_KEY,
    ENV_JINA_API_KEY,
    ENV_MCP_ENV_FILE,
    ENV_NIMBLE_API_KEY,
    ENV_PERPLEXITY_API_KEY,
    ENV_TAVILY_API_KEY,
    MCP_ENV_FILE,
    MCP_ENV_FILE_MAX_BYTES,
    MCP_ENV_POINTER_FILE,
    MCP_ENV_SOURCE_EXPLICIT,
    MCP_ENV_SOURCE_REMEMBERED,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_MISSING,
    MCP_ENV_STATUS_NO_CREDENTIALS,
    MCP_ENV_STATUS_NOTHING_NEW,
    MCP_ENV_STATUS_UNREADABLE,
    MCP_ENV_STATUS_USED,
    MCP_TOOL_LOAD_ENV,
    PROVIDER_AGENTCORE,
    PROVIDER_TAVILY,
)
from web_router.constants_mcp import (
    ENV_MCP_PROJECT_ENV_FILE,
    MCP_ENV_SOURCE_PROJECT,
    MCP_MSG_ENV_HINT_EXPLICIT_MISSING,
    MCP_MSG_ENV_HINT_EXPLICIT_UNREADABLE,
    MCP_MSG_ENV_HINT_EXPLICIT_UNUSED,
    MCP_MSG_ENV_ONLY,
)
from web_router.mcp import methods
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


def test_a_project_env_file_path_opts_in_to_the_working_directorys_env(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How the repo's own .mcp.json reads a clone's .env: by naming it, never by default."""
    project = write(Path.cwd() / MCP_ENV_FILE, f"{ENV_NIMBLE_API_KEY}=from-file\n")
    monkeypatch.setenv(ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()

    assert holds(ENV_NIMBLE_API_KEY, "from-file")
    assert sources.report()["loaded"] == {ENV_NIMBLE_API_KEY: str(project)}
    assert statuses(sources)[-1] == (MCP_ENV_SOURCE_PROJECT, MCP_ENV_STATUS_USED)


def test_a_project_env_only_adds_and_never_hides_the_users_keys(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale key in a clone's .env must not shadow the user's current one."""
    user_env = write(user_config_dir() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={KEY}\n")
    project = write(Path.cwd() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={OTHER_KEY}\n{ENV_BRAVE_API_KEY}={KEY}\n")
    monkeypatch.setenv(ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()

    assert holds(ENV_TAVILY_API_KEY, KEY) and holds(ENV_BRAVE_API_KEY, KEY)
    assert sources.report()["loaded"] == {ENV_TAVILY_API_KEY: str(user_env), ENV_BRAVE_API_KEY: str(project)}
    assert statuses(sources) == [
        (MCP_ENV_SOURCE_USER, MCP_ENV_STATUS_USED),
        (MCP_ENV_SOURCE_PROJECT, MCP_ENV_STATUS_USED),
    ]
    assert sources.startup[-1].already_set == (ENV_TAVILY_API_KEY,)


def test_an_exported_env_file_and_the_project_env_both_load(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside the clone, a user's own WEB_ROUTER_ENV_FILE still loads, and wins where the two disagree."""
    exported = write(isolated_home / "work.env", f"{ENV_TAVILY_API_KEY}={KEY}\n")
    project = write(Path.cwd() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={OTHER_KEY}\n{ENV_BRAVE_API_KEY}={KEY}\n")
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(exported))
    monkeypatch.setenv(ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()

    assert holds(ENV_TAVILY_API_KEY, KEY)
    assert sources.report()["loaded"] == {ENV_TAVILY_API_KEY: str(exported), ENV_BRAVE_API_KEY: str(project)}
    assert statuses(sources)[0] == (MCP_ENV_SOURCE_EXPLICIT, MCP_ENV_STATUS_USED)
    assert statuses(sources)[-1] == (MCP_ENV_SOURCE_PROJECT, MCP_ENV_STATUS_USED)


def test_a_project_env_that_adds_nothing_new_says_so(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={KEY}\n")
    write(Path.cwd() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={OTHER_KEY}\n")
    monkeypatch.setenv(ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()

    assert holds(ENV_TAVILY_API_KEY, KEY)
    assert statuses(sources)[-1] == (MCP_ENV_SOURCE_PROJECT, MCP_ENV_STATUS_NOTHING_NEW)


def test_a_remembered_file_wins_over_the_project_env(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path the user gave load_env is read before a clone's .env, which only fills what is still unset."""
    remembered = write(isolated_home / "remembered.env", f"{ENV_TAVILY_API_KEY}={KEY}\n")
    write(user_config_dir() / MCP_ENV_POINTER_FILE, f"{remembered}\n")
    write(Path.cwd() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}={OTHER_KEY}\n")
    monkeypatch.setenv(ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()

    assert holds(ENV_TAVILY_API_KEY, KEY)
    assert statuses(sources)[-2:] == [
        (MCP_ENV_SOURCE_REMEMBERED, MCP_ENV_STATUS_USED),
        (MCP_ENV_SOURCE_PROJECT, MCP_ENV_STATUS_NOTHING_NEW),
    ]


def test_a_missing_project_env_is_no_missing_explicit_file(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh clone or worktree has no .env yet: that is not a typo in the user's WEB_ROUTER_ENV_FILE."""
    monkeypatch.setenv(ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()
    report = sources.report()

    assert report["explicit_file_missing"] is False
    assert statuses(sources)[-1] == (MCP_ENV_SOURCE_PROJECT, MCP_ENV_STATUS_MISSING)
    hint = WebRouterTools(env=sources).capabilities()["env"]["hint"]
    path = sources.searched[-1].path
    assert hint == MCP_MSG_ENV_HINT_EXPLICIT_MISSING.format(env_var=ENV_MCP_PROJECT_ENV_FILE, path=path)


def test_a_project_env_with_blank_keys_gets_a_hint_naming_it(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copied .env.example has the names but no values: the hint points at that file, not at another one."""
    write(Path.cwd() / MCP_ENV_FILE, f"{ENV_TAVILY_API_KEY}=\n")
    monkeypatch.setenv(ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_FILE)

    sources = EnvSources.at_startup()
    hint = WebRouterTools(env=sources).capabilities()["env"]["hint"]

    lookup = sources.searched[-1]
    assert (lookup.source, lookup.status) == (MCP_ENV_SOURCE_PROJECT, MCP_ENV_STATUS_NO_CREDENTIALS)
    assert hint == MCP_MSG_ENV_HINT_EXPLICIT_UNUSED.format(
        env_var=ENV_MCP_PROJECT_ENV_FILE, path=lookup.path, status=MCP_ENV_STATUS_NO_CREDENTIALS
    )


def test_a_used_explicit_file_that_left_the_server_keyless_gets_a_hint_naming_it(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file with only a keyless backend's key was used, but the hint still points there, not at the variable."""
    path = write(isolated_home / "jina-only.env", f"{ENV_JINA_API_KEY}=jina-test-value\n")
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(path))

    sources = EnvSources.at_startup()
    hint = WebRouterTools(env=sources).capabilities()["env"]["hint"]

    assert statuses(sources)[0] == (MCP_ENV_SOURCE_EXPLICIT, MCP_ENV_STATUS_USED)
    assert hint == MCP_MSG_ENV_HINT_EXPLICIT_UNUSED.format(
        env_var=ENV_MCP_ENV_FILE, path=path, status=MCP_ENV_STATUS_USED
    )


def test_a_missing_explicit_file_gets_a_hint_to_create_it(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The variable is set already, so the hint names the file rather than asking for the variable."""
    missing = isolated_home / "nowhere" / ".env"
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(missing))

    hint = WebRouterTools(env=EnvSources.at_startup()).capabilities()["env"]["hint"]

    assert hint == MCP_MSG_ENV_HINT_EXPLICIT_MISSING.format(env_var=ENV_MCP_ENV_FILE, path=missing)


def test_an_explicit_file_with_no_key_gets_a_hint_naming_it(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file is there, so the hint neither calls it missing nor asks for a variable that is set already."""
    path = write(isolated_home / "keyless.env", "DATABASE_URL=x\n")
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(path))

    sources = EnvSources.at_startup()
    hint = WebRouterTools(env=sources).capabilities()["env"]["hint"]

    assert statuses(sources)[0] == (MCP_ENV_SOURCE_EXPLICIT, MCP_ENV_STATUS_NO_CREDENTIALS)
    assert hint == MCP_MSG_ENV_HINT_EXPLICIT_UNUSED.format(
        env_var=ENV_MCP_ENV_FILE, path=path, status=MCP_ENV_STATUS_NO_CREDENTIALS
    )


@pytest.mark.parametrize("kind", ["directory", "too-large"])
def test_an_explicit_path_the_server_cannot_read_gets_a_hint_to_repoint_it(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Keys put there would never load, so the hint asks for a readable file instead."""
    path = isolated_home / kind
    if kind == "directory":
        path.mkdir()
    else:
        write(path, "#" * (MCP_ENV_FILE_MAX_BYTES + 1))
    monkeypatch.setenv(ENV_MCP_ENV_FILE, str(path))

    sources = EnvSources.at_startup()
    hint = WebRouterTools(env=sources).capabilities()["env"]["hint"]

    assert statuses(sources)[0] == (MCP_ENV_SOURCE_EXPLICIT, MCP_ENV_STATUS_UNREADABLE)
    reason = sources.searched[0].reason
    assert reason
    assert hint == MCP_MSG_ENV_HINT_EXPLICIT_UNREADABLE.format(env_var=ENV_MCP_ENV_FILE, path=path, reason=reason)
    assert "put their keys there" not in hint


def test_the_repos_mcp_json_opts_the_clone_in_through_the_project_variable() -> None:
    """Without it, a clone's own .env would silently stop being read; with the other, a user's would be hidden."""
    (server,) = json.loads(MCP_JSON.read_text())["mcpServers"].values()

    assert server["env"] == {ENV_MCP_PROJECT_ENV_FILE: MCP_ENV_FILE}


def test_unlock_offers_no_variable_a_file_cannot_set(no_keys: None) -> None:
    """The model acts on ``unlock``: the gateway there would send the user to a .env that load_env refuses."""
    unlocks = methods.unlock()

    assert all(PROVIDER_AGENTCORE not in providers for providers in unlocks.values())
    assert ENV_AGENTCORE_GATEWAY_URL not in json.dumps(unlocks)


def test_unlock_keeps_a_backend_whose_key_is_set_but_blank(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a backend that needs nothing but env-only variables is left out: tavily is still listed, as before."""
    monkeypatch.setenv(ENV_TAVILY_API_KEY, " ")

    assert PROVIDER_TAVILY in methods.unlock()[AUTO_CHAIN_SEARCH]


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


@pytest.mark.parametrize(
    ("env_var", "source"),
    [(ENV_MCP_ENV_FILE, MCP_ENV_SOURCE_EXPLICIT), (ENV_MCP_PROJECT_ENV_FILE, MCP_ENV_SOURCE_PROJECT)],
)
def test_a_startup_file_that_sets_only_the_gateway_says_so(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch, env_var: str, source: str
) -> None:
    path = write(isolated_home / "gateway.env", f"{ENV_AGENTCORE_GATEWAY_URL}={GATEWAY}\n")
    monkeypatch.setenv(env_var, str(path))

    sources = EnvSources.at_startup()

    assert not is_set(ENV_AGENTCORE_GATEWAY_URL)
    (looked,) = [place for place in sources.report()["searched"] if place["source"] == source]
    assert (looked["status"], looked["note"]) == (MCP_ENV_STATUS_NO_CREDENTIALS, NOTE)
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
