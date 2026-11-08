"""DeepSeek provider tests.

Checked against the live API on 2026-10-19. ``site:`` alone keeps every result
on that site; ``site:a OR site:b`` widens to both (20 results: 17 and 3), in
either order and with ``-site:`` or ``lang:`` after it; parentheses around the
pair return nothing, so none are sent. ``-site:wikipedia.org`` removed Wikipedia
from "coffee". ``lang:zh`` turned an English query with no Chinese results into
five Chinese ones. ``count`` 3 and 50 return 3 and 50. A 129-character query is
a 400. A result carries exactly the documented fields, ``content`` empty without
``need_content``; ``date`` is an RFC 3339 string or null. Summaries ran 108 to
4,651 characters, not the documented 600 to 800. Fetch answers ``{title,
content}``, and 404 for a missing page or a metadata address. A zero-balance
account answers 402 ``insufficient_balance_error`` on both endpoints. The search
payloads below keep the documented example's values in the live shape.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import date
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers._clock import pinned_today
from web_router.providers.deepseek import DeepSeekExtractProvider, DeepSeekProvider
from web_router.types import ExtractRequest, ExtractResponse, SearchRequest

DOCUMENTED = {
    "results": [
        {
            "rerank_score": 2.4,
            "title": "Example result title",
            "url": "https://example.com/article",
            "snippet": "Short display snippet.",
            "summary": "Longer summary for a model to read. " * 20,
            "content": "",
            "site_name": "Example News",
            "site_icon": "https://example.com/favicon.ico",
            "date": "2026-06-01T00:00:00+00:00",
            "authority": 3,
        },
        {
            "rerank_score": 0.9,
            "title": "Undated page",
            "url": "https://example.org/page",
            "snippet": "",
            "summary": "A summary with no snippet beside it.",
            "content": "The full page body.",
            "site_name": None,
            "site_icon": None,
            "date": None,
            "authority": -1,
        },
    ]
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> DeepSeekProvider:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    return DeepSeekProvider()


def test_max_results_maps_onto_count_within_its_range(provider: DeepSeekProvider) -> None:
    assert provider._build_payload(SearchRequest(query="hi", max_results=5))["count"] == 5
    # max_results is an upper bound; 50 is the most DeepSeek accepts, and still within it.
    assert provider._build_payload(SearchRequest(query="hi", max_results=100))["count"] == 50


def test_domain_and_language_filters_become_query_operators(provider: DeepSeekProvider) -> None:
    request = SearchRequest(
        query="DeepSeek",
        include_domains=["github.com", "huggingface.co"],
        exclude_domains=["example.com"],
        locale="en",
    )

    assert provider._build_payload(request) == {
        "query": "DeepSeek site:github.com OR site:huggingface.co -site:example.com lang:en"
    }


@pytest.mark.parametrize("locale", ["EN-US", "en_us"])
def test_a_regional_locale_sends_its_language_alone(provider: DeepSeekProvider, locale: str) -> None:
    assert provider._build_payload(SearchRequest(query="hi", locale=locale)) == {"query": "hi lang:en"}


def test_a_fitting_query_goes_out_with_the_key_and_a_longer_one_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    sent: list[httpx.Request] = []
    provider = DeepSeekProvider(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={"results": []}))
    )

    provider.search(SearchRequest(query="q" * 128))
    assert sent[0].headers["Authorization"] == "Bearer test-key"
    with pytest.raises(ProviderError, match="at most 128 characters, and this one would be 129"):
        provider.search(SearchRequest(query="q" * 129))
    # The filters count: a query that fits alone does not once one is appended.
    with pytest.raises(ProviderError, match="would be 131"):
        provider.search(SearchRequest(query="q" * 120, include_domains=["a.com"]))
    assert len(sent) == 1, "only the query that fit went out"


@pytest.mark.parametrize("option", [{"country": "US"}, {"effort": "low"}])
def test_an_unmapped_option_is_refused_rather_than_dropped(provider: DeepSeekProvider, option: dict) -> None:
    with pytest.raises(UnsupportedParameterError, match=next(iter(option))):
        provider.validate(SearchRequest(query="hi", **option))


@pytest.mark.parametrize(
    ("recency", "day"), [("day", "2026-11-08"), ("week", "2026-11-02"), ("month", "2026-10-10"), ("year", "2025-11-09")]
)
def test_recency_becomes_an_after_operator_from_the_day_its_window_starts(
    provider: DeepSeekProvider, recency: str, day: str
) -> None:
    with pinned_today(date(2026, 11, 9)):
        payload = provider._build_payload(SearchRequest(query="hi", recency=recency))
    assert payload == {"query": f"hi after:{day}"}


def test_recency_counts_toward_the_query_limit(provider: DeepSeekProvider) -> None:
    """after:YYYY-MM-DD adds 17 characters, so a 112-character query no longer fits."""
    with pytest.raises(ProviderError, match="would be 129"):
        provider._build_payload(SearchRequest(query="q" * 112, recency="week"))


def test_lite_is_the_default_and_deep_asks_for_the_page_body(
    provider: DeepSeekProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snippet and summary always come back; need_content adds the body. Nothing sits between, so fast is refused."""
    assert provider._build_payload(SearchRequest(query="hi", search_depth="lite")) == {"query": "hi"}
    assert provider._build_payload(SearchRequest(query="hi", search_depth="deep")) == {
        "query": "hi",
        "need_content": True,
    }
    monkeypatch.setattr(provider, "_search", lambda request: pytest.fail("sent"))
    with pytest.raises(UnsupportedParameterError, match=r"search_depth='fast'.*'lite', 'deep'"):
        provider.search(SearchRequest(query="hi", search_depth="fast"))


def test_results_map_and_nothing_is_invented(provider: DeepSeekProvider) -> None:
    response = provider._to_response(SearchRequest(query="DeepSeek V4 release"), DOCUMENTED)
    first, second = response.results

    assert response.provider == "deepseek" and response.raw == DOCUMENTED
    assert first.description == "Short display snippet."
    # No page body was asked for, so the model-written summary is the fullest text.
    assert first.content == DOCUMENTED["results"][0]["summary"]
    assert first.score == 2.4 and first.published_date == "2026-06-01T00:00:00+00:00"
    assert first.extra == {
        "summary": DOCUMENTED["results"][0]["summary"],
        "site_name": "Example News",
        "site_icon": "https://example.com/favicon.ico",
        "authority": 3,
    }
    assert second.content == "The full page body."
    assert second.description == "A summary with no snippet beside it."
    assert second.published_date is None and second.extra["authority"] == -1


def test_a_date_that_is_not_a_string_stays_in_extra_rather_than_failing_the_search(
    provider: DeepSeekProvider,
) -> None:
    payload = {"results": [{"url": "u", "snippet": "S", "date": 1791654966}]}
    (result,) = provider._to_response(SearchRequest(query="hi"), payload).results

    assert result.published_date is None and result.extra == {"date": 1791654966}


def test_an_empty_answer_is_an_empty_response(provider: DeepSeekProvider) -> None:
    assert provider._to_response(SearchRequest(query="hi"), {"results": []}).results == []


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_live_search_returns_results() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("deepseek") as router, billing_aware("deepseek"):
        response = router.search("Who created the Python programming language?", max_results=3)

    assert response.provider == "deepseek"
    assert 0 < len(response.results) <= 3
    assert all(result.url and result.content for result in response.results)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_live_site_operators_joined_by_or_keep_every_result_on_those_sites() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    sites = ("github.com", "huggingface.co")
    with WebRouter("deepseek") as router, billing_aware("deepseek"):
        response = router.search("DeepSeek V3 model weights", max_results=10, include_domains=list(sites))

    assert response.results
    assert all(any(site in result.url for site in sites) for result in response.results)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_live_lang_operator_changes_the_language_of_the_results() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("deepseek") as router, billing_aware("deepseek"):
        response = router.search("coffee brewing methods", max_results=5, locale="zh")

    # An English query answered in English unfiltered; lang:zh should bring back Chinese pages.
    assert any(
        any("\u4e00" <= ch <= "\u9fff" for ch in result.title + result.description) for result in response.results
    )


PAGE = "https://en.wikipedia.org/wiki/Python_(programming_language)"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_live_deep_returns_page_bodies_longer_than_lite() -> None:
    """Look at the lengths: deep must carry whole pages, well past lite's summary."""
    from web_router import WebRouter

    with WebRouter("deepseek") as router:
        lite = router.search("what is retrieval augmented generation", max_results=3, search_depth="lite")
        deep = router.search("what is retrieval augmented generation", max_results=3, search_depth="deep")
    assert lite.results and deep.results
    assert max(len(r.content) for r in deep.results) > max(len(r.content) for r in lite.results)


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> DeepSeekExtractProvider:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    return DeepSeekExtractProvider()


@pytest.mark.parametrize("body_format", ["html", "text"])
def test_a_format_other_than_markdown_is_refused(extractor: DeepSeekExtractProvider, body_format: str) -> None:
    with pytest.raises(UnsupportedFormatError):
        extractor.validate(ExtractRequest(urls=[PAGE], format=body_format))


def test_the_page_maps_under_the_url_asked_for(extractor: DeepSeekExtractProvider) -> None:
    payload = {"title": "Python (programming language)", "content": "# Python\n\nA programming language."}
    (document,) = extractor._to_response(ExtractRequest(urls=[PAGE]), [PAGE], payload).documents

    assert document.url == PAGE and document.title == "Python (programming language)"
    assert document.content == payload["content"] and document.format == "markdown"
    assert document.extra == {}


def test_a_refused_or_missing_page_fails_that_url_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    refused, missing = "https://example.com/refused", "https://example.com/missing"
    limited = "https://example.com/limited"
    statuses = {refused: 403, missing: 404, limited: 429}

    def handle(request: httpx.Request) -> httpx.Response:
        status = statuses.get(json.loads(request.content)["url"])
        if status:
            error = {"message": "Fetch failed", "type": "invalid_request_error", "param": None, "code": status}
            return httpx.Response(status, json={"error": error})
        return httpx.Response(200, json={"title": "Python", "content": "A programming language."})

    extractor = DeepSeekExtractProvider(transport=httpx.MockTransport(handle))
    response = extractor.extract(ExtractRequest(urls=[PAGE, refused, missing, limited]))

    assert [d.url for d in response.documents] == [PAGE]
    assert [(f.url, str(statuses[f.url]) in f.error) for f in response.failures] == [
        (refused, True),
        (missing, True),
        (limited, True),
    ]


def test_fetches_start_no_faster_than_deepseek_allows() -> None:
    # 5 a second per account: no gap between starts may be shorter than a fifth of a second.
    assert 5 * DeepSeekExtractProvider.min_start_interval >= 1.0


def _rate_limited(starts: list[float], window: float) -> Any:
    """A Fetch that answers fast, and 429 once more than 5 calls started in the trailing ``window``."""

    def answer() -> httpx.Response:
        now = time.monotonic()
        starts.append(now)
        if sum(now - window < t for t in starts) > 5:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}})
        return httpx.Response(200, json={"title": "", "content": "page"})

    return answer


@pytest.mark.parametrize("path", ["sync", "async"])
def test_fast_pages_are_spaced_so_none_draws_a_429(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    """The review's repro, scaled ten times faster: unspaced, 25 of 30 fast pages failed."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(DeepSeekExtractProvider, "min_start_interval", DeepSeekExtractProvider.min_start_interval / 10)
    # The 0.9 is slack for timer jitter, so a start that wakes a hair early does not count six in the window.
    window = 5 * DeepSeekExtractProvider.min_start_interval * 0.9
    starts: list[float] = []
    answer = _rate_limited(starts, window)
    request = ExtractRequest(urls=[f"https://example.com/{i}" for i in range(30)])

    if path == "sync":
        extractor = DeepSeekExtractProvider(transport=httpx.MockTransport(lambda _: answer()))
        response = extractor.extract(request)
    else:

        async def handle(_: httpx.Request) -> httpx.Response:
            await asyncio.sleep(window / 10)
            return answer()

        async def run() -> ExtractResponse:
            extractor = DeepSeekExtractProvider(transport=httpx.MockTransport(handle))
            try:
                return await extractor.aextract(request)
            finally:
                await extractor.aclose()

        response = asyncio.run(run())

    assert len(response.documents) == 30 and response.failures == []


def test_an_empty_page_is_a_failure_not_an_empty_document(extractor: DeepSeekExtractProvider) -> None:
    response = extractor._to_response(ExtractRequest(urls=[PAGE]), [PAGE], {"title": "", "content": ""})

    assert response.documents == [] and [f.url for f in response.failures] == [PAGE]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_live_extract_returns_the_page() -> None:
    from tests.conftest import billing_aware
    from web_router import WebRouter

    with WebRouter("deepseek") as router, billing_aware("deepseek"):
        response = router.extract(PAGE)

    (document,) = response.documents
    assert document.format == "markdown" and "Python" in document.content
