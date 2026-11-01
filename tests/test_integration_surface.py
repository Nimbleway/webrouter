"""Live checks that the shared surface behaves the same on every provider.

These catch cross-provider drift: a parameter one backend accepts in a form
another rejects, or a field that arrives empty on one and full on another. Each
test runs only against providers that declare the option, so a deliberately
minimal provider is exercised for what it maps and skipped for the rest.

Needs credentials; skips without them. Run with: make test-integration
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import billing_aware
from web_router import WebRouter
from web_router.constants import ANSWER_ENV_BY_PROVIDER, ENV_BY_PROVIDER, EXTRACT_ENV_BY_PROVIDER
from web_router.registry import (
    available_answer_providers,
    available_extract_providers,
    available_providers,
    get_answer_provider_class,
    get_extract_provider_class,
    get_provider_class,
)

pytestmark = pytest.mark.integration

# The table lives in constants.py, where configured_providers() reads it too.
# A None entry means the provider needs no credential, so its checks always run.

QUERY = "what is retrieval augmented generation"
"""A broad, stable query. Narrow ones occasionally come back empty from a live
index, which fails these tests for reasons that have nothing to do with the code."""

PAGE = "https://example.com"
"""A tiny, stable page that every backend can fetch without a proxy or a challenge."""


def _providers(*required: str) -> list:
    """Every provider that maps ``required``, marked to skip without its key."""
    return [
        pytest.param(
            name,
            marks=pytest.mark.skipif(bool(env_var) and not os.getenv(env_var), reason=f"{env_var} not set"),
            id=name,
        )
        for name, env_var in ENV_BY_PROVIDER.items()
        if set(required) <= get_provider_class(name).supported_parameters
    ]


def test_every_builtin_provider_is_covered_here() -> None:
    """A new provider must be added to this file, not just to the registry."""
    assert set(ENV_BY_PROVIDER) == set(available_providers())


@pytest.mark.parametrize("provider", _providers("max_results"))
def test_max_results_is_an_upper_bound(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.search(QUERY, max_results=3)

    assert len(response) <= 3
    assert response.results, f"{provider} returned nothing -- transient upstream failure?"


@pytest.mark.parametrize("provider", _providers("search_depth"))
@pytest.mark.parametrize("depth", ["lite", "fast", "deep"])
def test_every_depth_returns_content(provider: str, depth: str) -> None:
    """`content` must never be empty when results came back -- see best_text()."""
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.search(QUERY, max_results=3, search_depth=depth)

    assert response.results
    assert all(result.url for result in response.results)
    assert response.results[0].content, f"{provider} returned no content at depth={depth}"


@pytest.mark.parametrize("provider", _providers())
def test_results_always_carry_text(provider: str) -> None:
    """`content` and `description` must be populated on every provider, at default depth."""
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.search(QUERY, max_results=3)

    assert response.results
    top = response.results[0]
    assert top.content, f"{provider} returned a result with no content"
    assert top.description, f"{provider} returned a result with no description"


@pytest.mark.parametrize("provider", _providers("include_domains"))
def test_domain_filters_are_honored(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.search("coffee", max_results=5, include_domains=["wikipedia.org"])

    assert response.results
    assert any("wikipedia.org" in result.url for result in response.results)


@pytest.mark.parametrize("provider", _providers("exclude_domains"))
def test_excluded_domains_do_not_come_back(provider: str) -> None:
    """Wikipedia leads the results for this query unfiltered, which is what makes the absence mean something."""
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.search("coffee", max_results=10, exclude_domains=["wikipedia.org"])

    assert response.results
    assert not any("wikipedia.org" in result.url for result in response.results)


@pytest.mark.parametrize("provider", _providers("country"))
def test_country_is_accepted_as_an_iso_code(provider: str) -> None:
    """The shared surface is an ISO code; a provider wanting another form must translate."""
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.search("coffee", max_results=3, country="US")
    assert response.results


@pytest.mark.parametrize("provider", _providers("locale"))
def test_locale_is_accepted_as_a_language_code(provider: str) -> None:
    """The shared surface is a language code; a provider wanting another form must translate."""
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.search("coffee", max_results=3, locale="en")
    assert response.results


# --- extract ----------------------------------------------------------------


def _extractors(body_format: str | None = None) -> list:
    """Every extract provider that can return ``body_format``, marked to skip without its keys."""
    return [
        pytest.param(
            name,
            marks=pytest.mark.skipif(
                any(not os.getenv(env_var) for env_var in env_vars),
                reason=f"{' / '.join(env_vars)} not set",
            ),
            id=name,
        )
        for name, env_vars in EXTRACT_ENV_BY_PROVIDER.items()
        if body_format is None or body_format in get_extract_provider_class(name).supported_formats
    ]


def test_every_builtin_extract_provider_is_covered_here() -> None:
    """A new extract provider must be added to this file, not just to the registry."""
    assert set(EXTRACT_ENV_BY_PROVIDER) == set(available_extract_providers())


@pytest.mark.parametrize("provider", _extractors())
def test_extract_returns_content_for_a_url(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.extract(PAGE)

    assert len(response) == 1
    document = response.documents[0]
    assert document.content, f"{provider} returned a document with no content"
    assert document.url


@pytest.mark.parametrize("provider", _extractors("html"))
def test_html_really_comes_back_as_markup(provider: str) -> None:
    """`format` is what a caller parses by, so a wrong label is worse than no label."""
    with WebRouter(provider) as router, billing_aware(provider):
        document = router.extract(PAGE, format="html").documents[0]

    assert document.format == "html"
    assert "<html" in document.content.lower(), f"{provider} returned no markup for format=html"


@pytest.mark.parametrize("provider", _extractors("markdown"))
def test_markdown_really_comes_back_without_markup(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        document = router.extract(PAGE, format="markdown").documents[0]

    assert document.format == "markdown"
    assert "<html" not in document.content.lower(), f"{provider} returned HTML for format=markdown"


@pytest.mark.parametrize("provider", _extractors())
def test_several_urls_come_back_in_the_order_they_were_asked_for(provider: str) -> None:
    """Whether the backend batches or is fanned out concurrently, the order is the caller's."""
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.extract([PAGE, "https://www.iana.org/domains/reserved"])

    assert len(response) == 2, f"{provider} lost a document: {response.failures}"
    assert "example.com" in response.urls[0]
    assert "iana.org" in response.urls[1]
    assert all(document.content for document in response.documents)


@pytest.mark.parametrize("provider", _extractors())
def test_one_unfetchable_url_does_not_sink_the_others(provider: str) -> None:
    """Partial success has to look the same on a batching backend and a fanned-out one."""
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.extract([PAGE, "https://not-a-real-domain-zzz9.invalid"])

    assert response.urls, "the good URL came back empty"
    assert len(response.failures) == 1, f"{provider} did not report the bad URL: {response}"


QUESTION = "who won the 2022 FIFA World Cup final"
"""A settled fact every answer backend can find, so a wrong or empty answer is the code's fault."""


def _answerers(*required: str) -> list:
    """Every answer provider that maps ``required``, marked to skip without its keys."""
    return [
        pytest.param(
            name,
            marks=pytest.mark.skipif(
                not all(os.getenv(env_var) for env_var in env_vars), reason=f"{', '.join(env_vars)} not set"
            ),
            id=name,
        )
        for name, env_vars in ANSWER_ENV_BY_PROVIDER.items()
        if set(required) <= get_answer_provider_class(name).supported_parameters
    ]


def test_every_builtin_answer_provider_is_covered_here() -> None:
    assert set(ANSWER_ENV_BY_PROVIDER) == set(available_answer_providers())


@pytest.mark.parametrize("provider", _answerers())
def test_an_answer_comes_with_text_and_sources(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer(QUESTION)

    assert "argentina" in (response.answer or "").lower()
    assert response.citations
    assert all(citation.url.startswith("http") for citation in response.citations)


@pytest.mark.parametrize("provider", _answerers("include_domains"))
def test_an_answers_sources_honor_the_domain_filter(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer(QUESTION, include_domains=["wikipedia.org"])

    assert response.citations
    assert all("wikipedia.org" in citation.url for citation in response.citations)


@pytest.mark.parametrize("provider", _answerers("exclude_domains"))
def test_an_answers_sources_leave_out_excluded_domains(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer(QUESTION, exclude_domains=["wikipedia.org"])

    assert response.citations
    assert not any("wikipedia.org" in citation.url for citation in response.citations)


@pytest.mark.parametrize("provider", _answerers("recency"))
def test_recency_is_accepted(provider: str) -> None:
    with WebRouter(provider) as router, billing_aware(provider):
        response = router.answer("what is the latest news about the James Webb Space Telescope", recency="week")

    assert response.answer
