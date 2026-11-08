"""What the budget tests share: a price list of their own, and an ``auto`` chain of three fake members per tool.

The members are named ``first``, ``second`` and ``third`` and answer unless
told to raise; :func:`priced` sets what each is estimated to cost and take,
so a test reads its prices beside its assertions rather than in prices.json.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from tests.routing_helpers import MEMBERS, TOOLS
from tests.routing_helpers import Tool as _Tool
from web_router import AsyncWebRouter, Budget, WebRouter, prices

ARGS = {"search": "q", "extract": "https://a.test", "answer": "q", "research": "q", "browse": "t"}
"""What each tool is called with: its query, URL or task."""

Table = dict[str, tuple[float | None, float | None]]
"""A member's name -> its (estimated cost, p90 seconds); ``None`` is unknown. A member not listed is unknown in both."""


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> Table:
    """The price list the budget reads, by member name, whatever the method and mode."""
    table: Table = {}

    def price(provider: str, method: str, mode: str | None = None) -> SimpleNamespace | None:
        cost = table.get(provider, (None, None))[0]
        row = SimpleNamespace(estimate_usd=cost, estimate_is_floor=False, mode=mode, results_assumed=None)
        return None if cost is None else row

    def estimate_latency(provider: str, method: str, mode: str | None = None) -> tuple[float, float] | None:
        p90 = table.get(provider, (None, None))[1]
        return None if p90 is None else (p90, p90)

    monkeypatch.setattr(prices, "price", price)
    monkeypatch.setattr(prices, "estimate_latency", estimate_latency)
    return table


@dataclass
class Chain:
    """Three members registered for one tool: who was called, and what each raises instead of answering."""

    tool: _Tool
    called: list[str] = field(default_factory=list)
    raises: dict[str, BaseException] = field(default_factory=dict)
    stalls: set[str] = field(default_factory=set)
    """Job members that never finish, so the caller's wait runs out."""
    sleeps: dict[str, float] = field(default_factory=dict)
    """Seconds a member takes before it answers or raises."""

    def auto(self) -> Any:
        return self.tool.auto(chain=[(name, {}) for name in MEMBERS])

    def member(self, name: str) -> type:
        chain, tool = self, self.tool

        def entry(self: Any, *args: Any) -> Any:
            chain.called.append(name)
            time.sleep(chain.sleeps.get(name, 0.0))
            if name in chain.raises:
                raise chain.raises[name]
            return getattr(tool.fake, tool.entry)(self, *args)

        def polls_before_done(self: Any) -> int:
            return 10**9 if name in chain.stalls else 0

        attrs = {"name": name, tool.entry: entry, "polls_before_done": property(polls_before_done)}
        return type(f"Budgeted_{name}", (tool.fake,), attrs)


@pytest.fixture(params=TOOLS, ids=[tool.name for tool in TOOLS])
def tool(request: pytest.FixtureRequest) -> _Tool:
    """Every tool in turn; a test narrows it by name with ``parametrize("tool", ["research"], indirect=True)``."""
    return next(tool for tool in TOOLS if request.param in (tool, tool.name))


@pytest.fixture
def chain(tool: _Tool) -> Iterator[Chain]:
    """``first``, ``second`` and ``third`` registered for ``tool``; unregistered after the test."""
    made = Chain(tool)
    for name in MEMBERS:
        tool.register(name, made.member(name))
    yield made
    for name in MEMBERS:
        tool.unregister(name)


def call(chain: Chain, budget: Budget | None, provider: Any = None, **options: Any) -> Any:
    """``tool`` through the sync router, on the chain's ``auto`` unless ``provider`` names another."""
    method = getattr(WebRouter(), chain.tool.name)
    return method(ARGS[chain.tool.name], provider=provider or chain.auto(), budget=budget, **options)


async def acall(chain: Chain, budget: Budget | None, provider: Any = None, **options: Any) -> Any:
    """As :func:`call`, through the async router."""
    method = getattr(AsyncWebRouter(), chain.tool.name)
    return await method(ARGS[chain.tool.name], provider=provider or chain.auto(), budget=budget, **options)
