"""A router attached to ``WebRouter`` and ``AsyncWebRouter``: when it is asked, what it sees, and what its answer does.

Each tool's ``auto`` runs over three fake members, ``first`` and ``second``
failing before they send anything and ``third`` answering, so the members
called show the order the call ran in.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

import pytest

from tests.routing_helpers import MEMBERS, Ranker, Tool, order
from web_router import AsyncWebRouter, RouteDecision, RouteRequest, RouterError, WebRouter
from web_router.exceptions import ConfigurationError

SECRET = "secret-value-never-shown-to-a-router"
ARGS: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
    "search": (("who won",), {}),
    "extract": ((["https://a.test", "https://b.test"],), {}),
    "answer": (("who won",), {}),
    "research": (("who won",), {}),
    "browse": (("book a table",), {"start_url": "https://c.test"}),
}
PROMPTS = {
    "search": ("who won", ()),
    "extract": ("https://a.test\nhttps://b.test", ("https://a.test", "https://b.test")),
    "answer": ("who won", ()),
    "research": ("who won", ()),
    "browse": ("book a table", ("https://c.test",)),
}


def _router(cls: type, router: Any, **kwargs: Any) -> Any:
    config = {"auto": {"chain": list(MEMBERS)}, "first": {"api_key": SECRET}}
    return cls(provider_config=config, router=router, **kwargs)


def _call(router: WebRouter, tool: Tool, **kwargs: Any) -> Any:
    args, options = ARGS[tool.name]
    return getattr(router, tool.name)(*args, **options, **kwargs)


async def _acall(router: AsyncWebRouter, tool: Tool, **kwargs: Any) -> Any:
    args, options = ARGS[tool.name]
    return await getattr(router, tool.name)(*args, **options, **kwargs)


def test_the_router_orders_the_call(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    response = _call(_router(WebRouter, ranker), tool)
    assert response.provider == "third"
    assert order(called) == ["third"]
    assert len(ranker.seen) == 1


async def test_the_router_orders_the_async_call(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    response = await _acall(_router(AsyncWebRouter, ranker), tool)
    assert response.provider == "third"
    assert order(called) == ["third"]


def test_the_rest_follow_in_static_order_and_unknown_names_are_dropped(tool: Tool, called: list[str]) -> None:
    response = _call(_router(WebRouter, Ranker("second", "nobody")), tool)
    assert order(called) == ["second", "first", "third"]
    assert response.decision.merged == [f"{tool.name}:{name}" for name in ("second", "first", "third")]


def test_the_router_sees_the_prompt_the_candidates_and_no_key(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    _call(_router(WebRouter, ranker), tool)
    (req,) = ranker.seen
    assert (req.prompt, req.urls) == PROMPTS[tool.name]
    assert req.pinned_tool == tool.name
    assert [candidate.id for candidate in req.candidates] == [f"{tool.name}:{name}" for name in MEMBERS]
    assert 0 < req.deadline_s <= 0.05
    assert SECRET not in repr(req)


def test_the_decision_record_says_what_the_router_did(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    decision = _call(_router(WebRouter, ranker), tool).decision
    ids = [f"{tool.name}:{name}" for name in MEMBERS]
    assert decision.router == "Ranker"
    assert decision.offered == ids
    assert decision.ranked == [f"{tool.name}:third"]
    assert decision.merged == [ids[2], ids[0], ids[1]]
    assert decision.reason == "ranked"
    assert decision.router_timeout is None  # set only when it ran out of time
    assert decision.router_ms is not None and decision.router_ms >= 0


@pytest.mark.parametrize("router", [lambda req: 1 / 0, lambda req: "third"], ids=["raises", "invalid"])
def test_a_failing_router_leaves_the_static_order(tool: Tool, called: list[str], router: Any) -> None:
    decision = _call(_router(WebRouter, router), tool).decision
    assert order(called) == list(MEMBERS)
    assert decision.reason in {"router_error", "router_invalid"}
    assert decision.merged == [f"{tool.name}:{name}" for name in MEMBERS]
    assert decision.ranked is None


def test_a_router_out_of_time_leaves_the_static_order(tool: Tool, called: list[str]) -> None:
    def slow(req: RouteRequest) -> RouteDecision:
        time.sleep(0.5)
        return RouteDecision(providers={tool.name: ["third"]})  # type: ignore[dict-item]

    decision = _call(_router(WebRouter, slow), tool).decision
    assert order(called) == list(MEMBERS)
    assert decision.router_timeout is True
    assert decision.reason == "router_timeout"


def test_fail_on_router_error_raises_before_any_backend_is_called(tool: Tool, called: list[str]) -> None:
    router = _router(WebRouter, lambda req: 1 / 0, fail_on_router_error=True)
    with pytest.raises(RouterError, match="router_error"):
        _call(router, tool)
    assert order(called) == []


async def test_fail_on_router_error_raises_from_async_code_too(tool: Tool, called: list[str]) -> None:
    router = _router(AsyncWebRouter, lambda req: None, fail_on_router_error=True)
    with pytest.raises(RouterError, match="router_invalid"):
        await _acall(router, tool)
    assert order(called) == []


def test_a_tool_left_out_of_route_tools_never_asks_the_router(tool: Tool, called: list[str]) -> None:
    others = [name for name in ARGS if name != tool.name]
    ranker = Ranker("third")
    response = _call(_router(WebRouter, ranker, route_tools=others), tool)
    assert ranker.seen == []
    assert order(called) == list(MEMBERS)
    assert response.decision is None


def test_route_tools_names_the_tools_that_ask(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("third")
    _call(_router(WebRouter, ranker, route_tools={tool.name}), tool)
    assert len(ranker.seen) == 1


def test_a_pinned_provider_never_asks_the_router(tool: Tool, called: list[str]) -> None:
    ranker = Ranker("second")
    response = _call(_router(WebRouter, ranker), tool, provider="third")
    assert ranker.seen == []
    assert response.decision is None


def test_with_no_router_the_call_runs_as_before(tool: Tool, called: list[str]) -> None:
    response = _call(_router(WebRouter, None), tool)
    assert order(called) == list(MEMBERS)
    assert response.decision is None


@pytest.mark.parametrize("cls", [WebRouter, AsyncWebRouter])
@pytest.mark.parametrize(
    "route_tools",
    ["search", ["search", "open_browser"], ["live_data"], ["plan"]],
    ids=["str", "browser", "tpl", "x"],
)
def test_route_tools_takes_only_the_five_tools(cls: type, route_tools: Any) -> None:
    with pytest.raises(ConfigurationError, match="route_tools"):
        cls(route_tools=route_tools)


def test_both_routers_take_the_same_routing_arguments() -> None:
    names = ("router", "route_tools", "fail_on_router_error")
    for cls in (WebRouter, AsyncWebRouter):
        parameters = inspect.signature(cls).parameters
        assert all(parameters[name].kind is inspect.Parameter.KEYWORD_ONLY for name in names)
        assert (parameters["router"].default, parameters["fail_on_router_error"].default) == ("features", False)
