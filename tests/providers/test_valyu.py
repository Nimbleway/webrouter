"""Valyu provider tests.

Mapped from Valyu's OpenAPI spec (docs.valyu.ai, version 2.3.0); no account
was available, so the live test skips and the mapping is documentation-derived.
The payload below keeps the spec's illustrative example in its documented shape.
"""

from __future__ import annotations

import os
from datetime import date

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers._clock import pinned_today
from web_router.providers.valyu import ValyuProvider
from web_router.types import SearchRequest

DOCUMENTED = {
    "success": True,
    "error": None,
    "tx_id": "tx_a1b2c3d4",
    "query": "latest developments in quantum computing",
    "results": [
        {
            "id": "https://example.com/research/source-1",
            "title": "Illustrative research source 1",
            "url": "https://example.com/research/source-1",
            "content": "Illustrative source 1 text.",
            "description": "Placeholder source text for the response format.",
            "source": "web",
            "price": 0.000625,
            "length": 27,
            "relevance_score": 0.95,
            "data_type": "unstructured",
            "source_type": "website",
            "publication_date": "2024-06-15",
        },
        {
            "id": "https://example.org/page",
            "title": "Undated page",
            "url": "https://example.org/page",
            "content": "",
            "description": None,
            "source": "web",
            "relevance_score": 0.61,
            "publication_date": "",
        },
    ],
    "results_by_source": {"web": 2},
    "total_deduction_dollars": 0.00125,
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ValyuProvider:
    monkeypatch.setenv("VALYU_API_KEY", "test-key")
    return ValyuProvider()


def test_every_mapped_option_reaches_its_field(provider: ValyuProvider) -> None:
    request = SearchRequest(
        query="quantum computing",
        max_results=7,
        include_domains=["arxiv.org"],
        exclude_domains=["reddit.com"],
        country="gb",
        recency="week",
    )
    with pinned_today(date(2026, 11, 12)):
        payload = provider._build_payload(request)

    assert payload == {
        "query": "quantum computing",
        "max_num_results": 7,
        "included_sources": ["arxiv.org"],
        "excluded_sources": ["reddit.com"],
        "country_code": "GB",
        "start_date": "2026-11-05",
        "search_type": "web",
    }


def test_a_bare_search_is_a_web_search_and_provider_options_win(provider: ValyuProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi")) == {"query": "hi", "search_type": "web"}
    request = SearchRequest(query="hi", provider_options={"search_type": "all", "response_length": "medium"})
    assert provider._build_payload(request)["search_type"] == "all"


def test_max_results_is_held_to_valyus_cap(provider: ValyuProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=50))["max_num_results"] == 20


def test_the_key_travels_in_its_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALYU_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = ValyuProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json=DOCUMENTED))
    )

    response = provider.search(SearchRequest(query="quantum"))

    assert sent[0].method == "POST" and sent[0].url.path == "/v1/search"
    assert sent[0].headers["X-API-Key"] == "test-key"
    assert len(response.results) == 2


@pytest.mark.parametrize("option", [{"locale": "en"}, {"search_depth": "lite"}, {"effort": "low"}])
def test_an_unmapped_option_is_refused_rather_than_dropped(provider: ValyuProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="hi", **option))


def test_a_country_valyu_does_not_list_is_refused(provider: ValyuProvider) -> None:
    provider.validate(SearchRequest(query="hi", country="us"))
    provider.validate(SearchRequest(query="hi", country="no"))
    with pytest.raises(UnsupportedParameterError, match="geo-targets only the countries it lists"):
        provider.validate(SearchRequest(query="hi", country="IL"))


def test_results_map_and_nothing_is_invented(provider: ValyuProvider) -> None:
    response = provider._to_response(SearchRequest(query="quantum"), DOCUMENTED)
    first, second = response.results

    assert response.provider == "valyu" and response.request_id == "tx_a1b2c3d4" and response.raw == DOCUMENTED
    assert first.url == "https://example.com/research/source-1" and first.score == 0.95
    assert first.title == "Illustrative research source 1"
    assert first.content == "Illustrative source 1 text."
    assert first.description == "Placeholder source text for the response format."
    assert first.published_date == "2024-06-15"
    assert first.extra["source"] == "web" and "content" not in first.extra
    # No description and no body: nothing is made up for either, and "" is no date.
    assert (second.description, second.content, second.published_date) == ("", "", None)


def test_with_no_description_the_snippet_is_the_opening_of_the_body(provider: ValyuProvider) -> None:
    body = "word " * 200
    (result,) = provider._to_response(
        SearchRequest(query="q"), {"success": True, "results": [{"url": "https://e.com", "content": body}]}
    ).results
    assert result.content == body
    assert body.startswith(result.description.rstrip("\u2026")) and len(result.description) < len(body)


def test_an_unsuccessful_answer_raises(provider: ValyuProvider) -> None:
    with pytest.raises(ProviderError, match="without success: tier_insufficient"):
        provider._to_response(SearchRequest(query="hi"), {"success": False, "error": "tier_insufficient"})


@pytest.mark.integration
def test_live_search_returns_web_results() -> None:
    if not os.environ.get("VALYU_API_KEY"):
        pytest.skip("VALYU_API_KEY not set")
    response = ValyuProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
