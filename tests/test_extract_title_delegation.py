"""Member screening sees backend titles; only the outer call fills inferred titles."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import FakeExtractProvider
from web_router.exceptions import ProviderError, WebRouterError
from web_router.providers._delegated import is_delegated
from web_router.providers.auto import AutoExtractProvider
from web_router.providers.max import MaxExtractProvider
from web_router.registry import register_extract_provider, unregister_extract_provider
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

REQUEST = ExtractRequest(urls=["https://example.com/article"])
CONTENT = "# Sign in\n" + "An article explaining authentication interfaces. " * 10


class _Article(FakeExtractProvider):
    name = "title_article"

    def __init__(self, *, fail: bool = False, backend_title: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail, self.backend_title = fail, backend_title

    def _respond(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        if self.fail:
            assert is_delegated()
            raise ProviderError(self.name, "member failed")
        return ExtractResponse(
            provider=self.name,
            documents=[
                ExtractedDocument(url=url, title=self.backend_title, content=CONTENT, format="markdown") for url in urls
            ],
        )


@pytest.fixture(autouse=True)
def article_backend() -> Iterator[None]:
    register_extract_provider(_Article.name, _Article)
    try:
        yield
    finally:
        unregister_extract_provider(_Article.name)


@pytest.mark.parametrize("provider_type", [AutoExtractProvider, MaxExtractProvider])
@pytest.mark.parametrize("backend_title", ["", "Sign in"])
def test_delegation_keeps_inferred_titles_but_screens_backend_titles(
    provider_type: type[AutoExtractProvider], backend_title: str
) -> None:
    provider = provider_type(chain=[(_Article.name, {"backend_title": backend_title})])
    try:
        if backend_title:
            with pytest.raises(WebRouterError):
                provider.extract(REQUEST)
        else:
            document = provider.extract(REQUEST).documents[0]
            assert (document.title, document.content) == ("Sign in", CONTENT)
    finally:
        provider.close()
    assert not is_delegated()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [AutoExtractProvider, MaxExtractProvider])
@pytest.mark.parametrize("backend_title", ["", "Sign in"])
async def test_async_delegation_keeps_inferred_titles_but_screens_backend_titles(
    provider_type: type[AutoExtractProvider], backend_title: str
) -> None:
    provider = provider_type(chain=[(_Article.name, {"backend_title": backend_title})])
    try:
        if backend_title:
            with pytest.raises(WebRouterError):
                await provider.aextract(REQUEST)
        else:
            document = (await provider.aextract(REQUEST)).documents[0]
            assert (document.title, document.content) == ("Sign in", CONTENT)
    finally:
        await provider.aclose()
    assert not is_delegated()


@pytest.mark.parametrize("provider_type", [AutoExtractProvider, MaxExtractProvider])
def test_a_failed_member_does_not_leave_title_filling_deferred(provider_type: type[AutoExtractProvider]) -> None:
    provider = provider_type(chain=[(_Article.name, {"fail": True})])
    try:
        with pytest.raises(WebRouterError):
            provider.extract(REQUEST)
    finally:
        provider.close()
    assert not is_delegated()
    assert _Article().extract(REQUEST).documents[0].title == "Sign in"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [AutoExtractProvider, MaxExtractProvider])
async def test_a_failed_async_member_does_not_leave_title_filling_deferred(
    provider_type: type[AutoExtractProvider],
) -> None:
    provider = provider_type(chain=[(_Article.name, {"fail": True})])
    try:
        with pytest.raises(WebRouterError):
            await provider.aextract(REQUEST)
    finally:
        await provider.aclose()
    assert not is_delegated()
    assert (await _Article().aextract(REQUEST)).documents[0].title == "Sign in"
