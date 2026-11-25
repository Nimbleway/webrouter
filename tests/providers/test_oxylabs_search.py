"""Oxylabs search provider tests.

``DOCUMENTED`` follows the Web Scraper API's parsed ``google_search`` answer:
one result whose ``content.results`` holds ``organic`` (pos, url, desc, title,
url_shown, pos_overall) beside ``paid``, with ``parse_status_code`` 12000 and
the job beside the results. It is not a recorded reply.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.oxylabs import OxylabsProvider, basic_auth
from web_router.types import SearchRequest


def _organic(pos: int, url: str, title: str, desc: str | None = None) -> dict[str, Any]:
    hit = {"pos": pos, "url": url, "title": title, "url_shown": url, "pos_overall": pos}
    return hit if desc is None else {**hit, "desc": desc}


DOCUMENTED: dict[str, Any] = {
    "results": [
        {
            "content": {
                "url": "https://www.google.com/search?q=python+asyncio",
                "page": 1,
                "results": {
                    "paid": [],
                    "organic": [
                        _organic(
                            1, "https://docs.python.org/3/library/asyncio.html", "asyncio", "Write concurrent code."
                        ),
                        _organic(2, "https://realpython.com/async-io-python/", "Real Python", "A walkthrough."),
                        _organic(3, "https://superfastpython.com/python-asyncio/", "Super Fast Python"),
                    ],
                    "total_results_count": 1630000,
                },
                "parse_status_code": 12000,
            },
            "created_at": "2026-11-24 10:00:00",
            "updated_at": "2026-11-24 10:00:03",
            "page": 1,
            "url": "https://www.google.com/search?q=python+asyncio",
            "job_id": "7400000000000000001",
            "status_code": 200,
            "parser_type": "",
        }
    ],
    "job": {"id": "7400000000000000001", "source": "google_search", "status": "done"},
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OXYLABS_USERNAME", "user")
    monkeypatch.setenv("OXYLABS_PASSWORD", "pass")


def _answered_with(body: dict[str, Any], sent: list[httpx.Request] | None = None) -> OxylabsProvider:
    def reply(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request)
        return httpx.Response(200, json=body)

    return OxylabsProvider(transport=httpx.MockTransport(reply))


@pytest.mark.usefixtures("env")
def test_a_search_is_one_parsed_google_page() -> None:
    sent: list[httpx.Request] = []
    _answered_with(DOCUMENTED, sent).search(SearchRequest(query="python asyncio"))

    assert sent[0].method == "POST" and sent[0].url.host == "realtime.oxylabs.io"
    assert sent[0].url.path == "/v1/queries"
    assert sent[0].headers["authorization"] == basic_auth("user", "pass")
    assert json.loads(sent[0].content) == {"source": "google_search", "query": "python asyncio", "parse": True}


@pytest.mark.usefixtures("env")
def test_max_results_is_sent_as_limit_and_provider_options_go_last() -> None:
    request = SearchRequest(query="q", max_results=5, provider_options={"geo_location": "Germany"})
    assert OxylabsProvider()._build_payload(request) == {
        "source": "google_search",
        "query": "q",
        "parse": True,
        "limit": 5,
        "geo_location": "Germany",
    }


def test_search_needs_the_username_as_extract_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OXYLABS_PASSWORD", "pass")
    monkeypatch.delenv("OXYLABS_USERNAME", raising=False)
    with pytest.raises(ConfigurationError, match="OXYLABS_USERNAME"):
        OxylabsProvider()
    assert OxylabsProvider(username="me", web_api_key="unused")._username == "me"


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize(
    "option",
    [
        {"include_domains": ["a.com"]},
        {"exclude_domains": ["b.com"]},
        {"recency": "week"},
        {"country": "US"},
        {"locale": "en"},
        {"effort": "low"},
        {"search_depth": "lite"},
    ],
)
def test_an_option_oxylabs_search_does_not_map_is_refused(option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        OxylabsProvider().validate(SearchRequest(query="q", **option))


@pytest.mark.usefixtures("env")
def test_organic_results_map_with_the_job_id_as_the_request_id() -> None:
    response = OxylabsProvider()._to_response(SearchRequest(query="q"), DOCUMENTED)
    first, _, third = response.results

    assert response.provider == "oxylabs" and response.raw == DOCUMENTED
    assert response.request_id == "7400000000000000001"
    assert (first.url, first.title) == ("https://docs.python.org/3/library/asyncio.html", "asyncio")
    assert first.description == first.content == "Write concurrent code."
    assert first.extra == {"pos": 1, "url_shown": first.url, "pos_overall": 1}
    assert first.score is None and first.published_date is None
    assert (third.description, third.content) == ("", "")


@pytest.mark.usefixtures("env")
def test_results_past_max_results_are_cut() -> None:
    response = OxylabsProvider()._to_response(SearchRequest(query="q", max_results=1), DOCUMENTED)
    assert [result.url for result in response.results] == ["https://docs.python.org/3/library/asyncio.html"]


@pytest.mark.usefixtures("env")
def test_a_parsed_page_without_organic_results_is_a_search_that_found_nothing() -> None:
    page = {"results": [{"content": {"results": {"paid": []}, "parse_status_code": 12000}, "status_code": 200}]}
    assert OxylabsProvider()._to_response(SearchRequest(query="q"), page).results == []


def _answer(content: Any, status: int = 200) -> dict[str, Any]:
    return {"results": [{"content": content, "status_code": status}]}


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"results": []},
        _answer({"results": {"organic": []}, "parse_status_code": 12003}),
        _answer({"results": {"organic": []}, "parse_status_code": 12000}, status=403),
        _answer("<html></html>"),
    ],
)
def test_no_page_a_failed_fetch_or_an_unparsed_page_is_an_error(body: dict) -> None:
    with pytest.raises(ProviderError, match="parsed Google page"):
        _answered_with(body).search(SearchRequest(query="q"))


@pytest.mark.integration
def test_live_search_returns_results() -> None:
    if not (os.environ.get("OXYLABS_USERNAME") and os.environ.get("OXYLABS_PASSWORD")):
        pytest.skip("OXYLABS_USERNAME and OXYLABS_PASSWORD not set")
    response = OxylabsProvider().search(SearchRequest(query="python asyncio tutorial", max_results=3))
    assert 0 < len(response.results) <= 3
    assert all(result.url.startswith("http") for result in response.results)
