"""The Nimble live data provider: a run that answered but holds no record is a failure, not an empty record.

The payloads are modelled on the API's response shapes: the run envelope on
a recorded ``extract/templates/run`` answer, and the catalogue items on the
SDK's generated ``TemplateListResponse`` / ``TemplateGetResponse`` models
plus the fields a recorded listing carried that the models do not declare
(``privacy``, ``managed_by``). Names, ids and timestamps are synthetic.
"""

from __future__ import annotations

import pytest

from tests.providers._nimble_live_data_payloads import (
    TEMPLATE,
    _run_payload,
)
from web_router.exceptions import ProviderError
from web_router.types_live_data import LiveDataRequest

pytest.importorskip("nimble_python", reason="nimble extra not installed")

from web_router.providers.nimble_live_data import NimbleLiveDataProvider
from web_router.providers.nimble_live_data_map import to_response


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> NimbleLiveDataProvider:
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    return NimbleLiveDataProvider()


def test_a_parsing_error_is_a_failure_not_an_empty_record(provider: NimbleLiveDataProvider) -> None:
    payload = _run_payload({"status": "error", "error": "selector matched nothing"})

    with pytest.raises(ProviderError, match="selector matched nothing") as caught:
        to_response(provider.name, LiveDataRequest(source=TEMPLATE), payload)

    assert TEMPLATE in str(caught.value)


def test_a_record_whose_own_field_is_called_status_is_not_mistaken_for_a_report(
    provider: NimbleLiveDataProvider,
) -> None:
    """Only the two wrapped shapes carry `entities` or `error` beside `status`; a record may use the word too."""
    record = {"status": "error", "title": "Out of stock"}

    response = to_response(provider.name, LiveDataRequest(source=TEMPLATE), _run_payload(record))

    assert response.data == record


def test_a_record_with_a_status_and_entities_beside_other_fields_is_kept_whole(
    provider: NimbleLiveDataProvider,
) -> None:
    """A wrapper is exactly the SDK's two fields; a record whose schema names both and more is a record."""
    record = {"status": "success", "entities": {"brand": "Acme"}, "title": "Widget"}

    response = to_response(provider.name, LiveDataRequest(source=TEMPLATE), _run_payload(record))

    assert response.data == record


def test_a_fetched_page_with_no_record_raises(provider: NimbleLiveDataProvider) -> None:
    with pytest.raises(ProviderError, match="without a record"):
        to_response(provider.name, LiveDataRequest(source=TEMPLATE), _run_payload(None))


@pytest.mark.parametrize("status_code", [404.0, 403, 500.0, 503])
def test_an_error_page_the_site_served_is_a_failed_run_not_a_record(
    provider: NimbleLiveDataProvider, status_code: float
) -> None:
    """A recorded response for a missing page: `success`, `status_code: 404`, and a "record" titled "404 Error"."""
    payload = _run_payload({"title": "404 Error"}, status_code=status_code)

    with pytest.raises(ProviderError, match=f"HTTP {int(status_code)}") as caught:
        to_response(provider.name, LiveDataRequest(source=TEMPLATE), payload)

    assert TEMPLATE in str(caught.value)


def test_a_page_the_site_served_with_a_success_status_is_still_a_record(provider: NimbleLiveDataProvider) -> None:
    """The control for the test above: the same payload at 200 returns its record."""
    payload = _run_payload({"title": "404 Error"}, status_code=200.0)

    response = to_response(provider.name, LiveDataRequest(source=TEMPLATE), payload)

    assert response.data == {"title": "404 Error"}
    assert response.status_code == 200


def test_a_status_other_than_success_raises_with_the_warnings(provider: NimbleLiveDataProvider) -> None:
    """Nimble answers 200 and refuses in `status`, which would otherwise look like an empty record."""
    payload = _run_payload(None, status="blocked", warnings=["challenge not solved"])

    with pytest.raises(ProviderError) as caught:
        to_response(provider.name, LiveDataRequest(source=TEMPLATE), payload)

    assert "blocked" in str(caught.value) and "challenge not solved" in str(caught.value)
