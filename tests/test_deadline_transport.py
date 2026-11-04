"""Per-call deadlines on the one-call methods -- search, extract, a template run, answer -- over every transport."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from tests.deadline_helpers import (
    DEADLINE,
    SLOW,
    TINY,
    URLS,
    _acall,
    _Acme,
    _AcmeExtract,
    _call,
    _Clock,
    _hangs,
    _hangs_until_timeout,
)
from web_router.constants import TAVILY_SDK_SEARCH_TIMEOUT
from web_router.exceptions import DeadlineExceededError, ProviderError
from web_router.providers._clamp import clamp_timeout
from web_router.providers._deadline import call_deadline
from web_router.providers.nimble import NimbleProvider
from web_router.providers.nimble_template import NimbleTemplateProvider
from web_router.providers.tavily import TavilyProvider
from web_router.types import ExtractFailure, ExtractRequest, ExtractResponse, SearchRequest, TemplateRequest

# -- Every method stops at the deadline -----------------------------------------


@pytest.mark.parametrize("method", ["search", "extract", "answer"])
def test_a_one_call_method_stops_at_the_deadline_and_says_so(clock: _Clock, method: str) -> None:
    seen: list[float] = []

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError, match="within the call's 3s deadline") as caught:
        _call(method, _hangs_until_timeout(clock, seen))

    assert seen == [DEADLINE], "the request is clamped to the time left, not the client's 60s"
    assert clock.now == DEADLINE
    assert caught.value.provider == "acme"
    assert caught.value.seconds == DEADLINE
    assert isinstance(caught.value.__cause__, ProviderError | httpx.ReadTimeout), (
        "the backend's timeout stays the cause"
    )


@pytest.mark.parametrize("method", ["search", "extract", "answer"])
async def test_an_async_call_is_cancelled_at_the_deadline(method: str) -> None:
    with call_deadline(TINY), pytest.raises(DeadlineExceededError, match=r"0\.05s deadline"):
        await _acall(method, _hangs())


def test_a_call_is_not_started_once_the_deadline_has_passed(clock: _Clock) -> None:
    seen: list[float] = []

    with call_deadline(DEADLINE):
        clock.now = DEADLINE
        with pytest.raises(DeadlineExceededError):
            _call("search", _hangs_until_timeout(clock, seen))

    assert seen == [], "nothing was sent"


def test_a_backend_failure_with_time_left_is_still_the_backends(clock: _Clock) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    with call_deadline(DEADLINE), pytest.raises(ProviderError, match="401"):
        _call("search", httpx.MockTransport(refuse))


# -- An extract keeps what came back in time --------------------------------------


async def test_an_extract_keeps_the_pages_that_came_back_and_reports_the_slow_one() -> None:
    provider = _AcmeExtract(transport=_hangs(slow=SLOW))

    with call_deadline(TINY):
        response = await provider.aextract(ExtractRequest(urls=URLS))

    assert [document.url for document in response.documents] == [URLS[0], URLS[2]]
    assert [failure.url for failure in response.failures] == [SLOW]
    assert "0.05s deadline" in response.failures[0].error


async def test_an_extract_whose_every_call_runs_out_raises_the_deadline() -> None:
    provider = _AcmeExtract(transport=_hangs())

    with call_deadline(TINY), pytest.raises(DeadlineExceededError, match=r"0\.05s deadline"):
        await provider.aextract(ExtractRequest(urls=URLS))


def test_a_sync_extract_keeps_what_came_back_before_the_deadline(clock: _Clock) -> None:
    """One call at a time: the slow URL spends what is left, so the one after it is never started."""
    seen: list[float] = []
    provider = _AcmeExtract(transport=_hangs_until_timeout(clock, seen, slow=SLOW))

    with call_deadline(DEADLINE):
        response = provider.extract(ExtractRequest(urls=URLS))

    assert [document.url for document in response.documents] == [URLS[0]]
    assert [failure.url for failure in response.failures] == [SLOW, URLS[2]]
    assert all("3s deadline" in failure.error for failure in response.failures)
    assert len(seen) == 2, "the URL after the deadline was never sent"


# -- The SDK and MCP transports --------------------------------------------------


class _Hanging:
    """An SDK call that stalls until its timeout runs out, recording what it was given."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        self.clock.now = DEADLINE
        raise TimeoutError("read timed out")


class _FakeNimble:
    """Enough of a Nimble SDK client for one call: ``with_options`` records the per-call options."""

    def __init__(self, call: Any) -> None:
        self.timeout = httpx.Timeout(5.0, read=180.0)
        self.options: list[dict[str, Any]] = []
        self.search = call
        self.extract = SimpleNamespace(run=call, templates=SimpleNamespace(run=call))

    def with_options(self, **options: Any) -> _FakeNimble:
        self.options.append(options)
        return self


def test_a_nimble_template_run_stops_at_the_deadline(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_template is Nimble's alone, so its per-method check runs on the SDK."""
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    provider = NimbleTemplateProvider()
    client = _FakeNimble(_Hanging(clock))
    provider._client = client  # type: ignore[assignment]

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError, match="3s deadline"):
        provider.run(TemplateRequest(template="books_pdp", inputs={"url": "https://example.com"}))

    (options,) = client.options
    assert options["max_retries"] == 0, "a retry would start a second request past the deadline"
    timeout = options["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (DEADLINE, DEADLINE, DEADLINE, DEADLINE)


def test_a_nimble_search_is_clamped_and_untouched_without_a_deadline(
    clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    provider = NimbleProvider()
    client = _FakeNimble(_Hanging(clock))
    provider._client = client  # type: ignore[assignment]

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError):
        provider.search(SearchRequest(query="q"))
    assert client.options[0]["timeout"].read == DEADLINE

    assert provider._within_deadline(client) is client, "with no deadline the client is used as it was configured"


async def test_an_async_nimble_template_run_is_cancelled_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    provider = NimbleTemplateProvider()

    async def never(**kwargs: Any) -> Any:
        await asyncio.sleep(30)

    provider._async_client = _FakeNimble(never)  # type: ignore[assignment]

    with call_deadline(TINY), pytest.raises(DeadlineExceededError, match=r"0\.05s deadline"):
        await provider.arun(TemplateRequest(template="books_pdp", inputs={"url": "https://example.com"}))


def test_tavily_takes_the_time_left_as_its_per_call_timeout(clock: _Clock) -> None:
    provider = TavilyProvider(api_key="test-key")
    hanging = _Hanging(clock)
    provider._client = SimpleNamespace(search=hanging)  # type: ignore[assignment]

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError, match="3s deadline"):
        provider.search(SearchRequest(query="q"))

    assert hanging.kwargs[0]["timeout"] == DEADLINE < TAVILY_SDK_SEARCH_TIMEOUT
    assert "timeout" not in provider._build_payload(SearchRequest(query="q")), "unset without a deadline"


# -- What a caller's options and a backend's pacing cannot stretch ------------------


def test_a_longer_tavily_timeout_in_provider_options_does_not_outlast_the_deadline(clock: _Clock) -> None:
    from web_router.providers.tavily import TavilyExtractProvider

    options = {"timeout": 120}
    search = TavilyProvider(api_key="test-key")
    extract = TavilyExtractProvider(api_key="test-key")

    with call_deadline(DEADLINE):
        assert search._build_payload(SearchRequest(query="q", provider_options=options))["timeout"] == DEADLINE
        assert (
            extract._build_payload(ExtractRequest(urls=URLS[:1], provider_options=options), URLS[:1])["timeout"]
            == DEADLINE
        )
    assert search._build_payload(SearchRequest(query="q", provider_options=options))["timeout"] == 120


def test_a_tavily_extract_with_less_than_a_second_left_sends_nothing(clock: _Clock) -> None:
    # Tavily's extract accepts a timeout of 1 to 60 s; a shorter one would come back a validation error.
    from web_router.providers.tavily import TavilyExtractProvider

    extract = TavilyExtractProvider(api_key="test-key")
    with call_deadline(0.5), pytest.raises(DeadlineExceededError):
        extract._build_payload(ExtractRequest(urls=URLS[:1]), URLS[:1])
    with call_deadline(DEADLINE):
        assert extract._build_payload(ExtractRequest(urls=URLS[:1]), URLS[:1])["timeout"] == DEADLINE


class _Spaced(_AcmeExtract):
    """A backend limited per second: two calls start at least two seconds apart, as DeepSeek's fetches do."""

    min_start_interval = 2.0


def _answers(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


def test_a_spaced_extract_never_sleeps_past_the_deadline(clock: _Clock) -> None:
    provider = _Spaced(transport=httpx.MockTransport(_answers))

    with call_deadline(DEADLINE):
        response = provider.extract(ExtractRequest(urls=URLS))

    assert [document.url for document in response.documents] == URLS[:2]
    assert [failure.url for failure in response.failures] == [URLS[2]]
    assert "3s deadline" in response.failures[0].error
    assert clock.now == 2.0, "the third call's wait would have reached the deadline, so it was never slept"


async def test_an_async_spaced_extract_never_sleeps_past_the_deadline() -> None:
    _Spaced.min_start_interval = 1.0
    try:
        provider = _Spaced(transport=httpx.MockTransport(_answers))
        started = asyncio.get_running_loop().time()
        with call_deadline(TINY):
            response = await provider.aextract(ExtractRequest(urls=URLS))
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        _Spaced.min_start_interval = 2.0

    assert [document.url for document in response.documents] == URLS[:1]
    assert [failure.url for failure in response.failures] == URLS[1:]
    assert elapsed < 0.5, "the spaced calls failed at once rather than sleeping a second each"


class _ReportsBlocked(_AcmeExtract):
    """A backend that answers 200 for the first URL and reports it blocked, as Tavily's ``failed_results`` do."""

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        return ExtractResponse(provider=self.name, failures=[ExtractFailure(url=urls[0], error="blocked")])


def test_an_extract_that_failed_for_other_reasons_too_is_not_reported_as_only_the_deadline(clock: _Clock) -> None:
    """One URL reported blocked, the other cut off by the deadline: nothing came back, and both are named."""
    provider = _ReportsBlocked(transport=_hangs_until_timeout(clock, [], slow=URLS[1]))

    with call_deadline(DEADLINE), pytest.raises(ProviderError) as caught:
        provider.extract(ExtractRequest(urls=URLS[:2]))

    message = str(caught.value)
    assert f"{URLS[0]}: blocked" in message
    assert URLS[1] in message and "3s deadline" in message


def test_a_one_call_transport_never_gets_more_than_the_time_left(clock: _Clock) -> None:
    """Not the 0.5s floor a job's polls keep: with 0.2s left and a 30s timeout, the request gets 0.2s at most."""
    seen: list[float] = []
    provider = _Acme(transport=_hangs_until_timeout(clock, seen), timeout=30.0)

    with call_deadline(DEADLINE):
        clock.now = DEADLINE - 0.2
        with pytest.raises(DeadlineExceededError):
            provider.search(SearchRequest(query="q"))

    assert seen and seen[0] <= 0.2 + 1e-9
    assert clamp_timeout(30.0) == 30.0, "with no deadline the provider's own timeout stands"


async def test_an_async_batch_that_reported_only_failures_is_not_hidden_by_the_deadline() -> None:
    """One call answers with nothing but a failure, another is cut off: the reported failure is still named."""
    provider = _ReportsBlocked(transport=_hangs(slow=URLS[1]))

    with call_deadline(TINY), pytest.raises(ProviderError) as caught:
        await provider.aextract(ExtractRequest(urls=URLS[:2]))

    assert not isinstance(caught.value, DeadlineExceededError)
    message = str(caught.value)
    assert f"{URLS[0]}: blocked" in message
    assert URLS[1] in message
