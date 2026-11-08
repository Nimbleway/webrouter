"""The router tests' shared members: three fake backends per routable method, and the fixtures that register them.

``first``, ``second`` and ``third`` are registered for a method as custom
backends, ``first`` and ``second`` failing before they send anything and
``third`` answering, so the members a call reaches show the order it ran in.
A test module imports ``tool_fixture`` and ``called_fixture`` to use the
``tool`` and ``called`` fixtures.
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
from web_router.exceptions import ConfigurationError
from web_router.providers.auto import AutoBrowseProvider, AutoExtractProvider, AutoProvider, AutoResearchProvider
from web_router.providers.auto_answer import AutoAnswerProvider
from web_router.types import BrowseRequest, ExtractRequest, ResearchRequest, SearchRequest
from web_router.types_answer import AnswerRequest

MEMBERS = ("first", "second", "third")


@dataclass(frozen=True)
class Tool:
    """One routable method: its fake, its ``auto``, its registry and a request."""

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
    Tool("search", FakeProvider, "_respond", AutoProvider, registry.register_provider,
         registry.unregister_provider, SearchRequest(query="q"), "search", "asearch"),
    Tool("extract", FakeExtractProvider, "_respond", AutoExtractProvider, registry.register_extract_provider,
         registry.unregister_extract_provider, ExtractRequest(urls=["https://a.test"]), "extract", "aextract"),
    Tool("answer", FakeAnswerProvider, "_answer", AutoAnswerProvider, registry_answer.register_answer_provider,
         registry_answer.unregister_answer_provider, AnswerRequest(query="q"), "answer", "aanswer"),
    Tool("research", FakeResearchProvider, "_start", AutoResearchProvider, registry.register_research_provider,
         registry.unregister_research_provider, ResearchRequest(query="q"), "research", "aresearch"),
    Tool("browse", FakeBrowseProvider, "_start", AutoBrowseProvider, registry.register_browse_provider,
         registry.unregister_browse_provider, BrowseRequest(task="t"), "browse", "abrowse"),
]  # fmt: skip


@pytest.fixture(name="tool", params=TOOLS, ids=[tool.name for tool in TOOLS])
def tool_fixture(request: pytest.FixtureRequest) -> Iterator[Tool]:
    yield request.param


@pytest.fixture(name="called")
def called_fixture(tool: Tool) -> Iterator[list[str]]:
    """Register three members for ``tool`` that fail before sending anything, but ``third``; yield who was called."""
    log: list[str] = []
    for name in MEMBERS:
        tool.register(name, member(tool, name, log, fails=name != "third"))
    yield log
    for name in MEMBERS:
        tool.unregister(name)


def member(tool: Tool, name: str, log: list[str], *, fails: bool) -> type:
    """A backend for ``tool`` named ``name`` that logs each call, then fails before sending anything or answers."""

    def entry(self: Any, *args: Any) -> Any:
        log.append(name)
        if fails:
            raise ConfigurationError(f"{name} holds no key")
        return getattr(tool.fake, tool.entry)(self, *args)

    return type(f"Member_{name}", (tool.fake,), {"name": name, tool.entry: entry})


def order(called: list[str]) -> list[str]:
    """The members called, each once: extract calls a member once per URL."""
    return list(dict.fromkeys(called))
