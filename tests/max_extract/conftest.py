"""The fakes every ``max`` test races: members that answer after a delay, fail, or answer in part.

Every member here is a fake, so a race is decided by the clock the test
controls rather than by a network.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from web_router.providers.max import MaxExtractProvider
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import ExtractedDocument, ExtractFailure, ExtractFormat, ExtractRequest, ExtractResponse

URL = "https://example.com"
URLS = [URL, "https://example.org", "https://example.net"]
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


def _timed(provider: MaxExtractProvider, request: ExtractRequest = REQUEST) -> tuple[ExtractResponse, float]:
    """One extract and how long it took, with the provider closed afterwards however the call ended."""
    try:
        started = time.perf_counter()
        response = provider.extract(request)
        return response, time.perf_counter() - started
    finally:
        provider.close()
