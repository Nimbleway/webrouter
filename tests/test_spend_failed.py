"""The spend log's failed attempts: a line per attempt that failed or answered empty, and how a report counts it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tests.conftest import FakeProvider
from web_router import Budget, ProviderError, SpendEntry, WebRouter, spend_report
from web_router.registry import register_provider, unregister_provider
from web_router.spend_record import passed_entries
from web_router.types import SearchRequest, SearchResponse
from web_router.types_decision import DecisionAttempt, DecisionHop, DecisionRecord


class _Broken(FakeProvider):
    name = "broken"

    def _search(self, request: SearchRequest) -> SearchResponse:
        raise ProviderError("broken", "HTTP 404: not found")


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


def _record(hops: list[DecisionHop], tried: list[str]) -> DecisionRecord:
    attempts = [DecisionAttempt(id=id, est_cost_usd=0.01, seconds=0.5) for id in tried]
    return DecisionRecord(decision_id="d1", hops=hops, attempts=attempts)


def test_a_candidate_skipped_before_it_started_has_no_line() -> None:
    """A budget skip never ran, so nothing was billed for it."""
    record = _record([DecisionHop(id="search:tavily", error="over budget")], ["search:fake"])
    assert passed_entries(record, "search") == []


def test_an_empty_answer_the_call_moved_past_is_logged_as_empty() -> None:
    record = _record([DecisionHop(id="search:exa"), DecisionHop(id="search:exa", error="timeout")], ["search:exa"] * 2)
    assert [(e.provider, e.outcome, e.error) for e in passed_entries(record, "search")] == [
        ("exa", "empty", None),
        ("exa", "failed", "timeout"),
    ]


def test_a_report_counts_failed_attempts_apart_from_calls() -> None:
    at = datetime(2026, 11, 18, tzinfo=UTC)
    entries = [
        SpendEntry(at=at, method="search", provider="exa", cost_usd=0.01, cost_source="reported", elapsed_s=2.0),
        SpendEntry(at=at, method="search", provider="exa", outcome="failed", error="503", elapsed_s=1.0),
    ]
    (row,) = spend_report(entries, ["provider"])
    assert (row.calls, row.failed_attempts, row.cost_usd, row.unpriced_calls, row.elapsed_s) == (1, 1, 0.01, 0, 2.0)
    by_outcome = spend_report(entries, ["outcome"])
    counts = [(r.key["outcome"], r.calls, r.failed_attempts) for r in by_outcome]
    assert counts == [("answered", 1, 0), ("failed", 0, 1)]


def test_a_line_written_before_outcomes_reads_as_answered() -> None:
    entry = SpendEntry.model_validate_json('{"at": "2026-11-18T00:00:00Z", "method": "search", "provider": "exa"}')
    assert entry.outcome == "answered" and entry.error is None
