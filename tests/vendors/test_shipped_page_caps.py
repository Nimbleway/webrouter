"""A shipped crawl that bills per page needs the caller to name its page cap."""

from __future__ import annotations

import pytest

from web_router.exceptions import MissingParameterError
from web_router.vendors.catalog import get_catalog
from web_router.vendors.plan import endpoint_plan


@pytest.mark.parametrize("endpoint", ["firecrawl.crawl.start", "spidercloud.crawl.site"])
def test_a_billed_crawl_without_a_page_cap_is_refused_before_sending(endpoint: str) -> None:
    with pytest.raises(MissingParameterError) as raised:
        endpoint_plan(endpoint, {"url": "https://example.com"})
    assert raised.value.parameters == ["limit"]


def test_a_firecrawl_crawl_sends_the_cap_it_was_given() -> None:
    plan = endpoint_plan("firecrawl.crawl.start", {"url": "https://example.com", "limit": 10})
    assert (plan.method, plan.path) == ("POST", "/crawl")
    assert plan.body == {"url": "https://example.com", "limit": 10}


def test_firecrawl_scrapes_stay_with_extract() -> None:
    """AGENTS.md: extract already scrapes one URL or a list through Firecrawl, so the catalog names no scrape."""
    paths = [endpoint.path for endpoint in get_catalog().vendor("firecrawl").endpoints]
    assert [path for path in paths if "scrape" in path] == []
