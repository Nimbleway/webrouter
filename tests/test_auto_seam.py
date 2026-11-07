"""The seam in ``auto``'s fallback loops: the per-call order and the hook before each attempt.

With no route in force the order is the configured chain and every member is
tried, as ``auto`` always has. A route reorders the members, leaves out the
ones it does not name, and passes over a member its hook refuses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.routing_helpers import MEMBERS
from tests.routing_helpers import Tool as _Tool
from web_router.exceptions import WebRouterError
from web_router.routing.attempts import Attempt, CallRoute, call_route


def _hooked(seen: list[Attempt], refuse: str | None = None) -> Callable[[Attempt], bool]:
    def hook(attempt: Attempt) -> bool:
        seen.append(attempt)
        return attempt.provider != refuse

    return hook


def _call(tool: _Tool, auto: Any) -> Any:
    return getattr(auto, tool.sync)(tool.request)


async def _acall(tool: _Tool, auto: Any) -> Any:
    return await getattr(auto, tool.async_)(tool.request)


def _auto(tool: _Tool) -> Any:
    return tool.auto(chain=[(name, {}) for name in MEMBERS])


def test_with_no_route_auto_tries_the_configured_chain(tool: _Tool, called: list[str]) -> None:
    assert _call(tool, _auto(tool)).provider == "third"
    assert called == list(MEMBERS)


def test_the_hook_is_called_once_per_attempt_in_the_configured_order(tool: _Tool, called: list[str]) -> None:
    seen: list[Attempt] = []
    with call_route(CallRoute(before_attempt=_hooked(seen))):
        assert _call(tool, _auto(tool)).provider == "third"
    assert [attempt.provider for attempt in seen] == called == list(MEMBERS)
    assert {attempt.tool for attempt in seen} == {tool.name}


async def test_the_async_hook_is_called_once_per_attempt_in_the_configured_order(
    tool: _Tool, called: list[str]
) -> None:
    seen: list[Attempt] = []
    with call_route(CallRoute(before_attempt=_hooked(seen))):
        assert (await _acall(tool, _auto(tool))).provider == "third"
    assert [attempt.provider for attempt in seen] == called == list(MEMBERS)


def test_a_call_order_reorders_and_leaves_out_what_it_does_not_name(tool: _Tool, called: list[str]) -> None:
    seen: list[Attempt] = []
    route = CallRoute(order={tool.name: ["second", "third"]}, before_attempt=_hooked(seen))
    with call_route(route):
        assert _call(tool, _auto(tool)).provider == "third"
    assert called == ["second", "third"]
    assert [attempt.provider for attempt in seen] == ["second", "third"]


async def test_an_async_call_order_puts_the_answering_member_first(tool: _Tool, called: list[str]) -> None:
    with call_route(CallRoute(order={tool.name: ["third", "first"]})):
        assert (await _acall(tool, _auto(tool))).provider == "third"
    assert called == ["third"]


def test_a_member_the_hook_refuses_is_never_called(tool: _Tool, called: list[str]) -> None:
    seen: list[Attempt] = []
    with call_route(CallRoute(before_attempt=_hooked(seen, refuse="second"))):
        _call(tool, _auto(tool))
    assert called == ["first", "third"]
    assert [attempt.provider for attempt in seen] == list(MEMBERS)


def test_a_member_the_call_order_leaves_out_is_never_tried(tool: _Tool, called: list[str]) -> None:
    with call_route(CallRoute(order={tool.name: ["second", "first"]})), pytest.raises(WebRouterError):
        _call(tool, _auto(tool))
    assert called == ["second", "first"]
