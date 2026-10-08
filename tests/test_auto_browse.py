"""`auto` for browse: a chain of billed task backends, checked against the request before any is started."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants import (
    AUTO_BROWSE_PREFERRED,
    BROWSE_ENV_BY_PROVIDER,
    PROVIDER_BROWSER_USE,
    PROVIDER_BROWSERBASE,
    PROVIDER_SKYVERN,
)
from web_router.exceptions import (
    ConfigurationError,
    MissingParameterError,
    ProviderError,
    UnsupportedParameterError,
)
from web_router.providers.auto import AutoBrowseProvider
from web_router.registry import (
    get_browse_provider_class,
    register_browse_provider,
    unregister_browse_provider,
)
from web_router.types import BrowseProgress, BrowseRequest

TASK = BrowseRequest(task="find the cheapest flight")


def _http_failure(member: str, status: int) -> ProviderError:
    """A member's failure as the base class wraps an HTTP status, with the httpx error on ``original``."""
    request = httpx.Request("POST", "https://browse.example.test/tasks")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError(f"{status}", request=request, response=response)
    return ProviderError(member, f"answered {status}", original=error)


class _Recorder:
    """Which members a chain started a task on, and which were asked about one afterwards."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.polled: list[str] = []
        self.registered: list[str] = []


def _member(
    recorder: _Recorder,
    member_name: str,
    *,
    supported: frozenset[str] = frozenset({"start_url"}),
    required: frozenset[str] = frozenset(),
    raises: Exception | None = None,
    poll_raises: Exception | None = None,
    polls: int = 0,
) -> type:
    """Register a chain member that answers after ``polls`` polls, or fails in a given way."""
    from tests.conftest import FakeBrowseProvider

    class Member(FakeBrowseProvider):  # type: ignore[misc, valid-type]
        name = member_name
        supported_parameters = supported
        required_parameters = required
        polls_before_done = polls

        def _start(self, request: BrowseRequest) -> BrowseProgress:
            recorder.started.append(member_name)
            if raises is not None:
                raise raises
            return self._labelled(super()._start(request))

        def _fetch(self, request: BrowseRequest, job_id: str) -> BrowseProgress:
            recorder.polled.append(member_name)
            if poll_raises is not None:
                raise poll_raises
            return self._labelled(super()._fetch(request, job_id))

        def _labelled(self, progress: BrowseProgress) -> BrowseProgress:
            return progress.model_copy(update={"job_id": f"{member_name}-task"})

    register_browse_provider(member_name, Member)
    recorder.registered.append(member_name)
    return Member


@pytest.fixture
def chain() -> Any:
    recorder = _Recorder()
    yield recorder
    for name in recorder.registered:
        unregister_browse_provider(name)


def _auto(*names: str) -> AutoBrowseProvider:
    return AutoBrowseProvider(chain=tuple((name, {}) for name in names))


def test_the_chain_is_tried_in_order_and_the_first_member_that_starts_wins(chain: _Recorder) -> None:
    _member(chain, "first")
    _member(chain, "second")

    provider = _auto("first", "second")
    try:
        response = provider.browse(TASK)
    finally:
        provider.close()

    assert chain.started == ["first"], "the second member should never have been reached"
    assert response.provider == "first"


def test_the_default_order_is_the_documented_one_over_the_backends_you_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_vars in BROWSE_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)
    for name in AUTO_BROWSE_PREFERRED:
        for env_var in BROWSE_ENV_BY_PROVIDER[name]:
            monkeypatch.setenv(env_var, "test-key")

    assert AUTO_BROWSE_PREFERRED == (PROVIDER_BROWSER_USE, PROVIDER_SKYVERN, PROVIDER_BROWSERBASE)
    assert AutoBrowseProvider.default_chain() == tuple((name, {}) for name in AUTO_BROWSE_PREFERRED)

    monkeypatch.delenv(BROWSE_ENV_BY_PROVIDER[PROVIDER_BROWSER_USE][0])
    assert AutoBrowseProvider.default_chain() == ((PROVIDER_SKYVERN, {}), (PROVIDER_BROWSERBASE, {}))


def test_with_no_credentials_at_all_there_is_no_chain_and_the_error_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_vars in BROWSE_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)

    provider = AutoBrowseProvider()
    assert provider._chain == ()
    with pytest.raises(ConfigurationError) as caught:
        provider.submit(TASK)

    for name in AUTO_BROWSE_PREFERRED:
        assert BROWSE_ENV_BY_PROVIDER[name][0] in str(caught.value)


def test_a_member_refusing_start_url_is_passed_over_without_starting_a_task(chain: _Recorder) -> None:
    """Browserbase has no start URL: a request that sets one must never bill a run there."""
    _member(chain, "no-url", supported=frozenset())
    _member(chain, "url")

    provider = _auto("no-url", "url")
    try:
        response = provider.browse(TASK.model_copy(update={"start_url": "https://example.com"}))
    finally:
        provider.close()

    assert chain.started == ["url"], "the member that refuses start_url was started anyway"
    assert response.provider == "url"


def test_a_member_lacking_output_schema_is_passed_over_without_starting_a_task(chain: _Recorder) -> None:
    _member(chain, "prose")
    _member(chain, "structured", supported=frozenset({"start_url", "output_schema"}))

    provider = _auto("prose", "structured")
    try:
        response = provider.browse(TASK.model_copy(update={"output_schema": {"type": "object"}}))
    finally:
        provider.close()

    assert chain.started == ["structured"]
    assert response.structured == {"ok": True}


def test_a_member_missing_a_required_parameter_is_passed_over(chain: _Recorder) -> None:
    _member(chain, "needs-url", required=frozenset({"start_url"}))
    _member(chain, "finds-its-own")

    provider = _auto("needs-url", "finds-its-own")
    try:
        response = provider.browse(TASK)
    finally:
        provider.close()

    assert chain.started == ["finds-its-own"]
    assert response.provider == "finds-its-own"


def test_output_schema_no_member_maps_raises_naming_why_each_was_skipped(chain: _Recorder) -> None:
    """No vendor maps `output_schema` yet, so `auto` must say so rather than return prose under a schema."""
    _member(chain, "url")
    _member(chain, "bare", supported=frozenset())

    provider = _auto("url", "bare")
    try:
        with pytest.raises(UnsupportedParameterError) as caught:
            provider.submit(
                TASK.model_copy(update={"output_schema": {"type": "object"}, "start_url": "https://example.com"})
            )
    finally:
        provider.close()

    message = str(caught.value)
    assert caught.value.parameters == ["output_schema"], "start_url is mapped, just not beside output_schema"
    assert "url (does not map 'output_schema')" in message
    assert "bare (does not map 'output_schema', 'start_url')" in message
    assert "provider_options" not in message, "auto's advice is why each member was skipped, not a mapping to add"
    assert chain.started == [], "nothing was started, so nothing was billed"
    assert provider._members == {}, "no member was even built"


def test_a_parameter_every_capable_member_needs_raises_missing_rather_than_unsupported(chain: _Recorder) -> None:
    """Setting `start_url` is the fix, so the error is the one that says to set it."""
    _member(chain, "needs-url", supported=frozenset({"start_url", "max_steps"}), required=frozenset({"start_url"}))
    _member(chain, "narrow", supported=frozenset())

    provider = _auto("needs-url", "narrow")
    try:
        with pytest.raises(MissingParameterError) as caught:
            provider.submit(TASK.model_copy(update={"max_steps": 5}))
    finally:
        provider.close()

    assert chain.started == []
    assert caught.value.parameters == ["start_url"]
    message = str(caught.value)
    assert "needs-url (cannot start without 'start_url')" in message
    assert "narrow (does not map 'max_steps')" in message
    assert provider._members == {}


def test_auto_itself_requires_nothing() -> None:
    assert AutoBrowseProvider.required_parameters == frozenset()
    assert get_browse_provider_class("auto") is AutoBrowseProvider


def test_a_member_that_fails_to_start_falls_through_to_the_next(chain: _Recorder) -> None:
    _member(chain, "first", raises=ConfigurationError.missing_api_key("first", "FIRST_KEY"))
    _member(chain, "second")

    provider = _auto("first", "second")
    try:
        response = provider.browse(TASK)
    finally:
        provider.close()

    assert chain.started == ["first", "second"]
    assert response.provider == "second"


def test_a_failure_that_may_have_created_the_task_stops_the_chain(chain: _Recorder) -> None:
    """A 503 can come from a gateway after the vendor made the task, so trying another would bill twice."""
    _member(chain, "first", raises=_http_failure("first", 503))
    _member(chain, "second")

    provider = _auto("first", "second")
    try:
        with pytest.raises(ProviderError, match="answered 503"):
            provider.browse(TASK)
    finally:
        provider.close()

    assert chain.started == ["first"], "the second member was started after an ambiguous failure"


def test_exhausting_the_chain_reports_every_backends_own_reason(chain: _Recorder) -> None:
    _member(chain, "first", raises=_http_failure("first", 429))
    _member(chain, "second", raises=_http_failure("second", 401))

    provider = _auto("first", "second")
    try:
        with pytest.raises(ProviderError) as caught:
            provider.browse(TASK)
    finally:
        provider.close()

    message = str(caught.value)
    assert "first" in message and "answered 429" in message
    assert "second" in message and "answered 401" in message


def test_a_failed_first_poll_keeps_the_handle_and_never_starts_a_second_task(chain: _Recorder) -> None:
    """The started task is being paid for: it must stay reachable, and its failure is its own."""
    _member(chain, "first", polls=1, poll_raises=ProviderError("first", "lost the session"))
    _member(chain, "second")

    provider = _auto("first", "second")
    try:
        job = provider.submit(TASK)
        assert chain.polled == [], "the handle is registered from the start progress, before any poll"
        with pytest.raises(ProviderError, match="lost the session"):
            provider.poll(TASK, job.job_id)
    finally:
        provider.close()

    assert job.job_id == "first:first-task"
    assert job.job_id in provider._jobs, "the billed task can still be polled or cancelled"
    assert chain.started == ["first"]


def test_a_task_that_fails_while_polled_stays_with_its_member(chain: _Recorder) -> None:
    _member(chain, "first", polls=2)
    _member(chain, "second")

    provider = _auto("first", "second")
    try:
        job = provider.submit(TASK)
        provider._jobs[job.job_id]._provider.reported = "FAILED"  # type: ignore[attr-defined]
        with pytest.raises(ProviderError):
            job.result(timeout=None)
    finally:
        provider.close()

    assert chain.started == ["first"]
    assert set(chain.polled) == {"first"}


def test_the_job_and_the_answer_name_the_backend_that_served_it(chain: _Recorder) -> None:
    _member(chain, "first", raises=_http_failure("first", 429))
    _member(chain, "second", polls=2)

    provider = _auto("first", "second")
    try:
        job = provider.submit(TASK)
        progress = provider.poll(TASK, job.job_id)
        progress = provider.poll(TASK, job.job_id)
    finally:
        provider.close()

    assert job.job_id == "second:second-task", "the handle names the member, so a poll can find it again"
    assert progress.job_id == job.job_id
    assert progress.state == "completed"
    assert progress.response is not None and progress.response.provider == "second"
    assert set(chain.polled) == {"second"}


def test_a_task_started_somewhere_else_cannot_be_followed_here(chain: _Recorder) -> None:
    _member(chain, "first")

    provider = _auto("first")
    with pytest.raises(ProviderError, match="was not started here"):
        provider.poll(TASK, "first:some-other-task")


def test_a_running_task_is_cancelled_through_the_member_holding_it(chain: _Recorder) -> None:
    _member(chain, "first", polls=5)

    provider = _auto("first")
    try:
        job = provider.submit(TASK)
        assert job.cancel()
    finally:
        provider.close()

    assert provider._members[0].cancelled == ["first-task"]  # type: ignore[attr-defined]


async def test_the_chain_routes_and_falls_through_the_same_way_async(chain: _Recorder) -> None:
    _member(chain, "no-url", supported=frozenset())
    _member(chain, "failing", raises=_http_failure("failing", 429))
    _member(chain, "second", polls=1)
    _member(chain, "third")

    provider = _auto("no-url", "failing", "second", "third")
    try:
        job = await provider.asubmit(TASK.model_copy(update={"start_url": "https://example.com"}))
        progress = await provider.apoll(TASK, job.job_id)
    finally:
        await provider.aclose()

    assert chain.started == ["failing", "second"]
    assert job.job_id == "second:second-task"
    assert progress.response is not None and progress.response.provider == "second"


async def test_no_member_raises_before_anything_starts_async(chain: _Recorder) -> None:
    _member(chain, "url")

    provider = _auto("url")
    with pytest.raises(UnsupportedParameterError, match="does not map 'output_schema'"):
        await provider.asubmit(TASK.model_copy(update={"output_schema": {"type": "object"}}))

    assert chain.started == []
    assert provider._members == {}


def test_the_router_browses_through_auto_when_no_provider_is_named(chain: _Recorder) -> None:
    _member(chain, "first")

    with WebRouter(provider_config={"auto": {"chain": (("first", {}),)}}) as router:
        response = router.browse("find the cheapest flight")

    assert response.provider == "first"


async def test_the_async_router_browses_through_auto_by_default(chain: _Recorder) -> None:
    _member(chain, "first")

    async with AsyncWebRouter(provider_config={"auto": {"chain": (("first", {}),)}}) as router:
        job = await router.submit_browse("find the cheapest flight")

    assert job.job_id == "first:first-task"


# -- Through the real HTTP wrapping ------------------------------------------
# The fakes above raise ProviderError by hand. These start the task through
# Skyvern's real HttpBrowseProvider, so the fall-through rule sees exactly what
# a vendor failure looks like once the base class has wrapped it.


def _read_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("no answer", request=request)


def _answer(status: int, body: dict[str, Any] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=body or {})


FALLS_THROUGH = {"401": _answer(401, {"detail": "bad key"})}

STOPS = {
    "read-timeout": _read_timeout,
    "503": _answer(503),
    "unknown-status": _answer(200, {"run_id": "tsk_1", "status": "teleporting"}),
}


def _http_auto(handler: Callable[[httpx.Request], httpx.Response]) -> AutoBrowseProvider:
    options = {"api_key": "test-key", "transport": httpx.MockTransport(handler)}
    return AutoBrowseProvider(chain=((PROVIDER_SKYVERN, options), ("second", {})))


@pytest.mark.parametrize("handler", FALLS_THROUGH.values(), ids=FALLS_THROUGH.keys())
def test_a_real_http_refusal_falls_through(chain: _Recorder, handler: Any) -> None:
    _member(chain, "second")
    provider = _http_auto(handler)
    try:
        assert provider.browse(TASK).provider == "second"
    finally:
        provider.close()


@pytest.mark.parametrize("handler", FALLS_THROUGH.values(), ids=FALLS_THROUGH.keys())
async def test_a_real_http_refusal_falls_through_async(chain: _Recorder, handler: Any) -> None:
    _member(chain, "second")
    provider = _http_auto(handler)
    try:
        assert (await provider.abrowse(TASK)).provider == "second"
    finally:
        await provider.aclose()


@pytest.mark.parametrize("handler", STOPS.values(), ids=STOPS.keys())
def test_a_real_http_failure_that_may_have_created_the_task_stops(chain: _Recorder, handler: Any) -> None:
    _member(chain, "second")
    provider = _http_auto(handler)
    try:
        with pytest.raises(ProviderError, match=PROVIDER_SKYVERN):
            provider.submit(TASK)
    finally:
        provider.close()

    assert chain.started == []


@pytest.mark.parametrize("handler", STOPS.values(), ids=STOPS.keys())
async def test_a_real_http_failure_that_may_have_created_the_task_stops_async(chain: _Recorder, handler: Any) -> None:
    _member(chain, "second")
    provider = _http_auto(handler)
    try:
        with pytest.raises(ProviderError, match=PROVIDER_SKYVERN):
            await provider.asubmit(TASK)
    finally:
        await provider.aclose()

    assert chain.started == []
