"""Released cheapest-first spellings keep working for one release with a migration warning."""

from __future__ import annotations

import pytest

from tests.routing_helpers import Table, Tool
from web_router import PreferredRouter, cheapest_first
from web_router.mcp.routing_config import McpRouting
from web_router.routing.contract import Candidate, RouteRequest
from web_router.routing.registry import resolve_router


@pytest.mark.parametrize("by_name", [False, True], ids=["export", "name"])
def test_the_old_spelling_warns_once_and_keeps_the_cheap_order(by_name: bool) -> None:
    req = RouteRequest(
        prompt="https://doi.org/10.1000/182",
        deadline_s=0.001,
        pinned_tool="search",
        candidates=(
            Candidate(tool="search", provider="exa", est_cost_usd=0.01),
            Candidate(tool="search", provider="you-mcp", est_cost_usd=0),
            Candidate(tool="extract", provider="jina", est_cost_usd=0),
        ),
    )
    with pytest.warns(DeprecationWarning, match=r'PreferredRouter\("cheap"\)') as caught:
        router, _ = resolve_router("cheapest_first" if by_name else cheapest_first, None)
        assert router is not None
        decision = router.route(req) if isinstance(router, PreferredRouter) else router(req)
    assert len(caught) == 1
    assert decision == PreferredRouter("cheap").route(req)
    assert router.route_modes is False


@pytest.mark.parametrize("tool", ["search"], indirect=True)
@pytest.mark.usefixtures("called")
async def test_the_mcp_environment_name_starts_and_routes_without_repeated_warnings(priced: Table, tool: Tool) -> None:
    priced.update(first=(0.01, 1.0), second=(0.02, 2.0), third=(0.0, 3.0))
    config = McpRouting.from_environ({"WEB_ROUTER_MCP_ROUTER": "cheapest_first"})
    with pytest.warns(DeprecationWarning, match=r'PreferredRouter\("cheap"\)') as caught:
        async with config.build(provider_config={"auto": {"chain": ["first", "second", "third"]}}) as router:
            first = await router.plan("http://localhost:8080")
            second = await router.plan("https://youtu.be/dQw4w9WgXcQ")
    assert len(caught) == 1
    assert first.decision.router == second.decision.router == "cheapest_first"
    assert first.decision.merged == second.decision.merged
    assert first.decision.merged[0] == f"{tool.name}:third"
