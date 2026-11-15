"""Under ``auto``, a URL a later member did not really read -- a blank page, another address -- stays failed."""

from __future__ import annotations

from tests.test_auto_extract_retry import DOCS, GITHUB, _chain, _member, registered
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

__all__ = ["registered"]


def test_a_url_a_later_member_read_as_blank_stays_failed(registered: list[str]) -> None:
    class Blank(_member("second")):  # type: ignore[misc]
        def _respond(self, urls: list[str]) -> ExtractResponse:
            return ExtractResponse(provider="second", documents=[ExtractedDocument(url=url) for url in urls])

    auto = _chain(registered, _member("first", refuses=frozenset({GITHUB})), Blank)
    try:
        response = auto.extract(ExtractRequest(urls=[DOCS, GITHUB]))
    finally:
        auto.close()

    assert [document.url for document in response.documents] == [DOCS]
    assert [failure.error for failure in response.failures] == ["[first] HTTP 403; then [second] returned a blank page"]


def test_a_url_a_later_member_answered_under_another_address_stays_failed(registered: list[str]) -> None:
    class Redirected(_member("second")):  # type: ignore[misc]
        def _respond(self, urls: list[str]) -> ExtractResponse:
            return ExtractResponse(provider="second", documents=[ExtractedDocument(url="https://github.com/login")])

    auto = _chain(registered, _member("first", refuses=frozenset({GITHUB})), Redirected)
    try:
        response = auto.extract(ExtractRequest(urls=[DOCS, GITHUB]))
    finally:
        auto.close()

    assert [(failure.url, failure.error) for failure in response.failures] == [(GITHUB, "[first] HTTP 403")]
