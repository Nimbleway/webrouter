from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# Every module here drives the server, which is the mcp extra's.
pytest.importorskip("mcp", reason="mcp extra not installed")

from tests.routing_helpers import MEMBERS, TOOLS
from tests.routing_helpers import member as _member
from web_router import prices
from web_router.client import AsyncWebRouter
from web_router.constants import (
    ENV_APPDATA,
    ENV_MCP_ENV_FILE,
    ENV_XDG_CONFIG_HOME,
)
from web_router.constants_mcp import ENV_MCP_PROJECT_ENV_FILE
from web_router.mcp.env_credentials import credential_variables
from web_router.mcp.routing_config import McpRouting
from web_router.mcp.server import call_tool
from web_router.mcp.tools import WebRouterTools
from web_router.routing import Budget

FAKE = "fake"

_EVERY_ENV_VAR = sorted(credential_variables())
_ENV_FILE_VARS = (ENV_MCP_ENV_FILE, ENV_MCP_PROJECT_ENV_FILE)


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
        key: value for key, value in os.environ.items() if key not in _EVERY_ENV_VAR and key not in _ENV_FILE_VARS
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
    """A home directory of the test's own, an empty working directory, and no ``.env`` path variable.

    So where a ``.env`` is found is decided by the test alone, never by this
    machine's own user config directory or the repo's ``.env``.
    """
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    for name, value in home_variables(home).items():
        monkeypatch.setenv(name, value)
    for env_var in _ENV_FILE_VARS:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.chdir(work)
    return home


@pytest.fixture
def tools(
    fake_provider: Any,
    fake_extract_provider: Any,
    fake_live_data_provider: Any,
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


# -- the routing tools' session: fake members, the test's prices, and its calls --------

URL = "https://example.com/report"
CONFIG = {"auto": {"chain": list(MEMBERS)}}

Table = dict[str, float | None]


@pytest.fixture
def members() -> Iterator[list[str]]:
    """``first``, ``second`` and ``third`` for every method, all answering; who was called."""
    log: list[str] = []
    for tool in TOOLS:
        for name in MEMBERS:
            tool.register(name, _member(tool, name, log, fails=False))
    yield log
    for tool in TOOLS:
        for name in MEMBERS:
            tool.unregister(name)


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> Table:
    """The price the budget reads for each member by name, whatever the method; unlisted is unknown."""
    table: Table = {}

    def price(provider: str, method: str, mode: str | None = None) -> SimpleNamespace | None:
        cost = table.get(provider)
        return None if cost is None else SimpleNamespace(estimate_usd=cost, estimate_is_floor=False, mode=mode)

    monkeypatch.setattr(prices, "price", price)
    monkeypatch.setattr(prices, "estimate_latency", lambda *args, **kwargs: None)
    return table


def routed(ceiling: Budget | None = None, **environ: str) -> WebRouterTools:
    """A session over the fake members, its router and ceiling as ``environ`` and ``ceiling`` set them."""
    routing = McpRouting.from_environ(environ)
    if ceiling is not None:
        routing = McpRouting(router=routing.router, router_name=routing.router_name, ceiling=ceiling)
    return WebRouterTools(routing.build(provider_config=CONFIG), routing=routing, research_wait=0.0)


async def outcome(tools: WebRouterTools, name: str, **arguments: Any) -> tuple[bool, Any]:
    result = await call_tool(tools, name, arguments)
    text = result.content[0].text
    return bool(result.is_error), text if result.is_error else json.loads(text)


async def ok_on(tools: WebRouterTools, name: str, **arguments: Any) -> dict[str, Any]:
    failed, payload = await outcome(tools, name, **arguments)
    assert not failed, payload
    return dict(payload)
