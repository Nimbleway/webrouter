"""The MCP search tool's data API matches: listed under ``data_apis`` only when there are some, never called."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.mcp.conftest import ok_on
from tests.vendors.data import ACME
from web_router.constants import MCP_TOOL_SEARCH
from web_router.constants_data_api import DATA_APIS_ONLY_PROVIDER
from web_router.constants_vendors import VENDOR_ENV_CATALOG_DIR
from web_router.mcp.tools import WebRouterTools
from web_router.vendors.catalog import reload_catalog


async def test_search_lists_the_matching_data_apis_beside_the_results(tools: WebRouterTools) -> None:
    payload = await ok_on(tools, MCP_TOOL_SEARCH, query="datadog monitors")
    assert payload["results"]
    assert payload["data_apis"]
    assert {match["data_api"] for match in payload["data_apis"]} == {"datadog"}
    assert {"endpoint", "configured", "missing", "kind"} <= set(payload["data_apis"][0])


async def test_no_match_and_discovery_off_leave_the_field_out(tools: WebRouterTools) -> None:
    assert "data_apis" not in await ok_on(tools, MCP_TOOL_SEARCH, query="who won the 2022 world cup")
    assert "data_apis" not in await ok_on(tools, MCP_TOOL_SEARCH, query="datadog monitors", data_apis=False)


async def test_data_apis_only_reaches_no_web_backend(tools: WebRouterTools, fake_provider: Any) -> None:
    payload = await ok_on(tools, MCP_TOOL_SEARCH, query="datadog monitors", data_apis_only=True)
    assert payload["provider"] == DATA_APIS_ONLY_PROVIDER
    assert payload["results"] == []
    assert payload["data_apis"]
    assert tools.router.get_provider("fake").requests == []  # type: ignore[attr-defined]


async def test_a_long_catalog_summary_is_cut_to_fit_the_tool_answer(
    tools: WebRouterTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendor = copy.deepcopy(ACME)
    vendor["endpoints"][0]["summary"] = "s" * 5000
    (tmp_path / "acme.json").write_text(json.dumps(vendor))
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
    reload_catalog()
    try:
        payload = await ok_on(tools, MCP_TOOL_SEARCH, query="acme metrics query")
    finally:
        monkeypatch.delenv(VENDOR_ENV_CATALOG_DIR)
        reload_catalog()
    match = payload["data_apis"][0]
    assert match["endpoint"] == "acme.metrics.query"
    assert len(match["summary"]) <= 300
    assert "docs_url" not in match
