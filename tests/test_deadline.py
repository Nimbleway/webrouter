"""Per-call deadlines: every method stops at the deadline set around it, and ``auto`` spends what is left.

There is no public parameter for a deadline yet; these set one the way the
router will, with :func:`~web_router.providers._deadline.call_deadline`. The sync
tests run on a fake clock -- a backend that "hangs" moves it on by the timeout
its request was given, as a clamped read timeout would -- and the async ones on
a deadline of a few hundredths of a second, so nothing here waits for real.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from tests.conftest import (
    FakeBrowseProvider,
    FakeExtractProvider,
    FakeProvider,
    FakeResearchProvider,
)
from web_router.constants import DEFAULT_HTTP_TIMEOUT, TAVILY_SDK_SEARCH_TIMEOUT
from web_router.exceptions import (
    BrowseTimeoutError,
    ConfigurationError,
    DeadlineExceededError,
    ProviderError,
    ResearchTimeoutError,
)
from web_router.providers import _deadline, base
from web_router.providers._deadline import call_deadline, current_deadline, job_budget, job_deadline
from web_router.providers._http import HttpAnswerProvider, HttpExtractProvider, HttpSearchProvider, clamp_timeout
from web_router.providers.auto import AutoProvider, AutoResearchProvider
from web_router.providers.max import MaxExtractProvider
from web_router.providers.nimble import NimbleProvider
from web_router.providers.nimble_template import NimbleTemplateProvider
from web_router.providers.tavily import TavilyProvider
from web_router.providers.you_mcp import YouMcpProvider
from web_router.registry import (
    register_extract_provider,
    register_provider,
    register_research_provider,
    unregister_extract_provider,
    unregister_provider,
    unregister_research_provider,
)
from web_router.types import (
    BrowseRequest,
    ExtractedDocument,
    ExtractFailure,
    ExtractRequest,
    ExtractResponse,
    ResearchProgress,
    ResearchRequest,
    SearchRequest,
    SearchResponse,
    TemplateRequest,
)
from web_router.types_answer import AnswerRequest, AnswerResponse

DEADLINE = 3.0
TINY = 0.05
"""An async deadline: long enough to start a call, short enough that nobody waits for it."""

URLS = ["https://a.example", "https://b.example", "https://c.example"]
SLOW = "https://b.example"


class _Clock:
    """``time`` as the router reads it, moved on only by a backend that hangs or a poll that sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(base, "time", fake)
    monkeypatch.setattr(_deadline, "time", fake)
    return fake


# -- Backends over the shared HTTP base ---------------------------------------


class _Acme(HttpSearchProvider):
    name = "acme"
    base_url = "https://acme.example"
    search_path = "/search"

    def _build_payload(self, request: SearchRequest) -> dict[str, Any]:
        return {"q": request.query}

    def _to_response(self, request: SearchRequest, payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(provider=self.name, query=request.query, results=[])


class _AcmeAnswer(HttpAnswerProvider):
    name = "acme"
    base_url = "https://acme.example"
    answer_path = "/answer"

    def _build_payload(self, request: AnswerRequest) -> dict[str, Any]:
        return {"q": request.query}

    def _to_response(self, request: AnswerRequest, payload: dict[str, Any]) -> AnswerResponse:
        return AnswerResponse(provider=self.name, query=request.query, answer="yes")


class _AcmeExtract(HttpExtractProvider):
    name = "acme"
    base_url = "https://acme.example"
    extract_path = "/extract"
    supported_formats = frozenset({"markdown"})
    default_format = "markdown"

    def _build_payload(self, request: ExtractRequest, urls: list[str]) -> dict[str, Any]:
        return {"url": urls[0]}

    def _to_response(self, request: ExtractRequest, urls: list[str], payload: dict[str, Any]) -> ExtractResponse:
        document = ExtractedDocument(url=urls[0], content="hi", format="markdown")
        return ExtractResponse(provider=self.name, documents=[document])


def _hangs_until_timeout(clock: _Clock, seen: list[float], *, slow: str | None = None) -> httpx.MockTransport:
    """A backend that stalls until each request's read timeout runs out -- every request, or only ``slow``'s."""

    def handle(request: httpx.Request) -> httpx.Response:
        read = request.extensions["timeout"]["read"]
        seen.append(read)
        if slow is None or slow in request.content.decode():
            clock.now += read
            raise httpx.ReadTimeout("read timed out", request=request)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handle)


def _hangs(*, slow: str | None = None) -> httpx.MockTransport:
    """Async: a backend that never answers -- every request, or only ``slow``'s."""

    async def handle(request: httpx.Request) -> httpx.Response:
        if slow is None or slow in request.content.decode():
            await asyncio.sleep(30)
        return httpx.Response(200, json={})

    return httpx.MockTransport(handle)


# -- Every method stops at the deadline -----------------------------------------


def _call(method: str, transport: httpx.MockTransport) -> Any:
    if method == "search":
        return _Acme(transport=transport).search(SearchRequest(query="q"))
    if method == "answer":
        return _AcmeAnswer(transport=transport).answer(AnswerRequest(query="q"))
    return _AcmeExtract(transport=transport).extract(ExtractRequest(urls=URLS[:1]))


async def _acall(method: str, transport: httpx.MockTransport) -> Any:
    if method == "search":
        return await _Acme(transport=transport).asearch(SearchRequest(query="q"))
    if method == "answer":
        return await _AcmeAnswer(transport=transport).aanswer(AnswerRequest(query="q"))
    return await _AcmeExtract(transport=transport).aextract(ExtractRequest(urls=URLS[:1]))


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


@pytest.mark.parametrize("method", ["search", "extract", "answer"])
def test_without_a_deadline_the_clients_own_timeout_applies(method: str) -> None:
    seen: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={})

    _call(method, httpx.MockTransport(handle))

    assert seen == [DEFAULT_HTTP_TIMEOUT]
    assert current_deadline() is None


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


def test_the_mcp_transport_is_clamped_too(clock: _Clock) -> None:
    seen: list[float] = []
    provider = YouMcpProvider(transport=_hangs_until_timeout(clock, seen))

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError, match="3s deadline"):
        provider.search(SearchRequest(query="q"))

    assert seen == [DEADLINE]


# -- Research and browse wait no longer than the deadline -----------------------


class _NeverDone(FakeResearchProvider):
    polls_before_done = 10**9


class _NeverDoneBrowse(FakeBrowseProvider):
    polls_before_done = 10**9


def test_research_waits_no_longer_than_an_enclosing_deadline(clock: _Clock) -> None:
    with call_deadline(DEADLINE), pytest.raises(ResearchTimeoutError, match="after 3s") as caught:
        _NeverDone().research(ResearchRequest(query="q"), timeout=600)

    assert caught.value.job_id == "job-1", "the run is still going, and the error says how to come back to it"
    assert clock.now == DEADLINE


def test_browse_waits_no_longer_than_an_enclosing_deadline(clock: _Clock) -> None:
    with call_deadline(DEADLINE), pytest.raises(BrowseTimeoutError, match="after 3s") as caught:
        _NeverDoneBrowse().browse(BrowseRequest(task="t"), timeout=900)

    assert caught.value.job_id == "task-1"


async def test_async_research_waits_no_longer_than_an_enclosing_deadline() -> None:
    with call_deadline(TINY), pytest.raises(ResearchTimeoutError, match=r"after 0\.05s"):
        await _NeverDone().aresearch(ResearchRequest(query="q"), timeout=600)


def test_a_shorter_research_timeout_still_wins_over_a_longer_deadline(clock: _Clock) -> None:
    with call_deadline(600), pytest.raises(ResearchTimeoutError, match="after 3s"):
        _NeverDone().research(ResearchRequest(query="q"), timeout=DEADLINE)


# -- The deadline itself ----------------------------------------------------------


def test_an_inner_deadline_can_tighten_but_never_extend(clock: _Clock) -> None:
    with call_deadline(DEADLINE):
        with call_deadline(600):
            assert job_budget(60) == DEADLINE
            assert current_deadline().seconds == DEADLINE  # type: ignore[union-attr]
        with job_deadline(1.0):
            assert job_budget(60) == 1.0
        with call_deadline(None):
            assert job_budget(60) == DEADLINE
    assert current_deadline() is None
    assert job_budget(60) == 60


# -- auto spends what is left ------------------------------------------------------


class _Calls:
    def __init__(self) -> None:
        self.started: list[tuple[str, float, float]] = []
        """(member, when it started, the timeout its request was given)."""
        self.names: list[str] = []


@pytest.fixture
def calls() -> Any:
    record = _Calls()
    yield record
    for name in record.names:
        unregister_provider(name)


def _hanging_member(calls: _Calls, clock: _Clock, name: str, *, own_timeout: float) -> None:
    """A search member that stalls until its request's timeout -- its own, clamped to the time left -- runs out."""

    class Member(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            budget = job_budget(own_timeout)
            calls.started.append((name, clock.now, budget))
            clock.now += budget
            raise TimeoutError("read timed out")

    Member.name = name
    register_provider(name, Member)
    calls.names.append(name)


def _empty_member(calls: _Calls, name: str) -> None:
    class Member(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            calls.started.append((name, 0.0, 0.0))
            return SearchResponse(provider=name, query=request.query, results=[])

    Member.name = name
    register_provider(name, Member)
    calls.names.append(name)


def test_auto_tries_the_next_member_within_the_deadline_and_names_it_at_the_end(clock: _Clock, calls: _Calls) -> None:
    """A chain of three and a 3s deadline: the first hangs, the second starts before the deadline.

    No time is held back for later members: the first runs to its own 2s
    timeout, the second -- not the last, yet -- gets min(its timeout, what is
    left), and the third never starts, because nothing is left.
    """
    for name in ("first", "second", "third"):
        _hanging_member(calls, clock, name, own_timeout=2.0)
    provider = AutoProvider(chain=(("first", {}), ("second", {}), ("third", {})))

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError) as caught:
        provider.search(SearchRequest(query="q"))

    assert calls.started == [("first", 0.0, 2.0), ("second", 2.0, 1.0)]
    assert caught.value.provider == "auto"
    message = str(caught.value)
    assert "3s deadline" in message
    assert "first:" in message and "second:" in message, "each member's reason is named"


def test_the_last_member_gets_everything_that_remains(clock: _Clock, calls: _Calls) -> None:
    _hanging_member(calls, clock, "first", own_timeout=0.5)
    _hanging_member(calls, clock, "last", own_timeout=60.0)
    provider = AutoProvider(chain=(("first", {}), ("last", {})))

    with call_deadline(DEADLINE), pytest.raises(DeadlineExceededError):
        provider.search(SearchRequest(query="q"))

    assert calls.started == [("first", 0.0, 0.5), ("last", 0.5, DEADLINE - 0.5)]


def test_an_empty_answer_that_came_back_in_time_outranks_the_deadline(clock: _Clock, calls: _Calls) -> None:
    _empty_member(calls, "empty")
    _hanging_member(calls, clock, "slow", own_timeout=60.0)
    provider = AutoProvider(chain=(("empty", {}), ("slow", {})))

    with call_deadline(DEADLINE):
        response = provider.search(SearchRequest(query="q"))

    assert response.provider == "empty"


async def test_async_auto_moves_on_and_stops_at_the_deadline(calls: _Calls) -> None:
    class Refuses(FakeProvider):
        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            calls.started.append(("refuses", 0.0, 0.0))
            raise ProviderError("refuses", "rate limited")

    class Never(FakeProvider):
        async def _asearch(self, request: SearchRequest) -> SearchResponse:
            calls.started.append(("never", 0.0, 0.0))
            await asyncio.sleep(30)
            raise AssertionError  # pragma: no cover

    for name, cls in (("refuses", Refuses), ("never", Never)):
        cls.name = name
        register_provider(name, cls)
        calls.names.append(name)
    provider = AutoProvider(chain=(("refuses", {}), ("never", {})))

    with call_deadline(TINY), pytest.raises(DeadlineExceededError, match=r"0\.05s deadline"):
        await provider.asearch(SearchRequest(query="q"))

    assert [name for name, _, _ in calls.started] == ["refuses", "never"]


# -- Billed submits are as they were -------------------------------------------------


@pytest.fixture
def research_members() -> Any:
    names: list[str] = []
    yield names
    for name in names:
        unregister_research_provider(name)


def test_auto_research_never_cuts_a_submit_short_for_a_later_member(clock: _Clock, research_members: list[str]) -> None:
    """A member that started nothing passes on the whole of what is left; the submit is clamped as on its own."""
    budgets: list[float] = []

    class NoKey(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            clock.now += 0.5
            raise ConfigurationError("no key")

    class Takes(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            budgets.append(job_budget(120.0))
            return super()._start(request)

    for name, cls in (("nokey", NoKey), ("takes", Takes)):
        cls.name = name
        register_research_provider(name, cls)
        research_members.append(name)
    provider = AutoResearchProvider(chain=(("nokey", {}), ("takes", {})))

    with call_deadline(DEADLINE):
        response = provider.research(ResearchRequest(query="q"), timeout=600)

    assert response.answer == "the answer"
    assert budgets == [DEADLINE - 0.5], "everything left, not a share of it"


def test_a_submit_that_times_out_at_the_deadline_raises_as_it_always_has(
    clock: _Clock, research_members: list[str]
) -> None:
    """A read timeout may come after the backend created the run, so auto starts no second one."""

    class Stalls(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            clock.now += job_budget(120.0)
            raise TimeoutError("read timed out")

    class Never(FakeResearchProvider):
        def _start(self, request: ResearchRequest) -> ResearchProgress:
            raise AssertionError("a second run must not start")  # pragma: no cover

    for name, cls in (("stalls", Stalls), ("never", Never)):
        cls.name = name
        register_research_provider(name, cls)
        research_members.append(name)
    provider = AutoResearchProvider(chain=(("stalls", {}), ("never", {})))

    with call_deadline(DEADLINE), pytest.raises(ProviderError, match="read timed out"):
        provider.research(ResearchRequest(query="q"), timeout=600)


# -- max hands the deadline to its worker threads -------------------------------------


def test_max_carries_the_deadline_onto_its_worker_threads() -> None:
    seen: list[tuple[bool, bool]] = []

    class Member(FakeExtractProvider):
        name = "member"

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            seen.append((threading.current_thread() is threading.main_thread(), current_deadline() is not None))
            return super()._extract(request, urls)

    register_extract_provider("member", Member)
    try:
        provider = MaxExtractProvider(chain=(("member", {}),))
        with call_deadline(60):
            provider.extract(ExtractRequest(urls=URLS[:1]))
        provider.close()
    finally:
        unregister_extract_provider("member")

    assert seen == [(False, True)]


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


def test_a_members_own_shorter_timeout_is_its_failure_and_auto_moves_on(clock: _Clock, calls: _Calls) -> None:
    """0.4s left and a member whose own timeout is 0.1s: it timed out on its own, so the next member is tried."""
    from web_router.providers._deadline import call_budget

    class Quick(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            budget = call_budget(0.1)
            calls.started.append(("quick", clock.now, budget))
            clock.now += budget
            raise TimeoutError("read timed out")

    class Answers(FakeProvider):
        def _search(self, request: SearchRequest) -> SearchResponse:
            calls.started.append(("answers", clock.now, 0.0))
            return super()._search(request)

    for name, cls in (("quick", Quick), ("answers", Answers)):
        cls.name = name
        register_provider(name, cls)
        calls.names.append(name)
    provider = AutoProvider(chain=(("quick", {}), ("answers", {})))

    with call_deadline(DEADLINE):
        clock.now = DEADLINE - 0.4
        response = provider.search(SearchRequest(query="q"))

    assert response.provider == "answers"
    assert [name for name, _, _ in calls.started] == ["quick", "answers"]
    assert calls.started[0][2] == pytest.approx(0.1), "the member's own timeout, not the time left, set the limit"


async def test_an_async_batch_that_reported_only_failures_is_not_hidden_by_the_deadline() -> None:
    """One call answers with nothing but a failure, another is cut off: the reported failure is still named."""
    provider = _ReportsBlocked(transport=_hangs(slow=URLS[1]))

    with call_deadline(TINY), pytest.raises(ProviderError) as caught:
        await provider.aextract(ExtractRequest(urls=URLS[:2]))

    assert not isinstance(caught.value, DeadlineExceededError)
    message = str(caught.value)
    assert f"{URLS[0]}: blocked" in message
    assert URLS[1] in message
