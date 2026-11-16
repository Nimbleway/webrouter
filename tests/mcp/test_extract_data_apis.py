"""The MCP extract tool's data API matches: listed on a page under ``data_apis`` only when there are some."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.mcp.conftest import ok_on
from tests.test_extract_data_apis import CLIPS
from web_router.constants import MCP_TOOL_EXTRACT
from web_router.constants_vendors import VENDOR_ENV_CATALOG_DIR
from web_router.mcp.tools import WebRouterTools
from web_router.vendors.catalog import reload_catalog


@pytest.fixture(autouse=True)
def _clips_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    (tmp_path / "clips.json").write_text(json.dumps(CLIPS))
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
    reload_catalog()
    yield
    monkeypatch.delenv(VENDOR_ENV_CATALOG_DIR)
    reload_catalog()


async def test_a_page_on_a_served_site_carries_its_data_apis(tools: WebRouterTools) -> None:
    payload = await ok_on(tools, MCP_TOOL_EXTRACT, urls=["https://clips.test/videos/1"])
    matches = payload["documents"][0]["data_apis"]
    assert matches[0]["endpoint"] == "clips.videos.list"
    assert {"endpoint", "configured", "missing", "kind"} <= set(matches[0])
    assert "docs_url" not in matches[0]


async def test_no_match_and_discovery_off_leave_the_field_out(tools: WebRouterTools) -> None:
    payload = await ok_on(tools, MCP_TOOL_EXTRACT, urls=["https://example.com"])
    assert "data_apis" not in payload["documents"][0]
    payload = await ok_on(tools, MCP_TOOL_EXTRACT, urls=["https://clips.test/v"], data_apis=False)
    assert "data_apis" not in payload["documents"][0]
