"""The Nimble Extract Template provider, over the SDK's own response shapes.

The payloads are modelled on the API's response shapes: the run envelope on
a recorded ``extract/templates/run`` answer, and the catalogue items on the
SDK's generated ``TemplateListResponse`` / ``TemplateGetResponse`` models
plus the fields a recorded listing carried that the models do not declare
(``privacy``, ``managed_by``). Names, ids and timestamps are synthetic.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from web_router.exceptions import ProviderError, TemplateNotFoundError, UnsupportedFormatError
from web_router.types import TemplateRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from nimble_python import AsyncNimble, Nimble

from web_router.providers.nimble_template import NimbleTemplateProvider

TEMPLATE = "books_pdp"
URL = "https://books.example/catalogue/a-light-in-the-attic/index.html"
RECORD = {"title": "A Light in the Attic", "price": "£51.77", "upc": "0000000000000001", "num_reviews": 0}
TASK_ID = "00000000-0000-4000-8000-000000000001"
TEMPLATE_ID = "00000000-0000-4000-8000-000000000002"
VERSION_ID = "00000000-0000-4000-8000-000000000003"


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleTemplateProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleTemplateProvider()


def _run_payload(parsing: Any, **extra: Any) -> dict[str, Any]:
    return {
        "task_id": TASK_ID,
        "url": URL,
        "status": "success",
        "status_code": 200.0,
        "metadata": {"driver": "fetch", "query_duration": 1000.0, "response_parameters": {"input_url": URL}},
        "data": {"markdown": None, "html": None, "links": None, "parsing": parsing},
        "pagination": None,
        "warnings": ["render_options is ignored because render is false"],
        **extra,
    }


def _version(**overrides: Any) -> dict[str, Any]:
    return {
        "id": VERSION_ID,
        "created_at": "2026-01-01T00:00:00Z",
        "name": TEMPLATE,
        "version_number": 1,
        "input_schema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
        "metadata": {
            "data_source": "books.example",
            "description": "Extracts book product details.",
            "display_name": "Books - Book Detail",
            "domain": "books.example",
            "entity_type": "Product Detail Page (PDP)",
            "tags": ["books", "ecommerce"],
            "vertical": "Ecommerce",
        },
        "samples": [{"input": {"url": URL}, "output": RECORD}],
        **overrides,
    }


def _item(version: dict[str, Any] | None, **overrides: Any) -> dict[str, Any]:
    return {
        "id": TEMPLATE_ID,
        "name": TEMPLATE,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "published_version": version,
        "published_version_id": version["id"] if version else None,
        "privacy": "private",
        "managed_by": None,
        **overrides,
    }


# -- Building the run --------------------------------------------------------


def test_the_template_and_its_inputs_are_sent_and_nothing_else(provider: NimbleTemplateProvider) -> None:
    """Nimble returns no page content unless `formats` names some, which is what an unset format means."""
    payload = provider._build_payload(TemplateRequest(template=TEMPLATE, inputs={"url": URL}))

    assert payload == {"template": TEMPLATE, "params": {"url": URL}}


@pytest.mark.parametrize(("body_format", "expected"), [("markdown", "markdown"), ("html", "html")])
def test_a_format_maps_onto_nimbles_formats_list(
    provider: NimbleTemplateProvider, body_format: Any, expected: str
) -> None:
    payload = provider._build_payload(TemplateRequest(template=TEMPLATE, inputs={"url": URL}, format=body_format))

    assert payload["formats"] == [expected]


def test_plain_text_is_rejected_because_nimble_does_not_return_it(provider: NimbleTemplateProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        provider.validate(TemplateRequest(template=TEMPLATE, inputs={"url": URL}, format="text"))


def test_provider_options_pass_through(provider: NimbleTemplateProvider) -> None:
    request = TemplateRequest(template=TEMPLATE, inputs={"url": URL}, provider_options={"localization": True})

    assert provider._build_payload(request)["localization"] is True


# -- Reading the run ---------------------------------------------------------


def test_a_bare_record_under_parsing_is_the_record(provider: NimbleTemplateProvider) -> None:
    """The shape the live API answered with: the template's fields straight under `data.parsing`."""
    payload = _run_payload(RECORD)

    response = provider._to_response(TemplateRequest(template=TEMPLATE, inputs={"url": URL}), payload)

    assert response.provider == "nimble"
    assert response.template == TEMPLATE
    assert response.inputs == {"url": URL}
    assert response.data == RECORD
    assert response.url == URL
    assert response.status_code == 200, "Nimble sends its status code as a float"
    assert response.request_id == TASK_ID
    assert response.document is None
    assert response.extra["metadata"]["driver"] == "fetch"
    assert "parsing" not in response.extra and "markdown" not in response.extra
    assert response.raw == payload


def test_a_wrapped_record_is_unwrapped(provider: NimbleTemplateProvider) -> None:
    """The SDK's `ParsingSuccessResult`: the record under `entities` beside a status."""
    payload = _run_payload({"status": "success", "entities": RECORD})

    response = provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert response.data == RECORD


def test_a_parsing_error_is_a_failure_not_an_empty_record(provider: NimbleTemplateProvider) -> None:
    payload = _run_payload({"status": "error", "error": "selector matched nothing"})

    with pytest.raises(ProviderError, match="selector matched nothing") as caught:
        provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert TEMPLATE in str(caught.value)


def test_a_record_whose_own_field_is_called_status_is_not_mistaken_for_a_report(
    provider: NimbleTemplateProvider,
) -> None:
    """Only the two wrapped shapes carry `entities` or `error` beside `status`; a record may use the word too."""
    record = {"status": "error", "title": "Out of stock"}

    response = provider._to_response(TemplateRequest(template=TEMPLATE), _run_payload(record))

    assert response.data == record


def test_a_record_with_a_status_and_entities_beside_other_fields_is_kept_whole(
    provider: NimbleTemplateProvider,
) -> None:
    """A wrapper is exactly the SDK's two fields; a record whose schema names both and more is a record."""
    record = {"status": "success", "entities": {"brand": "Acme"}, "title": "Widget"}

    response = provider._to_response(TemplateRequest(template=TEMPLATE), _run_payload(record))

    assert response.data == record


def test_a_fetched_page_with_no_record_raises(provider: NimbleTemplateProvider) -> None:
    with pytest.raises(ProviderError, match="without a record"):
        provider._to_response(TemplateRequest(template=TEMPLATE), _run_payload(None))


@pytest.mark.parametrize("status_code", [404.0, 403, 500.0, 503])
def test_an_error_page_the_site_served_is_a_failed_run_not_a_record(
    provider: NimbleTemplateProvider, status_code: float
) -> None:
    """A recorded response for a missing page: `success`, `status_code: 404`, and a "record" titled "404 Error"."""
    payload = _run_payload({"title": "404 Error"}, status_code=status_code)

    with pytest.raises(ProviderError, match=f"HTTP {int(status_code)}") as caught:
        provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert TEMPLATE in str(caught.value)


def test_a_page_the_site_served_with_a_success_status_is_still_a_record(provider: NimbleTemplateProvider) -> None:
    """The control for the test above: the same payload at 200 returns its record."""
    payload = _run_payload({"title": "404 Error"}, status_code=200.0)

    response = provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert response.data == {"title": "404 Error"}
    assert response.status_code == 200


def test_a_status_other_than_success_raises_with_the_warnings(provider: NimbleTemplateProvider) -> None:
    """Nimble answers 200 and refuses in `status`, which would otherwise look like an empty record."""
    payload = _run_payload(None, status="blocked", warnings=["challenge not solved"])

    with pytest.raises(ProviderError) as caught:
        provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert "blocked" in str(caught.value) and "challenge not solved" in str(caught.value)


def test_a_format_asked_for_comes_back_as_the_page_beside_the_record(provider: NimbleTemplateProvider) -> None:
    payload = _run_payload(RECORD)
    payload["data"]["markdown"] = "# A Light in the Attic"

    request = TemplateRequest(template=TEMPLATE, inputs={"url": URL}, format="markdown")
    response = provider._to_response(request, payload)

    assert response.document is not None
    assert response.document.url == URL
    assert response.document.content == "# A Light in the Attic"
    assert response.document.format == "markdown"
    assert response.document.status_code == 200
    assert response.data == RECORD


def test_pagination_and_unmapped_data_land_in_extra(provider: NimbleTemplateProvider) -> None:
    payload = _run_payload(RECORD, pagination={"next_page_params": {"page": 2}})
    payload["data"]["links"] = ["https://books.example/"]

    response = provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert response.extra["pagination"] == {"next_page_params": {"page": 2}}
    assert response.extra["links"] == ["https://books.example/"]


# -- The catalogue -----------------------------------------------------------


def test_a_published_template_is_described_from_its_version(provider: NimbleTemplateProvider) -> None:
    item = _item(_version())

    template = provider._to_template(item)

    assert template.provider == "nimble"
    assert template.name == TEMPLATE
    assert template.description == "Extracts book product details."
    assert template.domain == "books.example"
    assert template.entity_type == "Product Detail Page (PDP)"
    assert template.input_schema == _version()["input_schema"]
    assert template.output_schema == _version()["output_schema"]
    assert template.version == 1
    assert template.published is True
    assert template.created_at is not None and template.updated_at is not None
    assert template.extra["display_name"] == "Books - Book Detail"
    assert template.extra["tags"] == ["books", "ecommerce"]
    assert template.extra["samples"] == [{"input": {"url": URL}, "output": RECORD}]
    assert template.extra["privacy"] == "private", "a field the SDK model does not declare is still kept"
    assert template.raw == item


def test_an_unpublished_template_has_no_schemas_and_says_so(provider: NimbleTemplateProvider) -> None:
    template = provider._to_template(_item(None))

    assert template.published is False
    assert template.input_schema is None and template.output_schema is None
    assert template.version is None
    assert template.description == ""


def test_a_listing_is_a_page_with_its_place_in_the_catalogue(provider: NimbleTemplateProvider) -> None:
    payload = {"items": [_item(_version()), _item(None, name="draft")], "total": 250, "limit": 100, "offset": 0}

    page = provider._to_page(payload)

    assert page.provider == "nimble"
    assert page.names == [TEMPLATE, "draft"]
    assert (page.total, page.limit, page.offset) == (250, 100, 0)
    assert len(page) == 2
    assert page.raw == payload


# -- Through the SDK client ---------------------------------------------------


class _Api:
    """A fake of the three template endpoints behind the SDK, answering as the API does and recording each request."""

    def __init__(self, *, missing: str | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.missing = missing

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/extract/templates/run"):
            return httpx.Response(200, json=_run_payload(RECORD))
        if path.endswith("/extract/templates"):
            return httpx.Response(200, json={"items": [_item(_version())], "total": 1, "limit": 20, "offset": 5})
        name = path.rsplit("/", 1)[-1]
        if name == self.missing:
            return httpx.Response(404, json={"detail": f"template {name} not found"})
        return httpx.Response(200, json=_item(_version(name=name), name=name))

    def sync_provider(self, monkeypatch: pytest.MonkeyPatch) -> NimbleTemplateProvider:
        """A provider on its own clients, with the sync one routed through this fake API."""
        monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
        provider = NimbleTemplateProvider()
        transport = httpx.MockTransport(self.handle)
        provider._client = Nimble(api_key="test-key", http_client=httpx.Client(transport=transport))
        return provider

    def async_provider(self) -> NimbleTemplateProvider:
        """A provider on a caller's async client, as an app holding one per end user builds it."""
        transport = httpx.MockTransport(self.handle)
        client = AsyncNimble(api_key="end-user-key", http_client=httpx.AsyncClient(transport=transport))
        return NimbleTemplateProvider(async_client=client)


def test_run_list_and_describe_through_the_sdk_client(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api(missing="gone")
    provider = api.sync_provider(monkeypatch)
    try:
        response = provider.run(TemplateRequest(template=TEMPLATE, inputs={"url": URL}, format="markdown"))
        page = provider.templates(limit=20, offset=5)
        template = provider.template(TEMPLATE)
        with pytest.raises(TemplateNotFoundError, match="'gone'") as caught:
            provider.template("gone")
    finally:
        provider.close()

    run, listing, describe, missing = api.requests
    assert run.method == "POST" and run.url.path.endswith("/v2/extract/templates/run")
    assert json.loads(run.content) == {"template": TEMPLATE, "params": {"url": URL}, "formats": ["markdown"]}
    assert run.headers["authorization"] == "Bearer test-key"
    assert response.data == RECORD
    assert response.status_code == 200, "the API's float status code is coerced to an int"
    assert response.document is not None and response.document.format == "markdown"

    assert listing.method == "GET" and listing.url.path.endswith("/v2/extract/templates")
    assert dict(listing.url.params) == {"limit": "20", "offset": "5"}
    assert page.names == [TEMPLATE] and (page.total, page.limit, page.offset) == (1, 20, 5)

    assert describe.method == "GET" and describe.url.path.endswith(f"/v2/extract/templates/{TEMPLATE}")
    assert template.name == TEMPLATE and template.published

    assert missing.url.path.endswith("/v2/extract/templates/gone")
    assert caught.value.template == "gone" and isinstance(caught.value, ProviderError)


def test_a_run_on_an_unknown_template_is_the_same_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoSuchTemplateApi(_Api):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(404, json={"detail": "Template not found"})

    provider = _NoSuchTemplateApi().sync_provider(monkeypatch)
    try:
        with pytest.raises(TemplateNotFoundError, match="'gone'"):
            provider.run(TemplateRequest(template="gone", inputs={"url": URL}))
    finally:
        provider.close()


def test_paging_left_unset_sends_no_query(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _Api()
    provider = api.sync_provider(monkeypatch)
    try:
        provider.templates()
    finally:
        provider.close()

    assert dict(api.requests[0].url.params) == {}


async def test_the_async_calls_go_out_on_the_caller_s_client_as_its_end_user() -> None:
    api = _Api()
    provider = api.async_provider()
    try:
        response = await provider.arun(TemplateRequest(template=TEMPLATE, inputs={"url": URL}))
        page = await provider.atemplates(limit=1)
        template = await provider.atemplate(TEMPLATE)
    finally:
        await provider.aclose()

    assert response.data == RECORD and response.document is None
    assert page.names == [TEMPLATE]
    assert template.name == TEMPLATE
    assert all(request.headers["authorization"] == "Bearer end-user-key" for request in api.requests)
    sent = json.loads(api.requests[0].content)
    assert sent == {"template": TEMPLATE, "params": {"url": URL}}, "no format asked for, no formats sent"


def test_an_error_page_through_the_sdk_client_is_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class _MissingPageApi(_Api):
        def handle(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=_run_payload({"title": "404 Error"}, status_code=404.0))

    provider = _MissingPageApi().sync_provider(monkeypatch)
    try:
        with pytest.raises(ProviderError, match="HTTP 404"):
            provider.run(TemplateRequest(template=TEMPLATE, inputs={"url": URL}))
    finally:
        provider.close()


# -- Live ---------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_catalogue_and_run() -> None:
    """One listing, one description and one run, on whichever published template the account's catalogue holds.

    The template and its inputs come from the catalogue itself -- the first
    published template that publishes a sample input -- so nothing about the
    account is written down here. ``NIMBLE_TEMPLATE`` names one instead.
    """
    from web_router import WebRouter

    with WebRouter("nimble") as router:
        page = router.templates(provider="nimble", limit=50)
        assert page.provider == "nimble"
        assert 0 < len(page) <= 50
        assert page.total is not None and page.total >= len(page)

        name = os.getenv("NIMBLE_TEMPLATE")
        candidates = [router.template(name, provider="nimble")] if name else [t for t in page.templates if t.published]
        chosen = next(
            (
                (t, s["input"])
                for t in candidates
                for s in (t.extra.get("samples") or [])
                if isinstance(s.get("input"), dict)
            ),
            None,
        )
        if chosen is None:
            pytest.skip("no published template on this account publishes a sample input to run with")
        template, inputs = chosen
        assert template.input_schema is not None

        response = router.run_template(template.name, inputs, provider="nimble", format="markdown")

    assert response.provider == "nimble"
    assert response.data
    assert response.document is not None and response.document.content
    assert response.document.format == "markdown"
