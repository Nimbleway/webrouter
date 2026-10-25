"""The async race: the same rules, with the losers cancelled rather than waited for."""

from __future__ import annotations

import asyncio
import gc

import pytest

from tests.max_extract.conftest import REQUEST, URLS, _member, _race, _Recorder
from web_router.exceptions import ProviderError
from web_router.types import ExtractRequest


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


async def test_a_loser_that_failed_in_the_winners_tick_has_its_exception_read(members: _Recorder) -> None:
    """Both finish before the race resumes; the loser is done by cancel(), and only gathering it reads its error."""
    _member(members, "winner")
    _member(members, "loser", raises=ProviderError("loser", "HTTP 500"))
    loop = asyncio.get_running_loop()
    unretrieved: list[str] = []
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(str(context.get("message"))))

    try:
        provider = _race("winner", "loser")
        response = await provider.aextract(REQUEST)
        await provider.aclose()
        del provider
        gc.collect()
    finally:
        loop.set_exception_handler(None)

    assert response.provider == "winner"
    assert "loser" in members.finished, "the loser finished on its own; it was not cancelled"
    assert unretrieved == []


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
