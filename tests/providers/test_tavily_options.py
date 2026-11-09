"""Tavily search: how each shared option reaches the API, and what comes back for it."""

from __future__ import annotations

import os

import httpx
import pytest

from web_router.constants import SNIPPET_LIMIT
from web_router.exceptions import UnsupportedParameterError
from web_router.providers.tavily import TavilyProvider
from web_router.types import SearchRequest

pytest.importorskip("tavily", reason="tavily extra not installed")


BODY = "word " * 300
"""1,500 characters: what ``basic`` can return as three chunks of about 500."""


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> TavilyProvider:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    return TavilyProvider()


@pytest.mark.parametrize(("depth", "cut"), [("lite", True), ("fast", False)])
def test_lite_is_cut_to_a_snippet_and_raw_keeps_the_full_text(provider: TavilyProvider, depth: str, cut: bool) -> None:
    payload = {"results": [{"url": "https://a.com", "content": BODY}]}
    response = provider._to_response(SearchRequest(query="q", search_depth=depth), payload)  # type: ignore[arg-type]
    result = response.results[0]
    for text in (result.content, result.description):
        assert (len(text) <= SNIPPET_LIMIT + 1) is cut and (text == BODY) is not cut
    assert response.raw["results"][0]["content"] == BODY


def test_every_shared_option_but_effort_is_mapped(provider: TavilyProvider) -> None:
    """Tavily's latency tiers share the search_depth field our depth uses, so effort is refused."""
    assert provider.supported_parameters == {
        "max_results",
        "search_depth",
        "include_domains",
        "exclude_domains",
        "country",
        "locale",
        "recency",
    }
    with pytest.raises(UnsupportedParameterError, match="effort"):
        provider.validate(SearchRequest(query="hi", effort="max"))


def test_filters_map_onto_tavily_fields(provider: TavilyProvider) -> None:
    request = SearchRequest(
        query="hi", include_domains=["a.com"], exclude_domains=["b.com"], country="gb", locale="fr-CA", recency="day"
    )
    assert provider._build_payload(request) == {
        "query": "hi",
        "include_domains": ["a.com"],
        "exclude_domains": ["b.com"],
        "country": "united kingdom",
        "language": "fr",
        "filter_by_language": True,
        "time_range": "day",
        "filter_by_published_date": True,
    }


@pytest.mark.parametrize("recency", ["day", "week", "month", "year"])
def test_every_recency_is_a_time_range_that_drops_undated_pages(provider: TavilyProvider, recency: str) -> None:
    params = provider._build_payload(SearchRequest(query="hi", recency=recency))
    assert params["time_range"] == recency and params["filter_by_published_date"] is True


@pytest.mark.parametrize(
    "request_",
    [
        SearchRequest(query="hi", country="XK"),
        SearchRequest(query="hi", country="US", provider_options={"topic": "news"}),
    ],
)
async def test_a_country_tavily_cannot_boost_is_refused_unsent(
    transport: httpx.MockTransport, sent: list[httpx.Request], request_: SearchRequest
) -> None:
    """Only the 166 countries it names, and only on topic general."""
    provider = TavilyProvider(api_key="test-key", client=httpx.AsyncClient(transport=transport))
    with pytest.raises(UnsupportedParameterError, match="country=") as caught:
        await provider.asearch(request_)
    assert caught.value.values == {"country": request_.country}
    assert sent == []
    assert TavilyProvider.refused_values(request_) == {"country": request_.country}


def test_a_session_goes_to_the_sync_client_and_a_client_to_the_async_one() -> None:
    """Each SDK client gets only the transport argument it takes, so neither raises."""
    import requests

    from web_router.providers.tavily_extract import TavilyExtractProvider

    session, client = requests.Session(), httpx.AsyncClient()
    for cls in (TavilyProvider, TavilyExtractProvider):
        provider = cls(api_key="test-key", session=session, client=client)
        assert provider._client.session is session
        assert provider._async_client._client is client


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
def test_live_lite_content_size_with_and_without_one_chunk() -> None:
    """Look at the lengths: lite is meant to be about 500 characters; basic returns up to three ~500 chunks.

    If chunks_per_source=1 brings lite near 500 while the default does not, it belongs in the depth table.
    """
    from web_router import WebRouter

    with WebRouter("tavily") as router:
        default = router.search("what is retrieval augmented generation", max_results=5, search_depth="lite")
        one = router.search(
            "what is retrieval augmented generation",
            max_results=5,
            search_depth="lite",
            provider_options={"chunks_per_source": 1},
        )
    assert default.results and one.results
    print({"default": [len(r.content) for r in default.results], "one_chunk": [len(r.content) for r in one.results]})


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
def test_live_country_locale_and_recency_are_accepted() -> None:
    """Tavily answers 400 for a country it does not name, or filter_by_language without a language."""
    from web_router import WebRouter

    with WebRouter("tavily") as router:
        response = router.search("actualites", max_results=3, country="FR", locale="fr", recency="month")
    assert response.results
    assert all(result.published_date for result in response.results), "filter_by_published_date drops undated"
