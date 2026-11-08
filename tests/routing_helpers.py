"""The router tests' harness: three fake members per routable method, a router built over them, and fixtures.

``first``, ``second`` and ``third`` are registered for a method as custom
backends; ``auto``'s chain is set to them in that order, so the members a call
reaches show the order it ran in. :mod:`tests.conftest` registers the
fixtures here for every test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import (
    FakeAnswerProvider,
    FakeBrowseProvider,
    FakeExtractProvider,
    FakeProvider,
    FakeResearchProvider,
)
from web_router import RouteDecision, RouteRequest, WebRouter, prices, registry, registry_answer
from web_router.exceptions import ConfigurationError
from web_router.providers.auto import AutoBrowseProvider, AutoExtractProvider, AutoProvider, AutoResearchProvider
from web_router.providers.auto_answer import AutoAnswerProvider
from web_router.routing.effort_table import effort_table
from web_router.types import BrowseRequest, ExtractRequest, ResearchRequest, SearchRequest
from web_router.types_answer import AnswerRequest

MEMBERS = ("first", "second", "third")


@dataclass(frozen=True)
class Tool:
    """One routable method, as the harness drives it: its fake, its ``auto``, its registry and a request."""

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

Table = dict[str, tuple[float | None, float | None]]
"""A member's name -> its (estimated cost, p90 seconds); ``None`` is unknown. A member not listed is unknown in both."""


def member(tool: Tool, name: str, log: list[str], *, fails: bool) -> type:
    """A backend for ``tool`` named ``name`` that logs each call, then fails before sending anything or answers."""

    def entry(self: Any, *args: Any) -> Any:
        log.append(name)
        if fails:
            raise ConfigurationError(f"{name} holds no key")
        return getattr(tool.fake, tool.entry)(self, *args)

    return type(f"Member_{name}", (tool.fake,), {"name": name, tool.entry: entry})


def wr(cls: type = WebRouter, router: Any = None, **kwargs: Any) -> Any:
    """A router of ``cls`` whose ``auto`` chain, for every method, is the three members."""
    return cls(provider_config={"auto": {"chain": list(MEMBERS)}}, router=router, **kwargs)


class Ranker:
    """A router that ranks ``ranking`` first for whichever tool it is asked about, and keeps what it was shown."""

    def __init__(self, *ranking: str, reason: str | None = "ranked") -> None:
        self.ranking = list(ranking)
        self.reason = reason
        self.seen: list[RouteRequest] = []

    def route(self, req: RouteRequest) -> RouteDecision:
        self.seen.append(req)
        assert req.pinned_tool is not None
        return RouteDecision(providers={req.pinned_tool: self.ranking}, reason=self.reason)


@pytest.fixture(params=TOOLS, ids=[tool.name for tool in TOOLS])
def tool(request: pytest.FixtureRequest) -> Tool:
    """Every routable method in turn; narrowed by name with ``parametrize("tool", ["research"], indirect=True)``."""
    return next(tool for tool in TOOLS if request.param in (tool, tool.name))


@pytest.fixture
def called(tool: Tool) -> Iterator[list[str]]:
    """The three members registered for ``tool``, ``first`` and ``second`` failing; yields who was called."""
    log: list[str] = []
    for name in MEMBERS:
        tool.register(name, member(tool, name, log, fails=name != "third"))
    yield log
    for name in MEMBERS:
        tool.unregister(name)


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> Table:
    """The price list a budget reads, by member name, whatever the method and mode; empty until a test fills it."""
    table: Table = {}

    def price(provider: str, method: str, mode: str | None = None) -> SimpleNamespace | None:
        cost = table.get(provider, (None, None))[0]
        return (
            None
            if cost is None
            else SimpleNamespace(estimate_usd=cost, estimate_is_floor=False, mode=mode, results_assumed=None)
        )

    def estimate_latency(provider: str, method: str, mode: str | None = None) -> tuple[float, float] | None:
        p90 = table.get(provider, (None, None))[1]
        return None if p90 is None else (p90, p90)

    monkeypatch.setattr(prices, "price", price)
    monkeypatch.setattr(prices, "estimate_latency", estimate_latency)
    return table


def order(called: list[str]) -> list[str]:
    """The members called, each once: extract calls a member once per URL."""
    return list(dict.fromkeys(called))


MEASURED_EFFORTS = {effort: {"accuracy": 0.9} for effort in ("low", "medium", "high")}
"""A fake research backend's measured efforts: equally accurate, so the budget maps the cheapest it takes."""


@pytest.fixture
def research_backends(monkeypatch: pytest.MonkeyPatch, priced: Table) -> Iterator[Callable[..., None]]:
    """Registers fake research backends by name, in the effort table at :data:`MEASURED_EFFORTS` and at $0.01.

    ``unpriced`` leaves one out of the price list; ``refuses`` names the efforts it will not run on records.
    """
    names: list[str] = []

    def add(name: str, *, unpriced: bool = False, refuses: tuple[str, ...] = ()) -> None:
        def refused_values(cls: type, request: ResearchRequest) -> dict[str, Any]:
            return {"effort": request.effort} if request.records and request.effort in refuses else {}

        attrs = {"name": name, "refused_values": classmethod(refused_values)}
        registry.register_research_provider(name, type(f"Measured_{name}", (FakeResearchProvider,), attrs))
        names.append(name)
        monkeypatch.setitem(effort_table()["research"], name, {"measured": MEASURED_EFFORTS})
        if not unpriced:
            priced[name] = (0.01, None)

    yield add
    for name in names:
        registry.unregister_research_provider(name)
