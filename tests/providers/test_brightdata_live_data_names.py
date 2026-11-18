"""Bright Data live data: which scraper and collection method a source name picks, and what options cannot change.

Shapes follow https://docs.brightdata.com (synchronous requests, the scraper
management API, error codes). Values are synthetic; the provider was verified live on 2026-11-10.
"""

from __future__ import annotations

import httpx
import pytest

from tests.providers._brightdata_live_data import DATASET, SCRAPER, URL, _by_path, _provider
from web_router.exceptions_live_data import LiveDataSourceNotFoundError
from web_router.types_live_data import LiveDataRequest


def test_a_scraper_is_run_by_its_name() -> None:
    records = [{"title": "A thing"}]
    provider, api = _provider(_by_path(records))
    response = provider.run(LiveDataRequest(source="amazon_products", inputs={"url": URL}))

    assert api.last.url.path == "/datasets/v3/scrape"
    assert dict(api.last.url.params) == {"dataset_id": DATASET, "format": "json", "include_errors": "true"}
    assert response.source == "amazon_products" and response.records == records


@pytest.mark.parametrize("name", ["Amazon products", "amazon_products", DATASET])
def test_a_scraper_runs_by_bright_datas_name_as_written_ours_or_its_id(name: str) -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    provider.run(LiveDataRequest(source=name, inputs={"url": URL}))
    assert api.last.url.params["dataset_id"] == DATASET and "discover_by" not in api.last.url.params


def test_a_discovery_method_is_a_source_of_its_own() -> None:
    provider, api = _provider(_by_path([]))
    response = provider.run(LiveDataRequest(source="amazon_products_by_keyword", inputs={"keyword": "x"}))

    assert dict(api.last.url.params) == {
        "dataset_id": DATASET,
        "type": "discover_new",
        "discover_by": "keyword",
        "format": "json",
        "include_errors": "true",
    }
    assert response.records == [], "Bright Data answers [] when there is no data, which is not an error"


def test_the_catalogue_is_read_once_for_every_run_by_name() -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    for _ in range(3):
        provider.run(LiveDataRequest(source="amazon_products", inputs={"url": URL}))
    assert [r.url.path for r in api.requests].count("/datasets/v3/scrapers") == 1


def test_a_dataset_id_still_runs_its_url_method_without_the_catalogue() -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}))
    assert [r.url.path for r in api.requests] == ["/datasets/v3/scrape"]


def test_an_unknown_name_is_not_found_and_nothing_is_scraped() -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    with pytest.raises(LiveDataSourceNotFoundError):
        provider.run(LiveDataRequest(source="amazon_nothing", inputs={"url": URL}))
    assert "/datasets/v3/scrape" not in [r.url.path for r in api.requests]


def test_a_name_split_by_a_new_scraper_names_its_replacements() -> None:
    twin = {**SCRAPER, "id": "gd_twin000000000000000", "domain": "amazon.de"}
    provider, _ = _provider(_by_path([], catalogue=(SCRAPER, twin)))
    with pytest.raises(LiveDataSourceNotFoundError) as caught:
        provider.run(LiveDataRequest(source="amazon_products", inputs={"url": URL}))
    message = str(caught.value)
    assert "amazon_products_amazon_com, amazon_products_amazon_de" in message
    assert "by_keyword" not in message and "gd_" in message


def test_a_split_discovery_name_points_at_its_replacements_and_not_at_lookalikes() -> None:
    twin = {**SCRAPER, "id": "gd_twin000000000000000", "domain": "amazon.de"}
    lookalike = {**SCRAPER, "id": "gd_look000000000000000", "name": "Amazon products by keyword extra"}
    provider, _ = _provider(_by_path([], catalogue=(SCRAPER, twin, lookalike)))
    with pytest.raises(LiveDataSourceNotFoundError) as caught:
        provider.run(LiveDataRequest(source="amazon_products_by_keyword", inputs={"keyword": "x"}))
    message = str(caught.value)
    assert "amazon_products_amazon_com_by_keyword, amazon_products_amazon_de_by_keyword" in message
    assert "extra" not in message


def test_options_cannot_change_the_method_a_name_picks() -> None:
    provider, api = _provider(_by_path([]))
    options = {"type": "discover_new", "discover_by": "category_url", "limit_per_input": 5}
    provider.run(
        LiveDataRequest(source="amazon_products_by_keyword", inputs={"keyword": "x"}, provider_options=options)
    )
    assert api.last.url.params["discover_by"] == "keyword"
    assert api.last.url.params["limit_per_input"] == "5"


@pytest.mark.parametrize("source", ["amazon_products", DATASET])
def test_options_cannot_turn_a_url_source_into_discovery(source: str) -> None:
    provider, api = _provider(_by_path([{"title": "x"}]))
    options = {"type": "discover_new", "discover_by": "keyword"}
    provider.run(LiveDataRequest(source=source, inputs={"url": URL}, provider_options=options))
    assert "type" not in api.last.url.params and "discover_by" not in api.last.url.params


def test_options_cannot_name_a_different_dataset() -> None:
    provider, api = _provider(lambda request: httpx.Response(200, json=[{"title": "x"}]))
    provider.run(LiveDataRequest(source=DATASET, inputs={"url": URL}, provider_options={"dataset_id": "gd_other"}))
    assert api.last.url.params["dataset_id"] == DATASET
