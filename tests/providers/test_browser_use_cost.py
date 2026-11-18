"""Browser Use's own statement of what a task cost: a decimal string in both task payloads."""

from __future__ import annotations

from typing import Any

import pytest

from web_router.providers.browser_use import BrowserUseBrowseProvider
from web_router.types import BrowseRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> BrowserUseBrowseProvider:
    monkeypatch.setenv("BROWSER_USE_API_KEY", "test-key")
    return BrowserUseBrowseProvider()


def _answer(provider: BrowserUseBrowseProvider, status_cost: Any, task_cost: Any) -> tuple[Any, Any]:
    status = {"id": "t1", "status": "finished", "output": "Show HN", "cost": status_cost}
    task = {"id": "t1", "steps": [], "cost": task_cost}
    response = provider._to_response(BrowseRequest(task="t"), status, task)
    return response.cost_usd, response.cost_source


def test_the_full_task_read_wins_over_the_status_call(provider: BrowserUseBrowseProvider) -> None:
    """The full read comes after the task finished, so its figure is the later one."""
    assert _answer(provider, "0.004", "0.006") == (0.006, "reported")


def test_the_status_cost_stands_in_when_the_task_states_none(provider: BrowserUseBrowseProvider) -> None:
    assert _answer(provider, "0.004", None) == (0.004, "reported")


@pytest.mark.parametrize("cost", [None, "free", True])
def test_a_cost_that_is_no_figure_states_nothing(provider: BrowserUseBrowseProvider, cost: Any) -> None:
    """Without a usable figure the router estimates one, as for any backend that states none."""
    assert _answer(provider, cost, cost) == (None, None)
