"""Data API matching on the shipped catalog: an everyday search lists none, a search that names an API lists it.

Discovery is on for every search, so a match must be one the query asked for. These run against the whole
catalog that ships, so a vendor added later whose name or ids hold a common word fails here, not in a user's search.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from web_router import WebRouter
from web_router.constants_vendors import VENDOR_ENV_CATALOG_DIR
from web_router.data_api_discovery import find_matches
from web_router.exceptions import ConfigurationError
from web_router.vendors.catalog import reload_catalog
from web_router.vendors.credentials import EnvCredentials

EVERYDAY = [
    "how to make sourdough bread",
    "who won the 2022 world cup",
    "taylor swift tour dates",
    "symptoms of vitamin d deficiency",
    "best coffee near me",
    "latest nvidia earnings",
    "weather in paris tomorrow",
    "how to tie a tie",
    "best laptops 2026",
    "python list comprehension",
    "cheap flights to london",
    "people also ask about mortgages",
    "new york times crossword",
    "what is the capital of australia",
    "bitcoin price today",
    "apple stock news",
    "how many calories in an egg",
    "best movies of 2025",
    "latest news on ukraine",
    "nearest pharmacy open now",
    "how to learn spanish fast",
    "amazon prime day deals",
    "github copilot pricing",
    "youtube to mp3",
    "reddit best mechanical keyboard",
    "who is the ceo of openai",
    "job openings for data engineers in berlin",
    "covid vaccine side effects",
]

NAMED = {
    "datadog monitors": "datadog",
    "google ads campaign performance": "google-ads",
    "youtube channel statistics": "youtube",
    "github repository issues": "github",
    "stripe invoices list": "stripe",
    "hubspot contacts": "hubspot",
    "fred interest rate series": "fred",
    "coingecko bitcoin market chart": "coingecko",
    "semrush keyword overview": "semrush",
    "crunchbase organization lookup": "crunchbase",
    "hunter email finder": "hunter",
    "npm package downloads": "npm",
    "arxiv papers search": "arxiv",
}


@pytest.mark.parametrize("query", EVERYDAY)
def test_an_everyday_search_lists_no_data_api(query: str) -> None:
    assert find_matches(query, EnvCredentials()) == []


@pytest.mark.parametrize(("query", "data_api"), NAMED.items())
def test_a_search_that_names_a_data_api_lists_it_first(query: str, data_api: str) -> None:
    matches = find_matches(query, EnvCredentials())
    assert matches
    assert matches[0].data_api == data_api


def test_naming_an_api_by_a_common_word_of_its_name_is_not_enough() -> None:
    # "fed" and "who" are words of data API names (New York Fed, WHO), too common to name either alone.
    assert find_matches("what did the fed say", EnvCredentials()) == []


@pytest.fixture
def broken_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    (tmp_path / "broken.json").write_text("{not json")
    monkeypatch.setenv(VENDOR_ENV_CATALOG_DIR, str(tmp_path))
    yield
    monkeypatch.delenv(VENDOR_ENV_CATALOG_DIR)
    reload_catalog()


def test_a_broken_catalog_leaves_the_web_search_answering(
    fake_provider: Any, broken_catalog: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING), WebRouter("fake") as router:
        response = router.search("datadog monitors")
    assert response.urls == ["https://example.com"]
    assert response.data_apis == []
    assert "broken.json" in caplog.text


def test_a_broken_catalog_fails_a_data_apis_only_search(fake_provider: Any, broken_catalog: None) -> None:
    with WebRouter("fake") as router:
        with pytest.raises(ConfigurationError, match=r"broken\.json"):
            router.search("datadog monitors", data_apis_only=True)
        assert router.get_provider("fake").requests == []  # type: ignore[attr-defined]


def test_data_apis_only_keeps_only_the_configured_ones_when_asked(fake_provider: Any) -> None:
    datadog = {"datadog": {"api_key": "dd-api", "app_key": "dd-app"}}
    with WebRouter("fake", vendor_credentials=datadog) as router:
        assert (
            router.search("google ads campaign performance", data_apis_only=True, configured_only=True).data_apis == []
        )
        found = router.search("datadog monitors", data_apis_only=True, configured_only=True).data_apis
    assert found
    assert {match.data_api for match in found} == {"datadog"}
