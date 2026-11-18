"""Bright Data live data: the scraper catalogue listed, paged, named and described.

Shapes follow https://docs.brightdata.com (synchronous requests, the scraper
management API, error codes). Values are synthetic; the provider was verified live on 2026-11-10.
"""

from __future__ import annotations

import httpx
import pytest

from tests.providers._brightdata_live_data import DATASET, SCRAPER, URL, _provider
from web_router.exceptions import ProviderError
from web_router.exceptions_live_data import LiveDataSourceNotFoundError


def test_the_catalogue_is_paged_here_because_bright_data_answers_with_all_of_it() -> None:
    catalogue = [SCRAPER, {"id": "gd_x", "name": "LinkedIn people profiles", "domain": "linkedin.com"}]
    provider, api = _provider(lambda request: httpx.Response(200, json=catalogue))
    page = provider.live_data_sources(limit=2, offset=1)

    assert api.last.url.path == "/datasets/v3/scrapers"
    assert page.names == ["amazon_products_by_keyword", "linkedin_people_profiles"]
    assert page.total == 3 and page.offset == 1 and page.limit == 2
    first = page.sources[0]
    assert first.domain == "amazon.com" and first.entity_type == "E-commerce"
    assert first.description == "Amazon products, discovered by keyword -- Product pages from Amazon."
    assert first.extra["dataset_id"] == DATASET and first.extra["collection_method"] == "discover_by_keyword"


def test_scrapers_that_share_a_name_are_told_apart_by_site() -> None:
    twin = {"id": "gd_fr", "name": "Amazon products", "domain": "amazon.fr"}
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER, twin]))
    assert provider.live_data_sources().names == [
        "amazon_products_amazon_com",
        "amazon_products_amazon_com_by_keyword",
        "amazon_products_amazon_fr",
    ]


def test_a_scraper_named_like_anothers_discovery_method_keeps_both() -> None:
    lookalike = {"id": "gd_Look", "name": "Amazon products by keyword", "domain": "amazon.com"}
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER, lookalike]))
    assert provider.live_data_sources().names == [
        "amazon_products",
        f"amazon_products_by_keyword_{DATASET}",
        "amazon_products_by_keyword_gd_look",
    ]


def test_a_description_names_the_fields_bright_data_lists_and_no_types() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    source = provider.live_data_source("amazon_products")

    assert api.last.url.params["dataset_id"] == DATASET
    assert source.name == "amazon_products"
    assert source.input_schema == {"type": "object", "properties": {"url": {}, "zipcode": {}}}
    assert source.output_schema == {"type": "object", "properties": {"title": {}, "final_price": {}, "rating": {}}}
    assert source.samples[0].input == {"url": URL}
    assert source.extra["collection_method"] == "collect_by_url"
    assert source.extra["collection_methods"] == ["collect_by_url", "discover_by_keyword"]


def test_a_discovery_source_is_described_by_its_own_method() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    source = provider.live_data_source("amazon_products_by_keyword")
    assert source.input_schema == {"type": "object", "properties": {"keyword": {}}}
    assert source.extra["collection_method"] == "discover_by_keyword"


def test_a_description_of_an_id_not_in_the_answer_is_not_found() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.live_data_source("gd_nope")


def test_samples_published_as_a_list_are_each_a_sample() -> None:
    method = {"sample_input": [{"url": URL}, {"url": URL + "2"}], "sample_output": [{"title": "a"}, {"title": "b"}]}
    scraper = {**SCRAPER, "scrapers": {"collect_by_url": method}}
    provider, _ = _provider(lambda request: httpx.Response(200, json=[scraper]))
    source = provider.live_data_source(DATASET)
    assert [(s.input, s.output) for s in source.samples] == [
        ({"url": URL}, {"title": "a"}),
        ({"url": URL + "2"}, {"title": "b"}),
    ]


def test_the_catalogue_keeps_the_vendor_payload_and_a_description_keeps_listing_fields() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    assert provider.live_data_sources().raw == [SCRAPER]
    assert provider.live_data_source(DATASET).extra["scraper_type"] == "pdp"


@pytest.mark.parametrize(("limit", "offset"), [(-1, None), (None, -1)])
def test_a_negative_page_is_refused(limit: int | None, offset: int | None) -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=[SCRAPER]))
    with pytest.raises(ProviderError, match="0 or more"):
        provider.live_data_sources(limit=limit, offset=offset)
