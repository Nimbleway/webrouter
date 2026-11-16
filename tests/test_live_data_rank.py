"""The ranking's pieces on hand-built sources: what a query asks, which sources answer it, and how they rank.

The recorded catalogue (``tests/test_live_data_ranking_eval.py``) holds the
ranking to real queries; these pin each rule on its own, on sources built
here, so a rule that stops working fails by name.
"""

from __future__ import annotations

from web_router.live_data_rank import read_query, relevant, score
from web_router.live_data_search import ranked
from web_router.types_live_data import LiveDataSource


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
