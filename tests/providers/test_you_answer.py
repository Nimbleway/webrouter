"""The You.com answer provider, over a stubbed Answer API.

Verified against the live API on 2026-11-01. An answer is `POST /v1/answer` on
api.you.com with `X-API-Key`, answering in about two seconds with `answer`
(markdown citing `[[1, 2]]`), `citations` -- only the sources the answer cites,
each `{source, excerpts}` -- and `results.web`, every page considered, where
each cited URL was found with its title. No request id. The payload below is
trimmed from the call made with `country: "US"`.

Each mapped option was seen to apply: `exclude_domains: ["wikipedia.org"]` kept
every citation off wikipedia.org; an emergency-number question answered 911 for
`US` and 999 for `GB`; `freshness: "week"` kept every page considered within
the last two weeks. `include_domains` and `exclude_domains` together answered
422 `mutually_exclusive`, so the pair is refused before the call. A query over
400 characters answered 422 naming the limit, which reaches the caller as a
`ProviderError` in You.com's own words. `language: "FR"` was accepted and
changed neither the answer nor its sources, so `locale` stays unmapped.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.you import YouAnswerProvider
from web_router.types import AnswerRequest, Recency

QUESTION = "What is the emergency phone number?"
KU = "https://iss.ku.edu/when-call-911"
CITIES = "https://citiesinsider.com/country/united-states/CountryWide/emergency-phone-numbers/en"
UNCITED = "https://www.apartmentguide.com/blog/emergency-phone-numbers/"


def _answered(answer: str = "The emergency phone number is 911. [[1, 2]]") -> dict[str, Any]:
    return {
        "answer": answer,
        "citations": [
            {"source": KU, "excerpts": ["The emergency phone number in the United States is 911."]},
            {
                "source": CITIES,
                "excerpts": [
                    "The primary nationwide emergency number in the United States is 911.",
                    "911 is the nationwide emergency number for urgent, immediate threats.",
                ],
            },
        ],
        "results": {
            "web": [
                {"url": UNCITED, "title": "Emergency phone numbers", "snippets": ["Call 911."]},
                {
                    "url": KU,
                    "title": "When to call 911",
                    "snippets": ["The emergency phone number in the United States is 911."],
                    "page_age": "2023-05-02T00:00:00",
                },
                {"url": CITIES, "title": "Emergency Phone Numbers in United States - Cities Insider", "snippets": []},
            ]
        },
    }


class _Api:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[tuple[str, str, str]] = []
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.host, request.url.path))
        self.bodies.append(json.loads(request.content or b"{}"))
        return httpx.Response(self.status, json=self.payload)


def _stub(provider: YouAnswerProvider, payload: dict[str, Any], status: int = 200) -> _Api:
    api = _Api(payload, status)
    transport = httpx.MockTransport(api.handle)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    provider._async = httpx.AsyncClient(**provider._client_kwargs, transport=transport)
    return api


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> YouAnswerProvider:
    monkeypatch.setenv("YOU_API_KEY", "test-key")
    return YouAnswerProvider()


def test_an_answer_is_one_post_to_the_answer_api_on_the_research_host(provider: YouAnswerProvider) -> None:
    api = _stub(provider, _answered())

    provider.answer(AnswerRequest(query=QUESTION))

    assert api.calls == [("POST", "api.you.com", "/v1/answer")]
    assert api.bodies == [{"query": QUESTION}]
    assert provider._client_kwargs["headers"]["X-API-Key"] == "test-key"


def test_citations_are_the_cited_sources_with_their_titles_and_markers(provider: YouAnswerProvider) -> None:
    _stub(provider, _answered())

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.provider == "you"
    assert response.answer == "The emergency phone number is 911. [[1, 2]]"
    assert [c.url for c in response.citations] == [KU, CITIES], "an uncited page considered is not a citation"
    ku, cities = response.citations
    assert ku.title == "When to call 911"
    assert ku.snippet == "The emergency phone number in the United States is 911."
    assert ku.field == "[[1, 2]]"
    assert ku.extra["page_age"] == "2023-05-02T00:00:00"
    assert cities.snippet.count("\n\n") == 1, "every excerpt is kept"
    assert response.request_id is None
    assert response.outcome == "complete"
    assert response.raw == [_answered()]


def test_each_citation_carries_the_first_marker_naming_it(provider: YouAnswerProvider) -> None:
    _stub(provider, _answered("911 [[2]] works everywhere [[1, 2]]."))

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert [c.field for c in response.citations] == ["[[1, 2]]", "[[2]]"]


def test_a_source_the_answer_never_marks_has_no_field(provider: YouAnswerProvider) -> None:
    _stub(provider, _answered("The emergency phone number is 911. [[1]]"))

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert [c.field for c in response.citations] == ["[[1]]", None]


def test_the_filters_map_to_youcoms_own_fields(provider: YouAnswerProvider) -> None:
    api = _stub(provider, _answered())

    provider.answer(AnswerRequest(query=QUESTION, exclude_domains=["wikipedia.org"], country="US", recency="week"))

    assert api.bodies[0] == {
        "query": QUESTION,
        "exclude_domains": ["wikipedia.org"],
        "country": "US",
        "freshness": "week",
    }


@pytest.mark.parametrize("recency", ["day", "week", "month", "year"])
def test_every_recency_is_youcoms_own_value(provider: YouAnswerProvider, recency: Recency) -> None:
    api = _stub(provider, _answered())

    provider.answer(AnswerRequest(query=QUESTION, recency=recency))

    assert api.bodies[0]["freshness"] == recency


def test_both_domain_lists_together_are_refused_before_the_call(provider: YouAnswerProvider) -> None:
    api = _stub(provider, _answered())

    with pytest.raises(UnsupportedParameterError, match="one domain list") as caught:
        provider.answer(AnswerRequest(query=QUESTION, include_domains=["a.com"], exclude_domains=["b.com"]))

    assert caught.value.parameters == ["exclude_domains", "include_domains"]
    assert api.calls == []


def test_locale_is_refused(provider: YouAnswerProvider) -> None:
    api = _stub(provider, _answered())

    with pytest.raises(UnsupportedParameterError, match="locale"):
        provider.answer(AnswerRequest(query=QUESTION, locale="fr"))

    assert api.calls == []


def test_a_query_youcom_finds_too_long_is_its_own_refusal(provider: YouAnswerProvider) -> None:
    _stub(provider, {"detail": [{"type": "string_too_long", "msg": "String should have at most 400 characters"}]}, 422)

    with pytest.raises(ProviderError, match="at most 400 characters"):
        provider.answer(AnswerRequest(query="x" * 401))


def test_an_empty_answer_is_returned(provider: YouAnswerProvider) -> None:
    _stub(provider, {"answer": "", "citations": [], "results": {"web": []}})

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert (response.answer, response.citations) == ("", [])


async def test_the_async_path_answers_the_same_way(provider: YouAnswerProvider) -> None:
    api = _stub(provider, _answered())

    response = await provider.aanswer(AnswerRequest(query=QUESTION, country="US"))

    assert [c.field for c in response.citations] == ["[[1, 2]]", "[[1, 2]]"]
    assert api.bodies[0]["country"] == "US"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("YOU_API_KEY"), reason="YOU_API_KEY not set")
def test_live_answer_ties_its_markers_to_sources() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("you") as router, billing_aware("you"):
        response = router.answer(QUESTION, country="GB")

    assert "999" in (response.answer or "")
    assert response.citations
    assert all(citation.title for citation in response.citations)
    assert all(citation.field and citation.field in (response.answer or "") for citation in response.citations)
