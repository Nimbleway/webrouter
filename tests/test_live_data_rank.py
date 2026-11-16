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
