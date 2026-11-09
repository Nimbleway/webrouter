"""An empty error page is a failed fetch: the URL lands in ``failures``, and a call with nothing else raises."""

from __future__ import annotations

import pytest

from tests.conftest import FakeExtractProvider
from web_router.exceptions import ProviderError
from web_router.providers._error_pages import split_error_pages
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

GONE = "https://example.com/gone"
HERE = "https://example.com/here"


class _ErrorPages(FakeExtractProvider):
    """Answers every URL, with an empty 404 for :data:`GONE`."""

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        documents = [
            ExtractedDocument(url=url, content="" if url == GONE else "hello", status_code=404 if url == GONE else 200)
            for url in urls
        ]
        return ExtractResponse(provider=self.name, documents=documents)


@pytest.mark.parametrize(
    ("status", "content", "failed"),
    [
        (404, "", True),
        (503, "  \n", True),
        (404, "Not found, but here is why", False),
        (200, "", False),
        (None, "", False),
    ],
)
def test_only_an_empty_4xx_or_5xx_page_is_a_failure(status: int | None, content: str, failed: bool) -> None:
    kept, failures = split_error_pages([ExtractedDocument(url=GONE, content=content, status_code=status)])

    assert (len(failures), len(kept)) == ((1, 0) if failed else (0, 1))


def test_an_empty_404_beside_a_real_page_is_reported_as_failed() -> None:
    response = _ErrorPages().extract(ExtractRequest(urls=[HERE, GONE]))

    assert [document.url for document in response.documents] == [HERE]
    assert [failure.url for failure in response.failures] == [GONE]
    assert "404" in response.failures[0].error


def test_a_call_that_found_only_an_empty_404_raises_so_auto_tries_the_next_backend() -> None:
    with pytest.raises(ProviderError, match="404"):
        _ErrorPages().extract(ExtractRequest(urls=[GONE]))
