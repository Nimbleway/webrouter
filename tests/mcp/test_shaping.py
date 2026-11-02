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
    shape_browse,
    shape_extract,
    shape_search,
    shape_template,
    shape_template_run,
    shape_templates,
    truncate,
)
from web_router.types import (
    BrowseResponse,
    BrowseStep,
    ExtractedDocument,
    ExtractResponse,
    SearchResponse,
    SearchResult,
    Template,
    TemplatePage,
    TemplateResponse,
    TemplateSample,
)

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


_BIG_SCHEMA = {"type": "object", "properties": {f"field_{index}": {"type": "string"} for index in range(400)}}


def test_a_schema_that_fits_is_never_shortened() -> None:
    template = Template(provider="fake", name="t", input_schema=_BIG_SCHEMA, output_schema=_BIG_SCHEMA)

    shaped = shape_template(template, "template-0001", budget=100_000)

    assert shaped["template"]["input_schema"] == _BIG_SCHEMA
    assert shaped["template"]["output_schema"] == _BIG_SCHEMA
    assert shaped["truncated"] is False


def test_a_schema_that_cannot_fit_is_left_out_whole_with_a_pointer_to_the_handle() -> None:
    """A cut schema would send the model guessing at the inputs it dropped, so it goes whole or not at all."""
    template = Template(provider="fake", name="t", input_schema={"type": "object"}, output_schema=_BIG_SCHEMA)

    shaped = shape_template(template, "template-0001", budget=2_000)

    assert len(render(shaped)) <= 2_000
    assert shaped["template"]["output_schema"] is None, "the oversized one goes"
    assert shaped["template"]["input_schema"] == {"type": "object"}, "the one that fits stays"
    assert shaped["truncated"] is True
    assert "output_schema" in shaped["note"] and "template-0001" in shaped["note"]


def test_a_record_that_cannot_fit_is_left_out_whole_with_a_pointer_to_the_handle() -> None:
    response = TemplateResponse(
        provider="fake",
        template="t",
        data={f"field_{index}": _PROSE for index in range(10)},
        document=ExtractedDocument(url="https://example.com", content=_PROSE, format="markdown"),
    )

    shaped = shape_template_run(response, "run_template-0001", budget=3_000)

    assert len(render(shaped)) <= 3_000
    assert shaped["data"] is None
    assert MCP_TRUNCATION_MARKER in shaped["document"]["content"], "the page was cut first, as text is"
    assert shaped["truncated"] is True
    assert "data" in shaped["note"] and "run_template-0001" in shaped["note"]


def test_a_record_that_fits_once_the_page_is_cut_is_kept() -> None:
    response = TemplateResponse(
        provider="fake",
        template="t",
        data={"title": "Widget", "price": "9.99"},
        document=ExtractedDocument(url="https://example.com", content=_PROSE, format="markdown"),
    )

    shaped = shape_template_run(response, "run_template-0001", budget=1_500)

    assert shaped["data"] == {"title": "Widget", "price": "9.99"}
    assert len(render(shaped)) <= 1_500


def test_a_described_template_carries_its_samples_whole() -> None:
    sample = TemplateSample(input={"url": "https://example.com/p/1"}, output={"title": "Widget"})
    template = Template(provider="fake", name="t", input_schema={"type": "object"}, samples=[sample])

    shaped = shape_template(template, "template-0001", budget=100_000)

    assert shaped["template"]["samples"] == [sample.model_dump()]
    assert shaped["truncated"] is False


def test_samples_that_cannot_fit_go_whole_before_the_schemas_do() -> None:
    """A recorded input with half its fields is not one a run can copy, so samples go whole or not at all."""
    big_output = {f"field_{index}": _PROSE for index in range(10)}
    template = Template(
        provider="fake",
        name="t",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        samples=[TemplateSample(input={"url": "https://example.com"}, output=big_output)],
    )

    shaped = shape_template(template, "template-0001", budget=2_000)

    assert len(render(shaped)) <= 2_000
    assert shaped["template"]["samples"] is None, "the oversized samples go"
    assert shaped["template"]["input_schema"] == {"type": "object"}, "the schemas that fit stay"
    assert shaped["template"]["output_schema"] == {"type": "object"}
    assert shaped["truncated"] is True
    assert "samples" in shaped["note"] and "template-0001" in shaped["note"]


def test_a_listing_row_says_what_a_run_must_pass_without_the_schemas() -> None:
    schema = {"type": "object", "required": ["asin", "zip_code"], "properties": {"asin": {}, "zip_code": {}}}
    sample = TemplateSample(input={"asin": "B000000000", "zip_code": "10001"}, output={"title": "Widget"})
    page = TemplatePage(
        provider="fake",
        templates=[
            Template(provider="fake", name="keyed", input_schema=schema, samples=[sample], published=True),
            Template(provider="fake", name="optional", input_schema={"type": "object"}, published=True),
            Template(provider="fake", name="draft"),
            Template(provider="fake", name="empty", input_schema={}, published=True),
        ],
    )

    shaped = shape_templates(page, "templates-0001", budget=20_000)

    keyed, optional, draft, empty = shaped["templates"]
    assert keyed["required_inputs"] == ["asin", "zip_code"]
    assert keyed["sample_inputs"] == {"asin": "B000000000", "zip_code": "10001"}
    assert "input_schema" not in keyed and "samples" not in keyed, "the schemas and the records are `template`'s"
    assert optional["required_inputs"] == [] and optional["sample_inputs"] is None
    assert draft["required_inputs"] is None, "no published schema, so nothing to read the inputs from"
    assert empty["required_inputs"] == [], "an empty schema is a published one that requires nothing"


def test_a_listing_row_shows_the_first_sample_that_names_an_input() -> None:
    samples = [TemplateSample(output={"title": "Widget"}), TemplateSample(input={"url": "https://example.com/p/1"})]
    fixed_page = [TemplateSample(output={"title": "Widget"})]
    page = TemplatePage(
        provider="fake",
        templates=[
            Template(provider="fake", name="keyed", input_schema={"required": ["url"]}, samples=samples),
            Template(provider="fake", name="fixed", input_schema={}, samples=fixed_page),
        ],
    )

    keyed, fixed = shape_templates(page, "templates-0001", budget=20_000)["templates"]

    assert keyed["sample_inputs"] == {"url": "https://example.com/p/1"}, "not the input-less sample before it"
    assert fixed["sample_inputs"] == {}, "a fixed page's sample takes no inputs, and says so"


@pytest.mark.parametrize("rows", [1, 5])
def test_a_sample_input_too_large_for_the_listing_is_left_out_whole(rows: int) -> None:
    huge = TemplateSample(input={"url": "https://example.com/" + "x" * 24_000})
    small = TemplateSample(input={"url": "https://example.com/p/1"})
    schema = {"type": "object", "required": ["url"]}
    templates = [Template(provider="fake", name="huge", input_schema=schema, samples=[huge])]
    templates += [Template(provider="fake", name=f"t{i}", samples=[small]) for i in range(rows - 1)]

    shaped = shape_templates(TemplatePage(provider="fake", templates=templates), "templates-0001", budget=8_000)

    assert len(render(shaped)) <= 8_000
    assert shaped["template_count"] == rows, "one oversized sample does not crowd out the other rows"
    first, *rest = shaped["templates"]
    assert first["sample_inputs"] is None and first["required_inputs"] == ["url"]
    assert all(row["sample_inputs"] == small.input for row in rest)
    assert shaped["truncated"] is True
    assert "sample_inputs" in shaped["note"] and "templates-0001" in shaped["note"]


def test_a_listing_with_small_sample_inputs_is_not_truncated() -> None:
    sample = TemplateSample(input={"url": "https://example.com/p/1"})
    page = TemplatePage(provider="fake", templates=[Template(provider="fake", name="t", samples=[sample])])

    shaped = shape_templates(page, "templates-0001", budget=8_000)

    assert shaped["templates"][0]["sample_inputs"] == sample.input
    assert shaped["truncated"] is False and "note" not in shaped


def test_a_listing_counts_the_rows_it_shows_not_the_rows_it_was_given() -> None:
    page = TemplatePage(
        provider="fake",
        templates=[Template(provider="fake", name=f"t{i}", description=_PROSE) for i in range(200)],
        total=200,
    )

    shaped = shape_templates(page, "templates-0001", budget=20_000)

    assert shaped["template_count"] == len(shaped["templates"]) < 200
    assert shaped["total"] == 200, "the backend's own count of the catalogue is untouched"


def test_a_run_does_not_echo_its_inputs_so_they_cannot_crowd_out_the_record() -> None:
    response = TemplateResponse(
        provider="fake",
        template="t",
        inputs={f"p{i}": _PROSE for i in range(100)},
        data={"title": "Widget"},
    )

    shaped = shape_template_run(response, "run_template-0001", budget=20_000)

    assert "inputs" not in shaped
    assert shaped["data"] == {"title": "Widget"}
    assert len(render(shaped)) <= 20_000


def test_the_larger_schema_is_dropped_first_so_the_smaller_one_stays() -> None:
    big = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(1000)}}
    small = {"type": "object", "properties": {f"field_{i}": {"type": "string"} for i in range(60)}}
    template = Template(provider="fake", name="t", input_schema=big, output_schema=small)

    shaped = shape_template(template, "template-0001", budget=20_000)

    assert shaped["template"]["input_schema"] is None, "the 30k schema goes"
    assert shaped["template"]["output_schema"] == small, "the 3k schema stays"
    assert len(render(shaped)) <= 20_000
