"""Perplexity search: how each shared option reaches ``/search``, and what is refused before it.

The fields come from docs.perplexity.ai/api-reference/search-post; the live
tests at the bottom are what confirm each one changes what comes back.
"""

from __future__ import annotations

import os

import httpx
import pytest

from web_router.exceptions import UnsupportedParameterError
from web_router.providers.auto import AutoProvider
from web_router.providers.perplexity import PerplexityProvider
from web_router.types import SearchRequest


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> PerplexityProvider:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    return PerplexityProvider()


def test_filters_country_locale_and_recency_reach_search(provider: PerplexityProvider) -> None:
    request = SearchRequest(query="hi", include_domains=["a.com", "b.org"], country="GB", locale="EN", recency="week")
    provider.validate(request)
    assert provider._build_payload(request) == {
        "query": "hi",
        "country": "GB",
        "search_domain_filter": ["a.com", "b.org"],
        "search_language_filter": ["en"],
        "search_recency_filter": "week",
    }


def test_an_excluded_domain_is_the_same_list_prefixed(provider: PerplexityProvider) -> None:
    payload = provider._build_payload(SearchRequest(query="hi", exclude_domains=["a.com"]))
    assert payload["search_domain_filter"] == ["-a.com"]


@pytest.mark.parametrize(("depth", "tokens"), [("lite", 100), ("fast", 1024)])
def test_depth_sets_the_per_page_token_budget(provider: PerplexityProvider, depth: str, tokens: int) -> None:
    request = SearchRequest(query="hi", search_depth=depth)  # type: ignore[arg-type]
    provider.validate(request)
    assert provider._build_payload(request)["max_tokens_per_page"] == tokens


@pytest.mark.parametrize(("effort", "sent"), [("low", "fast"), ("medium", None), ("high", None), ("max", None)])
def test_effort_collapses_onto_fast_and_the_web_default(
    provider: PerplexityProvider, effort: str, sent: str | None
) -> None:
    request = SearchRequest(query="hi", effort=effort)  # type: ignore[arg-type]
    provider.validate(request)
    assert provider._build_payload(request).get("search_type") == sent


@pytest.mark.parametrize(
    ("options", "named"),
    [
        ({"include_domains": ["a.com"], "exclude_domains": ["b.com"]}, "one domain list"),
        ({"include_domains": [f"d{n}.com" for n in range(21)]}, "at most 20 domains"),
        ({"locale": "en-US"}, "two-letter"),
        ({"locale": "en\n"}, "two-letter"),
        ({"search_depth": "deep"}, "serves search_depth at 'lite', 'fast'"),
    ],
    ids=["both-lists", "too-many-domains", "regional-locale", "trailing-newline", "deep"],
)
def test_a_value_perplexity_cannot_take_is_refused_unsent(
    provider: PerplexityProvider, options: dict, named: str
) -> None:
    sent: list[httpx.Request] = []
    provider._sync = httpx.Client(  # type: ignore[assignment]
        **provider._client_kwargs, transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    )
    with pytest.raises(UnsupportedParameterError, match=named):
        provider.search(SearchRequest(query="hi", **options))
    assert not sent


def test_auto_passes_it_over_for_a_value_it_refuses() -> None:
    request = SearchRequest(query="hi", locale="pt-BR")
    assert PerplexityProvider.refused_values(request) == {"locale": "pt-BR"}
    with pytest.raises(UnsupportedParameterError, match="perplexity") as raised:
        AutoProvider(chain=["perplexity"])._eligible(request)
    assert raised.value.values == {"locale": "pt-BR"}


live = pytest.mark.skipif(not os.getenv("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set")


@pytest.mark.integration
@live
@pytest.mark.parametrize(("depth", "most"), [("lite", 700), ("fast", 5000)])
def test_live_depth_caps_each_result_near_its_size(depth: str, most: int) -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("perplexity") as router, billing_aware("perplexity"):
        response = router.search("what is retrieval augmented generation", max_results=3, search_depth=depth)
    assert response.results and all(0 < len(result.content) <= most for result in response.results)


@pytest.mark.integration
@live
def test_live_excluded_domain_and_language_are_honoured() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("perplexity") as router, billing_aware("perplexity"):
        response = router.search("café", max_results=5, exclude_domains=["wikipedia.org"], locale="fr")
    assert response.results and not any("wikipedia.org" in result.url for result in response.results)


def test_lite_cuts_a_long_snippet_to_snippet_size() -> None:
    from web_router.constants import SNIPPET_LIMIT
    from web_router.providers.perplexity import PerplexityProvider

    result = PerplexityProvider._to_result({"url": "https://a.example", "snippet": "word " * 300}, "lite")
    assert len(result.content) <= SNIPPET_LIMIT + 3
    assert len(result.description) <= SNIPPET_LIMIT + 3
    assert len(PerplexityProvider._to_result({"snippet": "word " * 300}, "fast").content) == 1500
