"""The fixtures that set the server's switches for ``tests/mcp``: opened up by default, and two that list more."""

from __future__ import annotations

import pytest

from tests.mcp.env_helpers import OPENED_UP
from web_router.constants_mcp_context import ENV_MCP_TOOLS
from web_router.mcp import methods


@pytest.fixture(autouse=True)
def opened_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every tool served and every switch open (:data:`OPENED_UP`), so a module tests what the tools do."""
    for name, value in OPENED_UP.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def all_runnable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every method taken as having a backend here, so no tool is left off the list for want of a key."""
    monkeypatch.setattr(methods, "usable", lambda method: True)


@pytest.fixture
def every_tool_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """An allowlist naming every tool, which lists one with nothing to serve it, its description saying so."""
    monkeypatch.setenv(ENV_MCP_TOOLS, "*")
