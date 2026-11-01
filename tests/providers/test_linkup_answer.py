"""The Linkup answer provider, over a stubbed Search API.

Verified against the live API on 2026-11-01. An answer is `POST /v1/search` with
`outputType: "sourcedAnswer"` and `depth: "fast"`; it answered in 2 to 4 seconds
with `{answer, sources}` at the top level, each source `{name, url, favicon,
snippet}`, and no request id. The payload below is trimmed from the call made
with `includeDomains: ["wikipedia.org"]`, every one of whose twenty sources was
on wikipedia.org. `fromDate` was checked on a question about a topic's latest
news: a week's window moved the answer from early-October sources to
late-October ones, and a `toDate` in 2019 gave a 2019-only answer.

`country` and `locale` have no Linkup field and are refused. The answer carries
no markers, so no citation is tied to a sentence.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.linkup import LinkupAnswerProvider
from web_router.types import AnswerRequest, Recency

QUESTION = "Who won the 2022 FIFA World Cup final and what was the score?"

ANSWERED: dict[str, Any] = {
    "answer": "Argentina won the 2022 FIFA World Cup final, drawing 3-3 with France before winning the "
    "penalty shootout 4-2.",
    "sources": [
        {
            "name": "2022 FIFA World Cup final",
            "url": "https://en.wikipedia.org/wiki/2022_FIFA_World_Cup_final",
            "favicon": "https://favicons.linkup.so?domain=en.wikipedia.org",
            "snippet": "The final match of the 2022 FIFA World Cup was played at Lusail Stadium in Lusail, Qatar, "
            "on 18 December 2022, and was contested by Argentina and defending champions France.",
        },
        {
            "name": "2022 FIFA World Cup",
            "url": "https://en.wikipedia.org/wiki/2022_FIFA_World_Cup",
            "favicon": "https://favicons.linkup.so?domain=en.wikipedia.org",
            "snippet": "Argentina won the tournament, beating France 4-2 on penalties after a 3-3 draw.",
        },
    ],
}


class _Api:
    """Stands in for the Search API: answers one payload and records every call."""

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.bodies.append(json.loads(request.content or b"{}"))
        self.headers.append(request.headers)
        return httpx.Response(self.status, json=self.payload)


def _stub(provider: LinkupAnswerProvider, payload: dict[str, Any], status: int = 200) -> _Api:
    api = _Api(payload, status)
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> LinkupAnswerProvider:
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    return LinkupAnswerProvider()


def test_an_answer_is_a_sourced_answer_search(provider: LinkupAnswerProvider) -> None:
    api = _stub(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION))

    assert api.calls == [("POST", "/v1/search")]
    assert api.bodies == [{"q": QUESTION, "depth": "fast", "outputType": "sourcedAnswer"}]
    assert api.headers[0]["authorization"] == "Bearer test-key"


def test_the_answer_and_every_source_are_normalized(provider: LinkupAnswerProvider) -> None:
    _stub(provider, ANSWERED)

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.provider == "linkup"
    assert response.answer == ANSWERED["answer"]
    assert [c.url for c in response.citations] == [s["url"] for s in ANSWERED["sources"]]
    first = response.citations[0]
    assert first.title == "2022 FIFA World Cup final"
    assert first.snippet.startswith("The final match")
    assert first.extra == {"favicon": "https://favicons.linkup.so?domain=en.wikipedia.org"}
    assert first.field is None, "Linkup's answer carries no markers, so no source is tied to a sentence"
    assert response.outcome == "complete"
    assert response.request_id is None
    assert response.raw == [ANSWERED]


def test_the_domain_filters_map_as_on_search(provider: LinkupAnswerProvider) -> None:
    api = _stub(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION, include_domains=["wikipedia.org"], exclude_domains=["fandom.com"]))

    assert api.bodies[0]["includeDomains"] == ["wikipedia.org"]
    assert api.bodies[0]["excludeDomains"] == ["fandom.com"]


@pytest.mark.parametrize(("recency", "days"), [("day", 1), ("week", 7), ("month", 30), ("year", 365)])
def test_recency_sets_from_date_that_far_back(provider: LinkupAnswerProvider, recency: Recency, days: int) -> None:
    api = _stub(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION, recency=recency))

    assert api.bodies[0]["fromDate"] == (date.today() - timedelta(days=days)).isoformat()


@pytest.mark.parametrize("option", [{"country": "US"}, {"locale": "en"}])
def test_options_linkup_has_no_field_for_are_refused(provider: LinkupAnswerProvider, option: dict[str, Any]) -> None:
    api = _stub(provider, ANSWERED)

    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.answer(AnswerRequest(query=QUESTION, **option))

    assert api.calls == []


def test_provider_options_reach_the_api_last(provider: LinkupAnswerProvider) -> None:
    """A deeper `depth` is Linkup's own choice, reachable here."""
    api = _stub(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION, provider_options={"depth": "deep"}))

    assert api.bodies[0]["depth"] == "deep"


def test_an_empty_answer_is_returned_with_its_sources(provider: LinkupAnswerProvider) -> None:
    _stub(provider, {"answer": None, "sources": ANSWERED["sources"]})

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.answer == ""
    assert len(response.citations) == 2


def test_an_http_error_is_a_provider_error(provider: LinkupAnswerProvider) -> None:
    _stub(provider, {"error": {"code": "BAD_REQUEST", "message": "depth must be one of"}}, status=400)

    with pytest.raises(ProviderError, match="400"):
        provider.answer(AnswerRequest(query=QUESTION))


async def test_the_async_path_answers_the_same_way(provider: LinkupAnswerProvider) -> None:
    api = _stub(provider, ANSWERED)

    response = await provider.aanswer(AnswerRequest(query=QUESTION, include_domains=["wikipedia.org"]))

    assert response.answer == ANSWERED["answer"]
    assert api.bodies[0]["includeDomains"] == ["wikipedia.org"]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("LINKUP_API_KEY"), reason="LINKUP_API_KEY not set")
def test_live_answer_comes_with_its_sources() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("linkup") as router, billing_aware("linkup"):
        response = router.answer(QUESTION, include_domains=["wikipedia.org"], recency="year")

    assert response.provider == "linkup"
    assert response.answer
    assert response.citations
    assert all("wikipedia.org" in citation.url for citation in response.citations)
