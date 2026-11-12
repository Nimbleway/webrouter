"""How well a local search ranks a real catalogue: the right source first, for the queries people type.

The catalogue is Nimble's public Extract Templates, recorded from its listing
on 2026-11-12 (``tests/fixtures/live_data/nimble_public_catalogue.json``) and
kept to what the ranking reads: name, site, the vendor's label, description,
and whether the template has a published version, an input schema and a
sample. Account-private templates are left out of the recording.

Each case is a query and the sources that answer it; more than one when the
catalogue holds equally good ones. The suite holds the ranking to a floor
for the right source at the top and in the top three, and the per-query
cases below name what each change to the ranking must keep.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from web_router.live_data_rank import kind_of, read_query
from web_router.live_data_search import ranked
from web_router.types_live_data import LiveDataSample, LiveDataSource

FIXTURE = Path(__file__).parent / "fixtures" / "live_data" / "nimble_public_catalogue.json"

CASES: list[tuple[str, set[str]]] = [
    ("amazon product", {"amazon_pdp", "amazon_pdp_localized"}),
    ("amazon pdp", {"amazon_pdp", "amazon_pdp_localized"}),
    ("amazon product page", {"amazon_pdp", "amazon_pdp_localized"}),
    ("amazon search", {"amazon_serp_localized"}),
    ("amazon uk product", {"amazon_uk_pdp"}),
    ("amazon japan product", {"amazon_jp_pdp"}),
    (
        "amazon bestsellers",
        {"amazon_nl_bestsellers_community_2026_05_11", "amazon_sg_bestsellers_community_2026_05_11"},
    ),
    ("walmart product", {"walmart_pdp"}),
    ("walmart canada search", {"walmart_ca_serp"}),
    ("bestbuy reviews", {"bestbuy_com_product_reviews_community_2026_03_17"}),
    ("best buy product", {"best_buy_pdp"}),
    ("bestbuy search", {"bestbuy_serp", "bestbuy_com_product_search_community_2026_03_16"}),
    ("ebay search", {"ebay_search_results_community_2026_03_23", "ebay_search_2026_02_23_pbgj8oft"}),
    ("ebay product", {"ebay_product_listing_community_2026_03_23"}),
    ("etsy product", {"etsy_product_page_community_2026_03_23"}),
    ("zillow pdp", {"zillow_pdp"}),
    ("google maps reviews", {"google_maps_reviews"}),
    ("google maps search", {"google_maps_search"}),
    ("indeed search", {"indeed_serp", "indeed_search_2026_02_23_vlgtrsgu"}),
    ("dice job posting", {"dice_com_job_posting_details_community_2026_03_23"}),
    ("flipkart reviews", {"flipkart_product_reviews_community_2026_03_24"}),
    ("flipkart product", {"flipkart_product_page_community_2026_03_24"}),
    ("tiktok shop product", {"tiktok_shop_pdp"}),
    ("lowes product", {"lowes_pdp"}),
    ("tesco category", {"tesco_clp"}),
    ("booking reviews", {"booking_com_property_reviews_community_2026_03_17"}),
    ("aliexpress reviews", {"aliexpress_product_reviews_community_2026_03_15_v2"}),
    ("aldi product", {"aldi_product_details", "aldi_pdp"}),
    ("douglas germany product", {"douglas_de_product_page_community_2026_03_23"}),
    ("duckduckgo news", {"duckduckgo_news_search_community_2026_03_23"}),
    ("staples search", {"staples_serp"}),
    ("dailymail article", {"dailymail_co_uk_article_page_community_2026_03_23"}),
]

TOP_1_FLOOR = 0.9
TOP_3_FLOOR = 1.0


def _catalogue() -> list[LiveDataSource]:
    """The recording as sources; a stand-in schema and sample for each the template has, all that ranking reads."""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [
        LiveDataSource(
            provider="nimble",
            name=row["name"],
            domain=row["domain"],
            entity_type=row["entity_type"],
            description=row["description"],
            published=row["published"],
            input_schema={"type": "object"} if row["has_input_schema"] else None,
            samples=[LiveDataSample()] if row["has_samples"] else [],
            extra=row["extra"],
        )
        for row in rows
    ]


CATALOGUE = _catalogue()


def _top(query: str, count: int) -> list[str]:
    return [source.name for source in ranked(CATALOGUE, query)[:count]]


def test_every_expected_source_is_in_the_recording() -> None:
    names = {source.name for source in CATALOGUE}
    assert not {name for _, expected in CASES for name in expected} - names


def test_the_ranking_puts_the_right_source_first_and_in_the_top_three() -> None:
    first = [query for query, expected in CASES if _top(query, 1)[:1] and _top(query, 1)[0] in expected]
    in_three = [query for query, expected in CASES if expected & set(_top(query, 3))]
    missed = {query: _top(query, 3) for query, expected in CASES if not expected & set(_top(query, 1))}

    assert len(first) / len(CASES) >= TOP_1_FLOOR, missed
    assert len(in_three) / len(CASES) >= TOP_3_FLOOR, missed


@pytest.mark.parametrize(("query", "expected"), CASES, ids=[query for query, _ in CASES])
def test_each_query_finds_its_source_in_the_top_three(query: str, expected: set[str]) -> None:
    assert expected & set(_top(query, 3)), _top(query, 5)


def test_a_kind_word_asks_for_the_kind_not_the_text() -> None:
    """'product' asks for a product page: a reviews page that says 'product' is not one, and is left out."""
    found = [source.name for source in ranked(CATALOGUE, "flipkart product")]

    assert "flipkart_product_reviews_community_2026_03_24" not in found
    assert read_query("amazon product page").kind == "product"


def test_the_name_says_the_kind_before_a_loose_label() -> None:
    reviews = next(source for source in CATALOGUE if source.name == "bestbuy_com_product_reviews_community_2026_03_17")

    assert reviews.entity_type == "Product Detail Page (PDP)"
    assert kind_of(reviews) == "reviews"


def test_the_main_site_comes_before_other_countries_unless_one_is_asked() -> None:
    assert _top("amazon product", 1)[0].startswith("amazon_pdp")
    assert _top("amazon uk product", 1) == ["amazon_uk_pdp"]


def test_a_tie_goes_to_the_name_the_query_explains_whole() -> None:
    """Same site, same kind, same quality: walmart_serp is all 'walmart search', the directory is not."""
    directory = LiveDataSource(
        provider="nimble", name="walmart_store_directory_by_town_2026_04_21", domain="walmart.com", entity_type="SERP"
    )
    serp = LiveDataSource(provider="nimble", name="walmart_serp", domain="www.walmart.com", entity_type="SERP")

    assert [source.name for source in ranked([directory, serp], "walmart search")] == ["walmart_serp", directory.name]
