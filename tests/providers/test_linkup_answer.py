"""The Linkup answer provider, over a stubbed Search API.

Verified against the live API on 2026-11-01. An answer is `POST /v1/search` with
`outputType: "sourcedAnswer"` and `depth: "fast"`; it answered in 2 to 4 seconds
with `{answer, sources}` at the top level, each source `{name, url, favicon,
snippet}`, and no request id. The payload below is trimmed from the call made
with `includeDomains: ["wikipedia.org"]`, every one of whose twenty sources was
on wikipedia.org. `fromDate` was checked on a question about a topic's latest
news: a week's window moved the answer from early-October sources to
late-October ones, and a `toDate` in 2019 gave a 2019-only answer.

`includeInlineCitations` is always sent. Checked live on 2026-11-02: the answer
then ends its sentences with `[2]` or `[1][2]`, numbering `sources` from 1 --
for "What is the emergency phone number in the UK?", `[1][2]` named two guides to
UK emergency numbers -- with no other change in shape or time. `CITED` is
trimmed from that call; `ANSWERED`, recorded the day before, has no markers.

`country` and `locale` have no Linkup field and are refused.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pytest

from tests.providers._answer_stub import stub_answer_api
from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers._utils import pinned_today
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


CITED_QUESTION = "What is the emergency phone number in the UK?"

CITED: dict[str, Any] = {
    "answer": "Call **999** for an emergency in the UK. **112** also connects to emergency services.[1][2]",
    "sources": [
        {
            "name": "A Guide to Emergency Numbers in the UK - Clearview Relocation",
            "url": "https://www.clearviewrelocation.com/a-guide-to-emergency-numbers-in-the-uk/",
            "favicon": "https://favicons.linkup.so?domain=www.clearviewrelocation.com",
            "snippet": "Knowing the emergency numbers in the UK is crucial for ensuring safety and swift response.",
        },
        {
            "name": "What Is the Emergency Number in England? (And Other Need-to-Know Info)",
            "url": "https://brighttax.com/blog/what-is-the-emergency-number-in-england/",
            "favicon": "https://favicons.linkup.so?domain=brighttax.com",
            "snippet": "The two main emergency numbers are 999 and 112.",
        },
        {
            "name": "UK Phone Number Format: Complete Calling Guide",
            "url": "https://www.dialphone.com/resources/blog/uk-phone-number-format",
            "favicon": "https://favicons.linkup.so?domain=www.dialphone.com",
            "snippet": "Learn the UK phone number format, country code +44, area codes, and how to dial from the US.",
        },
    ],
}
"""Trimmed from the call made on 2026-11-02 with ``includeInlineCitations``: the first three of twenty sources."""


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> LinkupAnswerProvider:
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    return LinkupAnswerProvider()


def test_an_answer_is_a_sourced_answer_search(provider: LinkupAnswerProvider) -> None:
    api = stub_answer_api(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION))

    assert api.calls == [("POST", "/v1/search")]
    assert api.bodies == [
        {"q": QUESTION, "depth": "fast", "outputType": "sourcedAnswer", "includeInlineCitations": True}
    ]
    assert api.headers[0]["authorization"] == "Bearer test-key"


def test_the_answer_and_every_source_are_normalized(provider: LinkupAnswerProvider) -> None:
    stub_answer_api(provider, ANSWERED)

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.provider == "linkup"
    assert response.answer == ANSWERED["answer"]
    assert [c.url for c in response.citations] == [s["url"] for s in ANSWERED["sources"]]
    first = response.citations[0]
    assert first.title == "2022 FIFA World Cup final"
    assert first.snippet.startswith("The final match")
    assert first.extra == {"favicon": "https://favicons.linkup.so?domain=en.wikipedia.org"}
    assert [c.field for c in response.citations] == [None, None], "recorded before inline citations: no markers"
    assert response.outcome == "complete"
    assert response.request_id is None
    assert response.raw == [ANSWERED]


def test_the_domain_filters_map_as_on_search(provider: LinkupAnswerProvider) -> None:
    api = stub_answer_api(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION, include_domains=["wikipedia.org"], exclude_domains=["fandom.com"]))

    assert api.bodies[0]["includeDomains"] == ["wikipedia.org"]
    assert api.bodies[0]["excludeDomains"] == ["fandom.com"]


@pytest.mark.parametrize(
    ("recency", "since"),
    [("day", "2026-01-14"), ("week", "2026-01-08"), ("month", "2025-12-16"), ("year", "2025-01-15")],
)
def test_recency_sets_from_date_that_far_back(provider: LinkupAnswerProvider, recency: Recency, since: str) -> None:
    api = stub_answer_api(provider, ANSWERED)

    with pinned_today(date(2026, 1, 15)):
        provider.answer(AnswerRequest(query=QUESTION, recency=recency))

    assert api.bodies[0]["fromDate"] == since


@pytest.mark.parametrize("option", [{"country": "US"}, {"locale": "en"}])
def test_options_linkup_has_no_field_for_are_refused(provider: LinkupAnswerProvider, option: dict[str, Any]) -> None:
    api = stub_answer_api(provider, ANSWERED)

    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.answer(AnswerRequest(query=QUESTION, **option))

    assert api.calls == []


def test_provider_options_reach_the_api_last(provider: LinkupAnswerProvider) -> None:
    """A deeper `depth` is Linkup's own choice, reachable here."""
    api = stub_answer_api(provider, ANSWERED)

    provider.answer(AnswerRequest(query=QUESTION, provider_options={"depth": "deep"}))

    assert api.bodies[0]["depth"] == "deep"


def test_an_empty_answer_is_returned_with_its_sources(provider: LinkupAnswerProvider) -> None:
    stub_answer_api(provider, {"answer": None, "sources": ANSWERED["sources"]})

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.answer == ""
    assert len(response.citations) == 2


def test_an_http_error_is_a_provider_error(provider: LinkupAnswerProvider) -> None:
    stub_answer_api(provider, {"error": {"code": "BAD_REQUEST", "message": "depth must be one of"}}, status=400)

    with pytest.raises(ProviderError, match="400"):
        provider.answer(AnswerRequest(query=QUESTION))


async def test_the_async_path_answers_the_same_way(provider: LinkupAnswerProvider) -> None:
    api = stub_answer_api(provider, ANSWERED)

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
    assert any(citation.field for citation in response.citations)
    assert all(citation.field in (response.answer or "") for citation in response.citations if citation.field)


def test_the_sources_the_answer_marks_carry_their_marker(provider: LinkupAnswerProvider) -> None:
    stub_answer_api(provider, CITED)

    response = provider.answer(AnswerRequest(query=CITED_QUESTION))

    assert [c.field for c in response.citations] == ["[1]", "[2]", None], "a page read but never cited has none"
