"""The decision record when ``auto`` retries a batch's failed URLs: a member that read some is no empty hop."""

from __future__ import annotations

from tests.test_auto_extract_retry import DOCS, GITHUB, _member, registered
from web_router import AsyncWebRouter, Budget, DecisionHop, WebRouter
from web_router.providers.base import ExtractProvider
from web_router.registry import register_extract_provider
from web_router.types import ExtractedDocument, ExtractResponse

__all__ = ["registered"]


def _decision(registered: list[str], *members: type[ExtractProvider]) -> list[DecisionHop] | None:
    for member in members:
        register_extract_provider(member.name, member)
        registered.append(member.name)
    with WebRouter(provider_config={"auto": {"chain": [member.name for member in members]}}) as wr:
        response = wr.extract([DOCS, GITHUB], budget=Budget())
    assert response.provider == "first"
    return response.decision.hops


def test_a_member_that_read_a_retried_url_is_no_hop(registered: list[str]) -> None:
    hops = _decision(registered, _member("first", refuses=frozenset({GITHUB})), _member("second"))
    assert hops is None


def test_a_retry_member_that_answered_empty_is_still_a_hop(registered: list[str]) -> None:
    class Blank(_member("second")):  # type: ignore[misc]
        def _respond(self, urls: list[str]) -> ExtractResponse:
            return ExtractResponse(
                provider="second", documents=[ExtractedDocument(url=url, content="") for url in urls]
            )

    hops = _decision(registered, _member("first", refuses=frozenset({GITHUB})), Blank, _member("third"))
    assert hops == [DecisionHop(id="extract:second")]


def test_a_last_retry_member_that_answered_blank_is_still_a_hop(registered: list[str]) -> None:
    class Blank(_member("second")):  # type: ignore[misc]
        def _respond(self, urls: list[str]) -> ExtractResponse:
            blank = [ExtractedDocument(url=url, content="") for url in urls]
            return ExtractResponse(provider="second", documents=blank)

    hops = _decision(registered, _member("first", refuses=frozenset({GITHUB})), Blank)
    assert hops == [DecisionHop(id="extract:second")]


def test_with_no_retry_there_is_no_hop(registered: list[str]) -> None:
    assert _decision(registered, _member("first"), _member("second")) is None


async def test_an_async_member_that_read_a_retried_url_is_no_hop(registered: list[str]) -> None:
    for member in (_member("first", refuses=frozenset({GITHUB})), _member("second")):
        register_extract_provider(member.name, member)
        registered.append(member.name)
    async with AsyncWebRouter(provider_config={"auto": {"chain": ["first", "second"]}}) as wr:
        response = await wr.extract([DOCS, GITHUB], budget=Budget())
    assert response.decision.hops is None
