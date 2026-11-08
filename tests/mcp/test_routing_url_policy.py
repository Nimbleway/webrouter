"""``web_run`` plans under the server's ``public_only`` URL policy.

The capabilities resource advertises the policy as a constant, so only a call
shows the server really passes it to the router it builds.
"""

from __future__ import annotations

import pytest

from tests.mcp.conftest import URL, ok_on, routed
from web_router.constants_mcp import MCP_TOOL_WEB_RUN


@pytest.mark.parametrize(
    ("prompt", "host"),
    [
        ("http://127.0.0.1/", "http://127.0.0.1"),
        ("read http://169.254.169.254/latest/meta-data/", "http://169.254.169.254"),
    ],
)
async def test_web_run_refuses_a_private_url_and_names_its_host(members: list[str], prompt: str, host: str) -> None:
    plan = await ok_on(routed(), MCP_TOOL_WEB_RUN, prompt=prompt, dry_run=True)
    assert plan["tool"] != "extract", "a private URL never reaches a backend"
    assert f"url:{host}" in plan["decision"]["dropped"]
    assert members == []


async def test_web_run_still_extracts_a_public_url(members: list[str]) -> None:
    plan = await ok_on(routed(), MCP_TOOL_WEB_RUN, prompt=f"read {URL}", dry_run=True)
    assert plan["tool"] == "extract"
    assert not any(key.startswith("url:") for key in plan["decision"].get("dropped", {}))
