"""Spend log lines from a call's record or its error, and how a report counts them."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.spend_helpers import Kept
from web_router import ProviderError, SpendEntry, spend_report
from web_router.exceptions import ConfigurationError, ProviderNotInstalledError
from web_router.exceptions_typed import AllProvidersFailedError
from web_router.providers._cooldown import Cooldowns, CoolingDownError
from web_router.routing.record import error_code
from web_router.spend_record import passed_entries, raised_entries, record_lines
from web_router.types_decision import DecisionAttempt, DecisionHop, DecisionRecord


def _cooling(provider: str) -> CoolingDownError:
    """The refusal a real cooldown gives, after a 429 the backend answered."""
    cooldowns = Cooldowns()
    cooldowns.note(provider, ProviderError(provider, "HTTP 429", kind="rate_limit"))
    refusal = cooldowns.refusal(provider)
    assert refusal is not None
    return refusal


NEVER_SENT = [
    ("tavily", ConfigurationError("no key")),
    ("nimble", ProviderNotInstalledError("nimble", "pip install")),
    ("exa", _cooling("exa")),
]


def _record(hops: list[DecisionHop], tried: list[str]) -> DecisionRecord:
    attempts = [DecisionAttempt(id=id, est_cost_usd=0.01, seconds=n + 0.5) for n, id in enumerate(tried)]
    return DecisionRecord(decision_id="d1", hops=hops, attempts=attempts)


def test_a_candidate_skipped_before_it_started_has_no_line() -> None:
    """A budget skip never ran, so nothing was billed for it."""
    record = _record([DecisionHop(id="search:tavily", error="over budget")], ["search:fake"])
    assert passed_entries(record, "search") == []


def test_an_empty_answer_the_call_moved_past_is_logged_as_empty() -> None:
    """Each hop takes its own attempt, in order, even when one backend was tried twice."""
    record = _record([DecisionHop(id="search:exa"), DecisionHop(id="search:exa", error="timeout")], ["search:exa"] * 2)
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
    """Unconfigured or cooling down: no request went out. A throttle a backend answered, MCP's too, was a request."""
    failures = {
        "tavily": ConfigurationError("no key"),
        "exa": CoolingDownError("exa", "cooling down", kind="rate_limit"),
        "exa_mcp": ProviderError("exa_mcp", "MCP error: rate limit exceeded", kind="rate_limit"),
        "you": ProviderError("you", "HTTP 429", original=RuntimeError("429"), kind="rate_limit"),
    }
    entries = raised_entries(AllProvidersFailedError("auto", "all failed", failures), "search", 2.0)
    assert [(e.provider, e.elapsed_s) for e in entries] == [("exa_mcp", None), ("you", 2.0)]


def test_a_hop_never_sent_has_no_line() -> None:
    """A decision record names a member never built by its error class, and a cooling one by its hop code."""
    never = [DecisionHop(id=f"search:{name}", error=error_code(exc)) for name, exc in NEVER_SENT]
    throttled = ProviderError("you", "HTTP 429", original=RuntimeError("429"), kind="rate_limit")
    throttled.status_code = 429
    hops = [*never, DecisionHop(id="search:you", error=error_code(throttled))]
    record = _record(hops, [hop.id for hop in hops])
    assert [e.provider for e in passed_entries(record, "search")] == ["you"]


def test_a_sink_that_refuses_one_line_still_gets_the_rest(caplog: pytest.LogCaptureFixture) -> None:
    class _FirstFails(Kept):
        def record(self, entry: SpendEntry) -> None:
            if not self.entries and not caplog.records:
                raise OSError("disk full")
            super().record(entry)

    sink = _FirstFails()
    error = ProviderError("auto", "all failed")
    error.call_errors = (TimeoutError("a"), TimeoutError("b"))
    record_lines(sink, raised_entries(error, "extract", 1.0))
    assert [e.elapsed_s for e in sink.entries] == [1.0] and len(caplog.records) == 1


def test_a_line_written_before_outcomes_reads_as_answered() -> None:
    entry = SpendEntry.model_validate_json('{"at": "2026-11-18T00:00:00Z", "method": "search", "provider": "exa"}')
    assert entry.outcome == "answered" and entry.error is None
