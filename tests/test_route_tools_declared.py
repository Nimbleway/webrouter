"""Which tools ask the router: the caller's ``route_tools``, else the router's own ``route_tools``, else all five."""

from __future__ import annotations

from typing import Any

import pytest

from tests.routing_helpers import MEMBERS, Ranker, Tool, order, wr
from web_router import AsyncWebRouter, RouteDecision, RouteRequest, WebRouter
from web_router.exceptions import ConfigurationError

ARGS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "search": (("who won",), {}),
    "extract": ((["https://a.test"],), {}),
    "answer": (("who won",), {}),
    "research": (("who won",), {}),
    "browse": (("book a table",), {"start_url": "https://c.test"}),
}


class Declaring(Ranker):
    """A ranker that declares the tools it is consulted on."""

    def __init__(self, route_tools: Any) -> None:
        super().__init__("third")
        self.route_tools = route_tools


def _call(router: WebRouter, tool: Tool) -> Any:
    args, options = ARGS[tool.name]
    return getattr(router, tool.name)(*args, **options)


def _others(tool: Tool) -> list[str]:
    return [name for name in ARGS if name != tool.name]


def test_a_router_that_declares_none_is_asked_on_every_tool(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    _call(wr(WebRouter, ranker), tool)
    assert len(ranker.seen) == 1


def test_the_router_s_own_route_tools_apply_when_the_caller_passes_none(tool: Tool, called: list[str]) -> None:
    left_out = Declaring(_others(tool))
    response = _call(wr(WebRouter, left_out), tool)
    assert left_out.seen == [] and response.decision is None
    assert order(called) == list(MEMBERS)

    named = Declaring((tool.name,))
    _call(wr(WebRouter, named), tool)
    assert len(named.seen) == 1


def test_the_caller_s_route_tools_win_over_the_router_s(tool: Tool, called: list[str]) -> None:
    declares_all = Declaring(list(ARGS))
    _call(wr(WebRouter, declares_all, route_tools=_others(tool)), tool)
    assert declares_all.seen == []

    declares_none = Declaring(())
    _call(wr(WebRouter, declares_none, route_tools=[tool.name]), tool)
    assert len(declares_none.seen) == 1


def test_a_router_function_may_declare_route_tools(tool: Tool, called: list[str]) -> None:
    seen: list[RouteRequest] = []

    def route(req: RouteRequest) -> RouteDecision:
        seen.append(req)
        return RouteDecision()

    route.route_tools = _others(tool)  # type: ignore[attr-defined]
    _call(wr(WebRouter, route), tool)
    assert seen == []


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
@pytest.mark.parametrize(
    "declared", ["search", ["search", "open_browser"], 3, [["search"]]], ids=["str", "unknown", "int", "unhashable"]
)
def test_a_router_s_route_tools_take_only_the_five_tools(cls: type, declared: Any) -> None:
    with pytest.raises(ConfigurationError, match="router's route_tools"):
        cls(router=Declaring(declared))


def test_a_router_built_by_name_declares_route_tools_from_its_config() -> None:
    from web_router import register_router, unregister_router

    register_router("test-declaring", lambda **config: Declaring(config["route_tools"]))
    try:
        with WebRouter(router="test-declaring", router_config={"route_tools": ["research"]}) as wr:
            assert wr._route_tools == frozenset({"research"})
        with WebRouter(router="test-declaring", router_config={"route_tools": ["research"]}, route_tools=()) as wr:
            assert wr._route_tools == frozenset()
    finally:
        unregister_router("test-declaring")


def test_the_remote_router_takes_route_tools_in_its_config() -> None:
    config = {"url": "http://127.0.0.1:1/route", "route_tools": ("answer", "research")}
    with WebRouter(router="remote", router_config=config) as wr:
        assert wr._route_tools == frozenset({"answer", "research"})
    with pytest.raises(ConfigurationError, match="router's route_tools"):
        WebRouter(router="remote", router_config={**config, "route_tools": ("plan",)})
