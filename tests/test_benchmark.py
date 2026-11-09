"""`benchmark()`: the same search fanned out across backends, one row each, and a report that always comes back."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from web_router import AsyncWebRouter, BenchmarkTimeoutError, WebRouter, configured_providers
from web_router.constants import ENV_BY_PROVIDER
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.registry import register_provider, unregister_provider
from web_router.types import NORMALIZED_PARAMETERS, SearchRequest, SearchResponse
from web_router.types_benchmark import BenchmarkReport, BenchmarkRun, ExtractBenchmarkReport


class _Registered:
    """The fakes a test registered, so the fixture can remove exactly those."""

    def __init__(self) -> None:
        self.names: list[str] = []


@pytest.fixture
def registered() -> Any:
    ledger = _Registered()
    yield ledger
    for name in ledger.names:
        unregister_provider(name)


def _member(
    ledger: _Registered,
    member_name: str,
    *,
    raises: Exception | None = None,
    delay: float = 0.0,
    supported: frozenset[str] = NORMALIZED_PARAMETERS,
    init_raises: Exception | None = None,
) -> type:
    """Register a search backend that answers after ``delay`` seconds, or fails in a given way."""
    from tests.conftest import FakeProvider

    class Member(FakeProvider):  # type: ignore[misc, valid-type]
        name = member_name
        supported_parameters = supported

        def __init__(self, **kwargs: Any) -> None:
            if init_raises is not None:
                raise init_raises
            super().__init__(**kwargs)

        def _search(self, request: SearchRequest) -> SearchResponse:
            if delay:
                time.sleep(delay)
            if raises is not None:
                raise raises
            return self._respond(request)

        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            if delay:
                await asyncio.sleep(delay)
            if raises is not None:
                raise raises
            return self._respond(request)

    register_provider(member_name, Member)
    ledger.names.append(member_name)
    return Member


def test_one_row_per_provider_in_the_order_asked(registered: _Registered) -> None:
    _member(registered, "first")
    _member(registered, "second")

    with WebRouter("first") as router:
        report = router.benchmark("q", providers=["second", "first", "second"])

    assert [run.provider for run in report.runs] == ["second", "first"], "named twice is still one row"
    assert len(report) == 2
    for run in report.runs:
        assert run.ok and run.error is None
        assert run.response is not None and run.response.provider == run.provider
        assert run.count == len(run.response) == 1
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
        router.benchmark("the query", providers=["first", "second"], max_results=3, search_depth="deep")
        requests = [router.get_provider(name).requests[0] for name in ("first", "second")]

    assert requests[0] == requests[1]
    assert requests[0].query == "the query"
    assert requests[0].max_results == 3 and requests[0].search_depth == "deep"


def test_a_backend_that_fails_is_its_own_rows_error_not_the_comparisons(registered: _Registered) -> None:
    _member(registered, "broken", raises=ProviderError("broken", "upstream 500"))
    _member(registered, "fine")

    with WebRouter("fine") as router:
        report = router.benchmark("q", providers=["broken", "fine"])

    broken, fine = report.runs
    assert isinstance(broken.error, ProviderError) and "upstream 500" in str(broken.error)
    assert broken.response is None and broken.count == 0 and not broken.ok
    assert fine.ok
    assert [run.provider for run in report.failed] == ["broken"]


def test_an_option_one_member_cannot_serve_is_that_members_refusal(registered: _Registered) -> None:
    """The comparison the caller asked for is itself information: the narrow member's row says why."""
    narrow_cls = _member(registered, "narrow", supported=frozenset({"max_results"}))
    _member(registered, "wide")

    with WebRouter("wide") as router:
        report = router.benchmark("q", providers=["narrow", "wide"], country="US")
        narrow = router.get_provider("narrow")

    assert isinstance(report["narrow"].error, UnsupportedParameterError)
    assert "country" in str(report["narrow"].error)
    assert isinstance(narrow, narrow_cls) and narrow.requests == [], "nothing was sent to the backend that refused"
    assert report["wide"].ok


def test_a_backend_that_cannot_be_built_lands_in_its_row(registered: _Registered) -> None:
    _member(registered, "keyless", init_raises=ConfigurationError.missing_api_key("keyless", "KEYLESS_API_KEY"))
    _member(registered, "fine")

    with WebRouter("fine") as router:
        report = router.benchmark("q", providers=["keyless", "fine"])

    assert isinstance(report["keyless"].error, ConfigurationError)
    assert "KEYLESS_API_KEY" in str(report["keyless"].error)
    assert report["fine"].ok


def test_a_slow_backend_times_out_alone_and_the_report_still_comes_back(registered: _Registered) -> None:
    _member(registered, "slow", delay=0.5)
    _member(registered, "fast")

    with WebRouter("fast") as router:
        started = time.perf_counter()
        report = router.benchmark("q", providers=["slow", "fast"], timeout=0.2)
        # Measured before the router closes: close() deliberately waits for
        # the abandoned row, but the report itself must not.
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
        report = await router.benchmark("q", providers=["slow", "fast"], timeout=0.2)
    elapsed = time.perf_counter() - started

    assert isinstance(report["slow"].error, BenchmarkTimeoutError)
    assert report["fast"].ok
    assert elapsed < 0.45


def test_no_timeout_waits_for_the_slow_backend(registered: _Registered) -> None:
    _member(registered, "slow", delay=0.3)

    with WebRouter("slow") as router:
        report = router.benchmark("q", providers=["slow"], timeout=None)

    assert report["slow"].ok
    assert report["slow"].latency >= 0.3


async def test_rows_run_at_once_rather_than_one_after_another(registered: _Registered) -> None:
    _member(registered, "one", delay=0.2)
    _member(registered, "two", delay=0.2)

    started = time.perf_counter()
    async with AsyncWebRouter("one") as router:
        report = await router.benchmark("q", providers=["one", "two"])
    elapsed = time.perf_counter() - started

    assert all(run.ok for run in report.runs)
    assert elapsed < 0.35, "two 0.2s backends took as long as one"


def test_naming_no_provider_is_a_caller_error(registered: _Registered) -> None:
    _member(registered, "first")

    with WebRouter("first") as router, pytest.raises(ValueError, match="at least one provider"):
        router.benchmark("q", providers=[])


def test_the_default_comparison_is_every_configured_backend_but_auto(
    monkeypatch: pytest.MonkeyPatch, registered: _Registered
) -> None:
    """`auto` delegates to the others, so a row for it would only repeat one of theirs.

    A key handed to the router through `provider_config` counts like one in
    the environment; a config for a backend that cannot search does not.
    """
    for env_var in ENV_BY_PROVIDER.values():
        if env_var is not None:
            monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    _member(registered, "keyed-here")

    defaults = WebRouter(
        "keyed-here", provider_config={"keyed-here": {"api_key": "k"}, "oxylabs": {"username": "u"}}
    )._default_benchmark_providers()

    assert "auto" in configured_providers() and "auto" not in defaults
    assert set(defaults) == (set(configured_providers()) - {"auto"}) | {"keyed-here"}
    assert "oxylabs" not in defaults, "extract-only: a search comparison cannot use it"
    assert "nimble" in defaults and "you-mcp" in defaults
    assert defaults == sorted(defaults)


def test_a_row_carries_exactly_one_outcome() -> None:
    from pydantic import ValidationError

    answered = SearchResponse(provider="p", query="q")
    assert BenchmarkRun(provider="p", latency=0.0, response=answered).ok
    assert not BenchmarkRun(provider="p", latency=0.0, error=ProviderError("p", "x")).ok
    with pytest.raises(ValidationError):
        BenchmarkRun(provider="p", latency=0.0)
    with pytest.raises(ValidationError):
        BenchmarkRun(provider="p", latency=0.0, response=answered, error=ProviderError("p", "x"))


def test_a_rows_latency_is_its_own_calls_not_the_comparisons(registered: _Registered) -> None:
    _member(registered, "slow", delay=0.3)
    _member(registered, "fast")

    with WebRouter("fast") as router:
        report = router.benchmark("q", providers=["slow", "fast"])

    assert report["fast"].latency < 0.1 < report["slow"].latency


def test_a_timed_out_row_never_builds_a_provider_after_the_router_closed(registered: _Registered) -> None:
    """The row's provider is built before it starts, so the router that owns it is the one that closes it."""
    _member(registered, "slow", delay=0.3)

    router = WebRouter("slow")
    report = router.benchmark("q", providers=["slow"], timeout=0.1)
    router.close()
    built = dict(router._instances)
    time.sleep(0.35)

    assert isinstance(report["slow"].error, BenchmarkTimeoutError)
    assert router._instances == built and all(provider.closed for provider in built.values())


def test_an_already_built_provider_is_a_row_under_its_own_name(registered: _Registered) -> None:
    member_cls = _member(registered, "mine")
    _member(registered, "other")
    instance = member_cls()

    with WebRouter("other") as router:
        report = router.benchmark("q", providers=[instance, "other"])

    assert [run.provider for run in report.runs] == ["mine", "other"]
    assert instance.requests, "the passed-in instance served its row"
    assert not instance.closed, "the router never built it, so it is not the router's to close"


def test_the_router_closes_the_providers_it_built_for_the_comparison(registered: _Registered) -> None:
    _member(registered, "first")
    _member(registered, "second")

    with WebRouter("first") as router:
        router.benchmark("q", providers=["first", "second"])
        built = [router.get_provider(name) for name in ("first", "second")]

    assert all(provider.closed for provider in built)


def test_a_failure_outside_the_librarys_own_errors_is_that_rows_error_not_the_comparisons(
    registered: _Registered,
) -> None:
    """A provider bug -- here a `search` that bypasses the base class and raises KeyError -- still yields a report."""
    from tests.conftest import FakeProvider

    class Buggy(FakeProvider):  # type: ignore[misc, valid-type]
        name = "buggy"

        def search(self, request: SearchRequest) -> SearchResponse:  # the base wraps; a bug may not go through it
            raise KeyError("results")

        async def asearch(self, request: SearchRequest) -> SearchResponse:
            raise KeyError("results")

    register_provider("buggy", Buggy)
    registered.names.append("buggy")
    _member(registered, "fine")

    with WebRouter("fine") as router:
        report = router.benchmark("q", providers=["buggy", "fine"])
    assert isinstance(report["buggy"].error, ProviderError)
    assert isinstance(report["buggy"].error.original, KeyError)
    assert report["buggy"].error.__cause__ is report["buggy"].error.original, "the traceback is kept, as when raised"
    assert report["fine"].ok

    async def on_the_loop() -> BenchmarkReport:
        async with AsyncWebRouter("fine") as router:
            return await router.benchmark("q", providers=["buggy", "fine"])

    report = asyncio.run(on_the_loop())
    assert isinstance(report["buggy"].error.original, KeyError) and report["fine"].ok  # type: ignore[union-attr]


def test_a_member_whose_constructor_raises_outside_the_librarys_errors_is_its_row(registered: _Registered) -> None:
    _member(registered, "untypable", init_raises=TypeError("unexpected keyword"))
    _member(registered, "fine")

    with WebRouter("fine") as router:
        report = router.benchmark("q", providers=["untypable", "fine"])

    assert isinstance(report["untypable"].error, ProviderError)
    assert isinstance(report["untypable"].error.original, TypeError)
    assert report["untypable"].error.__cause__ is report["untypable"].error.original


def test_a_dumped_row_carries_the_error_as_its_message(registered: _Registered) -> None:
    _member(registered, "broken", raises=ProviderError("broken", "upstream 500"))
    _member(registered, "fine")

    with WebRouter("fine") as router:
        report = router.benchmark("q", providers=["broken", "fine"])

    dumped = report.model_dump()
    assert len(dumped["runs"]) == 2
    assert dumped["runs"][0]["error"] == str(report["broken"].error) and dumped["runs"][0]["response"] is None
    assert dumped["runs"][1]["error"] is None and dumped["runs"][1]["response"]["provider"] == "fine"


def test_close_waits_for_an_abandoned_row_instead_of_closing_under_it(registered: _Registered) -> None:
    from tests.conftest import FakeProvider

    order: list[str] = []

    class Straggler(FakeProvider):  # type: ignore[misc, valid-type]
        name = "straggler"

        def _search(self, request: SearchRequest) -> SearchResponse:
            time.sleep(0.3)
            order.append("search returned")
            return self._respond(request)

        def close(self) -> None:
            order.append("provider closed")
            super().close()

    register_provider("straggler", Straggler)
    registered.names.append("straggler")

    router = WebRouter("straggler")
    report = router.benchmark("q", providers=["straggler"], timeout=0.05)
    assert isinstance(report["straggler"].error, BenchmarkTimeoutError)
    router.close()

    assert order == ["search returned", "provider closed"], "the abandoned call outlived its provider's client"


def test_two_threads_asking_for_one_provider_build_it_once(registered: _Registered) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from tests.conftest import FakeProvider

    built: list[int] = []

    class SlowInit(FakeProvider):  # type: ignore[misc, valid-type]
        name = "slowinit"

        def __init__(self, **kwargs: Any) -> None:
            built.append(1)
            # Long enough that an unlocked check-then-create would overlap.
            time.sleep(0.1)
            super().__init__(**kwargs)

    register_provider("slowinit", SlowInit)
    registered.names.append("slowinit")

    with WebRouter("slowinit") as router, ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: router.get_provider("slowinit"), range(2))

    assert first is second and len(built) == 1, "the loser of the race would be a client nobody closes"


def test_a_report_serializes_what_names_the_comparison_before_its_rows() -> None:
    """The order callers saw before the two reports shared anything: `query` then `runs`, `urls` then `runs`."""
    assert list(BenchmarkReport(query="q").model_dump()) == ["query", "runs"]
    assert list(ExtractBenchmarkReport(urls=["https://a.test"]).model_dump()) == ["urls", "runs"]
    assert list(BenchmarkReport.model_fields) == ["query", "runs"]
