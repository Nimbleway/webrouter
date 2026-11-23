"""A near-empty page, a tracking pixel or another site's page: flagged everywhere; `auto` fails over the last two."""

from __future__ import annotations

import asyncio

import pytest

from tests.test_auto_extract_retry import TEXT, _chain, _member, registered
from web_router.constants_extract import MSG_EXTRACT_NEAR_EMPTY, MSG_EXTRACT_OFF_SITE, MSG_EXTRACT_TRACKER_PIXEL
from web_router.exceptions import ProviderError
from web_router.exceptions_typed import ExtractFailedError
from web_router.providers._error_pages import junk, off_site, split_error_pages
from web_router.providers.base import ExtractProvider
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

__all__ = ["registered"]

ROOMS = "https://www.rentals.example/rooms/42"
PIXEL = "A 1x1 image, likely be a tacker probe"
SHORT = "Hello."


@pytest.mark.parametrize(
    ("content", "url", "warnings"),
    [
        (PIXEL, ROOMS, [MSG_EXTRACT_NEAR_EMPTY, MSG_EXTRACT_TRACKER_PIXEL]),
        (f"{TEXT * 2} It loads a tracking pixel. {TEXT}", ROOMS, []),
        (TEXT, "https://ad.doubleclick.net/pixel?id=1", [MSG_EXTRACT_OFF_SITE]),
        (TEXT, "https://www.rentals.co.uk/rooms/42", []),
        (TEXT, "https://news.rentals.example/story", []),
        (TEXT, "https://blog.x.co/story", [MSG_EXTRACT_OFF_SITE]),
    ],
)
def test_a_junk_page_is_told_by_its_text_and_where_it_landed(content: str, url: str, warnings: list[str]) -> None:
    assert junk(ExtractedDocument(url=url, content=content), ROOMS) == warnings


def test_a_link_shortener_landing_elsewhere_is_not_off_site() -> None:
    assert junk(ExtractedDocument(url="https://example.org/story", content=TEXT), "https://bit.ly/abc") == []


def test_a_subdomain_of_a_short_name_under_a_country_code_is_the_same_site() -> None:
    assert junk(ExtractedDocument(url="https://blog.bar.co/story", content=TEXT), "https://www.bar.co/") == []


@pytest.mark.parametrize("reverse", [False, True])
def test_a_www_redirect_of_a_government_domain_stays_on_site(reverse: bool) -> None:
    asked, landed = "https://gov.uk/browse", "https://www.gov.uk/browse"
    assert not off_site(landed, asked) if reverse else not off_site(asked, landed)


def test_every_backend_keeps_a_page_on_another_site_with_a_warning() -> None:
    kept, failures = split_error_pages([ExtractedDocument(url="https://ads.example.net/x", content=TEXT)], [ROOMS])

    assert failures == []
    assert kept[0].warnings == [MSG_EXTRACT_OFF_SITE]


def _serving(
    name: str, content: str, *, raises: bool = False, landed: str | None = None, status_code: int | None = None
) -> type[ExtractProvider]:
    class Serving(_member(name)):  # type: ignore[misc]
        def _respond(self, urls: list[str]) -> ExtractResponse:
            type(self).calls.append(list(urls))
            if raises:
                raise ProviderError(name, "unavailable")
            return ExtractResponse(
                provider=name,
                documents=[ExtractedDocument(url=landed or u, content=content, status_code=status_code) for u in urls],
            )

    return Serving


def _extract(registered: list[str], *members: type[ExtractProvider]) -> ExtractResponse:
    auto = _chain(registered, *members)
    try:
        return auto.extract(ExtractRequest(urls=[ROOMS]))
    finally:
        auto.close()


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_auto_keeps_a_near_empty_page_without_asking_the_next_backend(
    registered: list[str], asynchronous: bool
) -> None:
    second = _serving("second", TEXT)
    auto = _chain(registered, _serving("first", SHORT), second)
    request = ExtractRequest(urls=[ROOMS])
    try:
        response = asyncio.run(auto.aextract(request)) if asynchronous else auto.extract(request)
    finally:
        auto.close()

    assert [(document.content, document.warnings) for document in response.documents] == [
        (SHORT, [MSG_EXTRACT_NEAR_EMPTY])
    ]
    assert response.failures == []
    assert second.calls == [], "a near-empty page is not sent on down the chain"


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
