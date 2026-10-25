"""``max``: every extract member at once, the first document back wins.

Every member here is a fake that answers after a delay, fails, or hangs, so a
race is decided by the clock the test controls rather than by a network.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from web_router import WebRouter
from web_router.constants import BENCHMARK_EXCLUDED, PROVIDER_MAX
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.max import MaxExtractProvider
from web_router.registry import (
    available_extract_providers,
    get_extract_provider_class,
    register_extract_provider,
    unregister_extract_provider,
)
from web_router.types import ExtractedDocument, ExtractFailure, ExtractFormat, ExtractRequest, ExtractResponse

URL = "https://example.com"
REQUEST = ExtractRequest(urls=[URL])
URLS = [URL, "https://example.org", "https://example.net"]


class _Recorder:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.cancelled: list[str] = []
        self.registered: list[str] = []
        self.lock = threading.Lock()

    def note(self, which: list[str], name: str) -> None:
        with self.lock:
            which.append(name)


@pytest.fixture
def members() -> Any:
    recorder = _Recorder()
    yield recorder
    for name in recorder.registered:
        unregister_extract_provider(name)


def _member(
    recorder: _Recorder,
    member_name: str,
    *,
    delay: float = 0.0,
    raises: Exception | None = None,
    serves: int | None = None,
    formats: frozenset[ExtractFormat] = frozenset({"markdown", "html", "text"}),
) -> type:
    """A fake member that takes the whole URL list in one call and answers it after ``delay`` seconds.

    It raises ``raises`` instead when given one, and with ``serves`` it returns
    a document for that many URLs and a failure for each of the rest -- a
    partial answer, as a backend that fetched some of the list gives.
    """
    from tests.conftest import FakeExtractProvider

    class Member(FakeExtractProvider):  # type: ignore[misc, valid-type]
        name = member_name
        supported_formats = formats
        default_format = sorted(formats)[0]
        batches_urls = True

        def _answer(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            recorder.note(recorder.finished, member_name)
            if raises is not None:
                raise raises
            served = urls if serves is None else urls[:serves]
            return ExtractResponse(
                provider=member_name,
                documents=[ExtractedDocument(url=url, content="hi", format=self.format_of(request)) for url in served],
                failures=[ExtractFailure(url=url, error="HTTP 404") for url in urls[len(served) :]],
                raw=[{"from": member_name}],
            )

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            recorder.note(recorder.started, member_name)
            time.sleep(delay)
            return self._answer(request, urls)

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            recorder.note(recorder.started, member_name)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                recorder.note(recorder.cancelled, member_name)
                raise
            return self._answer(request, urls)

    register_extract_provider(member_name, Member)
    recorder.registered.append(member_name)
    return Member


def _race(*names: str, **options: Any) -> MaxExtractProvider:
    return MaxExtractProvider(chain=[(name, {}) for name in names], **options)


# -- The race ----------------------------------------------------------------


def test_the_first_document_back_wins_whatever_the_chain_order(members: _Recorder) -> None:
    _member(members, "slow", delay=0.3)
    _member(members, "fast", delay=0.0)

    provider = _race("slow", "fast")
    try:
        started = time.perf_counter()
        response = provider.extract(REQUEST)
        elapsed = time.perf_counter() - started
    finally:
        provider.close()

    assert response.provider == "fast", "the answer names who served it, never max"
    assert response.raw == [{"from": "fast"}]
    assert elapsed < 0.25, "the winner is returned without waiting for the loser"
    assert set(members.started) == {"slow", "fast"}, "without a hedge every member is started at once"


def test_a_failure_does_not_win_however_fast(members: _Recorder) -> None:
    _member(members, "fast-but-broken", raises=ProviderError("fast-but-broken", "HTTP 403"))
    _member(members, "slow", delay=0.1)

    provider = _race("fast-but-broken", "slow")
    try:
        response = provider.extract(REQUEST)
    finally:
        provider.close()

    assert response.provider == "slow"


def test_every_member_failing_names_each_ones_reason(members: _Recorder) -> None:
    _member(members, "first", raises=ProviderError("first", "HTTP 403"))
    _member(members, "second", raises=ProviderError("second", "timed out"))

    provider = _race("first", "second")
    try:
        with pytest.raises(ProviderError, match="no backend answered") as caught:
            provider.extract(REQUEST)
    finally:
        provider.close()

    assert "[first] HTTP 403" in str(caught.value) and "[second] timed out" in str(caught.value)


def test_a_member_that_cannot_be_built_is_a_failure_not_a_crash(members: _Recorder, monkeypatch: Any) -> None:
    """A real backend with no key raises ConfigurationError when built, which is that member's own loss."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _member(members, "fine", delay=0.05)

    provider = MaxExtractProvider(chain=[("fine", {}), ("exa", {})])
    try:
        response = provider.extract(REQUEST)
    finally:
        provider.close()

    assert response.provider == "fine"


def test_a_caller_bug_in_a_member_propagates_rather_than_being_raced_around(members: _Recorder) -> None:
    _member(members, "strict", raises=UnsupportedParameterError("strict", ["format"]))
    _member(members, "slow", delay=0.3)

    provider = _race("strict", "slow")
    try:
        with pytest.raises(UnsupportedParameterError):
            provider.extract(REQUEST)
    finally:
        provider.close()


def test_every_member_validates_the_request_before_anything_is_fetched(members: _Recorder) -> None:
    """A member that refuses an option says so first, so a faster member's document cannot hide it."""
    _member(members, "fast")
    Strict = _member(members, "strict", delay=0.5)
    Strict.supported_parameters = frozenset()

    provider = _race("fast", "strict")
    try:
        with pytest.raises(UnsupportedParameterError, match="format"):
            provider.extract(ExtractRequest(urls=[URL], format="markdown"))
    finally:
        provider.close()

    assert members.started == [], "nothing was fetched, so nothing was paid for"


def test_calls_are_handed_back_in_the_order_they_finished_not_the_order_they_were_waited_on() -> None:
    """``wait()`` returns a set; the race records completion order itself, so the first document really is first."""
    from web_router.providers.max import _Race

    race = _Race()
    with ThreadPoolExecutor(max_workers=2) as pool:
        race.add("late", pool.submit(lambda: time.sleep(0.05) or "late"))
        race.add("early", pool.submit(lambda: "early"))
        time.sleep(0.2)  # both finished before anyone asked
        first = race.next()
        second = race.next()

    assert first is not None and first[0] == "early"
    assert second is not None and second[0] == "late"
    assert race.next() is None, "nothing left running"


def test_the_format_is_pinned_and_a_member_that_cannot_return_it_is_left_out(members: _Recorder) -> None:
    _member(members, "text-only", formats=frozenset({"text"}))
    _member(members, "markdown", formats=frozenset({"markdown"}))

    provider = _race("text-only", "markdown")
    try:
        response = provider.extract(REQUEST)
    finally:
        provider.close()

    assert response.provider == "markdown"
    assert response.documents[0].format == "markdown"
    assert members.started == ["markdown"], "a member passed over for the format is never called"


def test_a_format_no_member_returns_raises_before_anything_is_fetched(members: _Recorder) -> None:
    _member(members, "text-only", formats=frozenset({"text"}))

    provider = _race("text-only")
    try:
        with pytest.raises(UnsupportedFormatError, match="html"):
            provider.extract(ExtractRequest(urls=[URL], format="html"))
    finally:
        provider.close()

    assert members.started == []


def test_several_urls_are_one_race_and_the_winner_serves_them_all(members: _Recorder) -> None:
    _member(members, "slow", delay=0.3)
    _member(members, "fast")

    provider = _race("slow", "fast")
    try:
        response = provider.extract(ExtractRequest(urls=[URL, "https://example.org"]))
    finally:
        provider.close()

    assert response.provider == "fast", "one race for the whole list: every document comes from the winner"
    assert response.urls == [URL, "https://example.org"]


def test_a_partial_answer_is_held_while_a_slower_member_may_still_answer_in_full(members: _Recorder) -> None:
    """Most backends take one URL per request, so the member that fetched less is usually the faster one."""
    _member(members, "partial", serves=1)
    _member(members, "complete", delay=0.2)

    provider = _race("partial", "complete")
    try:
        response = provider.extract(ExtractRequest(urls=URLS))
    finally:
        provider.close()

    assert response.provider == "complete", "a fast partial answer does not beat a complete one"
    assert response.urls == URLS and response.failures == []


def test_a_partial_answer_is_returned_once_no_member_can_do_better(members: _Recorder) -> None:
    _member(members, "partial", serves=1)
    _member(members, "broken", raises=ProviderError("broken", "HTTP 403"))

    provider = _race("partial", "broken")
    try:
        response = provider.extract(ExtractRequest(urls=URLS))
    finally:
        provider.close()

    assert response.provider == "partial"
    assert response.urls == URLS[:1] and [failure.url for failure in response.failures] == URLS[1:]


def test_of_two_partial_answers_the_one_with_fewer_failures_is_returned(members: _Recorder) -> None:
    _member(members, "one", serves=1)
    _member(members, "two", serves=2, delay=0.1)

    provider = _race("one", "two")
    try:
        response = provider.extract(ExtractRequest(urls=URLS))
    finally:
        provider.close()

    assert response.provider == "two"
    assert response.urls == URLS[:2]


def test_with_a_hedge_a_partial_first_answer_starts_the_rest_at_once(members: _Recorder) -> None:
    _member(members, "first", serves=1)
    _member(members, "second", delay=0.05)

    provider = _race("first", "second", hedge=2.0)
    try:
        started = time.perf_counter()
        response = provider.extract(ExtractRequest(urls=URLS))
        elapsed = time.perf_counter() - started
    finally:
        provider.close()

    assert response.provider == "second"
    assert elapsed < 1.0, "a partial answer within the hedge starts the rest as a failure does"


# -- The hedge ---------------------------------------------------------------


def test_with_a_hedge_a_first_member_that_answers_in_time_is_the_only_fetch(members: _Recorder) -> None:
    _member(members, "first", delay=0.0)
    _member(members, "second")

    provider = _race("first", "second", hedge=0.5)
    try:
        response = provider.extract(REQUEST)
    finally:
        provider.close()

    assert response.provider == "first"
    assert members.started == ["first"], "the rest were never started, so nothing else was paid for"


def test_with_a_hedge_a_slow_first_member_is_joined_by_the_rest_once_it_runs_out(members: _Recorder) -> None:
    _member(members, "first", delay=0.6)
    _member(members, "second", delay=0.0)

    provider = _race("first", "second", hedge=0.1)
    try:
        started = time.perf_counter()
        response = provider.extract(REQUEST)
        elapsed = time.perf_counter() - started
    finally:
        provider.close()

    assert response.provider == "second"
    assert 0.1 <= elapsed < 0.5, "the rest start when the hedge runs out, and the winner is not held for the first"
    assert members.started == ["first", "second"]


def test_with_a_hedge_a_first_member_that_fails_early_does_not_hold_the_rest(members: _Recorder) -> None:
    _member(members, "first", raises=ProviderError("first", "HTTP 403"))
    _member(members, "second")

    provider = _race("first", "second", hedge=2.0)
    try:
        started = time.perf_counter()
        response = provider.extract(REQUEST)
        elapsed = time.perf_counter() - started
    finally:
        provider.close()

    assert response.provider == "second"
    assert elapsed < 1.0, "a failure before the hedge runs out starts the rest at once"


def test_with_a_hedge_a_first_member_that_cannot_be_built_does_not_hold_the_rest(
    members: _Recorder, monkeypatch: Any
) -> None:
    """A member with no key fails at construction, before the hedge even starts; the rest must still run."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    _member(members, "second")

    provider = MaxExtractProvider(chain=[("exa", {}), ("second", {})], hedge=5.0)
    try:
        started = time.perf_counter()
        response = provider.extract(REQUEST)
        elapsed = time.perf_counter() - started
    finally:
        provider.close()

    assert response.provider == "second"
    assert elapsed < 1.0


def test_two_races_at_once_share_one_instance_of_each_member(members: _Recorder) -> None:
    """Callers on their own threads build a member once: a slow constructor widens the window the lock closes."""
    built: list[str] = []

    class Counted(_member(members, "counted", delay=0.05)):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            time.sleep(0.1)  # long enough for every caller to be inside the cache miss without a lock
            built.append("counted")
            super().__init__(**kwargs)

    register_extract_provider("counted", Counted)
    provider = _race("counted")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: provider.extract(REQUEST), range(4)))
    finally:
        provider.close()

    assert all(response.provider == "counted" for response in responses)
    assert built == ["counted"]


@pytest.mark.parametrize("hedge", [-1, "soon", True, float("inf"), float("nan")])
def test_a_hedge_that_is_not_a_non_negative_number_is_refused(hedge: Any) -> None:
    with pytest.raises(ConfigurationError, match="hedge"):
        MaxExtractProvider(chain=[("jina", {})], hedge=hedge)


def test_a_zero_hedge_means_no_hedge() -> None:
    assert MaxExtractProvider(chain=[("jina", {})], hedge=0).hedge is None


# -- What happens to the losers ----------------------------------------------


def test_close_waits_for_a_loser_still_running_rather_than_closing_under_it(members: _Recorder) -> None:
    _member(members, "slow", delay=0.3)
    _member(members, "fast")

    provider = _race("slow", "fast")
    response = provider.extract(REQUEST)
    assert response.provider == "fast"
    assert "slow" not in members.finished, "the loser is still running when the winner is returned"

    provider.close()

    assert "slow" in members.finished, "close() waited for it"


def test_close_waits_for_a_race_still_running_on_another_thread(members: _Recorder) -> None:
    """Not a snapshot of losers: a call in flight when close() starts is waited for too."""
    _member(members, "slow", delay=0.3)
    provider = _race("slow")
    outcome: list[ExtractResponse] = []

    racer = threading.Thread(target=lambda: outcome.append(provider.extract(REQUEST)))
    racer.start()
    time.sleep(0.05)  # the race has submitted its call and is waiting on it
    provider.close()

    assert "slow" in members.finished, "close() returned only once the live call had finished"
    racer.join()
    assert outcome[0].provider == "slow"


def test_close_waits_for_a_race_that_has_not_submitted_its_first_call_yet(members: _Recorder) -> None:
    """A race still building its members has nothing in flight; close() must wait for it all the same."""
    closed_before_use: list[bool] = []

    class Slow(_member(members, "slow-to-build")):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            time.sleep(0.2)
            super().__init__(**kwargs)

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            closed_before_use.append(self.closed)
            return super()._extract(request, urls)

    register_extract_provider("slow-to-build", Slow)
    provider = _race("slow-to-build")
    outcome: list[ExtractResponse] = []
    racer = threading.Thread(target=lambda: outcome.append(provider.extract(REQUEST)))
    racer.start()
    time.sleep(0.05)  # the race is inside the member's constructor: active, nothing in flight

    provider.close()
    racer.join()

    assert outcome[0].provider == "slow-to-build"
    assert closed_before_use == [False], "the member was used before close() closed it"


def test_many_quick_races_never_hang_on_a_call_that_finished_before_it_was_registered(members: _Recorder) -> None:
    """A done callback registered on a finished future runs inline; it must not need a lock the caller holds."""
    _member(members, "instant")
    provider = _race("instant")
    done = threading.Event()

    def run() -> None:
        for _ in range(50):
            provider.extract(REQUEST)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=10), "a race deadlocked"
    provider.close()


async def test_the_async_race_cancels_the_losers(members: _Recorder) -> None:
    _member(members, "slow", delay=1.0)
    _member(members, "fast")

    provider = _race("slow", "fast")
    try:
        response = await provider.aextract(REQUEST)
    finally:
        await provider.aclose()

    assert response.provider == "fast"
    assert members.cancelled == ["slow"]


async def test_the_async_race_holds_a_partial_answer_too(members: _Recorder) -> None:
    _member(members, "partial", serves=1)
    _member(members, "complete", delay=0.2)

    provider = _race("partial", "complete")
    try:
        response = await provider.aextract(ExtractRequest(urls=URLS))
    finally:
        await provider.aclose()

    assert response.provider == "complete"
    assert response.urls == URLS and response.failures == []


async def test_the_async_hedge_works_the_same_way(members: _Recorder) -> None:
    _member(members, "first", delay=0.6)
    _member(members, "second")

    provider = _race("first", "second", hedge=0.1)
    try:
        response = await provider.aextract(REQUEST)
    finally:
        await provider.aclose()

    assert response.provider == "second"
    assert members.started == ["first", "second"]
    assert members.cancelled == ["first"]


async def test_the_async_race_names_each_reason_when_nothing_answers(members: _Recorder) -> None:
    _member(members, "first", raises=ProviderError("first", "HTTP 403"))
    _member(members, "second", raises=ProviderError("second", "timed out"))

    provider = _race("first", "second")
    try:
        with pytest.raises(ProviderError, match="no backend answered") as caught:
            await provider.aextract(REQUEST)
    finally:
        await provider.aclose()

    assert "[first] HTTP 403" in str(caught.value) and "[second] timed out" in str(caught.value)


# -- Where it sits in the package -------------------------------------------


def test_max_is_an_extract_backend_and_nothing_else() -> None:
    assert PROVIDER_MAX in available_extract_providers()
    assert get_extract_provider_class(PROVIDER_MAX) is MaxExtractProvider
    assert PROVIDER_MAX in BENCHMARK_EXCLUDED, "a row for max would repeat one of the others"


def test_max_races_exactly_the_members_auto_would_try(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBLE_API_KEY", "x")

    assert MaxExtractProvider().members == AutoExtractProvider().members


def test_the_router_hands_max_the_members_config_as_it_does_auto(members: _Recorder) -> None:
    seen: dict[str, Any] = {}

    class Member(_member(members, "configured")):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            super().__init__(**kwargs)

    register_extract_provider("configured", Member)
    router = WebRouter(
        PROVIDER_MAX,
        provider_config={"configured": {"api_key": "k-123"}, PROVIDER_MAX: {"chain": ["configured"], "hedge": 0.2}},
    )
    try:
        response = router.extract(URL)
    finally:
        router.close()

    assert response.provider == "configured"
    assert seen["api_key"] == "k-123"
    assert router.get_extract_provider().hedge == 0.2  # type: ignore[attr-defined]


def test_a_chain_set_under_auto_narrows_max_too(members: _Recorder) -> None:
    """Someone who took a paid backend out of auto's chain must not keep paying for it under max."""
    _member(members, "kept")

    router = WebRouter(PROVIDER_MAX, provider_config={"auto": {"chain": {"extract": ["kept"]}}})
    try:
        assert router.get_extract_provider().members == ("kept",)  # type: ignore[attr-defined]
        assert router.get_extract_provider("auto").members == ("kept",)  # type: ignore[attr-defined]
    finally:
        router.close()


def test_a_chain_set_under_max_wins_over_the_one_under_auto(members: _Recorder) -> None:
    _member(members, "for-auto")
    _member(members, "for-max")

    router = WebRouter(
        PROVIDER_MAX,
        provider_config={"auto": {"chain": ["for-auto"]}, PROVIDER_MAX: {"chain": {"extract": ["for-max"]}}},
    )
    try:
        assert router.get_extract_provider().members == ("for-max",)  # type: ignore[attr-defined]
        assert router.get_extract_provider("auto").members == ("for-auto",)  # type: ignore[attr-defined]
    finally:
        router.close()


def test_a_members_config_set_under_auto_reaches_max_s_members(members: _Recorder) -> None:
    seen: dict[str, Any] = {}

    class Member(_member(members, "configured")):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            super().__init__(**kwargs)

    register_extract_provider("configured", Member)
    router = WebRouter(
        PROVIDER_MAX,
        provider_config={"auto": {"chain": ["configured"], "members_config": {"configured": {"api_key": "k-456"}}}},
    )
    try:
        assert router.extract(URL).provider == "configured"
    finally:
        router.close()

    assert seen["api_key"] == "k-456"


def test_a_sync_router_refuses_an_async_client_in_max_s_chain() -> None:
    """Through max the call would quietly lose that member, as it would through auto; say so at construction instead."""
    with pytest.raises(ConfigurationError, match="nimble"):
        WebRouter(PROVIDER_MAX, provider_config={PROVIDER_MAX: {"chain": [("nimble", {"async_client": object()})]}})


@pytest.mark.integration
def test_live_max_races_the_keyless_tier_with_no_key_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Needs no credential: with none, the race is between the keyless backends."""
    from web_router.constants import EXTRACT_ENV_BY_PROVIDER

    for env_vars in EXTRACT_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)

    with WebRouter(PROVIDER_MAX) as router:
        members = router.get_extract_provider().members  # type: ignore[attr-defined]
        response = router.extract("https://example.com", format="markdown")

    assert response.provider in members and response.provider != PROVIDER_MAX
    assert response.documents[0].content
