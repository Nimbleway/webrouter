"""The Perplexity answer provider, over a stubbed Agent API.

Verified against the live API on 2026-11-01. An answer is `POST /v1/responses`
with `effort: "fast"` and no `background`: it answered `completed` in about two
seconds, with a `search_results` output item whose results carry an integer
`id`, then a `message` citing them inline as `[2]` or `[3][10]`; `annotations`
was always empty. `effort` and `preset` were both accepted, both echoed as
`fast@2026-10-16.0`. The payload below is trimmed from the call made with
`user_location.country: "GB"`.

The filters go to the `web_search` tool and were each seen to apply:
`search_domain_filter` `["wikipedia.org"]` kept every result on wikipedia.org,
and `["-wikipedia.org"]` kept every one off it. Both at once -- which the docs
say a list cannot hold -- answered 200 and kept to wikipedia.org, which shows
nothing about the excluded domain, so the pair is refused before the call;
`search_recency_filter: "year"` kept every result dated within the year; and an
emergency-number question answered 999 for `GB` and 911 for `US`. `locale` has
no field and is refused.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.providers._answer_stub import stub_answer_api
from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.perplexity import PerplexityAnswerProvider
from web_router.types_answer import AnswerRequest, Recency

QUESTION = "What is the emergency phone number?"
RESPONSE_ID = "resp_107a569e-fd55-4708-8f0b-8f386355ac50"
TEXT = (
    "In the UK, call **999** or **112** for emergency services. Both are free and connect you to "
    "police, fire, or ambulance services.[3][10]"
)


def _result(result_id: int, url: str, title: str, snippet: str) -> dict[str, Any]:
    return {
        "date": "2024-03-26",
        "id": result_id,
        "last_updated": "2026-10-05",
        "snippet": snippet,
        "source": "web",
        "title": title,
        "url": url,
    }


def _response(text: str = TEXT, status: str = "completed") -> dict[str, Any]:
    return {
        "id": RESPONSE_ID,
        "object": "response",
        "status": status,
        "model": "openai/gpt-6-luna",
        "effort": "fast@2026-10-16.0",
        "output": [
            {
                "type": "search_results",
                "results": [
                    _result(
                        1,
                        "https://en.wikipedia.org/wiki/Emergency_telephone_number",
                        "Emergency telephone number",
                        "An emergency telephone number is a number that allows a caller to contact local "
                        "emergency services.",
                    ),
                    _result(
                        3,
                        "https://en.wikipedia.org/wiki/999_(emergency_telephone_number)",
                        "999 (emergency telephone number)",
                        "999 is the official emergency services number for the United Kingdom.",
                    ),
                    _result(
                        10,
                        "https://www.eastamb.nhs.uk/your-service/999",
                        "999 | EEAST - East of England Ambulance Service",
                        "In a medical emergency you should stay calm and dial 999 or 112.",
                    ),
                ],
            },
            {
                "type": "message",
                "id": "msg_9a4f10fd-c911-46bd-b30e-5b20c2cfb8ff",
                "role": "assistant",
                "status": status,
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            },
        ],
        "usage": {"cost": {"currency": "USD", "total_cost": 0.00128}},
    }


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> PerplexityAnswerProvider:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    return PerplexityAnswerProvider()


def test_an_answer_is_one_response_at_the_fast_effort(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    provider.answer(AnswerRequest(query=QUESTION))

    assert api.calls == [("POST", "/v1/responses")]
    assert api.bodies == [{"input": QUESTION, "effort": "fast"}], "no background, and no tool unless a filter is set"


def test_the_answer_its_sources_and_its_markers_are_normalized(provider: PerplexityAnswerProvider) -> None:
    stub_answer_api(provider, _response())

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.provider == "perplexity"
    assert response.answer == TEXT
    assert response.request_id == RESPONSE_ID
    assert response.outcome == "complete"
    assert [c.field for c in response.citations] == [None, "[3]", "[10]"], "only sources the answer cites are tied"
    nhs = response.citations[2]
    assert nhs.url == "https://www.eastamb.nhs.uk/your-service/999"
    assert nhs.title.startswith("999 | EEAST")
    assert nhs.snippet.startswith("In a medical emergency")
    assert nhs.extra["id"] == 10
    assert nhs.extra["date"] == "2024-03-26"
    assert response.raw is not None
    assert response.raw[0]["usage"]["cost"]["total_cost"] == 0.00128


def test_the_filters_configure_the_web_search_tool(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    provider.answer(AnswerRequest(query=QUESTION, include_domains=["wikipedia.org"], recency="year", country="GB"))

    assert api.bodies[0]["tools"] == [
        {
            "type": "web_search",
            "filters": {"search_domain_filter": ["wikipedia.org"], "search_recency_filter": "year"},
            "user_location": {"country": "GB"},
        }
    ]


def test_an_excluded_domain_is_prefixed(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    provider.answer(AnswerRequest(query=QUESTION, exclude_domains=["fandom.com", "reddit.com"]))

    assert api.bodies[0]["tools"][0]["filters"] == {"search_domain_filter": ["-fandom.com", "-reddit.com"]}


def test_both_domain_lists_together_are_refused_before_the_call(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    with pytest.raises(UnsupportedParameterError, match="one domain list") as caught:
        provider.answer(AnswerRequest(query=QUESTION, include_domains=["a.com"], exclude_domains=["b.com"]))

    assert caught.value.parameters == ["exclude_domains", "include_domains"]
    assert api.calls == []


def test_a_country_alone_sets_only_the_location(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    provider.answer(AnswerRequest(query=QUESTION, country="US"))

    assert api.bodies[0]["tools"] == [{"type": "web_search", "user_location": {"country": "US"}}]


@pytest.mark.parametrize("recency", ["day", "week", "month", "year"])
def test_every_recency_is_perplexitys_own_value(provider: PerplexityAnswerProvider, recency: Recency) -> None:
    api = stub_answer_api(provider, _response())

    provider.answer(AnswerRequest(query=QUESTION, recency=recency))

    assert api.bodies[0]["tools"][0]["filters"] == {"search_recency_filter": recency}


def test_locale_has_no_field_and_is_refused(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    with pytest.raises(UnsupportedParameterError, match="locale"):
        provider.answer(AnswerRequest(query=QUESTION, locale="en"))

    assert api.calls == []


def test_provider_options_reach_the_api_last(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    provider.answer(AnswerRequest(query=QUESTION, provider_options={"effort": "low"}))

    assert api.bodies[0]["effort"] == "low"


def test_an_incomplete_response_is_partial(provider: PerplexityAnswerProvider) -> None:
    stub_answer_api(provider, _response(status="incomplete"))

    assert provider.answer(AnswerRequest(query=QUESTION)).outcome == "partial"


def test_an_empty_answer_is_returned_with_its_sources(provider: PerplexityAnswerProvider) -> None:
    stub_answer_api(provider, _response(text=""))

    response = provider.answer(AnswerRequest(query=QUESTION))

    assert response.answer == ""
    assert len(response.citations) == 3
    assert all(c.field is None for c in response.citations)


def test_an_http_error_is_a_provider_error(provider: PerplexityAnswerProvider) -> None:
    stub_answer_api(provider, {"error": {"message": "invalid effort"}}, status=400)

    with pytest.raises(ProviderError, match="400"):
        provider.answer(AnswerRequest(query=QUESTION))


async def test_the_async_path_answers_the_same_way(provider: PerplexityAnswerProvider) -> None:
    api = stub_answer_api(provider, _response())

    response = await provider.aanswer(AnswerRequest(query=QUESTION, country="GB"))

    assert response.answer == TEXT
    assert api.bodies[0]["tools"][0]["user_location"] == {"country": "GB"}


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set")
def test_live_answer_ties_its_markers_to_sources() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("perplexity") as router, billing_aware("perplexity"):
        response = router.answer(QUESTION, country="GB")

    assert response.provider == "perplexity"
    assert "999" in (response.answer or "")
    assert any(citation.field for citation in response.citations)
    assert all(citation.field in (response.answer or "") for citation in response.citations if citation.field)
