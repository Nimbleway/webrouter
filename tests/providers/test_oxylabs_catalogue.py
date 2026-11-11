"""Oxylabs live data: the scraper list, its pages, and each scraper described from its OPTIONS.

Shapes follow https://developers.oxylabs.io/products/web-api (endpoint
discovery, dedicated scrapers, delivery modes), read on 2026-11-10. Values are
synthetic. Not yet verified live.
"""

from __future__ import annotations

import pytest

from tests.providers._oxylabs import BASE, OPTIONS, _provider, _sent
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError

# -- catalogue ----------------------------------------------------------------


def test_the_catalogue_is_the_web_apis_own_scraper_list() -> None:
    provider, api = _provider()
    page = provider.live_data_sources()

    assert str(api.last.url) == f"{BASE}/v1/scrapers"
    assert api.last.headers["Authorization"] == "Bearer web-api-key"
    # The generic /v1/scrape is a page fetch, not a source; a batch-only listing adds no source of its own.
    assert page.names == ["amazon", "amazon/product", "google/search", "media/youtube/metadata", "chatgpt"]
    assert page.total == 5


def test_a_listed_source_says_its_site_kind_and_modes() -> None:
    provider, _ = _provider()
    by_name = {source.name: source for source in provider.live_data_sources().sources}

    product = by_name["amazon/product"]
    assert (product.domain, product.entity_type) == ("amazon", "product")
    assert product.extra == {"endpoint": "/v1/scrape/amazon/product", "modes": ["async", "batch", "sync"]}
    assert (by_name["amazon"].domain, by_name["amazon"].entity_type) == ("amazon", None)
    assert (by_name["media/youtube/metadata"].domain, by_name["media/youtube/metadata"].entity_type) == (
        "youtube",
        "metadata",
    )
    assert by_name["chatgpt"].extra["endpoint"] == "/v1/async/scrape/chatgpt"
    assert "run as a job" in by_name["chatgpt"].description


def test_the_scraper_list_is_read_once_per_provider() -> None:
    provider, api = _provider()
    provider.live_data_sources(limit=2)
    provider.live_data_sources(limit=2, offset=2)
    provider.live_data_source("amazon/product")

    assert len([r for r in _sent(api, "GET") if r.url.path == "/v1/scrapers"]) == 1


def test_a_negative_page_is_refused() -> None:
    provider, _ = _provider()
    with pytest.raises(ProviderError, match="0 or more"):
        provider.live_data_sources(offset=-1)


# -- describe -----------------------------------------------------------------


def test_a_description_is_the_endpoints_own_parameters_as_a_schema() -> None:
    provider, api = _provider()
    source = provider.live_data_source("amazon/product")

    assert str(api.last.url) == f"{BASE}/v1/scrape/amazon/product" and api.last.method == "OPTIONS"
    schema = source.input_schema
    assert schema is not None and schema["required"] == ["query"]
    properties = schema["properties"]
    assert properties["query"] == {"type": "string", "maxLength": 2048, "description": "ASIN, e.g. `B0935DN1BN`."}
    assert properties["domain"] == {
        "type": "string",
        "enum": ["com", "de", "co.uk"],
        "default": "com",
        "description": "Marketplace TLD.",
    }
    assert properties["pages"] == {"type": "integer", "minimum": 1, "maximum": 10, "description": "Pages to fetch."}
    assert properties["autoselect_variant"]["default"] is False
    assert properties["json"]["type"] == "object"
    # Oxylabs counts that min_len in characters of the serialized object: no JSON Schema keyword means that.
    assert properties["json"]["properties"]["schema"] == {
        "type": "object",
        "description": "The json schema the page is parsed into.",
    }
    assert properties["json"]["properties"]["prompt"]["maxLength"] == 8192
    assert "output" not in properties and "callback_url" not in properties, "set by the run, not by the caller"
    assert source.raw == OPTIONS


def test_an_array_parameter_is_read_from_its_one_element_list() -> None:
    from web_router.providers.oxylabs_options import parameter

    schema, required = parameter(OPTIONS["output"])
    assert schema == {
        "type": "array",
        "items": {"type": "string", "enum": ["html", "markdown", "json"]},
        "default": ["html"],
        "description": "Formats to return.",
    }
    assert required is False


def test_a_nested_array_type_is_one_fact_and_the_facts_after_it_still_count() -> None:
    from web_router.providers.oxylabs_options import input_schema

    schema = input_schema({"tags": "[array of [string]] [required] [max_len: 5]. Tags to match."})
    assert schema["properties"]["tags"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tags to match.",
    }, "a length bound on an array is not a string length, so it is left out"
    assert schema["required"] == ["tags"]


def test_an_async_only_scraper_is_described_on_its_async_path() -> None:
    provider, api = _provider()
    provider.live_data_source("chatgpt")
    assert str(api.last.url) == f"{BASE}/v1/async/scrape/chatgpt"


def test_a_source_may_be_named_by_its_path() -> None:
    provider, _ = _provider()
    assert provider.live_data_source("/v1/scrape/amazon/product").name == "amazon/product"


def test_an_unknown_source_is_not_found_without_asking_its_endpoint() -> None:
    provider, api = _provider()
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.live_data_source("amazon_product")
    assert _sent(api, "OPTIONS") == []
