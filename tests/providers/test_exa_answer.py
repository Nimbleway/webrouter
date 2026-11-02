"""The Exa answer provider, over a stubbed Answer API.

Verified against the live API on 2026-11-01. `POST /answer` with the query
alone answered in about 1.4 seconds with `answer` -- citing inline as
`[1][2][3]`, numbering `citations` from 1 -- `citations` of `{id, url, title,
author, publishedDate, image}`, `requestId` and `costDollars` (`{"total":
0.005}`). The payload below is trimmed from that call.

No normalized option is mapped. The reference
(https://docs.exa.ai/reference/answer) documents no domain or date filter, and
`userLocation` was accepted but showed no clear effect: an emergency-number
question for GB answered with 112 and 911 and never named 999. It stays
reachable through `provider_options`.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.providers._answer_stub import stub_answer_api
from web_router.exceptions import UnsupportedParameterError
from web_router.providers.exa import ExaAnswerProvider
from web_router.types import AnswerRequest

QUESTION = "Who won the 2022 FIFA World Cup final and what was the score?"
WIKI = "https://en.wikipedia.org/wiki/2022_FIFA_World_Cup_final"

AP = "https://apnews.com/article/lionel-messi-argentina-win-world-cup-final-against-france"
ESPN = "https://espn.com/soccer/match?gameId=633850"

ANSWERED: dict[str, Any] = {
    "requestId": "c3b0d30dcfda1690ec6727c00d84a25c",
    "answer": "Argentina won the 2022 FIFA World Cup final. The match ended in a 3-3 draw after extra time, and "
    "Argentina subsequently won 4-2 in a penalty shootout [1][2].",
    "citations": [
        {
            "id": AP,
            "title": "Messi wins World Cup, Argentina beats France on penalties | AP News",
            "url": AP,
            "publishedDate": "2022-12-18T21:02:48.000Z",
            "author": "Steve Douglas",
        },
        {"id": ESPN, "title": "Argentina 3-3 France (Dec 18, 2022) Final Score - ESPN", "url": ESPN},
        {"id": WIKI, "title": "2022 FIFA World Cup final", "url": WIKI, "publishedDate": "2022-12-18T00:00:00.000Z"},
    ],
    "costDollars": {"total": 0.005},
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ExaAnswerProvider:
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    return ExaAnswerProvider()


def test_an_answer_is_one_post_with_the_query_alone(provider: ExaAnswerProvider) -> None:
    api = stub_answer_api(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION))

    assert api.calls == [("POST", "/answer")]
    assert api.bodies == [{"query": QUESTION}], "text is not asked for, so cited pages are not fetched"
    assert api.headers[0]["x-api-key"] == "test-key"


def test_the_answer_its_citations_and_their_markers_are_normalized(provider: ExaAnswerProvider) -> None:
    stub_answer_api(provider, ANSWERED)

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.provider == "exa"
    assert response.answer == ANSWERED["answer"]
    assert response.request_id == "c3b0d30dcfda1690ec6727c00d84a25c"
    assert [c.url for c in response.citations] == [AP, ESPN, WIKI]
    assert [c.field for c in response.citations] == ["[1]", "[2]", None], "only cited positions are tied"
    ap = response.citations[0]
    assert ap.title.startswith("Messi wins World Cup")
    assert ap.snippet == ""
    assert ap.extra["author"] == "Steve Douglas"
    assert response.raw == [ANSWERED], "costDollars, Exa's stated cost, is kept"


def test_a_cited_pages_text_is_its_snippet_when_exa_returns_it(provider: ExaAnswerProvider) -> None:
    cited = {**ANSWERED["citations"][0], "text": "The final was played at Lusail Stadium on 18 December 2022."}
    stub_answer_api(provider, {**ANSWERED, "citations": [cited]})

    response = provider.answer(AnswerRequest(query=QUESTION, provider_options={"text": True}))

    assert response.citations[0].snippet.startswith("The final was played")
    assert "text" not in response.citations[0].extra


def test_a_structured_answer_is_not_read_as_prose(provider: ExaAnswerProvider) -> None:
    """With an outputSchema through provider_options, `answer` is an object; it stays in `raw`."""
    stub_answer_api(provider, {**ANSWERED, "answer": {"winner": "Argentina"}})

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.answer == ""
    assert response.raw is not None
    assert response.raw[0]["answer"] == {"winner": "Argentina"}


@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["a.com"]},
        {"country": "US"},
        {"locale": "en"},
        {"recency": "week"},
    ],
)
def test_every_option_is_refused_since_none_was_seen_to_apply(
    provider: ExaAnswerProvider, option: dict[str, Any]
) -> None:
    api = stub_answer_api(provider, ANSWERED)

    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.answer(AnswerRequest(query=QUESTION, **option))

    assert api.calls == []


async def test_the_async_path_answers_the_same_way(provider: ExaAnswerProvider) -> None:
    stub_answer_api(provider, ANSWERED)

    response = await provider.aanswer(AnswerRequest(query=QUESTION))

    assert response.citations[0].url == AP


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("EXA_API_KEY"), reason="EXA_API_KEY not set")
def test_live_answer_comes_with_its_citations() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("exa") as router, billing_aware("exa"):
        response = router.answer(QUESTION)

    assert "argentina" in (response.answer or "").lower()
    assert response.citations
    assert all(citation.url.startswith("http") for citation in response.citations)
    assert any(citation.field for citation in response.citations)
    assert all(citation.field in (response.answer or "") for citation in response.citations if citation.field)
