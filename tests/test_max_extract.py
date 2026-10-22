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
from web_router.types import ExtractedDocument, ExtractFormat, ExtractRequest, ExtractResponse

URL = "https://example.com"
REQUEST = ExtractRequest(urls=[URL])


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
    formats: frozenset[ExtractFormat] = frozenset({"markdown", "html", "text"}),
) -> type:
    """A fake member that answers after ``delay`` seconds, or raises ``raises`` after them."""
    from tests.conftest import FakeExtractProvider

    class Member(FakeExtractProvider):  # type: ignore[misc, valid-type]
        name = member_name
        supported_formats = formats
        default_format = sorted(formats)[0]

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            recorder.note(recorder.started, member_name)
            time.sleep(delay)
            recorder.note(recorder.finished, member_name)
            if raises is not None:
                raise raises
            return ExtractResponse(
                provider=member_name,
                documents=[ExtractedDocument(url=url, content="hi", format=self.format_of(request)) for url in urls],
                raw=[{"from": member_name}],
            )

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            recorder.note(recorder.started, member_name)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                recorder.note(recorder.cancelled, member_name)
                raise
            recorder.note(recorder.finished, member_name)
            if raises is not None:
                raise raises
            return ExtractResponse(
                provider=member_name,
                documents=[ExtractedDocument(url=url, content="hi", format=self.format_of(request)) for url in urls],
                raw=[{"from": member_name}],
            )

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
    """Members are built on the caller's thread, so concurrent races never build -- and leak -- a second one."""
    built: list[str] = []

    class Counted(_member(members, "counted", delay=0.05)):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
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


@pytest.mark.parametrize("hedge", [-1, "soon", True])
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
