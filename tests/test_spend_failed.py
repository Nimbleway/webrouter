"""The spend log's failed attempts, as a router writes them: a line per attempt that failed, none for one never sent."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import FakeProvider
from tests.spend_helpers import Kept
from web_router import (
    AsyncWebRouter,
    Budget,
    DeadlineExceededError,
    InvalidParameterError,
    ProviderError,
    WebRouter,
)
from web_router.exceptions import ConfigurationError
from web_router.registry import register_provider, unregister_provider
from web_router.types import SearchRequest, SearchResponse


class _Broken(FakeProvider):
    name = "broken"

    def _search(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError("broken", "HTTP 404: not found")

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError("broken", "HTTP 404: not found")


ONLY_BROKEN = {"auto": {"chain": ["broken"]}}


class _NoKey(FakeProvider):
    name = "nokey"

    def __init__(self, **kwargs: Any) -> None:
        raise ConfigurationError("no key")


class _Late(FakeProvider):
    name = "late"

    def _search(self, request: SearchRequest) -> SearchResponse:
        raise DeadlineExceededError("late", 5.0)


@pytest.fixture
def chain(fake_provider: Any) -> Any:
    register_provider("broken", _Broken)
    yield WebRouter(provider_config={"auto": {"chain": ["broken", "fake"]}})
    unregister_provider("broken")


def test_an_attempt_that_failed_before_the_answer_has_its_own_line(chain: WebRouter) -> None:
    """Read from the call's decision record, which a budget or a router keeps."""
    sink = Kept()
    chain.log_spend(sink)
    chain.search("q", budget=Budget(time_limit_s=60))
    assert [(e.provider, e.outcome) for e in sink.entries] == [("broken", "failed"), ("fake", "answered")]
    failed, answered = sink.entries
    assert failed.error == "ProviderError"
    assert failed.decision_id == answered.decision_id is not None
    assert failed.cost_usd is None and failed.elapsed_s is not None


def _logged(router: Any) -> Kept:
    sink = Kept()
    router.log_spend(sink)
    return sink


@pytest.mark.usefixtures("chain")
def test_a_call_that_raised_has_a_failed_line() -> None:
    router = WebRouter(provider_config=ONLY_BROKEN)
    sink = _logged(router)
    with pytest.raises(ProviderError):
        router.search("q")
    with pytest.raises(ProviderError):
        router.run("q")  # planned on the way: the line names the method the plan chose
    assert [(e.method, e.provider, e.outcome) for e in sink.entries] == [("search", "broken", "failed")] * 2
    assert all(e.error and e.elapsed_s is not None and e.cost_usd is None for e in sink.entries)


@pytest.mark.usefixtures("chain")
async def test_an_async_call_that_raised_has_a_failed_line() -> None:
    router = AsyncWebRouter(provider_config=ONLY_BROKEN)
    sink = _logged(router)
    with pytest.raises(ProviderError):
        await router.search("q")
    with pytest.raises(ProviderError):
        await router.run("q")
    assert [(e.method, e.outcome) for e in sink.entries] == [("search", "failed")] * 2


def test_a_call_past_its_deadline_has_a_failed_line() -> None:
    """The backend may have worked, and billed, until the deadline cut it off."""
    register_provider("late", _Late)
    try:
        router = WebRouter(provider_config={"auto": {"chain": ["late"]}})
        sink = _logged(router)
        with pytest.raises(DeadlineExceededError):
            router.search("q")
    finally:
        unregister_provider("late")
    assert [(e.provider, e.outcome, e.error) for e in sink.entries] == [("auto", "failed", "timeout")]


@pytest.mark.usefixtures("chain")
def test_a_refused_argument_logs_nothing() -> None:
    """Refused before any backend started: nothing was billed."""
    router = WebRouter(provider_config=ONLY_BROKEN)
    sink = _logged(router)
    with pytest.raises(InvalidParameterError):
        router.search("q", max_results=-1)
    assert sink.entries == []


@pytest.mark.usefixtures("fake_provider")
def test_a_member_never_built_has_no_line_when_the_call_answers() -> None:
    """Unconfigured: no request went out ahead of the answer, so nothing was billed."""
    register_provider("nokey", _NoKey)
    try:
        router = WebRouter(provider_config={"auto": {"chain": ["nokey", "fake"]}})
        sink = _logged(router)
        router.search("q", budget=Budget(time_limit_s=60))
    finally:
        unregister_provider("nokey")
    assert [(e.provider, e.outcome) for e in sink.entries] == [("fake", "answered")]
