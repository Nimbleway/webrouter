"""The Nimble live data provider: published and draft templates, a listing page, and their samples.

The payloads are modelled on the API's response shapes: the run envelope on
a recorded ``extract/templates/run`` answer, and the catalogue items on the
SDK's generated ``TemplateListResponse`` / ``TemplateGetResponse`` models
plus the fields a recorded listing carried that the models do not declare
(``privacy``, ``managed_by``). Names, ids and timestamps are synthetic.
"""

from __future__ import annotations

import pytest

from tests.providers._nimble_live_data_payloads import (
    RECORD,
    TEMPLATE,
    URL,
    _item,
    _version,
)
from web_router.types_live_data import LiveDataSample

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble_live_data import NimbleLiveDataProvider
from web_router.providers.nimble_live_data_map import to_page, to_source


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleLiveDataProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleLiveDataProvider()


def test_a_published_template_is_described_from_its_version(provider: NimbleLiveDataProvider) -> None:
    item = _item(_version())

    template = to_source(provider.name, item)

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
    template = to_source(provider.name, _item(None))

    assert template.published is False
    assert template.input_schema is None and template.output_schema is None
    assert template.version is None
    assert template.description == ""


def test_a_listing_is_a_page_with_its_place_in_the_catalogue(provider: NimbleLiveDataProvider) -> None:
    payload = {"items": [_item(_version()), _item(None, name="draft")], "total": 250, "limit": 100, "offset": 0}

    page = to_page(provider.name, payload)

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

    template = to_source(provider.name, item)

    assert template.samples == [
        LiveDataSample(input={"url": URL}, output=None),
        LiveDataSample(input={}, output=RECORD),
        LiveDataSample(input={}, output=RECORD),
    ]
