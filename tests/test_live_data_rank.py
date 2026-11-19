"""The ranking's pieces on hand-built sources: what a query asks, which sources answer it, and how they rank.

The recorded catalogue (``tests/test_live_data_ranking_eval.py``) holds the
ranking to real queries; these pin each rule on its own, on sources built
here, so a rule that stops working fails by name.
"""

from __future__ import annotations

from web_router.live_data_rank import kind_of, read_query, relevant, score
from web_router.live_data_rank_site import country_of, site_score
from web_router.live_data_search import ranked
from web_router.providers import brightdata_shapes, oxylabs_scrapers
from web_router.providers import nimble_live_data_map as nimble_map
from web_router.providers.brightdata_names import Scraper as BrightDataScraper
from web_router.providers.oxylabs_scrapers import Scraper as OxylabsScraper
from web_router.types_live_data import LiveDataSample, LiveDataSource


def _source(name: str, domain: str = "www.amazon.com", entity_type: str | None = None) -> LiveDataSource:
    return LiveDataSource(provider="nimble", name=name, domain=domain, entity_type=entity_type)


# -- which sources answer -----------------------------------------------------


def test_a_source_that_does_not_say_its_kind_answers_a_kind_alone_as_it_does_with_a_site() -> None:
    unlabelled = _source("amazon_scraper")

    assert relevant(unlabelled, read_query("amazon product"))
    assert relevant(unlabelled, read_query("product"))
    assert not relevant(_source("amazon_reviews"), read_query("product"))
    # It answers, below one that says it is the kind asked for.
    assert ranked([unlabelled, _source("amazon_pdp")], "product")[0].name == "amazon_pdp"
    assert score(_source("amazon_pdp"), read_query("product")) > score(unlabelled, read_query("product"))


def test_a_country_alone_keeps_that_countrys_sources() -> None:
    uk, main = _source("amazon_uk_pdp", "www.amazon.co.uk"), _source("amazon_pdp")

    assert read_query("uk") == read_query("britain")
    assert relevant(uk, read_query("uk")) and not relevant(main, read_query("uk"))
    # With a site and a kind the main site still answers, below the country asked for.
    assert [source.name for source in ranked([main, uk], "amazon uk product")] == ["amazon_uk_pdp", "amazon_pdp"]


def test_in_it_and_be_in_a_query_are_filler_not_site_words() -> None:
    german = _source("amazon_de_pdp", "www.amazon.de", "Product Detail Page (PDP)")
    query = read_query("amazon products in germany")

    assert query.site_words == ("amazon",) and query.country == "de"
    assert relevant(german, query)
    assert ranked([_source("amazon_pdp"), german], "amazon products in germany")[0] is german
    # A source's name still reads "in" as India.
    assert relevant(_source("amazon_in_product_reviews", domain=""), read_query("india"))


def test_a_query_of_filler_words_only_matches_nothing() -> None:
    assert ranked([_source("amazon_pdp"), _source("amazon_uk_pdp", "www.amazon.co.uk")], "page data") == []


def _quality(name: str) -> int:
    """What ``name`` alone adds to a source's score: nothing in the query explains it, and the site is the same."""
    return score(_source(name), read_query("")) - score(_source("x_product"), read_query(""))


def test_a_test_or_copy_in_the_name_ranks_lower_as_a_whole_word_only() -> None:
    for name in ("old_navy_product", "google_news_latest", "contest_entries", "latest_news"):
        assert _quality(name) == 0, name
    for name in ("amazon_pdp_test", "amazon_pdp_copy", "test_pdp_copy2"):
        assert _quality(name) < 0, name


def test_a_generic_ending_is_the_main_site_and_a_country_code_is_not() -> None:
    query = read_query("x product")
    main = score(_source("x_product", "www.amazon.com"), query)

    for domain in ("www.perplexity.ai", "www.example.app", "www.twitch.tv", "www.example.io", "www.example.shop"):
        assert score(_source("x_product", domain), query) == main, domain
    assert relevant(_source("amazon_pdp", "www.amazon.co.uk"), read_query("uk"))
    assert relevant(_source("amazon_pdp", "www.amazon.com.mx"), read_query("mexico"))
    assert score(_source("x_product", "www.amazon.de"), query) < main


def _from_each_vendor() -> list[LiveDataSource]:
    """An amazon.com product source from each vendor's own mapper, as its listing gives it."""
    nimble_row = {
        "name": "amazon_pdp",
        "privacy": "public",
        "managed_by": "nimble",
        "metadata": {"domain": "www.amazon.com", "entity_type": "Product Detail Page (PDP)"},
    }
    bright_data = {"id": "gd_x", "name": "Amazon products", "domain": "amazon.com", "category": "Product"}
    return [
        nimble_map.to_source("nimble", nimble_row),
        brightdata_shapes.listed("brightdata", BrightDataScraper("amazon_products", "gd_x", "url", bright_data)),
        oxylabs_scrapers.listed("oxylabs", OxylabsScraper("amazon/product", {"realtime"})),
    ]


def test_equal_sources_from_every_vendor_score_the_same() -> None:
    """Nothing one vendor's mapper says and another's cannot lifts its sources: the order is the turns'."""
    query = read_query("amazon product")
    sources = _from_each_vendor()

    assert len({score(source, query) for source in sources}) == 1
    assert [source.provider for source in ranked(sources, "amazon product")] == ["nimble", "brightdata", "oxylabs"]
    unfinished = sources[0].model_copy(update={"name": "amazon_pdp_test"})
    assert ranked([unfinished, *sources], "amazon product")[-1] is unfinished


def test_the_title_is_read_for_the_match_and_the_kind_alike() -> None:
    titled = _source("tpl_1", "www.example.com").model_copy(update={"extra": {"display_name": "Books - Reviews"}})

    assert relevant(titled, read_query("books")) and kind_of(titled) == "reviews"
    assert not relevant(titled.model_copy(update={"extra": {"title": 7}}), read_query("books"))


# -- what each weight decides -------------------------------------------------
# Each test turns on one weight: setting it to 0 makes the two sources tie (the
# input order then stands, and the losing source is put first) or flips them.


def test_an_apify_actor_is_read_by_its_name_after_its_owner() -> None:
    def scraper(name: str) -> LiveDataSource:
        return LiveDataSource(provider="apify", name=name, description="Amazon UK product scraper")

    owned, unowned = scraper("someone~amazon_uk_product"), scraper("amazon_uk_product")
    query = read_query("amazon uk product")

    assert country_of(owned) == "uk"
    assert ranked([_source("amazon_pdp"), owned], "amazon uk product")[0] is owned
    assert site_score(owned, query.site_words) == site_score(unowned, query.site_words)
    assert country_of(_source("amazon_in_product_reviews", domain="")) == "in"


def _names(sources: list[LiveDataSource], query: str) -> list[str]:
    return [f"{source.name}@{source.domain}" for source in ranked(sources, query)]


def test_the_main_site_ranks_above_a_country_edition_when_no_country_is_asked() -> None:
    uk, main = _source("amazon_pdp", "www.amazon.co.uk"), _source("amazon_pdp")

    assert _names([uk, main], "amazon product") == ["amazon_pdp@www.amazon.com", "amazon_pdp@www.amazon.co.uk"]
    assert _names([main, uk], "amazon uk product") == ["amazon_pdp@www.amazon.co.uk", "amazon_pdp@www.amazon.com"]


def test_a_named_country_outweighs_a_better_site_match_on_the_main_site() -> None:
    """A UK reseller's page against Amazon's own .com page with a schema and a sample, for "amazon uk product"."""
    reseller = _source("amazon_uk_product", "www.shopamazon.co.uk")
    main = _source("amazon_product").model_copy(
        update={"input_schema": {"type": "object"}, "samples": [LiveDataSample()]}
    )

    assert ranked([main, reseller], "amazon uk product") == [reseller, main]


def test_the_kind_asked_ranks_above_a_source_that_does_not_say_its_kind() -> None:
    unsaid, product = _source("amazon_x"), _source("amazon_x", entity_type="Product page")

    assert ranked([unsaid, product], "amazon product") == [product, unsaid]


def test_a_schema_and_a_sample_each_rank_a_source_higher() -> None:
    bare = _source("amazon_pdp")
    with_schema = bare.model_copy(update={"input_schema": {"type": "object"}})
    with_sample = bare.model_copy(update={"samples": [LiveDataSample()]})

    assert ranked([bare, with_schema], "amazon product") == [with_schema, bare]
    assert ranked([bare, with_sample], "amazon product") == [with_sample, bare]


def test_a_site_word_in_the_name_ranks_above_one_only_in_the_description() -> None:
    described = LiveDataSource(provider="nimble", name="other_x", domain="www.example.com", description="Acme pages")
    named = LiveDataSource(provider="nimble", name="acme_x", domain="www.example.com")

    assert ranked([described, named], "acme") == [named, described]


def test_a_test_or_copy_twin_ranks_below_the_source() -> None:
    source = _source("amazon_pdp")
    twins = [source.model_copy(update={"name": f"amazon_pdp_{word}"}) for word in ("test", "copy")]

    assert ranked([*twins, source], "amazon product")[0] is source
