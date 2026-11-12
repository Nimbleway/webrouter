"""Under ``auto``, the URLs a member failed in a batch are tried on the members after it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest

from web_router.exceptions import ProviderError
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.base import ExtractProvider
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import ExtractedDocument, ExtractFailure, ExtractRequest, ExtractResponse

DOCS = "https://docs.example.org/authors/"
GITHUB = "https://github.com/psf/requests/blob/main/AUTHORS.rst"
TEXT = "A page with enough text in it to count as the page that was asked for, not a shell."


def _member(name: str, *, refuses: frozenset[str] = frozenset(), raises: bool = False) -> type[ExtractProvider]:
    """A backend that reads every URL but ``refuses``, which it reports failed, or raises for the whole call."""

    class Member(ExtractProvider):
        supported_parameters = frozenset({"urls", "format"})
        supported_formats = frozenset({"markdown"})
        default_format = "markdown"
        batches_urls = True
        calls: ClassVar[list[list[str]]] = []

        def _respond(self, urls: list[str]) -> ExtractResponse:
            type(self).calls.append(list(urls))
            if raises:
                raise ProviderError(name, "unavailable")
            return ExtractResponse(
                provider=name,
                documents=[ExtractedDocument(url=url, content=f"{name}: {TEXT}") for url in urls if url not in refuses],
                failures=[ExtractFailure(url=url, error=f"[{name}] HTTP 403") for url in urls if url in refuses],
                raw=[{"from": name}],
            )

        def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            return self._respond(urls)

        async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
            return self._respond(urls)

    Member.name = name
    Member.calls = []
    return Member


@pytest.fixture
def registered() -> Iterator[list[str]]:
    names: list[str] = []
    yield names
    for name in names:
        unregister_extract_provider(name)


def _chain(registered: list[str], *members: type[ExtractProvider]) -> AutoExtractProvider:
    for member in members:
        register_extract_provider(member.name, member)
        registered.append(member.name)
    return AutoExtractProvider(chain=[(member.name, {}) for member in members])


def test_a_url_the_first_member_failed_is_read_by_the_next(registered: list[str]) -> None:
    first, second = _member("first", refuses=frozenset({GITHUB})), _member("second")
    auto = _chain(registered, first, second)
    try:
        response = auto.extract(ExtractRequest(urls=[DOCS, GITHUB]))
    finally:
        auto.close()

    assert [document.url for document in response.documents] == [DOCS, GITHUB]
    assert response.documents[1].content.startswith("second:")
    assert response.failures == []
    assert second.calls == [[GITHUB]], "only the failed URL goes on"
    assert response.provider == "first", "the response names the member that answered first"
    assert response.raw == [{"from": "first"}, {"from": "second"}]


async def test_the_async_call_retries_the_same_way(registered: list[str]) -> None:
    first, second = _member("first", refuses=frozenset({DOCS})), _member("second")
    auto = _chain(registered, first, second)
    try:
        response = await auto.aextract(ExtractRequest(urls=[DOCS, GITHUB]))
    finally:
        await auto.aclose()

    assert [document.url for document in response.documents] == [DOCS, GITHUB], "in the order asked for"
    assert response.failures == []


def test_a_url_every_member_failed_keeps_each_reason(registered: list[str]) -> None:
    refused = frozenset({GITHUB})
    auto = _chain(registered, _member("first", refuses=refused), _member("second", refuses=refused))
    try:
        response = auto.extract(ExtractRequest(urls=[DOCS, GITHUB]))
    finally:
        auto.close()

    assert [document.url for document in response.documents] == [DOCS]
    assert [failure.url for failure in response.failures] == [GITHUB]
    assert "[first]" in response.failures[0].error
    assert "[second]" in response.failures[0].error


def test_a_later_member_that_raises_leaves_the_first_answer_as_it_was(registered: list[str]) -> None:
    first = _member("first", refuses=frozenset({GITHUB}))
    auto = _chain(registered, first, _member("broken", raises=True), _member("third"))
    try:
        response = auto.extract(ExtractRequest(urls=[DOCS, GITHUB]))
    finally:
        auto.close()

    assert [document.url for document in response.documents] == [DOCS, GITHUB], "the broken one is passed over"
    assert response.documents[1].content.startswith("third:")


def test_with_no_member_left_the_partial_answer_is_returned(registered: list[str]) -> None:
    auto = _chain(registered, _member("only", refuses=frozenset({GITHUB})))
    try:
        response = auto.extract(ExtractRequest(urls=[DOCS, GITHUB]))
    finally:
        auto.close()

    assert [failure.url for failure in response.failures] == [GITHUB]
    assert response.failures[0].error == "[only] HTTP 403", "nothing else was tried, so nothing else to say"
