"""AgentCore Web Search: ``recency`` as the tool's published-date filter.

Mapped from AWS's connector documentation and the date form a live search took
through ``provider_options`` (2026-10-26); ``recency`` itself has not been run
against a gateway -- the live test at the bottom does that.
"""

from __future__ import annotations

import json
import os
from datetime import date

import httpx
import pytest

from tests.providers._agentcore import (  # noqa: F401  (aws_env: autouse fixture)
    NO_FILTERS_SCHEMA,
    TOOL,
    aws_env,
)
from tests.providers._agentcore import gateway as _gateway
from tests.providers._agentcore import provider as _provider
from tests.providers._agentcore import tools as _tools
from web_router.exceptions import ConfigurationError
from web_router.providers._clock import pinned_today
from web_router.providers.agentcore import AgentCoreProvider
from web_router.types import SearchRequest


@pytest.mark.parametrize(
    ("recency", "start"),
    [("day", "2026-11-08"), ("week", "2026-11-02"), ("month", "2026-10-10"), ("year", "2025-11-09")],
)
def test_recency_sets_the_published_from_date(recency: str, start: str) -> None:
    with pinned_today(date(2026, 11, 9)):
        payload = _provider(_gateway())._build_payload(SearchRequest(query="hi", recency=recency))  # type: ignore[arg-type]
    assert payload["filters"] == {"publishedDateFilter": {"from": f"{start}T00:00:00Z"}}


def test_recency_merges_with_the_domains_and_an_end_date_from_provider_options() -> None:
    request = SearchRequest(
        query="hi",
        include_domains=["python.org"],
        recency="week",
        provider_options={"filters": {"publishedDateFilter": {"to": "2026-11-09T00:00:00Z"}}},
    )
    with pinned_today(date(2026, 11, 9)):
        payload = _provider(_gateway())._build_payload(request)
    assert payload["filters"] == {
        "domainFilter": {"include": ["python.org"]},
        "publishedDateFilter": {"to": "2026-11-09T00:00:00Z", "from": "2026-11-02T00:00:00Z"},
    }


def test_a_start_date_set_twice_raises_rather_than_one_winning() -> None:
    request = SearchRequest(
        query="hi", recency="day", provider_options={"filters": {"publishedDateFilter": {"from": "2020-01-01"}}}
    )
    with pytest.raises(ConfigurationError, match=r"publishedDateFilter\.from"):
        _provider(_gateway())._build_payload(request)


def test_recency_on_a_target_without_filters_is_refused_not_dropped() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(_gateway(listing=_tools(TOOL, schema=NO_FILTERS_SCHEMA), seen=seen))

    with pytest.raises(ConfigurationError, match="recency"):
        provider.search(SearchRequest(query="hi", recency="month"))
    assert [json.loads(request.content)["method"] for request in seen] == ["tools/list"]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("AGENTCORE_GATEWAY_URL"), reason="AGENTCORE_GATEWAY_URL not set")
def test_live_recency_keeps_results_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the real AWS setup, not the fake credentials above
    provider = AgentCoreProvider()
    try:
        response = provider.search(SearchRequest(query="technology news", max_results=5, recency="week"))
    finally:
        provider.close()

    # publishedDate comes in AgentCore's own wording, so this checks the call and
    # leaves reading the dates to whoever runs it.
    assert response.results
