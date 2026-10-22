"""The Nimble Extract Template provider, over the SDK's own response shapes.

The run payload is shaped after a live call on 2026-10-22 against a
books.toscrape.com template, and the catalogue payloads after the SDK's
generated ``TemplateListResponse`` / ``TemplateGetResponse`` models and the
same live listing, which also carried fields the models do not declare
(``privacy``, ``managed_by``).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from web_router.exceptions import ProviderError, UnsupportedFormatError
from web_router.types import TemplateRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble import NimbleTemplateProvider

TEMPLATE = "wfl718_luna_prod_verify_books"
URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
RECORD = {"title": "A Light in the Attic", "price": "£51.77", "upc": "a897fe39b1053632", "num_reviews": 0}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleTemplateProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleTemplateProvider()


def _run_payload(parsing: Any, **extra: Any) -> dict[str, Any]:
    return {
        "task_id": "380684c5-8b07-4469-ad98-cec4aa396147",
        "url": URL,
        "status": "success",
        "status_code": 200.0,
        "metadata": {"driver": "vx6", "query_duration": 2244.0, "response_parameters": {"input_url": URL}},
        "data": {"markdown": None, "html": None, "links": None, "parsing": parsing},
        "pagination": None,
        "warnings": ["render_options is ignored because render is false"],
        **extra,
    }


def _version(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "19bd09af-7a5b-4aa5-a538-2c738b688698",
        "created_at": "2026-10-11T22:13:16.858830Z",
        "name": TEMPLATE,
        "version_number": 1,
        "input_schema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
        "metadata": {
            "data_source": "books.toscrape.com",
            "description": "Extracts book product details.",
            "display_name": "Books to Scrape - Book Detail",
            "domain": "books.toscrape.com",
            "entity_type": "Product Detail Page (PDP)",
            "tags": ["books", "ecommerce"],
            "vertical": "Ecommerce",
        },
        "samples": [{"input": {"url": URL}, "output": RECORD}],
        **overrides,
    }


def _item(version: dict[str, Any] | None, **overrides: Any) -> dict[str, Any]:
    return {
        "id": "4d38fb57-7d8c-48db-8ddb-ae845574416d",
        "name": TEMPLATE,
        "created_at": "2026-10-11T22:13:16.858830Z",
        "updated_at": "2026-10-11T22:14:54.997455Z",
        "published_version": version,
        "published_version_id": version["id"] if version else None,
        "privacy": "private",
        "managed_by": None,
        **overrides,
    }


# -- Building the run --------------------------------------------------------


def test_the_template_and_its_inputs_are_sent_and_nothing_else(provider: NimbleTemplateProvider) -> None:
    """Nimble returns no page content unless `formats` names some, which is what an unset format means."""
    payload = provider._build_payload(TemplateRequest(template=TEMPLATE, params={"url": URL}))

    assert payload == {"template": TEMPLATE, "params": {"url": URL}}


@pytest.mark.parametrize(("body_format", "expected"), [("markdown", "markdown"), ("html", "html")])
def test_a_format_maps_onto_nimbles_formats_list(
    provider: NimbleTemplateProvider, body_format: Any, expected: str
) -> None:
    payload = provider._build_payload(TemplateRequest(template=TEMPLATE, params={"url": URL}, format=body_format))

    assert payload["formats"] == [expected]


def test_plain_text_is_rejected_because_nimble_does_not_return_it(provider: NimbleTemplateProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        provider.validate(TemplateRequest(template=TEMPLATE, params={"url": URL}, format="text"))


def test_provider_options_pass_through(provider: NimbleTemplateProvider) -> None:
    request = TemplateRequest(template=TEMPLATE, params={"url": URL}, provider_options={"localization": True})

    assert provider._build_payload(request)["localization"] is True


# -- Reading the run ---------------------------------------------------------


def test_a_bare_record_under_parsing_is_the_record(provider: NimbleTemplateProvider) -> None:
    """The shape the live API answered with: the template's fields straight under `data.parsing`."""
    payload = _run_payload(RECORD)

    response = provider._to_response(TemplateRequest(template=TEMPLATE, params={"url": URL}), payload)

    assert response.provider == "nimble"
    assert response.template == TEMPLATE
    assert response.params == {"url": URL}
    assert response.data == RECORD
    assert response.url == URL
    assert response.status_code == 200, "Nimble sends its status code as a float"
    assert response.request_id == "380684c5-8b07-4469-ad98-cec4aa396147"
    assert response.document is None
    assert response.extra["metadata"]["driver"] == "vx6"
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


@pytest.mark.parametrize("parsing", [None, {}])
def test_a_fetched_page_with_no_record_raises(provider: NimbleTemplateProvider, parsing: Any) -> None:
    with pytest.raises(ProviderError, match="without a record"):
        provider._to_response(TemplateRequest(template=TEMPLATE), _run_payload(parsing))


def test_a_status_other_than_success_raises_with_the_warnings(provider: NimbleTemplateProvider) -> None:
    """Nimble answers 200 and refuses in `status`, which would otherwise look like an empty record."""
    payload = _run_payload(None, status="blocked", warnings=["challenge not solved"])

    with pytest.raises(ProviderError) as caught:
        provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert "blocked" in str(caught.value) and "challenge not solved" in str(caught.value)


def test_a_format_asked_for_comes_back_as_the_page_beside_the_record(provider: NimbleTemplateProvider) -> None:
    payload = _run_payload(RECORD)
    payload["data"]["markdown"] = "# A Light in the Attic"

    request = TemplateRequest(template=TEMPLATE, params={"url": URL}, format="markdown")
    response = provider._to_response(request, payload)

    assert response.document is not None
    assert response.document.url == URL
    assert response.document.content == "# A Light in the Attic"
    assert response.document.format == "markdown"
    assert response.document.status_code == 200
    assert response.data == RECORD


def test_pagination_and_unmapped_data_land_in_extra(provider: NimbleTemplateProvider) -> None:
    payload = _run_payload(RECORD, pagination={"next_page_params": {"page": 2}})
    payload["data"]["links"] = ["https://books.toscrape.com/"]

    response = provider._to_response(TemplateRequest(template=TEMPLATE), payload)

    assert response.extra["pagination"] == {"next_page_params": {"page": 2}}
    assert response.extra["links"] == ["https://books.toscrape.com/"]


# -- The catalogue -----------------------------------------------------------


def test_a_published_template_is_described_from_its_version(provider: NimbleTemplateProvider) -> None:
    item = _item(_version())

    template = provider._to_template(item)

    assert template.provider == "nimble"
    assert template.name == TEMPLATE
    assert template.description == "Extracts book product details."
    assert template.domain == "books.toscrape.com"
    assert template.entity_type == "Product Detail Page (PDP)"
    assert template.input_schema == _version()["input_schema"]
    assert template.output_schema == _version()["output_schema"]
    assert template.version == 1
    assert template.published is True
    assert template.created_at is not None and template.updated_at is not None
    assert template.extra["display_name"] == "Books to Scrape - Book Detail"
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
    payload = {"items": [_item(_version()), _item(None, name="draft")], "total": 4127, "limit": 100, "offset": 0}

    page = provider._to_page(payload)

    assert page.provider == "nimble"
    assert page.names == [TEMPLATE, "draft"]
    assert (page.total, page.limit, page.offset) == (4127, 100, 0)
    assert len(page) == 2
    assert page.raw == payload


# -- Live ---------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NIMBLE_API_KEY"), reason="NIMBLE_API_KEY not set")
def test_live_catalogue_and_run() -> None:
    """One listing, one description and one run on a scrape-sandbox page the account's catalogue holds."""
    from web_router import WebRouter

    with WebRouter("nimble") as router:
        page = router.templates(limit=5)
        assert page.provider == "nimble"
        assert 0 < len(page) <= 5
        assert page.total is not None and page.total >= len(page)

        template = router.template(TEMPLATE)
        assert template.published and template.input_schema is not None
        assert "url" in template.input_schema.get("properties", {})

        response = router.run_template(TEMPLATE, {"url": URL}, format="markdown")

    assert response.provider == "nimble"
    assert response.data.get("title")
    assert response.document is not None and response.document.content
    assert response.document.format == "markdown"
