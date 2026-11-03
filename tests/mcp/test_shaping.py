"""What the MCP layer is allowed to throw away, and what it must say when it does."""

from __future__ import annotations

import json

import pytest

from web_router.constants import (
    ENV_MCP_MAX_CHARS,
    MCP_MAX_RESPONSE_CHARS,
    MCP_REDACTED,
    MCP_TRUNCATION_MARKER,
)
from web_router.mcp.shaping import (
    HandleStore,
    max_response_chars,
    redact,
    render,
    shape_answer,
    shape_browse,
    shape_extract,
    shape_search,
    truncate,
)
from web_router.types import (
    BrowseResponse,
    BrowseStep,
    Citation,
    ExtractedDocument,
    ExtractResponse,
    SearchResponse,
    SearchResult,
)
from web_router.types_answer import AnswerResponse

_PROSE = "alpha beta gamma delta " * 200


def _search_response(count: int = 5) -> SearchResponse:
    return SearchResponse(
        provider="fake",
        query="q",
        results=[
            SearchResult(url=f"https://example.com/{index}", title=f"page {index}", content=_PROSE)
            for index in range(count)
        ],
        raw={"everything": "SENTINEL " + _PROSE},
    )


def test_truncate_cuts_at_a_word_boundary_and_marks_it() -> None:
    cut = truncate(_PROSE, 50)

    assert cut.endswith(MCP_TRUNCATION_MARKER)
    body = cut.removesuffix(MCP_TRUNCATION_MARKER)
    assert len(body) <= 50
    assert body.split()[-1] in {"alpha", "beta", "gamma", "delta"}


def test_truncate_leaves_text_that_already_fits_alone() -> None:
    assert truncate("short", 50) == "short"


def test_a_shaped_search_fits_the_budget_and_says_it_was_cut() -> None:
    payload = shape_search(_search_response(), "search-0001", budget=1200)

    assert len(render(payload)) <= 1200
    assert payload["truncated"] is True
    assert "fetch_raw" in payload["note"]
    assert payload["handle"] in payload["note"]


def test_raw_is_never_inlined_in_a_shaped_result() -> None:
    payload = shape_search(_search_response(), "search-0001", budget=MCP_MAX_RESPONSE_CHARS)

    assert "raw" not in payload
    assert "SENTINEL" not in json.dumps(payload)


def test_dropping_whole_results_is_reported_with_the_count() -> None:
    payload = shape_search(_search_response(count=20), "search-0001", budget=800)

    assert len(payload["results"]) < 20
    # The count of what came back is kept even when the results themselves are not.
    assert payload["result_count"] == 20
    assert "did not fit" in payload["note"]


def test_an_untruncated_result_carries_no_note() -> None:
    payload = shape_search(
        SearchResponse(provider="fake", query="q", results=[SearchResult(url="https://example.com", content="hi")]),
        "search-0001",
        budget=MCP_MAX_RESPONSE_CHARS,
    )

    assert payload["truncated"] is False
    assert "note" not in payload


def test_extract_keeps_failures_whole() -> None:
    response = ExtractResponse(
        provider="fake",
        documents=[ExtractedDocument(url="https://example.com/a", content=_PROSE, format="markdown")],
        failures=[{"url": "https://example.com/b", "error": "403"}],  # type: ignore[list-item]
    )

    payload = shape_extract(response, "extract-0001", budget=600)

    assert payload["failures"] == [{"url": "https://example.com/b", "error": "403"}]
    assert payload["documents"][0]["content"].endswith(MCP_TRUNCATION_MARKER)


def test_the_store_evicts_the_oldest_handle() -> None:
    store = HandleStore(limit=2)

    first = store.add("search", raw={"n": 1})
    second = store.add("search", raw={"n": 2})
    third = store.add("search", raw={"n": 3})

    assert len(store) == 2
    assert store.get(first.handle) is None
    assert store.get(second.handle) is not None
    assert store.get(third.handle) is not None


def test_handles_are_unique_across_a_session() -> None:
    store = HandleStore()

    assert store.add("search").handle != store.add("search").handle


def test_the_budget_is_overridable_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MCP_MAX_CHARS, "123")
    assert max_response_chars() == 123

    # A typo in a client config must not stop the server starting.
    monkeypatch.setenv(ENV_MCP_MAX_CHARS, "lots")
    assert max_response_chars() == MCP_MAX_RESPONSE_CHARS


def test_the_last_result_is_squeezed_rather_than_dropped() -> None:
    payload = shape_search(_search_response(count=1), "search-0001", budget=800)

    assert len(payload["results"]) == 1
    assert payload["results"][0]["url"] == "https://example.com/0"
    assert payload["results"][0]["content"].endswith(MCP_TRUNCATION_MARKER)
    assert len(render(payload)) <= 800


def test_a_budget_too_small_for_the_shape_keeps_the_shape() -> None:
    # The note explaining the truncation is itself longer than this budget.
    # Better to overshoot than to hand back something with no URL in it.
    payload = shape_search(_search_response(count=1), "search-0001", budget=100)

    assert payload["results"][0]["url"] == "https://example.com/0"
    assert payload["truncated"] is True


@pytest.mark.parametrize(
    "key",
    ["liveViewUrl", "live_view_url", "debuggerUrl", "CDPUrl", "cdp_url", "wsEndpoint", "websocketUrl", "connectUrl"],
)
def test_a_url_under_a_session_key_is_redacted_whatever_it_looks_like(key: str) -> None:
    assert redact({key: "https://plain.example.test/path"}) == ({key: MCP_REDACTED}, 1)


@pytest.mark.parametrize("key", ["sessionId", "session_id", "browserSessionId", "SESSION-ID", "sessionIds"])
def test_a_session_id_is_redacted_whatever_its_value(key: str) -> None:
    assert redact({key: "a1b2c3"}) == ({key: MCP_REDACTED}, 1)


def test_only_an_id_is_taken_for_a_session_id_not_every_session_field() -> None:
    kept = {"session": {"status": "RUNNING", "region": "us-west-2"}, "sessions": 2, "sessionTimeout": 300}

    assert redact(kept) == (kept, 0)


@pytest.mark.parametrize("param", ["token", "apiKey", "api_key", "signingKey", "jwt", "key", "access_token"])
def test_a_url_carrying_a_credential_is_redacted_under_any_key(param: str) -> None:
    assert redact({"url": f"https://example.com/a?x=1&{param}=s"}) == ({"url": MCP_REDACTED}, 1)


def test_redaction_leaves_ordinary_values_alone() -> None:
    ordinary = {"url": "https://example.com/?q=monkey", "monkey": "banana", "keyboard": "https://example.com/k", "n": 3}

    assert redact(ordinary) == (ordinary, 0)


def test_a_websocket_url_and_a_url_with_userinfo_are_redacted_anywhere() -> None:
    cleaned, count = redact(["see wss://cdp.example.test/abc", "https://user:pass@example.com/"])

    assert cleaned == [f"see {MCP_REDACTED}", MCP_REDACTED]
    assert count == 2


def test_a_shaped_browse_task_keeps_its_steps_and_drops_their_extra() -> None:
    response = BrowseResponse(
        provider="fake",
        task="t",
        answer="done",
        steps=[BrowseStep(action="click", detail=_PROSE, extra={"secret": "SENTINEL"}) for _ in range(20)],
        raw=[{"everything": "SENTINEL"}],
    )

    payload = shape_browse(response, "browse-0001", budget=1500)

    assert len(render(payload)) <= 1500
    assert payload["step_count"] == 20
    assert payload["truncated"] is True
    assert "SENTINEL" not in render(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"live": {"url": "https://plain.example.test/"}},
        {"session": {"liveViews": [{"href": "https://plain.example.test/"}]}},
        {"token": {"value": "S"}},
        {"tokens": ["S"]},
        {"cookies": [{"name": "sid", "value": "S"}]},
        {"auth": ("S", "T")},
    ],
    ids=["nested-session", "plural-session", "nested-secret", "plural-secret", "cookie-list", "tuple"],
)
def test_key_context_reaches_everything_beneath_it(payload: dict[str, object]) -> None:
    cleaned, count = redact(payload)

    assert count > 0
    assert "S" not in render(cleaned).replace("session", "").replace("sid", "")
    assert "plain.example.test" not in render(cleaned)


@pytest.mark.parametrize("key", ["CDPURL", "connectionurl", "ConnectionURL", "devToolsFrontendUrl"])
def test_session_keys_match_without_separators_or_case(key: str) -> None:
    assert redact({key: "https://plain.example.test/"}) == ({key: MCP_REDACTED}, 1)


@pytest.mark.parametrize(
    "key",
    [
        "authorization",
        "Authorization",
        "cookie",
        "set-cookie",
        "Set-Cookie",
        "credentials",
        "auth",
        "privateKey",
        "private_key",
        "accessKey",
        "secret",
        "passwd",
        "password",
    ],
)
def test_a_credential_named_field_is_redacted(key: str) -> None:
    assert redact({key: "S"}) == ({key: MCP_REDACTED}, 1)


@pytest.mark.parametrize("param", ["x-api-key", "xapikey", "X_API_KEY", "auth", "signature", "sig"])
def test_the_extra_credential_query_parameters_are_redacted(param: str) -> None:
    assert redact({"url": f"https://example.com/a?{param}=s"}) == ({"url": MCP_REDACTED}, 1)


def test_a_url_with_only_a_username_is_redacted() -> None:
    assert redact("see https://user@example.com/x") == (f"see {MCP_REDACTED}", 1)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9222/devtools/browser/abc",
        "https://example.test/cdp/session",
        "https://live.example.test/view",
        "https://debug.example.test/",
        "ws://127.0.0.1:9222/",
    ],
)
def test_a_session_url_in_free_text_is_redacted(url: str) -> None:
    assert redact(f"the agent attached to {url} and carried on") == (
        f"the agent attached to {MCP_REDACTED} and carried on",
        1,
    )


@pytest.mark.parametrize(
    "text",
    [
        "connect with ws=127.0.0.1:9222/devtools/page/ABC123 now",
        "the inspector is at localhost:9222/devtools/browser/1f2e",
        "path /devtools/browser/1f2e was logged",
        "[::1]:9222/devtools/page/9",
    ],
)
def test_a_devtools_endpoint_written_without_a_scheme_is_redacted(text: str) -> None:
    cleaned, count = redact(text)

    assert count == 1 and "/devtools/" not in cleaned and MCP_REDACTED in cleaned


def test_a_long_answer_is_cut_and_keeps_its_cited_sources_first() -> None:
    uncited = [Citation(url=f"https://read.example/{i}", snippet=_PROSE) for i in range(30)]
    cited = Citation(url="https://cited.example", snippet=_PROSE, field="[31]")
    response = AnswerResponse(provider="perplexity", query="q", answer=_PROSE * 3, citations=[*uncited, cited])

    shaped = shape_answer(response, "answer-0001", budget=4000)

    assert len(render(shaped)) <= 4000
    assert shaped["answer"].endswith(MCP_TRUNCATION_MARKER)
    assert shaped["truncated"] is True
    assert shaped["citation_count"] == 31, "the count is the backend's, whatever the cut left"
    assert shaped["citations"][0]["url"] == "https://cited.example", "a cited source outlives the cut"
