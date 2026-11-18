"""The Nimble live data provider: the run it sends, and the record and page it reads back.

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
    _run_payload,
)
from web_router.exceptions import UnsupportedFormatError
from web_router.types_live_data import LiveDataRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble_live_data import NimbleLiveDataProvider
from web_router.providers.nimble_live_data_map import to_response


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

    response = to_response(provider.name, LiveDataRequest(source=TEMPLATE, inputs={"url": URL}), payload)

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

    response = to_response(provider.name, LiveDataRequest(source=TEMPLATE), payload)

    assert response.data == RECORD


def test_a_format_asked_for_comes_back_as_the_page_beside_the_record(provider: NimbleLiveDataProvider) -> None:
    payload = _run_payload(RECORD)
    payload["data"]["markdown"] = "# A Light in the Attic"

    request = LiveDataRequest(source=TEMPLATE, inputs={"url": URL}, format="markdown")
    response = to_response(provider.name, request, payload)

    assert response.document is not None
    assert response.document.url == URL
    assert response.document.content == "# A Light in the Attic"
    assert response.document.format == "markdown"
    assert response.document.status_code == 200
    assert response.data == RECORD


def test_pagination_and_unmapped_data_land_in_extra(provider: NimbleLiveDataProvider) -> None:
    payload = _run_payload(RECORD, pagination={"next_page_params": {"page": 2}})
    payload["data"]["links"] = ["https://books.example/"]

    response = to_response(provider.name, LiveDataRequest(source=TEMPLATE), payload)

    assert response.extra["pagination"] == {"next_page_params": {"page": 2}}
    assert response.extra["links"] == ["https://books.example/"]
