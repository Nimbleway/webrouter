"""What LiveDataProvider guarantees on every backend's behalf, checked on a fake backend with one behaviour swapped."""

from __future__ import annotations

from typing import Any

import pytest

from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.base import LiveDataProvider
from web_router.types_live_data import (
    LiveDataRequest,
    LiveDataResponse,
)

TEMPLATE = "books_pdp"
INPUTS = {"url": "https://example.com/page"}


def _fake(**overrides: Any) -> LiveDataProvider:
    """A fake template backend with one behaviour swapped out."""
    from tests._live_data_fake import FakeLiveDataProvider

    return type("Swapped", (FakeLiveDataProvider,), overrides)()


def test_a_request_with_no_inputs_is_valid() -> None:
    """Some templates take nothing: the page is fixed, and the record is the whole of it."""
    response = _fake().run(LiveDataRequest(source=TEMPLATE))

    assert response.inputs == {}
    assert response.data["inputs"] == {}


def test_validation_runs_before_anything_is_run() -> None:
    provider = _fake(supported_formats=frozenset({"markdown"}))
    with pytest.raises(UnsupportedFormatError):
        provider.run(LiveDataRequest(source=TEMPLATE, inputs=INPUTS, format="html"))

    assert provider.requests == []  # type: ignore[attr-defined]


def test_an_unmapped_option_is_rejected_before_anything_is_run() -> None:
    provider = _fake(supported_parameters=frozenset())
    with pytest.raises(UnsupportedParameterError, match="format"):
        provider.run(LiveDataRequest(source=TEMPLATE, inputs=INPUTS, format="markdown"))

    assert provider.requests == []  # type: ignore[attr-defined]


def test_no_format_asked_for_means_no_page_beside_the_record() -> None:
    """Unlike an extract, nothing is pinned: the record is the answer and the page is an extra."""
    response = _fake().run(LiveDataRequest(source=TEMPLATE, inputs=INPUTS))

    assert response.document is None
    assert response.data


def test_a_format_asked_for_is_what_the_page_reports() -> None:
    response = _fake().run(LiveDataRequest(source=TEMPLATE, inputs=INPUTS, format="html"))

    assert response.document is not None
    assert response.document.format == "html"


def test_a_backend_failure_on_a_run_is_wrapped_with_the_original() -> None:
    original = RuntimeError("connection reset")

    def _run(self: Any, request: LiveDataRequest) -> LiveDataResponse:
        raise original

    with pytest.raises(ProviderError, match="connection reset") as caught:
        _fake(_run=_run).run(LiveDataRequest(source=TEMPLATE, inputs=INPUTS))

    assert caught.value.original is original


def test_a_backend_failure_on_a_listing_is_wrapped() -> None:
    def _list(self: Any, *, limit: int | None, offset: int | None) -> Any:
        raise RuntimeError("HTTP 500")

    with pytest.raises(ProviderError, match="listing live data sources failed: HTTP 500"):
        _fake(_list=_list).live_data_sources()


def test_an_unknown_template_name_is_a_provider_error_naming_it() -> None:
    provider = _fake(unknown_names=frozenset({"missing"}))
    with pytest.raises(ProviderError, match="'missing'") as caught:
        provider.live_data_source("missing")

    assert "404" in str(caught.value)


def test_a_router_error_from_a_backend_is_not_rewrapped() -> None:
    """An UnsupportedParameterError is a caller bug, not a backend failure."""

    def _run(self: Any, request: LiveDataRequest) -> LiveDataResponse:
        raise UnsupportedParameterError("fake", ["format"])

    with pytest.raises(UnsupportedParameterError):
        _fake(_run=_run).run(LiveDataRequest(source=TEMPLATE, inputs=INPUTS))


def test_paging_left_unset_reaches_the_backend_unset() -> None:
    """Unset means unset: the backend pages as it would for any client of its own."""
    provider = _fake()
    provider.live_data_sources()
    provider.live_data_sources(limit=5, offset=10)

    assert provider.listed == [(None, None), (5, 10)]  # type: ignore[attr-defined]


async def test_the_async_path_runs_lists_and_describes_the_same_way() -> None:
    provider = _fake()

    response = await provider.arun(LiveDataRequest(source=TEMPLATE, inputs=INPUTS, format="markdown"))
    page = await provider.alive_data_sources(limit=1)
    template = await provider.alive_data_source(TEMPLATE)

    assert response.data["inputs"] == INPUTS and response.document is not None
    assert page.names == ["books_pdp", "jobs_listing"] and page.limit == 1
    assert template.name == TEMPLATE and template.published
