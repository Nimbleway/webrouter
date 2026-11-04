"""AgentCore Web Search provider tests.

Shapes from AWS's documentation of the web-search connector and its gateway
(docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html
and gateway-using-mcp-call.html): tools are named ``<target>___WebSearch``, the
input schema gains ``filters`` from connector 1.2.0, and the tool answers with
its results as a JSON string in a text block and, from 1.1.0, in
``structuredContent``.

Checked live on 2026-10-26 against a gateway with a default-version target:
``tools/list`` named the tool ``web-search___WebSearch`` with ``filters`` in its
schema; both calls answered plain JSON, not a stream; every hit carried
``text``, ``url``, ``title`` and ``publishedDate``; domain and date filters were
honoured; and ``publishedDate`` came as ``05:00PM, Sunday, October 06 2024, PDT``
rather than the ISO date AWS's example shows. It is passed through as sent, as
other backends' dates are.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from tests.providers._agentcore import (  # noqa: F401  (aws_env: autouse fixture)
    FILTERS_SCHEMA,
    GATEWAY,
    HITS,
    NO_FILTERS_SCHEMA,
    TOOL,
    aws_env,
)
from tests.providers._agentcore import gateway as _gateway
from tests.providers._agentcore import provider as _provider
from tests.providers._agentcore import reply as _reply
from tests.providers._agentcore import tools as _tools
from web_router import Provider, WebRouter
from web_router.exceptions import ConfigurationError, ProviderError, UnsupportedParameterError
from web_router.providers.agentcore import AgentCoreProvider
from web_router.providers.auto import AutoProvider
from web_router.types import SearchRequest

# -- The request -------------------------------------------------------------


def test_max_results_and_domains_map_onto_the_tool_arguments() -> None:
    provider = _provider(_gateway())
    payload = provider._build_payload(
        SearchRequest(query="hi", max_results=5, include_domains=["python.org"], exclude_domains=["spam.com"])
    )

    assert payload == {
        "query": "hi",
        "maxResults": 5,
        "filters": {"domainFilter": {"include": ["python.org"], "exclude": ["spam.com"]}},
    }


def test_a_date_range_in_provider_options_keeps_the_mapped_domains() -> None:
    provider = _provider(_gateway())
    dates = {"from": "2026-10-01T00:00:00Z"}
    payload = provider._build_payload(
        SearchRequest(
            query="hi", include_domains=["python.org"], provider_options={"filters": {"publishedDateFilter": dates}}
        )
    )

    assert payload["filters"] == {"domainFilter": {"include": ["python.org"]}, "publishedDateFilter": dates}


def test_a_domain_list_set_twice_raises_rather_than_one_winning() -> None:
    provider = _provider(_gateway())
    request = SearchRequest(
        query="hi",
        include_domains=["python.org"],
        provider_options={"filters": {"domainFilter": {"include": ["other.org"]}}},
    )

    with pytest.raises(ConfigurationError, match=r"domainFilter\.include"):
        provider._build_payload(request)


def test_other_domain_keys_in_provider_options_merge_with_the_mapped_ones() -> None:
    provider = _provider(_gateway())
    request = SearchRequest(
        query="hi",
        include_domains=["python.org"],
        provider_options={"filters": {"domainFilter": {"exclude": ["x.com"]}}},
    )

    assert provider._build_payload(request)["filters"] == {
        "domainFilter": {"exclude": ["x.com"], "include": ["python.org"]}
    }


@pytest.mark.parametrize(
    ("request_", "message"),
    [
        (SearchRequest(query="q" * 201), "up to 200 characters"),
        (SearchRequest(query="hi", max_results=26), "at most 25 results"),
    ],
    ids=["query", "max_results"],
)
def test_requests_past_the_tools_limits_raise_before_any_call(request_: SearchRequest, message: str) -> None:
    seen: list[httpx.Request] = []

    with pytest.raises(ProviderError, match=message):
        _provider(_gateway(seen=seen)).search(request_)
    assert seen == []


def test_an_option_agentcore_does_not_map_is_refused() -> None:
    with pytest.raises(UnsupportedParameterError, match="country"):
        _provider(_gateway()).search(SearchRequest(query="hi", country="US"))


# -- The call ----------------------------------------------------------------


def test_the_call_goes_to_the_gateway_path_with_the_prefixed_tool() -> None:
    seen: list[httpx.Request] = []
    response = _provider(_gateway(seen=seen)).search(SearchRequest(query="python 3.13", max_results=2))

    assert [str(request.url) for request in seen] == [GATEWAY, GATEWAY]
    call = json.loads(seen[1].content)
    assert call["method"] == "tools/call"
    assert call["params"] == {"name": TOOL, "arguments": {"query": "python 3.13", "maxResults": 2}}
    assert response.provider == "agentcore"


def test_the_tool_list_is_read_once() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(_gateway(seen=seen))
    provider.search(SearchRequest(query="one"))
    provider.search(SearchRequest(query="two"))

    methods = [json.loads(request.content)["method"] for request in seen]
    assert methods == ["tools/list", "tools/call", "tools/call"]


def test_the_tool_list_is_followed_across_pages() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "tools/list":
            if body["params"].get("cursor") == "page-2":
                return _reply(_tools(TOOL))
            return _reply({**_tools("other___Thing"), "nextCursor": "page-2"})
        return _reply({"content": [{"type": "text", "text": json.dumps({"results": HITS})}]})

    response = _provider(httpx.MockTransport(handle)).search(SearchRequest(query="hi"))

    assert len(response.results) == 1


def _repeating_cursor(request: httpx.Request) -> httpx.Response:
    return _reply({**_tools("other___Thing"), "nextCursor": "same"})


def test_a_repeated_tool_list_cursor_stops_the_listing() -> None:
    with pytest.raises(ProviderError, match="repeated the page cursor"):
        _provider(httpx.MockTransport(_repeating_cursor)).search(SearchRequest(query="hi"))


async def test_a_repeated_tool_list_cursor_stops_the_async_listing() -> None:
    provider = _provider(httpx.MockTransport(_repeating_cursor))
    try:
        with pytest.raises(ProviderError, match="repeated the page cursor"):
            await provider.asearch(SearchRequest(query="hi"))
    finally:
        await provider.aclose()


@pytest.mark.parametrize("names", [(), (TOOL, "second___WebSearch")], ids=["none", "two"])
def test_a_gateway_without_exactly_one_search_tool_raises(names: tuple[str, ...]) -> None:
    provider = _provider(_gateway(listing=_tools(*names)))

    with pytest.raises(ConfigurationError, match="needs exactly one"):
        provider.search(SearchRequest(query="hi"))


@pytest.mark.parametrize(
    "request_",
    [
        SearchRequest(query="hi", include_domains=["python.org"]),
        SearchRequest(query="hi", provider_options={"filters": {"publishedDateFilter": {"from": "2026-01-01"}}}),
    ],
    ids=["domains", "dates"],
)
def test_filters_on_a_target_without_them_are_refused_not_dropped(request_: SearchRequest) -> None:
    seen: list[httpx.Request] = []
    provider = _provider(_gateway(listing=_tools(TOOL, schema=NO_FILTERS_SCHEMA), seen=seen))

    # A ConfigurationError, so auto moves on to its next member rather than stopping.
    with pytest.raises(ConfigurationError, match=r"1\.2\.0"):
        provider.search(request_)
    assert [json.loads(request.content)["method"] for request in seen] == ["tools/list"]


@pytest.mark.parametrize("options", [{}, {"filters": {}}, {"filters": None}], ids=["none", "empty", "null"])
def test_a_target_without_filters_still_searches(options: dict[str, Any]) -> None:
    provider = _provider(_gateway(listing=_tools(TOOL, schema=NO_FILTERS_SCHEMA)))

    assert len(provider.search(SearchRequest(query="hi", provider_options=options)).results) == 1


@pytest.mark.parametrize("url", ["http://example.com/mcp", "not a url", "https://[bad/mcp"])
def test_a_gateway_url_that_is_not_https_raises(url: str) -> None:
    with pytest.raises(ConfigurationError, match="AGENTCORE_GATEWAY_URL"):
        _provider(_gateway(), url=url).search(SearchRequest(query="hi"))


def test_a_missing_gateway_url_raises_when_called_not_when_built() -> None:
    provider = AgentCoreProvider(transport=_gateway())

    with pytest.raises(ConfigurationError, match="AGENTCORE_GATEWAY_URL"):
        provider.search(SearchRequest(query="hi"))


def test_the_gateway_url_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTCORE_GATEWAY_URL", GATEWAY)
    seen: list[httpx.Request] = []
    AgentCoreProvider(transport=_gateway(seen=seen)).search(SearchRequest(query="hi"))

    assert str(seen[0].url) == GATEWAY


async def test_async_search_lists_the_tool_and_calls_it() -> None:
    seen: list[httpx.Request] = []
    provider = _provider(_gateway(seen=seen))
    try:
        response = await provider.asearch(SearchRequest(query="hi", include_domains=["python.org"]))
    finally:
        await provider.aclose()

    assert [json.loads(request.content)["method"] for request in seen] == ["tools/list", "tools/call"]
    assert len(response.results) == 1


# -- The response ------------------------------------------------------------


def test_hits_normalize_into_results() -> None:
    response = _provider(_gateway()).search(SearchRequest(query="python 3.13"))

    (first,) = response.results
    assert first.url == HITS[0]["url"]
    assert first.title == HITS[0]["title"]
    assert first.content == HITS[0]["text"]
    assert first.description == HITS[0]["text"]
    assert first.published_date == "05:00PM, Sunday, October 06 2024, PDT"
    assert first.score is None
    assert response.request_id == "824f89d0"
    # A hit with no page, as a knowledge-graph fact may be, is no result to cite; raw keeps it.
    assert response.raw["results"][1] == HITS[1]


@pytest.mark.parametrize(
    "result",
    [
        {"content": [], "structuredContent": {"results": HITS}},
        {"content": [{"type": "text", "text": "prose"}], "structuredContent": {"results": HITS}},
        {"content": [{"type": "text", "text": json.dumps(HITS)}]},
    ],
    ids=["structured-only", "structured-over-prose", "bare-list"],
)
def test_every_documented_reply_shape_is_read(result: dict[str, Any]) -> None:
    response = _provider(_gateway(lambda _: _reply(result))).search(SearchRequest(query="hi"))

    assert [hit.content for hit in response.results] == [hit["text"] for hit in HITS if hit.get("url")]


def test_a_streamed_reply_is_read() -> None:
    reply = {"content": [{"type": "text", "text": json.dumps({"results": HITS})}]}
    response = _provider(_gateway(lambda _: _reply(reply, sse=True))).search(SearchRequest(query="hi"))

    assert len(response.results) == 1


def test_an_empty_structured_reply_is_an_empty_answer() -> None:
    reply = {"content": [], "structuredContent": {"results": []}}

    assert _provider(_gateway(lambda _: _reply(reply))).search(SearchRequest(query="hi")).results == []


def test_raw_keeps_every_field_the_tool_sent() -> None:
    reply = {"content": [{"type": "text", "text": json.dumps({"id": "x1", "results": HITS, "answer": "kg fact"})}]}
    raw = _provider(_gateway(lambda _: _reply(reply))).search(SearchRequest(query="hi")).raw

    assert raw["answer"] == "kg fact"
    assert raw["id"] == "x1"


def test_no_results_is_an_empty_answer_not_a_failure() -> None:
    reply = {"content": [{"type": "text", "text": json.dumps({"results": []})}]}

    assert _provider(_gateway(lambda _: _reply(reply))).search(SearchRequest(query="hi")).results == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad"}}),
        _reply({"isError": True, "content": [{"type": "text", "text": "throttled"}]}),
        _reply({"content": [{"type": "text", "text": "not json"}]}),
        httpx.Response(403, text="signature mismatch"),
    ],
    ids=["jsonrpc-error", "tool-error", "not-json", "http-403"],
)
def test_a_failed_call_raises_a_provider_error(response: httpx.Response) -> None:
    with pytest.raises(ProviderError):
        _provider(_gateway(lambda _: response)).search(SearchRequest(query="hi"))


def test_auto_counts_a_gateway_set_in_code_as_configured() -> None:
    assert "agentcore" not in AutoProvider().members
    assert "agentcore" in AutoProvider(members_config={"agentcore": {"gateway_url": GATEWAY}}).members


def test_the_router_reaches_it_by_name() -> None:
    router = WebRouter(
        Provider.AGENTCORE, provider_config={"agentcore": {"gateway_url": GATEWAY, "transport": _gateway()}}
    )

    assert router.search("hi").provider == "agentcore"


# -- Live --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("AGENTCORE_GATEWAY_URL"), reason="AGENTCORE_GATEWAY_URL not set")
def test_live_search_with_domains_and_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the real AWS setup, not the fake credentials above
    provider = AgentCoreProvider()
    try:
        response = provider.search(
            SearchRequest(
                query="python release",
                max_results=5,
                include_domains=["python.org"],
                provider_options={"filters": {"publishedDateFilter": {"from": "2024-01-01T00:00:00Z"}}},
            )
        )
    finally:
        provider.close()

    assert response.results
    assert all("python.org" in result.url for result in response.results if result.url)


def test_the_tool_list_stops_paging_once_the_deadline_has_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each page is clamped, but never below the timeout floor, so the next one is refused outright."""
    from types import SimpleNamespace

    from web_router.exceptions import DeadlineExceededError
    from web_router.providers import _utils, base
    from web_router.providers._utils import call_deadline

    clock = SimpleNamespace(now=0.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now, sleep=lambda seconds: None)
    monkeypatch.setattr(base, "time", fake_time)
    monkeypatch.setattr(_utils, "time", fake_time)
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        clock.now = 5.0
        return _reply({**_tools("other___Thing"), "nextCursor": f"page-{len(seen) + 1}"})

    with call_deadline(3.0), pytest.raises(DeadlineExceededError, match="3s deadline"):
        _provider(httpx.MockTransport(handle)).search(SearchRequest(query="hi"))

    assert len(seen) == 1, "no page was asked for after the deadline"
