from __future__ import annotations

import os

import httpx
import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.brave import BraveProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> BraveProvider:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    return BraveProvider()


def test_query_maps_onto_braves_q_parameter(provider: BraveProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi")) == {"q": "hi"}


def test_max_results_maps_onto_count(provider: BraveProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5)) == {"q": "hi", "count": 5}


def test_lite_sends_nothing_and_longer_depths_are_refused_unsent(
    provider: BraveProvider, transport: httpx.MockTransport, sent: list[httpx.Request]
) -> None:
    """The endpoint returns a description and a few snippets, never a body, so lite is what it always does."""
    assert provider._build_payload(SearchRequest(query="hi", search_depth="lite")) == {"q": "hi"}
    provider = BraveProvider(transport=transport)
    for depth in ("fast", "deep"):
        with pytest.raises(UnsupportedParameterError, match=rf"search_depth='{depth}'.*'lite'"):
            provider.search(SearchRequest(query="hi", search_depth=depth))
    assert not sent


def test_filters_map_onto_brave_fields_and_operators(provider: BraveProvider) -> None:
    request = SearchRequest(
        query="hi",
        include_domains=["a.com", "b.org"],
        exclude_domains=["c.net"],
        country="gr",
        locale="pt-BR",
        recency="month",
    )
    assert provider._build_payload(request) == {
        "q": "hi site:a.com OR site:b.org NOT site:c.net",
        "country": "GR",
        "search_lang": "pt-br",
        "freshness": "pm",
    }


@pytest.mark.parametrize(("recency", "freshness"), [("day", "pd"), ("week", "pw"), ("month", "pm"), ("year", "py")])
def test_every_recency_is_a_freshness_value(provider: BraveProvider, recency: str, freshness: str) -> None:
    assert provider._build_payload(SearchRequest(query="hi", recency=recency))["freshness"] == freshness


@pytest.mark.parametrize(("option", "value"), [("country", "IL"), ("locale", "zh"), ("locale", "xx")])
def test_a_value_off_the_lists_is_refused_unsent(
    provider: BraveProvider, transport: httpx.MockTransport, sent: list[httpx.Request], option: str, value: str
) -> None:
    provider = BraveProvider(transport=transport)
    with pytest.raises(UnsupportedParameterError, match=rf"{option}='{value}'"):
        provider.search(SearchRequest(query="hi", **{option: value}))
    assert BraveProvider.refused_values(SearchRequest(query="hi", **{option: value})) == {option: value}
    assert not sent


@pytest.mark.parametrize("depth", [None, "lite"])
def test_lite_cuts_the_description_and_extra_snippets_to_a_snippet(provider: BraveProvider, depth: str | None) -> None:
    """Joined, they ran to 2,000 characters live; Brave has no size setting, so lite is cut here."""
    hit = {"url": "https://a.com", "description": "a <strong>b</strong>", "extra_snippets": ["word " * 100] * 3}
    payload = {"web": {"results": [hit]}}
    result = provider._to_response(SearchRequest(query="hi", search_depth=depth), payload).results[0]  # type: ignore[arg-type]
    assert result.description == "a b"
    assert result.content.startswith("a b\n\n")
    assert len(result.content) <= SNIPPET_LIMIT + 1 if depth == "lite" else len(result.content) > 1500


def test_effort_is_refused(provider: BraveProvider) -> None:
    with pytest.raises(UnsupportedParameterError, match="effort"):
        provider.validate(SearchRequest(query="hi", effort="low"))


@pytest.mark.parametrize(
    "request_",
    [
        SearchRequest(query="x" * 590, include_domains=["example.com"]),
        SearchRequest(query=" ".join(["w"] * 74), exclude_domains=["a.com", "b.com"]),
    ],
)
def test_a_query_past_braves_limits_is_refused_never_cut(provider: BraveProvider, request_: SearchRequest) -> None:
    """600 characters and 75 words, operators included."""
    with pytest.raises(ProviderError, match="at most 600 characters and 75 words"):
        provider._build_payload(request_)


def test_highlight_markup_is_stripped(provider: BraveProvider) -> None:
    """Brave wraps query terms in <strong>; that is its markup, not page content."""
    payload = {
        "web": {
            "results": [
                {
                    "title": "What is <strong>RAG</strong>",
                    "url": "https://example.com",
                    "description": "<strong>RAG</strong> is a technique",
                    "extra_snippets": ["more about <strong>RAG</strong>"],
                    "page_age": "2026-01-01T00:00:00",
                    "age": "4 weeks ago",
                }
            ]
        }
    }

    result = provider._to_response(SearchRequest(query="hi"), payload).results[0]

    assert result.title == "What is RAG"
    assert result.description == "RAG is a technique"
    assert result.content == "RAG is a technique\n\nmore about RAG"
    assert result.published_date == "2026-01-01T00:00:00"
    assert result.extra["age"] == "4 weeks ago"


def test_a_response_with_no_web_section_is_empty_not_an_error(provider: BraveProvider) -> None:
    assert provider._to_response(SearchRequest(query="hi"), {"query": {}}).results == []


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BRAVE_SEARCH_API_KEY"), reason="BRAVE_SEARCH_API_KEY not set")
def test_live_search_returns_results() -> None:
    from web_router import WebRouter

    with WebRouter("brave") as router:
        response = router.search("nimbleway", max_results=3)

    assert response.provider == "brave"
    assert response.results
    assert all(result.url for result in response.results)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BRAVE_SEARCH_API_KEY"), reason="BRAVE_SEARCH_API_KEY not set")
def test_live_site_operators_keep_results_on_those_sites() -> None:
    """Look at raw query.search_operators.applied: True means every result matched the operators exactly."""
    from web_router import WebRouter

    with WebRouter("brave") as router:
        response = router.search("coffee", max_results=5, include_domains=["wikipedia.org", "britannica.com"])

    assert response.results
    assert all(any(site in r.url for site in ("wikipedia.org", "britannica.com")) for r in response.results)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("BRAVE_SEARCH_API_KEY"), reason="BRAVE_SEARCH_API_KEY not set")
def test_live_a_regional_locale_and_country_are_accepted() -> None:
    from web_router import WebRouter

    with WebRouter("brave") as router:
        response = router.search("futebol noticias", max_results=3, locale="pt-BR", country="BR")
    assert response.results
