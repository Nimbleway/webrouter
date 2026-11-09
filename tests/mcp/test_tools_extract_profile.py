"""The MCP extract tool and ``profile``: a saved, logged-in identity is set in code, never from a conversation."""

from __future__ import annotations

import pytest

from tests.mcp.test_tools import _tool
from web_router.client import AsyncWebRouter
from web_router.constants import ENV_BROWSERBASE_API_KEY, MCP_TOOL_EXTRACT, PROVIDER_BROWSERBASE
from web_router.mcp.tools import WebRouterTools


def test_extract_offers_no_way_to_name_a_saved_profile(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A logged-in identity is set in code, as on browser_open; one sent anyway is dropped before the library."""
    monkeypatch.setenv(ENV_BROWSERBASE_API_KEY, "x")
    tools = WebRouterTools(AsyncWebRouter(), research_wait=0.0, browse_wait=0.0)
    extract = _tool(tools, MCP_TOOL_EXTRACT)

    assert PROVIDER_BROWSERBASE in extract.input_schema["properties"]["provider"]["enum"]
    assert "profile" not in extract.input_schema["properties"]
    assert "profile" not in tools.arguments(MCP_TOOL_EXTRACT, {"urls": ["https://example.com"], "profile": "ctx-1"})
