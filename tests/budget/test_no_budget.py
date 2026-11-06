"""``budget=`` on the five routable methods: which take it, and that a call with no budget changes nothing."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants import BROWSE_TIMEOUT, RESEARCH_TIMEOUT
from web_router.providers._deadline import current_deadline
from web_router.routing.attempts import current_route

_OWN_WAIT = {"research": RESEARCH_TIMEOUT, "browse": BROWSE_TIMEOUT}
"""The deadline a job's own ``timeout=`` sets, which is there with or without a budget."""

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("search", ("q",)),
    ("extract", ("https://a.test",)),
    ("answer", ("q",)),
    ("research", ("q",)),
    ("browse", ("t",)),
]


@pytest.fixture
def seen(
    monkeypatch: pytest.MonkeyPatch,
    fake_provider: Any,
    fake_extract_provider: Any,
    fake_answer_provider: Any,
    fake_research_provider: Any,
    fake_browse_provider: Any,
) -> list[tuple[object, float | None]]:
    """Every method's fake registered under ``fake``; each records the route and the deadline in force when called."""
    log: list[tuple[object, float | None]] = []
    for fake, entry in (
        (fake_provider, "_respond"),
        (fake_extract_provider, "_respond"),
        (fake_answer_provider, "_answer"),
        (fake_research_provider, "_start"),
        (fake_browse_provider, "_start"),
    ):
        real = getattr(fake, entry)

        def spy(self: Any, *args: Any, _real: Any = real) -> Any:
            deadline = current_deadline()
            log.append((current_route(), None if deadline is None else deadline.seconds))
            return _real(self, *args)

        monkeypatch.setattr(fake, entry, spy)
    return log


@pytest.mark.parametrize(("method", "args"), _CALLS, ids=[name for name, _ in _CALLS])
def test_with_no_budget_a_call_runs_with_no_route_no_budget_deadline_and_no_record(
    method: str, args: tuple[Any, ...], seen: list[tuple[object, float | None]]
) -> None:
    response = getattr(WebRouter("fake"), method)(*args)
    assert seen == [(None, pytest.approx(_OWN_WAIT[method], abs=1) if method in _OWN_WAIT else None)]
    assert response.decision is None


@pytest.mark.parametrize(("method", "args"), _CALLS, ids=[name for name, _ in _CALLS])
async def test_with_no_budget_an_async_call_runs_with_no_route_no_budget_deadline_and_no_record(
    method: str, args: tuple[Any, ...], seen: list[tuple[object, float | None]]
) -> None:
    response = await getattr(AsyncWebRouter("fake"), method)(*args)
    assert seen == [(None, pytest.approx(_OWN_WAIT[method], abs=1) if method in _OWN_WAIT else None)]
    assert response.decision is None


@pytest.mark.parametrize("router", [WebRouter, AsyncWebRouter])
def test_only_the_routable_methods_take_a_budget(router: type) -> None:
    takes = {name for name in dir(router) if "budget" in _parameters(router, name)}
    assert takes == {"search", "extract", "answer", "research", "browse", "plan", "run"}


def _parameters(router: type, name: str) -> set[str]:
    member = getattr(router, name)
    if name.startswith("_") or not callable(member) or isinstance(member, type):
        return set()
    try:
        return set(inspect.signature(member).parameters)
    except (TypeError, ValueError):
        return set()
