"""The Nimble Extract Template provider, over the SDK's own response shapes.

The payloads are modelled on the API's response shapes: the run envelope on
a recorded ``extract/templates/run`` answer, and the catalogue items on the
SDK's generated ``TemplateListResponse`` / ``TemplateGetResponse`` models
plus the fields a recorded listing carried that the models do not declare
(``privacy``, ``managed_by``). Names, ids and timestamps are synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.providers._nimble_live_data_payloads import (
    RECORD,
    TASK_ID,
    TEMPLATE,
    URL,
    _item,
    _run_payload,
    _version,
)
from web_router.exceptions import ProviderError, UnsupportedFormatError
from web_router.types_live_data import LiveDataRequest, LiveDataSample

pytest.importorskip("nimble_python", reason="nimble extra not installed")


from web_router.providers.nimble_live_data import NimbleLiveDataProvider


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleLiveDataProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleLiveDataProvider()


# -- Building the run --------------------------------------------------------


def test_the_template_and_its_inputs_are_sent_and_nothing_else(provider: NimbleLiveDataProvider) -> None:
    """Nimble returns no page content unless `formats` names some, which is what an unset format means."""
    payload = provider._build_payload(LiveDataRequest(source=TEMPLATE, inputs={"url": URL}))

    assert payload == {"template": TEMPLATE, "params": {"url": URL}}


@pytest.mark.parametrize(("body_format", "expected"), [("markdown", "markdown"), ("html", "html")])
def test_a_format_maps_onto_nimbles_formats_list(
    provider: NimbleLiveDataProvider, body_format: Any, expected: str
) -> None:
    payload = provider._build_payload(LiveDataRequest(source=TEMPLATE, inputs={"url": URL}, format=body_format))

    assert payload["formats"] == [expected]


def test_plain_text_is_rejected_because_nimble_does_not_return_it(provider: NimbleLiveDataProvider) -> None:
    with pytest.raises(UnsupportedFormatError, match="text"):
        provider.validate(LiveDataRequest(source=TEMPLATE, inputs={"url": URL}, format="text"))


def test_provider_options_pass_through(provider: NimbleLiveDataProvider) -> None:
    request = LiveDataRequest(source=TEMPLATE, inputs={"url": URL}, provider_options={"localization": True})

    assert provider._build_payload(request)["localization"] is True


# -- Reading the run ---------------------------------------------------------


def test_a_bare_record_under_parsing_is_the_record(provider: NimbleLiveDataProvider) -> None:
    """The shape the live API answered with: the template's fields straight under `data.parsing`."""
    payload = _run_payload(RECORD)

    response = provider._to_response(LiveDataRequest(source=TEMPLATE, inputs={"url": URL}), payload)

    assert response.provider == "nimble"
    assert response.source == TEMPLATE
    assert response.inputs == {"url": URL}
    assert response.data == RECORD
    assert response.url == URL
    assert response.status_code == 200, "Nimble sends its status code as a float"
    assert response.request_id == TASK_ID
    assert response.document is None
    assert response.extra["metadata"]["driver"] == "fetch"
    assert "parsing" not in response.extra and "markdown" not in response.extra
    assert response.raw == payload


def test_a_wrapped_record_is_unwrapped(provider: NimbleLiveDataProvider) -> None:
    """The SDK's `ParsingSuccessResult`: the record under `entities` beside a status."""
    payload = _run_payload({"status": "success", "entities": RECORD})

    response = provider._to_response(LiveDataRequest(source=TEMPLATE), payload)

    assert response.data == RECORD


def test_a_parsing_error_is_a_failure_not_an_empty_record(provider: NimbleLiveDataProvider) -> None:
    payload = _run_payload({"status": "error", "error": "selector matched nothing"})

    with pytest.raises(ProviderError, match="selector matched nothing") as caught:
        provider._to_response(LiveDataRequest(source=TEMPLATE), payload)

    assert TEMPLATE in str(caught.value)


def test_a_record_whose_own_field_is_called_status_is_not_mistaken_for_a_report(
    provider: NimbleLiveDataProvider,
) -> None:
    """Only the two wrapped shapes carry `entities` or `error` beside `status`; a record may use the word too."""
    record = {"status": "error", "title": "Out of stock"}

    response = provider._to_response(LiveDataRequest(source=TEMPLATE), _run_payload(record))

    assert response.data == record


def test_a_record_with_a_status_and_entities_beside_other_fields_is_kept_whole(
    provider: NimbleLiveDataProvider,
) -> None:
    """A wrapper is exactly the SDK's two fields; a record whose schema names both and more is a record."""
    record = {"status": "success", "entities": {"brand": "Acme"}, "title": "Widget"}

    response = provider._to_response(LiveDataRequest(source=TEMPLATE), _run_payload(record))

    assert response.data == record


def test_a_fetched_page_with_no_record_raises(provider: NimbleLiveDataProvider) -> None:
    with pytest.raises(ProviderError, match="without a record"):
        provider._to_response(LiveDataRequest(source=TEMPLATE), _run_payload(None))


@pytest.mark.parametrize("status_code", [404.0, 403, 500.0, 503])
def test_an_error_page_the_site_served_is_a_failed_run_not_a_record(
    provider: NimbleLiveDataProvider, status_code: float
) -> None:
    """A recorded response for a missing page: `success`, `status_code: 404`, and a "record" titled "404 Error"."""
    payload = _run_payload({"title": "404 Error"}, status_code=status_code)

    with pytest.raises(ProviderError, match=f"HTTP {int(status_code)}") as caught:
        provider._to_response(LiveDataRequest(source=TEMPLATE), payload)

    assert TEMPLATE in str(caught.value)


def test_a_page_the_site_served_with_a_success_status_is_still_a_record(provider: NimbleLiveDataProvider) -> None:
    """The control for the test above: the same payload at 200 returns its record."""
    payload = _run_payload({"title": "404 Error"}, status_code=200.0)

    response = provider._to_response(LiveDataRequest(source=TEMPLATE), payload)

    assert response.data == {"title": "404 Error"}
    assert response.status_code == 200


def test_a_status_other_than_success_raises_with_the_warnings(provider: NimbleLiveDataProvider) -> None:
    """Nimble answers 200 and refuses in `status`, which would otherwise look like an empty record."""
    payload = _run_payload(None, status="blocked", warnings=["challenge not solved"])

    with pytest.raises(ProviderError) as caught:
        provider._to_response(LiveDataRequest(source=TEMPLATE), payload)

    assert "blocked" in str(caught.value) and "challenge not solved" in str(caught.value)


def test_a_format_asked_for_comes_back_as_the_page_beside_the_record(provider: NimbleLiveDataProvider) -> None:
    payload = _run_payload(RECORD)
    payload["data"]["markdown"] = "# A Light in the Attic"

    request = LiveDataRequest(source=TEMPLATE, inputs={"url": URL}, format="markdown")
    response = provider._to_response(request, payload)

    assert response.document is not None
    assert response.document.url == URL
    assert response.document.content == "# A Light in the Attic"
    assert response.document.format == "markdown"
    assert response.document.status_code == 200
    assert response.data == RECORD


def test_pagination_and_unmapped_data_land_in_extra(provider: NimbleLiveDataProvider) -> None:
    payload = _run_payload(RECORD, pagination={"next_page_params": {"page": 2}})
    payload["data"]["links"] = ["https://books.example/"]

    response = provider._to_response(LiveDataRequest(source=TEMPLATE), payload)

    assert response.extra["pagination"] == {"next_page_params": {"page": 2}}
    assert response.extra["links"] == ["https://books.example/"]


# -- The catalogue -----------------------------------------------------------


def test_a_published_template_is_described_from_its_version(provider: NimbleLiveDataProvider) -> None:
    item = _item(_version())

    template = provider._to_source(item)

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
    assert template.samples == [LiveDataSample(input={"url": URL}, output=RECORD)]
    assert "samples" not in template.extra, "a sample has a field of its own"
    assert template.extra["privacy"] == "private", "a field the SDK model does not declare is still kept"
    assert template.raw == item


def test_an_unpublished_template_has_no_schemas_and_says_so(provider: NimbleLiveDataProvider) -> None:
    template = provider._to_source(_item(None))

    assert template.published is False
    assert template.input_schema is None and template.output_schema is None
    assert template.version is None
    assert template.description == ""


def test_a_listing_is_a_page_with_its_place_in_the_catalogue(provider: NimbleLiveDataProvider) -> None:
    payload = {"items": [_item(_version()), _item(None, name="draft")], "total": 250, "limit": 100, "offset": 0}

    page = provider._to_page(payload)

    assert page.provider == "nimble"
    assert page.names == [TEMPLATE, "draft"]
    assert (page.total, page.limit, page.offset) == (250, 100, 0)
    assert len(page) == 2
    assert page.raw == payload
    # Nimble's listing carries each published version whole, so a row is as full as a describe.
    assert page.sources[0].input_schema == _version()["input_schema"]
    assert page.sources[0].samples[0].input == {"url": URL}
    assert page.sources[1].samples == []


def test_a_malformed_sample_is_skipped_and_a_sample_with_no_input_takes_none(
    provider: NimbleLiveDataProvider,
) -> None:
    item = _item(
        _version(
            samples=[
                {"input": {"url": URL}},
                "not a sample",
                {"input": "https://example.com"},
                {"input": [["url", URL]]},
                {"input": []},
                {"input": ""},
                {"input": 0},
                {"input": False},
                {"output": RECORD},
                {"input": None, "output": RECORD},
            ]
        )
    )

    template = provider._to_source(item)

    assert template.samples == [
        LiveDataSample(input={"url": URL}, output=None),
        LiveDataSample(input={}, output=RECORD),
        LiveDataSample(input={}, output=RECORD),
    ]
