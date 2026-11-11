"""Data API discovery in search: matches listed beside the web results, found locally and never called."""

from __future__ import annotations

from typing import Any

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants_vendors import DATA_API_SEARCH_LIMIT, DATA_APIS_ONLY_PROVIDER

DATADOG = {"datadog": {"api_key": "dd-api", "app_key": "dd-app"}}


@pytest.fixture(autouse=True)
def _no_google_ads_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_a_web_search_lists_the_data_apis_that_match(fake_provider: Any) -> None:
    with WebRouter("fake", vendor_credentials=DATADOG) as router:
        response = router.search("datadog monitors")
    assert response.urls == ["https://example.com"]
    assert 0 < len(response.data_apis) <= DATA_API_SEARCH_LIMIT
    first = response.data_apis[0]
    assert first.data_api == "datadog"
    assert first.endpoint.startswith("datadog.monitors.")
    assert first.configured
    assert first.missing == []
    assert all(match.kind == "read" for match in response.data_apis)


def test_a_query_no_data_api_matches_well_lists_none(fake_provider: Any) -> None:
    with WebRouter("fake") as router:
        assert router.search("who won the 2022 world cup").data_apis == []


def test_a_match_without_its_key_names_the_variable_to_set(fake_provider: Any) -> None:
    with WebRouter("fake") as router:
        matches = router.search("google ads campaign performance").data_apis
    assert matches
    assert all(match.data_api == "google-ads" and not match.configured for match in matches)
    assert "GOOGLE_ADS_DEVELOPER_TOKEN" in matches[0].missing


def test_configured_only_keeps_the_callable_matches(fake_provider: Any) -> None:
    with WebRouter("fake", vendor_credentials=DATADOG) as router:
        assert router.search("google ads campaign performance", configured_only=True).data_apis == []
        assert router.search("datadog monitors", configured_only=True).data_apis


def test_data_apis_false_is_a_plain_web_search(fake_provider: Any) -> None:
    with WebRouter("fake", vendor_credentials=DATADOG) as router:
        assert router.search("datadog monitors", data_apis=False).data_apis == []


def test_data_apis_only_calls_no_web_backend(fake_provider: Any) -> None:
    with WebRouter("fake", vendor_credentials=DATADOG) as router:
        response = router.search("datadog monitors", data_apis_only=True, max_results=1)
        assert router.get_provider("fake").requests == []  # type: ignore[attr-defined]
    assert response.provider == DATA_APIS_ONLY_PROVIDER
    assert response.results == []
    assert [match.data_api for match in response.data_apis] == ["datadog"]


@pytest.mark.parametrize(
    "options",
    [{"provider": "fake"}, {"country": "US"}, {"include_domains": ["example.com"]}, {"data_apis": False}],
    ids=["provider", "country", "include_domains", "discovery-off"],
)
def test_data_apis_only_refuses_what_it_would_drop(fake_provider: Any, options: dict[str, Any]) -> None:
    with WebRouter("fake") as router, pytest.raises(ValueError):
        router.search("datadog monitors", data_apis_only=True, **options)


async def test_the_async_router_lists_the_same_matches(fake_provider: Any) -> None:
    async with AsyncWebRouter("fake", vendor_credentials=DATADOG) as router:
        response = await router.search("datadog monitors")
        only = await router.search("datadog monitors", data_apis_only=True)
    with WebRouter("fake", vendor_credentials=DATADOG) as router:
        assert response.data_apis == router.search("datadog monitors").data_apis
    assert only.data_apis == response.data_apis


@pytest.mark.parametrize(
    ("query", "max_results"), [("datadog monitors", 0), ("datadog monitors", -1), ("", None)], ids=["zero", "neg", "q"]
)
def test_data_apis_only_holds_the_web_paths_checks(query: str, max_results: int | None) -> None:
    with WebRouter() as router, pytest.raises(ValueError):
        router.search(query, data_apis_only=True, max_results=max_results)


def test_a_match_is_importable_and_its_brief_form_is_bounded() -> None:
    from web_router import DataApiMatch

    match = DataApiMatch(
        endpoint="acme.x",
        data_api="acme",
        name="n" * 500,
        summary="s" * 500,
        kind="read",
        docs_url="https://x",
        configured=True,
    )
    brief = match.brief(50)
    assert len(brief["name"]) == len(brief["summary"]) == 50
    assert "docs_url" not in brief
