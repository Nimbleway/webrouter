"""Credentials of more than one variable: a ``.env`` applies one only as a whole, and skips half of one."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.mcp.env_helpers import KEY, OTHER_KEY, holds, is_set, statuses, write
from web_router.constants import (
    ENV_BRIGHTDATA_API_KEY,
    ENV_BRIGHTDATA_ZONE,
    ENV_NIMBLE_API_KEY,
    MCP_ENV_FILE,
    MCP_ENV_SOURCE_CWD,
    MCP_ENV_SOURCE_USER,
    MCP_ENV_STATUS_NOTHING_NEW,
    MCP_ENV_STATUS_USED,
)
from web_router.mcp.env import EnvSources, user_config_dir


def test_a_file_never_completes_a_credential_the_user_half_holds(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project's zone beside the user's own Bright Data token would route the user's account its way."""
    monkeypatch.setenv(ENV_BRIGHTDATA_API_KEY, KEY)
    write(
        Path.cwd() / MCP_ENV_FILE,
        f"{ENV_BRIGHTDATA_API_KEY}={OTHER_KEY}\n{ENV_BRIGHTDATA_ZONE}=project-zone\n{ENV_NIMBLE_API_KEY}={OTHER_KEY}\n",
    )

    sources = EnvSources.at_startup()

    assert not is_set(ENV_BRIGHTDATA_ZONE) and holds(ENV_BRIGHTDATA_API_KEY, KEY)
    assert holds(ENV_NIMBLE_API_KEY, OTHER_KEY), "an unrelated key still adds"
    assert list(sources.report()["loaded"]) == [ENV_NIMBLE_API_KEY]


def test_half_a_credential_in_one_file_never_shuts_out_the_whole_one_in_the_next(isolated_home: Path) -> None:
    """A lone token applies nothing, so a later file's token and zone arrive as the pair they are."""
    write(user_config_dir() / MCP_ENV_FILE, f"{ENV_BRIGHTDATA_API_KEY}={KEY}\n")
    project = write(Path.cwd() / MCP_ENV_FILE, f"{ENV_BRIGHTDATA_API_KEY}={OTHER_KEY}\n{ENV_BRIGHTDATA_ZONE}=zone\n")

    sources = EnvSources.at_startup()

    assert holds(ENV_BRIGHTDATA_API_KEY, OTHER_KEY) and holds(ENV_BRIGHTDATA_ZONE, "zone")
    assert sources.report()["loaded"] == {ENV_BRIGHTDATA_API_KEY: str(project), ENV_BRIGHTDATA_ZONE: str(project)}
    assert statuses(sources) == [
        (MCP_ENV_SOURCE_USER, MCP_ENV_STATUS_NOTHING_NEW),
        (MCP_ENV_SOURCE_CWD, MCP_ENV_STATUS_USED),
    ]
