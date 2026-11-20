"""A near-empty page, a tracking pixel or another site's page: flagged on every backend, failed over under ``auto``."""

from __future__ import annotations

import asyncio

import pytest

from tests.test_auto_extract_retry import TEXT, _chain, _member, registered
from web_router.constants_extract import MSG_EXTRACT_NEAR_EMPTY, MSG_EXTRACT_OFF_SITE, MSG_EXTRACT_TRACKER_PIXEL
from web_router.exceptions import ProviderError
from web_router.exceptions_typed import ExtractFailedError
from web_router.providers._error_pages import junk, split_error_pages
from web_router.providers.base import ExtractProvider
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

__all__ = ["registered"]

ROOMS = "https://www.airbnb.com/rooms/42"
PIXEL = "A 1x1 image, likely be a tacker probe"
SHORT = "Hello."


@pytest.mark.parametrize(
    ("content", "url", "warnings"),
    [
        (PIXEL, ROOMS, [MSG_EXTRACT_NEAR_EMPTY, MSG_EXTRACT_TRACKER_PIXEL]),
        (f"{TEXT * 2} It loads a tracking pixel. {TEXT}", ROOMS, []),
        (TEXT, "https://ad.doubleclick.net/pixel?id=1", [MSG_EXTRACT_OFF_SITE]),
        (TEXT, "https://www.airbnb.co.il/rooms/42", []),
        (TEXT, "https://news.airbnb.com/story", []),
    ],
)
def test_a_junk_page_is_told_by_its_text_and_where_it_landed(content: str, url: str, warnings: list[str]) -> None:
    assert junk(ExtractedDocument(url=url, content=content), ROOMS) == warnings


def test_a_link_shortener_landing_elsewhere_is_not_off_site() -> None:
    assert junk(ExtractedDocument(url="https://example.org/story", content=TEXT), "https://bit.ly/abc") == []


def test_every_backend_keeps_a_page_on_another_site_with_a_warning() -> None:
    kept, failures = split_error_pages([ExtractedDocument(url="https://ads.example.net/x", content=TEXT)], [ROOMS])

    assert failures == []
    assert kept[0].warnings == [MSG_EXTRACT_OFF_SITE]


def _serving(name: str, content: str, *, raises: bool = False) -> type[ExtractProvider]:
    class Serving(_member(name)):  # type: ignore[misc]
        def _respond(self, urls: list[str]) -> ExtractResponse:
            if raises:
                raise ProviderError(name, "unavailable")
            return ExtractResponse(provider=name, documents=[ExtractedDocument(url=u, content=content) for u in urls])

    return Serving


def _extract(registered: list[str], *members: type[ExtractProvider]) -> ExtractResponse:
    auto = _chain(registered, *members)
    try:
        return auto.extract(ExtractRequest(urls=[ROOMS]))
    finally:
        auto.close()


def test_auto_reads_a_near_empty_page_again_on_the_next_backend(registered: list[str]) -> None:
    response = _extract(registered, _serving("first", SHORT), _serving("second", TEXT))

    assert [document.content for document in response.documents] == [TEXT]
    assert response.failures == []


@pytest.mark.parametrize("last", [_serving("second", "Hi."), _serving("second", "", raises=True)])
def test_a_near_empty_page_no_later_backend_beats_comes_back_with_its_warning(
    registered: list[str], last: type[ExtractProvider]
) -> None:
    response = _extract(registered, _serving("first", SHORT), last)

    assert [document.warnings for document in response.documents] == [[MSG_EXTRACT_NEAR_EMPTY]]
    assert response.failures == []


def test_a_near_empty_page_from_the_only_backend_is_kept(registered: list[str]) -> None:
    response = _extract(registered, _serving("first", SHORT))

    assert [document.content for document in response.documents] == [SHORT]


def test_a_tracking_pixel_fails_over_and_stays_failed_at_the_end_of_the_chain(registered: list[str]) -> None:
    with pytest.raises(ExtractFailedError) as raised:
        _extract(registered, _serving("first", PIXEL), _serving("second", PIXEL))

    assert f"[first] {MSG_EXTRACT_TRACKER_PIXEL}; then [second] {MSG_EXTRACT_TRACKER_PIXEL}" in str(raised.value)


def test_async_auto_reads_a_tracking_pixel_url_on_the_next_backend(registered: list[str]) -> None:
    auto = _chain(registered, _serving("first", PIXEL), _serving("second", TEXT))
    try:
        response = asyncio.run(auto.aextract(ExtractRequest(urls=[ROOMS])))
    finally:
        auto.close()

    assert [document.content for document in response.documents] == [TEXT]
    assert response.failures == []
