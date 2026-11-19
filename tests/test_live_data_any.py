"""Live data without naming a vendor: one search over every configured backend, and runs routed by their source.

The backends are the in-memory ``fake`` and ``paged`` of
``tests/_live_data_any_fixtures.py``. How their answers are ordered for a
query is ``tests/test_live_data_any_ranking.py``; a backend that fails,
``tests/test_live_data_any_failures.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests._live_data_any_fixtures import no_vendor_keys_fixture, two_backends_fixture  # noqa: F401  (fixtures)
from tests._live_data_fake import FakeLiveDataProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.constants_live_data import LIVE_DATA_ENV_BY_PROVIDER
from web_router.exceptions import ConfigurationError, LiveDataBackendChoiceError, ProviderError
from web_router.live_data_backends import usable_backends

# -- finding a source ---------------------------------------------------------


def test_no_provider_merges_every_backend_taking_each_in_turn(two_backends: None) -> None:
    page = WebRouter().live_data_sources(limit=4)

    assert page.provider == "auto"
    # fake's first, paged's first, fake's second, paged's second: no backend crowds out the other.
    assert [(source.provider, source.name) for source in page.sources] == [
        ("fake", "books_pdp"),
        ("paged", "shop_reviews"),
        ("fake", "jobs_listing"),
        ("paged", "filler_0"),
    ]
    assert page.failures == {}
    assert set(page.raw) == {"fake", "paged"}


def test_a_query_is_searched_on_every_backend(two_backends: None) -> None:
    page = WebRouter().live_data_sources(query="amazon")

    assert {source.provider for source in page.sources} == {"paged"}
    assert page.names[:2] == ["amazon_product", "amazon_search"]
    assert page.query == "amazon" and page.search == "local"


def test_limit_and_offset_page_the_merged_list(two_backends: None) -> None:
    first = WebRouter().live_data_sources(limit=2)
    second = WebRouter().live_data_sources(limit=2, offset=2)

    assert first.names == ["books_pdp", "shop_reviews"]
    assert second.names == ["jobs_listing", "filler_0"]
    assert second.offset == 2 and second.limit == 2


def test_no_backend_at_all_names_the_variables_to_set() -> None:
    with pytest.raises(ConfigurationError) as caught:
        WebRouter().live_data_sources(query="amazon")

    for env_vars in LIVE_DATA_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            assert env_var in str(caught.value)


def test_a_named_provider_still_lists_only_its_own(two_backends: None) -> None:
    page = WebRouter().live_data_sources(provider="paged", limit=2)

    assert page.provider == "paged" and page.failures == {}


def test_a_builtin_backend_counts_with_a_key_in_the_environment_or_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    assert [backend.name for backend in usable_backends(WebRouter())] == []

    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    assert [backend.name for backend in usable_backends(WebRouter())] == ["apify"]

    monkeypatch.delenv("APIFY_TOKEN")
    router = WebRouter(provider_config={"oxylabs": {"web_api_key": "test-key"}})
    assert [backend.name for backend in usable_backends(router)] == ["oxylabs"]


# -- running and describing a source ------------------------------------------


def test_a_listed_source_runs_on_its_own_backend_without_naming_it(two_backends: None) -> None:
    router = WebRouter()
    source = router.live_data_sources(query="books").sources[0]

    result = router.live_data(source, {"url": "https://example.com/p/1"})

    assert result.provider == "fake" and result.source == "books_pdp"
    assert result.inputs == {"url": "https://example.com/p/1"}


def test_a_listed_source_is_described_on_its_own_backend(two_backends: None) -> None:
    router = WebRouter()
    source = router.live_data_sources(query="books").sources[0]

    described = router.live_data_source(source)

    assert described.provider == "fake" and described.input_schema is not None


def test_a_provider_that_contradicts_the_source_is_refused(two_backends: None) -> None:
    router = WebRouter()
    source = router.live_data_sources(query="books").sources[0]

    with pytest.raises(ConfigurationError, match="'fake'"):
        router.live_data(source, {"url": "https://example.com/p/1"}, provider="paged")


def test_a_bare_name_runs_when_one_backend_is_configured(fake_live_data_provider: Any) -> None:
    result = WebRouter().live_data("books_pdp", {"url": "https://example.com/p/1"})

    assert result.provider == "fake"


def test_a_bare_name_with_several_backends_asks_which(two_backends: None) -> None:
    with pytest.raises(LiveDataBackendChoiceError, match="fake, paged") as caught:
        WebRouter().live_data("books_pdp", {"url": "https://example.com/p/1"})
    assert isinstance(caught.value, ConfigurationError)  # an except for that still catches it
    assert caught.value.providers == ["fake", "paged"]


def test_a_bare_name_with_its_provider_works_as_before(two_backends: None) -> None:
    result = WebRouter().live_data("books_pdp", {"url": "https://example.com/p/1"}, provider="fake")

    assert result.provider == "fake"


# -- async ---------------------------------------------------------------------


def test_the_async_router_merges_and_runs_the_same_way(two_backends: None) -> None:
    async def scenario() -> tuple[list[tuple[str, str]], str]:
        router = AsyncWebRouter()
        page = await router.live_data_sources(limit=2)
        result = await router.live_data(page.sources[0], {"url": "https://example.com/p/1"})
        await router.aclose()
        return [(source.provider, source.name) for source in page.sources], result.provider

    merged, provider = asyncio.run(scenario())

    assert merged == [("fake", "books_pdp"), ("paged", "shop_reviews")]
    assert provider == "fake"


def test_the_async_router_names_a_failing_backend(two_backends: None) -> None:
    FakeLiveDataProvider.list_raises = ProviderError("fake", "HTTP 503")

    async def scenario() -> dict[str, str]:
        router = AsyncWebRouter()
        page = await router.live_data_sources(limit=2)
        await router.aclose()
        return page.failures

    assert "fake" in asyncio.run(scenario())
