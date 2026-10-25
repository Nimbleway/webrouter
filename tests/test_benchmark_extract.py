"""`benchmark_extract()`: the same URLs across extract backends, one row each, and a report that always comes back.

The extract twin of test_benchmark.py. What the two share -- the threads, the
deadline, the rows that outlive it -- is covered there once; these guard what
is extract's own: the format rule, the partly successful call that stays an
answered row, and the default set drawn from the extract registry.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from web_router import AsyncWebRouter, BenchmarkTimeoutError, WebRouter, configured_extract_providers
from web_router.constants import BENCHMARK_EXCLUDED, EXTRACT_ENV_BY_PROVIDER
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedFormatError
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import (
    ExtractBenchmarkRun,
    ExtractedDocument,
    ExtractFailure,
    ExtractFormat,
    ExtractRequest,
    ExtractResponse,
)


class _Registered:
    """The fakes a test registered, so the fixture can remove exactly those."""

    def __init__(self) -> None:
        self.names: list[str] = []


@pytest.fixture
def registered() -> Any:
    ledger = _Registered()
    yield ledger
    for name in ledger.names:
        unregister_extract_provider(name)


def _member(
    ledger: _Registered,
    member_name: str,
    *,
    raises: Exception | None = None,
    delay: float = 0.0,
    formats: frozenset[ExtractFormat] = frozenset({"markdown", "html", "text"}),
    default: ExtractFormat = "markdown",
    init_raises: Exception | None = None,
    drops: str | None = None,
) -> type:
    """Register an extract backend that answers after ``delay`` seconds, fails in a given way, or drops one URL."""
    from tests.conftest import FakeExtractProvider

    class Member(FakeExtractProvider):  # type: ignore[misc, valid-type]
        name = member_name
        supported_formats = formats
        default_format = default
        # A backend that reports its own per-URL failures takes every URL in one call, as Tavily and Exa do.
        batches_urls = drops is not None

        def __init__(self, **kwargs: Any) -> None:
            if init_raises is not None:
                raise init_raises
            super().__init__(**kwargs)

        def _answer(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            if raises is not None:
                raise raises
            response = self._respond(request, urls)
            if drops is not None:
                response.failures = [ExtractFailure(url=drops, error="HTTP 404")]
                response.documents = [document for document in response.documents if document.url != drops]
            return response

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            if delay:
                time.sleep(delay)
            return self._answer(request, urls)

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            if delay:
                await asyncio.sleep(delay)
            return self._answer(request, urls)

    register_extract_provider(member_name, Member)
    ledger.names.append(member_name)
    return Member


URLS = ["https://example.com", "https://www.iana.org/domains/reserved"]


def test_one_row_per_provider_in_the_order_asked(registered: _Registered) -> None:
    _member(registered, "first")
    _member(registered, "second")

    with WebRouter("first") as router:
        report = router.benchmark_extract(URLS, providers=["second", "first", "second"])

    assert report.urls == URLS
    assert [run.provider for run in report.runs] == ["second", "first"], "named twice is still one row"
    assert len(report) == 2
    for run in report.runs:
        assert run.ok and run.error is None
        assert run.response is not None and run.response.provider == run.provider
        assert run.count == len(run.response) == 2, "count is the documents that came back"
        assert run.response.urls == URLS
        assert run.latency >= 0.0
    assert report["first"].provider == "first"
    with pytest.raises(KeyError):
        report["nobody"]
    assert [run.provider for run in report.succeeded] == ["second", "first"]
    assert report.failed == []


def test_every_provider_is_sent_the_identical_request(registered: _Registered) -> None:
    _member(registered, "first")
    _member(registered, "second")

    with WebRouter("first") as router:
        report = router.benchmark_extract(URLS, providers=["first", "second"], format="html")
        calls = [list(router.get_extract_provider(name).calls) for name in ("first", "second")]

    assert calls[0] == calls[1] and [url for call in calls[0] for url in call] == URLS
    for run in report.runs:
        assert run.response is not None
        assert {document.format for document in run.response.documents} == {"html"}, (
            "the format goes to every row unchanged, and each row says so"
        )


def test_a_backend_that_fails_is_its_own_rows_error_not_the_comparisons(registered: _Registered) -> None:
    _member(registered, "broken", raises=ProviderError("broken", "upstream 500"))
    _member(registered, "fine")

    with WebRouter("fine") as router:
        report = router.benchmark_extract(URLS, providers=["broken", "fine"])

    broken, fine = report.runs
    assert isinstance(broken.error, ProviderError) and "upstream 500" in str(broken.error)
    assert broken.response is None and broken.count == 0 and not broken.ok
    assert fine.ok
    assert [run.provider for run in report.failed] == ["broken"]


def test_a_call_that_fetched_some_urls_is_an_answered_row_that_says_which(registered: _Registered) -> None:
    """The row keeps the call's own partial-failure contract: documents for what came back, failures for the rest."""
    _member(registered, "partial", drops=URLS[1])
    _member(registered, "whole")

    with WebRouter("whole") as router:
        report = router.benchmark_extract(URLS, providers=["partial", "whole"])

    partial = report["partial"]
    assert partial.ok and partial.count == 1
    assert partial.response is not None
    assert partial.response.urls == [URLS[0]]
    assert [failure.url for failure in partial.response.failures] == [URLS[1]]
    assert report["whole"].count == 2


def test_a_format_one_member_cannot_return_is_that_members_refusal(registered: _Registered) -> None:
    """The comparison the caller asked for is itself information: the narrow member's row says why."""
    narrow_cls = _member(registered, "text-only", formats=frozenset({"text"}), default="text")
    _member(registered, "wide")

    with WebRouter("wide") as router:
        report = router.benchmark_extract(URLS, providers=["text-only", "wide"], format="markdown")
        narrow = router.get_extract_provider("text-only")

    assert isinstance(report["text-only"].error, UnsupportedFormatError)
    assert "markdown" in str(report["text-only"].error)
    assert isinstance(narrow, narrow_cls) and narrow.calls == [], "nothing was sent to the backend that refused"
    assert report["wide"].ok


def test_with_no_format_named_every_member_answers_in_its_own(registered: _Registered) -> None:
    """Unset is the one request every backend can serve: markdown where it can, its own default where it cannot."""
    _member(registered, "text-only", formats=frozenset({"text"}), default="text")
    _member(registered, "wide")

    with WebRouter("wide") as router:
        report = router.benchmark_extract(URLS, providers=["text-only", "wide"])

    assert all(run.ok for run in report.runs)
    assert report["text-only"].response is not None and report["wide"].response is not None
    assert {document.format for document in report["text-only"].response.documents} == {"text"}
    assert {document.format for document in report["wide"].response.documents} == {"markdown"}


def test_a_backend_that_cannot_be_built_lands_in_its_row(registered: _Registered) -> None:
    _member(registered, "keyless", init_raises=ConfigurationError.missing_api_key("keyless", "KEYLESS_API_KEY"))
    _member(registered, "fine")

    with WebRouter("fine") as router:
        report = router.benchmark_extract(URLS, providers=["keyless", "fine"])

    assert isinstance(report["keyless"].error, ConfigurationError)
    assert "KEYLESS_API_KEY" in str(report["keyless"].error)
    assert report["keyless"].latency == 0.0, "nothing ran, so nothing was timed"
    assert report["fine"].ok


def test_a_slow_backend_times_out_alone_and_the_report_still_comes_back(registered: _Registered) -> None:
    _member(registered, "slow", delay=0.5)
    _member(registered, "fast")

    with WebRouter("fast") as router:
        started = time.perf_counter()
        report = router.benchmark_extract(URLS, providers=["slow", "fast"], timeout=0.2)
        elapsed = time.perf_counter() - started

    slow = report["slow"]
    assert isinstance(slow.error, BenchmarkTimeoutError)
    assert slow.error.provider == "slow" and slow.error.timeout == 0.2
    assert slow.latency >= 0.2
    assert report["fast"].ok
    assert elapsed < 0.45, "the report waited for the timeout, not for the slow backend"


async def test_the_async_router_times_a_slow_row_out_the_same_way(registered: _Registered) -> None:
    _member(registered, "slow", delay=0.5)
    _member(registered, "fast")

    started = time.perf_counter()
    async with AsyncWebRouter("fast") as router:
        report = await router.benchmark_extract(URLS, providers=["slow", "fast"], timeout=0.2)
    elapsed = time.perf_counter() - started

    assert isinstance(report["slow"].error, BenchmarkTimeoutError)
    assert report["fast"].ok and report["fast"].count == 2
    assert elapsed < 0.45


async def test_rows_run_at_once_rather_than_one_after_another(registered: _Registered) -> None:
    _member(registered, "one", delay=0.2)
    _member(registered, "two", delay=0.2)

    started = time.perf_counter()
    async with AsyncWebRouter("one") as router:
        report = await router.benchmark_extract(URLS, providers=["one", "two"])
    elapsed = time.perf_counter() - started

    assert all(run.ok for run in report.runs)
    assert elapsed < 0.35, "two 0.2s backends took as long as one"


def test_naming_no_provider_is_a_caller_error(registered: _Registered) -> None:
    _member(registered, "first")

    with WebRouter("first") as router, pytest.raises(ValueError, match=r"benchmark_extract.*at least one provider"):
        router.benchmark_extract(URLS, providers=[])


def test_the_default_comparison_is_every_configured_extract_backend_but_the_delegating_ones(
    monkeypatch: pytest.MonkeyPatch, registered: _Registered
) -> None:
    """`auto` and `max` delegate to the others, so a row for either would only repeat one of theirs.

    Credentials handed to the router through `provider_config` count like
    ones in the environment; a config for a backend that cannot extract does
    not.
    """
    for env_vars in EXTRACT_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    _member(registered, "keyed-here")

    defaults = WebRouter(
        "keyed-here",
        provider_config={"keyed-here": {"api_key": "k"}, "oxylabs": {"username": "u", "password": "p"}, "brave": {}},
    )._default_extract_benchmark_providers()

    assert {"auto", "max"} <= set(configured_extract_providers()) and not {"auto", "max"} & set(defaults)
    assert set(defaults) == (set(configured_extract_providers()) - BENCHMARK_EXCLUDED) | {"keyed-here", "oxylabs"}
    assert "brave" not in defaults, "search-only: an extract comparison cannot use it"
    assert "nimble" in defaults and "jina" in defaults, "a key in the environment, and the keyless tier"
    assert defaults == sorted(defaults)


def test_a_row_carries_exactly_one_outcome() -> None:
    from pydantic import ValidationError

    answered = ExtractResponse(provider="p", documents=[ExtractedDocument(url="https://example.com")])
    assert ExtractBenchmarkRun(provider="p", latency=0.0, response=answered).ok
    assert not ExtractBenchmarkRun(provider="p", latency=0.0, error=ProviderError("p", "x")).ok
    with pytest.raises(ValidationError):
        ExtractBenchmarkRun(provider="p", latency=0.0)
    with pytest.raises(ValidationError):
        ExtractBenchmarkRun(provider="p", latency=0.0, response=answered, error=ProviderError("p", "x"))


def test_an_already_built_provider_is_a_row_under_its_own_name(registered: _Registered) -> None:
    member_cls = _member(registered, "mine")
    _member(registered, "other")
    instance = member_cls()

    with WebRouter("other") as router:
        report = router.benchmark_extract(URLS, providers=[instance, "other"])

    assert [run.provider for run in report.runs] == ["mine", "other"]
    assert instance.calls, "the passed-in instance served its row"
    assert not instance.closed, "the router never built it, so it is not the router's to close"


def test_the_router_closes_the_providers_it_built_for_the_comparison(registered: _Registered) -> None:
    _member(registered, "first")
    _member(registered, "second")

    with WebRouter("first") as router:
        router.benchmark_extract(URLS, providers=["first", "second"])
        built = [router.get_extract_provider(name) for name in ("first", "second")]

    assert all(provider.closed for provider in built)


def test_close_waits_for_an_abandoned_row_instead_of_closing_under_it(registered: _Registered) -> None:
    from tests.conftest import FakeExtractProvider

    order: list[str] = []

    class Straggler(FakeExtractProvider):  # type: ignore[misc, valid-type]
        name = "straggler"

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            time.sleep(0.3)
            order.append("extract returned")
            return self._respond(request, urls)

        def close(self) -> None:
            order.append("provider closed")
            super().close()

    register_extract_provider("straggler", Straggler)
    registered.names.append("straggler")

    router = WebRouter("straggler")
    report = router.benchmark_extract("https://example.com", providers=["straggler"], timeout=0.05)
    assert isinstance(report["straggler"].error, BenchmarkTimeoutError)
    router.close()

    assert order == ["extract returned", "provider closed"], "the abandoned call outlived its provider's client"


def test_a_search_benchmark_and_an_extract_benchmark_keep_their_own_registries(
    registered: _Registered, fake_provider: Any
) -> None:
    """The same name resolves against each method's own table, so `fake` here is the extract one."""
    _member(registered, "fake")

    with WebRouter("fake") as router:
        extracted = router.benchmark_extract("https://example.com", providers=["fake"])
        searched = router.benchmark("q", providers=["fake"])

    assert extracted["fake"].response is not None and extracted["fake"].response.documents
    assert searched["fake"].response is not None and searched["fake"].response.results


def test_an_extract_rows_timeout_speaks_of_a_call_not_a_query(registered: _Registered) -> None:
    """The error's text lands in rows.jsonl; an extract has URLs, not a query."""
    _member(registered, "slow", delay=0.3)

    with WebRouter("slow") as router:
        report = router.benchmark_extract("https://a.test", providers=["slow"], timeout=0.05)

    message = str(report["slow"].error)
    assert "query" not in message and "call" in message and "0.05s" in message
