"""The spend log's failed attempts: a line per attempt that failed or answered empty, and how a report counts it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tests.conftest import FakeProvider
from web_router import (
    AsyncWebRouter,
    Budget,
    DeadlineExceededError,
    InvalidParameterError,
    ProviderError,
    SpendEntry,
    WebRouter,
    spend_report,
)
from web_router.exceptions import ConfigurationError
from web_router.exceptions_typed import AllProvidersFailedError
from web_router.registry import register_provider, unregister_provider
from web_router.spend_record import passed_entries, raised_entries
from web_router.types import SearchRequest, SearchResponse
from web_router.types_decision import DecisionAttempt, DecisionHop, DecisionRecord


class _Broken(FakeProvider):
    name = "broken"

    def _search(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError("broken", "HTTP 404: not found")

    async def _asearch(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError("broken", "HTTP 404: not found")


ONLY_BROKEN = {"auto": {"chain": ["broken"]}}


class _Late(FakeProvider):
    name = "late"

    def _search(self, request: SearchRequest) -> SearchResponse:
        raise DeadlineExceededError("late", 5.0)


class _Kept:
    def __init__(self) -> None:
        self.entries: list[SpendEntry] = []

    def record(self, entry: SpendEntry) -> None:
        self.entries.append(entry)


@pytest.fixture
def chain(fake_provider: Any) -> Any:
    register_provider("broken", _Broken)
    yield WebRouter(provider_config={"auto": {"chain": ["broken", "fake"]}})
    unregister_provider("broken")


def test_an_attempt_that_failed_before_the_answer_has_its_own_line(chain: WebRouter) -> None:
    """Read from the call's decision record, which a budget or a router keeps."""
    sink = _Kept()
    chain.log_spend(sink)
    chain.search("q", budget=Budget(time_limit_s=60))
    assert [(e.provider, e.outcome) for e in sink.entries] == [("broken", "failed"), ("fake", "answered")]
    failed, answered = sink.entries
    assert failed.error == "ProviderError"
    assert failed.decision_id == answered.decision_id is not None
    assert failed.cost_usd is None and failed.elapsed_s is not None


def _logged(router: Any) -> _Kept:
    sink = _Kept()
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


def _record(hops: list[DecisionHop], tried: list[str]) -> DecisionRecord:
    attempts = [DecisionAttempt(id=id, est_cost_usd=0.01, seconds=n + 0.5) for n, id in enumerate(tried)]
    return DecisionRecord(decision_id="d1", hops=hops, attempts=attempts)


def test_a_candidate_skipped_before_it_started_has_no_line() -> None:
    """A budget skip never ran, so nothing was billed for it."""
    record = _record([DecisionHop(id="search:tavily", error="over budget")], ["search:fake"])
    assert passed_entries(record, "search") == []


def test_an_empty_answer_the_call_moved_past_is_logged_as_empty() -> None:
    record = _record([DecisionHop(id="search:exa"), DecisionHop(id="search:exa", error="timeout")], ["search:exa"] * 2)
    """Each hop takes its own attempt, in order, even when one backend was tried twice."""
    assert [(e.provider, e.outcome, e.error, e.elapsed_s) for e in passed_entries(record, "search")] == [
        ("exa", "empty", None, 0.5),
        ("exa", "failed", "timeout", 1.5),
    ]


def test_a_report_counts_failed_attempts_apart_from_calls() -> None:
    at = datetime(2026, 11, 18, tzinfo=UTC)
    entries = [
        SpendEntry(at=at, method="search", provider="exa", cost_usd=0.01, cost_source="reported", elapsed_s=2.0),
        SpendEntry(at=at, method="search", provider="exa", outcome="failed", error="503", elapsed_s=1.0),
        SpendEntry(at=at, method="search", provider="exa", outcome="failed", cost_usd=0.02, cost_source="reported"),
    ]
    (row,) = spend_report(entries, ["provider"])
    assert (row.calls, row.failed_attempts, row.unpriced_calls, row.elapsed_s) == (1, 2, 0, 2.0)
    assert (row.cost_usd, row.reported_usd) == pytest.approx((0.03, 0.03)), "a failed attempt's known cost counts"
    by_outcome = spend_report(entries, ["outcome"])
    counts = [(r.key["outcome"], r.calls, r.failed_attempts) for r in by_outcome]
    assert counts == [("answered", 1, 0), ("failed", 0, 2)]


def test_an_extract_whose_every_call_failed_has_a_line_per_call() -> None:
    error = ProviderError("jina", "nothing returned")
    error.call_errors = (TimeoutError("read"), ProviderError("jina", "HTTP 404"))
    entries = raised_entries(error, "extract", 3.0)
    assert [(e.provider, e.error, e.elapsed_s) for e in entries] == [
        ("jina", "TimeoutError", None),
        ("jina", "upstream", 3.0),
    ]


def test_a_member_the_chain_never_called_has_no_line() -> None:
    """Unconfigured, not installed, or cooling down: no request went out, so nothing was billed."""
    cooling = ProviderError("exa", "cooling down", kind="rate_limit")
    throttled = ProviderError("you", "HTTP 429", original=RuntimeError("429"), kind="rate_limit")
    failures = {
        "tavily": ConfigurationError("no key"),
        "exa": cooling,
        "you": throttled,
        "jina": ProviderError("jina", "HTTP 404"),
    }
    entries = raised_entries(AllProvidersFailedError("auto", "all failed", failures), "search", 2.0)
    assert [(e.provider, e.error, e.elapsed_s) for e in entries] == [
        ("you", "rate_limit", None),
        ("jina", "upstream", 2.0),
    ]


def test_a_line_written_before_outcomes_reads_as_answered() -> None:
    entry = SpendEntry.model_validate_json('{"at": "2026-11-18T00:00:00Z", "method": "search", "provider": "exa"}')
    assert entry.outcome == "answered" and entry.error is None
