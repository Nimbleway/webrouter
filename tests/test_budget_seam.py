"""``budget=`` on search, extract, answer, research and browse: accepted, handed to the budget scope, nothing else."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from web_router import AsyncWebRouter, Budget, WebRouter
from web_router.routing import budget as budget_module

BUDGET = Budget(max_cost_usd=0.01, time_limit_s=5.0)

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("search", ("q",)),
    ("extract", ("https://a.test",)),
    ("answer", ("q",)),
    ("research", ("q",)),
    ("browse", ("t",)),
]


@pytest.fixture
def scoped(monkeypatch: pytest.MonkeyPatch) -> list[Budget | None]:
    """Every budget the routers hand the scope, in call order."""
    seen: list[Budget | None] = []
    real = budget_module.budget_scope

    @contextmanager
    def spy(budget: Budget | None) -> Iterator[None]:
        seen.append(budget)
        with real(budget):
            yield

    monkeypatch.setattr("web_router.client.budget_scope", spy)
    monkeypatch.setattr("web_router.client_answer.budget_scope", spy)
    return seen


@pytest.fixture
def fakes(
    fake_provider: Any,
    fake_extract_provider: Any,
    fake_answer_provider: Any,
    fake_research_provider: Any,
    fake_browse_provider: Any,
) -> None:
    """Every method's fake registered under ``fake``."""


def _plain(response: Any) -> dict[str, Any]:
    return response.model_dump(exclude={"latency"})


@pytest.mark.usefixtures("fakes")
@pytest.mark.parametrize(("method", "args"), _CALLS, ids=[name for name, _ in _CALLS])
def test_a_sync_call_with_a_budget_answers_as_one_without(
    method: str, args: tuple[Any, ...], scoped: list[Budget | None]
) -> None:
    router = WebRouter("fake")
    without = getattr(router, method)(*args)
    with_budget = getattr(router, method)(*args, budget=BUDGET)
    assert _plain(with_budget) == _plain(without)
    assert with_budget.decision is None
    assert scoped == [None, BUDGET]


@pytest.mark.usefixtures("fakes")
@pytest.mark.parametrize(("method", "args"), _CALLS, ids=[name for name, _ in _CALLS])
async def test_an_async_call_with_a_budget_answers_as_one_without(
    method: str, args: tuple[Any, ...], scoped: list[Budget | None]
) -> None:
    router = AsyncWebRouter("fake")
    without = await getattr(router, method)(*args)
    with_budget = await getattr(router, method)(*args, budget=BUDGET)
    assert _plain(with_budget) == _plain(without)
    assert scoped == [None, BUDGET]


@pytest.mark.parametrize("router", [WebRouter, AsyncWebRouter])
def test_only_the_routable_methods_take_a_budget(router: type) -> None:
    takes = {name for name in dir(router) if "budget" in _parameters(router, name)}
    assert takes == {"search", "extract", "answer", "research", "browse"}


def _parameters(router: type, name: str) -> set[str]:
    member = getattr(router, name)
    if name.startswith("_") or not callable(member) or isinstance(member, type):
        return set()
    try:
        return set(inspect.signature(member).parameters)
    except (TypeError, ValueError):
        return set()
