"""The seam in ``auto``'s fallback loops: the per-call order and the hook before each attempt.

With no route in force the order is the configured chain and every member is
tried, as ``auto`` always has. A route reorders the members, leaves out the
ones it does not name, and passes over a member its hook refuses.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from tests.conftest import (
    FakeAnswerProvider,
    FakeBrowseProvider,
    FakeExtractProvider,
    FakeProvider,
    FakeResearchProvider,
)
from web_router import registry, registry_answer
from web_router.exceptions import ConfigurationError, WebRouterError
from web_router.providers.auto import AutoBrowseProvider, AutoExtractProvider, AutoProvider, AutoResearchProvider
from web_router.providers.auto_answer import AutoAnswerProvider
from web_router.routing.attempts import Attempt, CallRoute, call_route
from web_router.types import BrowseRequest, ExtractRequest, ResearchRequest, SearchRequest
from web_router.types_answer import AnswerRequest

MEMBERS = ("first", "second", "third")


@dataclass(frozen=True)
class _Tool:
    name: str
    fake: type
    entry: str
    auto: type
    register: Callable[[str, type], None]
    unregister: Callable[[str], None]
    request: Any
    sync: str
    async_: str


TOOLS = [
    _Tool("search", FakeProvider, "_respond", AutoProvider, registry.register_provider,
          registry.unregister_provider, SearchRequest(query="q"), "search", "asearch"),
    _Tool("extract", FakeExtractProvider, "_respond", AutoExtractProvider, registry.register_extract_provider,
          registry.unregister_extract_provider, ExtractRequest(urls=["https://a.test"]), "extract", "aextract"),
    _Tool("answer", FakeAnswerProvider, "_answer", AutoAnswerProvider, registry_answer.register_answer_provider,
          registry_answer.unregister_answer_provider, AnswerRequest(query="q"), "answer", "aanswer"),
    _Tool("research", FakeResearchProvider, "_start", AutoResearchProvider, registry.register_research_provider,
          registry.unregister_research_provider, ResearchRequest(query="q"), "research", "aresearch"),
    _Tool("browse", FakeBrowseProvider, "_start", AutoBrowseProvider, registry.register_browse_provider,
          registry.unregister_browse_provider, BrowseRequest(task="t"), "browse", "abrowse"),
]  # fmt: skip


@pytest.fixture(params=TOOLS, ids=[tool.name for tool in TOOLS])
def tool(request: pytest.FixtureRequest) -> Iterator[_Tool]:
    yield request.param


@pytest.fixture
def called(tool: _Tool) -> Iterator[list[str]]:
    """Register three members for ``tool`` that fail before sending anything, but ``third``; yield who was called."""
    log: list[str] = []
    for name in MEMBERS:
        tool.register(name, _member(tool, name, log, fails=name != "third"))
    yield log
    for name in MEMBERS:
        tool.unregister(name)


def _member(tool: _Tool, member: str, log: list[str], *, fails: bool) -> type:
    def entry(self: Any, *args: Any) -> Any:
        log.append(member)
        if fails:
            raise ConfigurationError(f"{member} holds no key")
        return getattr(tool.fake, tool.entry)(self, *args)

    return type(f"Member_{member}", (tool.fake,), {"name": member, tool.entry: entry})


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
