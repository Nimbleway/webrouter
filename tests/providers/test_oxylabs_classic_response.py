"""Oxylabs live data over the Web Scraper API ("Classic"): what a run's answer reads as, and the catalogue.

Shapes follow the answers seen live on 2026-11-17: a parsed page under
``results[].content`` with ``parse_status_code`` 12000, 12004 and 12005 parsed
in part, and 12003 with no fields for a source Oxylabs does not parse. Values
here are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.providers._oxylabs_classic import PAGE, classic, no_oxylabs_env_fixture, parsed  # noqa: F401  (fixture)
from web_router.exceptions import ProviderError
from web_router.providers.oxylabs_classic import classic_sources
from web_router.types_live_data import LiveDataRequest

SHIPPED = Path(__file__).resolve().parents[2] / "src" / "web_router" / "data" / "oxylabs_classic_sources.json"
"""Read from disk, not through the provider, so the expected order is not the code's own."""

# -- answers ------------------------------------------------------------------


def test_a_page_parsed_in_part_is_records_with_the_parsers_errors_beside_them() -> None:
    """``parse_status_code`` 12004, seen live on ``target_search``: most fields, and ``_errors`` naming the rest."""
    partial = {"results": {"organic": [{"price": 129.99}]}, "_errors": ["no title"], "parse_status_code": 12004}
    provider, _ = classic(parsed(partial))
    response = provider.run(LiveDataRequest(source="target_search", inputs={"query": "headphones"}))
    assert response.records == [{"results": {"organic": [{"price": 129.99}]}}]
    assert response.extra["results"][0]["parse_status_code"] == 12004
    assert response.extra["results"][0]["_errors"] == ["no title"]


def test_a_page_parsed_with_fields_missing_is_records_with_the_parsers_warnings_beside_them() -> None:
    """``parse_status_code`` 12005, seen live on ``etsy_product``: the fields it read, ``_warnings`` the rest."""
    missing = {
        "title": "A mug",
        "price": 31.72,
        "_warnings": ["Could not parse field .images"],
        "parse_status_code": 12005,
    }
    provider, _ = classic(parsed(missing))
    response = provider.run(LiveDataRequest(source="etsy_product", inputs={"product_id": "1"}))
    assert response.records == [{"title": "A mug", "price": 31.72}]
    assert response.extra["results"][0]["_warnings"] == ["Could not parse field .images"]


@pytest.mark.parametrize(
    ("answer", "status", "match"),
    [
        (parsed({"parse_status_code": 12003}), 200, r"did not parse 'amazon_search': parse_status_code 12003"),
        (parsed("<html></html>"), 200, "returned no parsed fields"),
        (parsed(PAGE, status=404), 200, "could not fetch 'amazon_search': status 404"),
        ({"results": []}, 200, "returned no parsed fields"),
        ({"message": "Unsupported source."}, 400, "Unsupported source"),
    ],
)
def test_a_run_that_brings_back_no_parsed_page_raises(answer: dict[str, Any], status: int, match: str) -> None:
    provider, _ = classic(answer, status)
    with pytest.raises(ProviderError, match=match):
        provider.run(LiveDataRequest(source="amazon_search", inputs={"query": "x"}))


async def test_an_async_run_reads_the_same_answer() -> None:
    provider, _ = classic()
    response = await provider.arun(LiveDataRequest(source="amazon_search", inputs={"query": "x"}))
    assert response.records == [{"title": "A thing", "price": 9.99}]


# -- catalogue ----------------------------------------------------------------


def test_the_catalogue_is_the_shipped_list_with_each_sources_inputs() -> None:
    provider, api = classic()
    page = provider.live_data_sources(limit=2, offset=1)
    names = [entry["name"] for entry in json.loads(SHIPPED.read_text("utf-8"))["sources"]]
    assert names[:3] == ["amazon_bestsellers", "amazon_pricing", "amazon_product"] and len(names) == 25
    assert [source.name for source in page.sources] == names[1:3] and page.total == len(names)
    assert api.requests == [], "the API lists no sources, so the catalogue is read from the package"

    described = provider.live_data_source("amazon_product")
    assert described.domain == "amazon" and described.entity_type == "product" and described.published
    assert described.input_schema is not None and described.input_schema["required"] == ["query"]
    assert "geo_location" in described.input_schema["properties"]


def test_every_shipped_source_is_parsed_and_takes_a_required_input() -> None:
    for name, entry in classic_sources().items():
        assert entry["inputs"], name
        assert any(spec["required"] for spec in entry["inputs"].values()), name
        assert entry["docs"].startswith("https://developers.oxylabs.io/"), name
    assert "universal" not in classic_sources(), "a page fetch by URL is extract's"
