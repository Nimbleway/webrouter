"""Guards on the Extract Template contract itself.

The counterpart of test_extract_contract.py for the method that runs a
backend's saved extraction. These catch drift that is invisible at runtime
until a user hits it: a backend that reports a page format it did not return,
a run that comes back with an empty record instead of saying why, a provider
that skipped validation by overriding the public method.
"""

from __future__ import annotations

import inspect
from typing import Any, get_args

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants_live_data import LIVE_DATA_ENV_BY_PROVIDER
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.base import LiveDataProvider
from web_router.registry import (
    available_live_data_providers,
    configured_live_data_providers,
    get_live_data_provider_class,
)
from web_router.types import (
    ExtractFormat,
)
from web_router.types_live_data import (
    LIVE_DATA_ALWAYS_SENT,
    LIVE_DATA_PARAMETERS,
    LiveDataRequest,
    LiveDataResponse,
)

ROUTER_ONLY_ARGUMENTS = {"self", "source", "inputs", "provider"}

FORMATS: tuple[ExtractFormat, ...] = get_args(ExtractFormat)

ENV_VARS = sorted({env_var for env_vars in LIVE_DATA_ENV_BY_PROVIDER.values() for env_var in env_vars})

PUBLIC_METHODS = (
    "run",
    "arun",
    "live_data_sources",
    "alive_data_sources",
    "live_data_source",
    "alive_data_source",
    "validate",
)
"""Everything a caller reaches, all of which enforce something on the backend's behalf."""

TEMPLATE = "books_pdp"
INPUTS = {"url": "https://example.com/page"}


@pytest.fixture
def credentialed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every template credential set to a placeholder, so providers construct offline."""
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")


def _build(name: str) -> LiveDataProvider:
    return get_live_data_provider_class(name)()


@pytest.mark.parametrize("method", [WebRouter.live_data, AsyncWebRouter.live_data], ids=["sync", "async"])
def test_router_run_template_exposes_every_shared_option(method: object) -> None:
    """The two signatures restate LiveDataRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == LIVE_DATA_PARAMETERS | LIVE_DATA_ALWAYS_SENT - {"source", "inputs"}


def test_every_provider_declares_where_its_credentials_come_from() -> None:
    """configured_live_data_providers() reads this table, so a gap there hides a provider."""
    assert set(LIVE_DATA_ENV_BY_PROVIDER) == set(available_live_data_providers())


def test_there_is_no_keyless_tier_and_no_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run fetches on the backend's account, and a template's name belongs to one vendor: nothing to route."""
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    assert configured_live_data_providers() == []
    assert "auto" not in available_live_data_providers()


@pytest.mark.parametrize("method", ["live_data_sources", "live_data_source", "live_data"])
def test_the_router_requires_the_provider_to_be_named(method: str) -> None:
    """No default: the router's default is `auto`, and a template's name means something to one vendor only."""
    parameter = inspect.signature(getattr(WebRouter, method)).parameters["provider"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_template_parameters_tracks_the_model() -> None:
    assert set(LiveDataRequest.model_fields) - LIVE_DATA_ALWAYS_SENT == LIVE_DATA_PARAMETERS


@pytest.mark.parametrize("name", available_live_data_providers())
def test_providers_declare_only_real_options(name: str) -> None:
    """A typo in supported_parameters would silently reject a valid option forever."""
    assert get_live_data_provider_class(name).supported_parameters <= LIVE_DATA_PARAMETERS


@pytest.mark.parametrize("name", available_live_data_providers())
def test_providers_declare_only_real_formats(name: str) -> None:
    assert get_live_data_provider_class(name).supported_formats <= set(FORMATS)


@pytest.mark.parametrize("name", available_live_data_providers())
@pytest.mark.parametrize("method", PUBLIC_METHODS)
def test_public_methods_are_always_the_base_implementation(name: str, method: str) -> None:
    """Validation and error wrapping live on `LiveDataProvider`; an override would bypass both."""
    provider_cls = get_live_data_provider_class(name)
    assert getattr(provider_cls, method) is getattr(LiveDataProvider, method), f"{name} overrides {method}()"


@pytest.mark.parametrize("name", available_live_data_providers())
def test_providers_are_concrete(name: str) -> None:
    assert not inspect.isabstract(get_live_data_provider_class(name))


@pytest.mark.parametrize("name", available_live_data_providers())
def test_an_unsupported_format_is_rejected_not_substituted(name: str, credentialed: None) -> None:
    provider = _build(name)
    unsupported = set(FORMATS) - provider.supported_formats
    try:
        for body_format in unsupported:
            with pytest.raises(UnsupportedFormatError, match=body_format):
                provider.run(LiveDataRequest(source=TEMPLATE, inputs=INPUTS, format=body_format))  # type: ignore[arg-type]
    finally:
        provider.close()


@pytest.mark.parametrize("name", available_live_data_providers())
def test_provider_repr_never_leaks_a_credential(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SEKRIT-0123456789abcdef"
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, secret)

    provider = get_live_data_provider_class(name)(api_key=secret)
    try:
        assert secret not in repr(provider)
        assert secret not in repr(provider.options)
    finally:
        provider.close()


# -- What LiveDataProvider guarantees on every backend's behalf ---------------


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


def test_the_live_data_env_table_names_the_same_variables_as_the_other_methods() -> None:
    """Spelled out in constants_live_data.py to avoid an import loop, so checked against the originals here."""
    from web_router.constants import (
        ENV_BRIGHTDATA_API_KEY,
        ENV_NIMBLE_API_KEY,
        ENV_OXYLABS_PASSWORD,
        ENV_OXYLABS_USERNAME,
    )

    assert LIVE_DATA_ENV_BY_PROVIDER["nimble"] == (ENV_NIMBLE_API_KEY,)
    assert LIVE_DATA_ENV_BY_PROVIDER["brightdata"] == (ENV_BRIGHTDATA_API_KEY,)
    assert LIVE_DATA_ENV_BY_PROVIDER["oxylabs"] == (ENV_OXYLABS_USERNAME, ENV_OXYLABS_PASSWORD)


def test_the_old_template_names_still_work_and_warn() -> None:
    import web_router
    from web_router import LiveDataResponse, WebRouter

    with pytest.warns(DeprecationWarning, match="TemplateResponse"):
        assert web_router.TemplateResponse is LiveDataResponse
    with pytest.warns(DeprecationWarning, match="template"):
        request = LiveDataRequest(template="books_pdp")  # type: ignore[call-arg]
    assert request.source == "books_pdp"
    assert hasattr(WebRouter, "run_template") and hasattr(WebRouter, "templates")
