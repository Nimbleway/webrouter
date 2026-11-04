from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# Every module here drives the server, which is the mcp extra's.
pytest.importorskip("mcp", reason="mcp extra not installed")

from web_router.client import AsyncWebRouter
from web_router.constants import (
    ANSWER_ENV_BY_PROVIDER,
    BROWSE_ENV_BY_PROVIDER,
    BROWSER_ENV_BY_PROVIDER,
    ENV_APPDATA,
    ENV_BY_PROVIDER,
    ENV_MCP_ENV_FILE,
    ENV_XDG_CONFIG_HOME,
    EXTRACT_ENV_BY_PROVIDER,
    RESEARCH_ENV_BY_PROVIDER,
    TEMPLATE_ENV_BY_PROVIDER,
)
from web_router.mcp.tools import WebRouterTools

FAKE = "fake"

_EVERY_ENV_VAR = sorted(
    {name for name in ENV_BY_PROVIDER.values() if name}
    | {name for names in EXTRACT_ENV_BY_PROVIDER.values() for name in names}
    | {name for names in TEMPLATE_ENV_BY_PROVIDER.values() for name in names}
    | {name for names in RESEARCH_ENV_BY_PROVIDER.values() for name in names}
    | {name for names in BROWSE_ENV_BY_PROVIDER.values() for name in names}
    | {name for names in BROWSER_ENV_BY_PROVIDER.values() for name in names}
    | {name for names in ANSWER_ENV_BY_PROVIDER.values() for name in names}
)


def home_variables(home: Path) -> dict[str, str]:
    """The variables every platform finds its per-user config directory from, all pointed under ``home``."""
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        ENV_XDG_CONFIG_HOME: str(home / ".config"),
        ENV_APPDATA: str(home / "AppData" / "Roaming"),
    }


def isolated_environment(home: Path) -> dict[str, str]:
    """This process's environment for a server child: no credential, and nowhere to find a ``.env`` but ``home``.

    Derived from the credential tables rather than from a name pattern, so a
    provider whose variable is not spelled ``*_API_KEY`` -- Oxylabs' password,
    Bright Data's zone -- is stripped too. The home directory moves as well,
    or the child would read this machine's own user config ``.env`` back.
    """
    environment = {
        key: value for key, value in os.environ.items() if key not in _EVERY_ENV_VAR and key != ENV_MCP_ENV_FILE
    }
    return {**environment, **home_variables(home)}


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every credential out of the environment, so a test sees one known install.

    `tests/conftest.py` loads a developer's `.env` before collection, which
    would otherwise make the generated schemas depend on whose machine they
    were generated on.
    """
    for env_var in _EVERY_ENV_VAR:
        # Set before deleting: monkeypatch records nothing for a variable that
        # was never there, and a key a test then loads from a file would
        # outlive it.
        monkeypatch.setenv(env_var, "")
        monkeypatch.delenv(env_var)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_keys: None) -> Path:
    """A home directory of the test's own, an empty working directory, and no ``WEB_ROUTER_ENV_FILE``.

    So where a ``.env`` is found is decided by the test alone, never by this
    machine's own user config directory or the repo's ``.env``.
    """
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    for name, value in home_variables(home).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(ENV_MCP_ENV_FILE, raising=False)
    monkeypatch.chdir(work)
    return home


@pytest.fixture
def tools(
    fake_provider: Any,
    fake_extract_provider: Any,
    fake_template_provider: Any,
    fake_research_provider: Any,
    fake_answer_provider: Any,
    fake_browse_provider: Any,
) -> WebRouterTools:
    """A session whose every method routes to the in-memory fakes.

    A zero wait is what makes the "it did not finish in time" path testable
    without waiting: the first poll that reports a running job is already out
    of budget.
    """
    return WebRouterTools(AsyncWebRouter(FAKE), research_wait=0.0, browse_wait=0.0)
