"""A page that is not the one asked for -- an error status, a bot challenge, a login wall -- fails on every backend."""

from __future__ import annotations

import json
from typing import Any

import pytest

from web_router.constants_extract import (
    EXTRACT_NEAR_EMPTY_CHARS,
    MSG_EXTRACT_CHALLENGE,
    MSG_EXTRACT_LOGIN_WALL,
    MSG_EXTRACT_NEAR_EMPTY,
)
from web_router.exceptions import ProviderError
from web_router.integrations._render import render_extract
from web_router.providers._error_pages import split_error_pages
from web_router.providers.base import ExtractProvider
from web_router.providers.jina import JinaExtractProvider
from web_router.registry import available_extract_providers, get_extract_provider_class
from web_router.types import ExtractedDocument, ExtractRequest, ExtractResponse

URL = "https://example.com/page"
GONE = "https://example.com/does-not-exist-xyz"
ARTICLE = "A long article. " * 300


def _screened(**fields: Any) -> tuple[list[ExtractedDocument], list[str]]:
    kept, failures = split_error_pages([ExtractedDocument(url=fields.pop("url", URL), **fields)])
    return kept, [failure.error for failure in failures]


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"title": "Just a moment...", "content": ARTICLE}, MSG_EXTRACT_CHALLENGE),
        ({"title": "Attention Required! | Cloudflare"}, MSG_EXTRACT_CHALLENGE),
        (
            {"content": "Checking your browser before accessing example.com."},
            MSG_EXTRACT_CHALLENGE,
        ),
        ({"title": "Sign In | LinkedIn", "content": ARTICLE}, MSG_EXTRACT_LOGIN_WALL),
        ({"title": "LinkedIn Login, Sign in | LinkedIn"}, MSG_EXTRACT_LOGIN_WALL),
        (
            {"content": "Join LinkedIn. Sign in to view the full profile of this member."},
            MSG_EXTRACT_LOGIN_WALL,
        ),
        ({"url": "https://www.linkedin.com/authwall?trk=x", "content": ARTICLE}, MSG_EXTRACT_LOGIN_WALL),
        (
            {"format": "html", "content": f"<html><title>Just a moment&#8230;</title>{ARTICLE}</html>"},
            MSG_EXTRACT_CHALLENGE,
        ),
    ],
)
def test_a_challenge_or_login_page_is_a_failure_saying_which(fields: dict[str, Any], reason: str) -> None:
    kept, errors = _screened(**fields)

    assert (kept, errors) == ([], [reason])


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "Sign in with Apple: a guide", "content": ARTICLE},
        {"title": "Cloudflare challenges explained", "content": ARTICLE + "verify you are human"},
        {
            "title": "Example Domain",
            "content": "This domain is for use in illustrative examples in documents. You may use it freely.",
        },
    ],
)
def test_a_page_that_only_mentions_a_wall_is_kept(fields: dict[str, Any]) -> None:
    kept, errors = _screened(**fields)

    assert errors == []
    assert kept[0].warnings == []


def test_a_near_empty_page_is_kept_with_a_warning() -> None:
    """The 37-character shell a script fills in: kept, since some real answers are that short, but flagged."""
    kept, errors = _screened(content="x" * (EXTRACT_NEAR_EMPTY_CHARS - 27), status_code=200)

    assert errors == []
    assert kept[0].warnings == [MSG_EXTRACT_NEAR_EMPTY]
    assert split_error_pages(kept)[0][0].warnings == [MSG_EXTRACT_NEAR_EMPTY], "screening twice adds nothing"


@pytest.mark.parametrize("name", sorted(set(available_extract_providers()) - {"auto", "max"}))
def test_every_backend_answers_through_the_shared_screen(name: str) -> None:
    """The screen runs in :meth:`ExtractProvider.extract`; a backend overriding it would skip the rule."""
    cls = get_extract_provider_class(name)

    assert cls.extract is ExtractProvider.extract
    assert cls.aextract is ExtractProvider.aextract


def _jina_payload(**data: Any) -> dict[str, Any]:
    """Reader's answer, shaped as the live service returns it."""
    return {"code": 200, "status": 20000, "data": {"url": GONE, "title": "", "content": "", **data}}


class _RecordedJina(JinaExtractProvider):
    """Jina's own mapping over a recorded payload, with no request sent."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload

    def _extract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return self._to_response(request, urls, self.payload)

    async def _aextract(self, request: ExtractRequest, urls: list[str]) -> ExtractResponse:
        return self._to_response(request, urls, self.payload)


NOT_FOUND = _jina_payload(
    title="404 Not Found",
    content="# Example Domain\n\nThis page does not exist. Go back to the home page.",
    httpStatus=404,
    warning="Target URL returned error 404: Not Found",
)


def test_a_jina_404_with_text_raises_naming_the_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    provider = _RecordedJina(NOT_FOUND)
    try:
        with pytest.raises(ProviderError, match="404"):
            provider.extract(ExtractRequest(urls=[GONE]))
    finally:
        provider.close()


async def test_a_jina_challenge_page_raises_from_the_async_call_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    provider = _RecordedJina(
        _jina_payload(title="Just a moment...", content="Verifying you are human.", httpStatus=200)
    )
    try:
        with pytest.raises(ProviderError, match="bot challenge"):
            await provider.aextract(ExtractRequest(urls=[GONE]))
    finally:
        await provider.aclose()


def test_jinas_warning_is_a_typed_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    payload = _jina_payload(content=ARTICLE, httpStatus=200, warning="This page maybe not yet fully loaded")
    provider = _RecordedJina(payload)
    try:
        document = provider.extract(ExtractRequest(urls=[GONE])).documents[0]
    finally:
        provider.close()

    assert document.warnings == ["This page maybe not yet fully loaded"]
    assert "warning" not in document.extra


def test_the_rendered_extract_keeps_the_status_and_warnings() -> None:
    """What a Claude, OpenAI or LangChain tool reads back carries the status, as the MCP tool's does."""
    document = ExtractedDocument(url=URL, content="short", status_code=200, warnings=[MSG_EXTRACT_NEAR_EMPTY])

    rendered = json.loads(render_extract(ExtractResponse(provider="fake", documents=[document])))

    assert rendered["documents"][0]["status_code"] == 200
    assert rendered["documents"][0]["warnings"] == [MSG_EXTRACT_NEAR_EMPTY]
