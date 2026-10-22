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
from web_router.constants import TEMPLATE_ENV_BY_PROVIDER
from web_router.exceptions import ProviderError, UnsupportedFormatError, UnsupportedParameterError
from web_router.providers.base import TemplateProvider
from web_router.registry import (
    available_template_providers,
    configured_template_providers,
    get_template_provider_class,
)
from web_router.types import (
    TEMPLATE_ALWAYS_SENT,
    TEMPLATE_PARAMETERS,
    ExtractFormat,
    TemplateRequest,
    TemplateResponse,
)

ROUTER_ONLY_ARGUMENTS = {"self", "template", "params", "provider"}

FORMATS: tuple[ExtractFormat, ...] = get_args(ExtractFormat)

ENV_VARS = sorted({env_var for env_vars in TEMPLATE_ENV_BY_PROVIDER.values() for env_var in env_vars})

PUBLIC_METHODS = ("run", "arun", "templates", "atemplates", "template", "atemplate", "validate")
"""Everything a caller reaches, all of which enforce something on the backend's behalf."""

TEMPLATE = "books_pdp"
PARAMS = {"url": "https://example.com/page"}


def _translates(name: str) -> bool:
    """Whether this provider builds a payload of its own; `auto` delegates and is covered in test_auto_template.py."""
    return hasattr(get_template_provider_class(name), "_build_payload")


TRANSLATING = [name for name in available_template_providers() if _translates(name)]


@pytest.fixture
def credentialed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every template credential set to a placeholder, so providers construct offline."""
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")


def _build(name: str) -> TemplateProvider:
    return get_template_provider_class(name)()


@pytest.mark.parametrize("method", [WebRouter.run_template, AsyncWebRouter.run_template], ids=["sync", "async"])
def test_router_run_template_exposes_every_shared_option(method: object) -> None:
    """The two signatures restate TemplateRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == TEMPLATE_PARAMETERS | TEMPLATE_ALWAYS_SENT - {"template", "params"}


def test_every_provider_declares_where_its_credentials_come_from() -> None:
    """configured_template_providers() reads this table, so a gap there hides a provider."""
    assert set(TEMPLATE_ENV_BY_PROVIDER) == set(available_template_providers())


def test_there_is_no_keyless_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run fetches on the backend's account, so with no key only `auto` is left, which then says so."""
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    assert configured_template_providers() == ["auto"]


def test_template_parameters_tracks_the_model() -> None:
    assert set(TemplateRequest.model_fields) - TEMPLATE_ALWAYS_SENT == TEMPLATE_PARAMETERS


@pytest.mark.parametrize("name", available_template_providers())
def test_providers_declare_only_real_options(name: str) -> None:
    """A typo in supported_parameters would silently reject a valid option forever."""
    assert get_template_provider_class(name).supported_parameters <= TEMPLATE_PARAMETERS


@pytest.mark.parametrize("name", available_template_providers())
def test_providers_declare_only_real_formats(name: str) -> None:
    assert get_template_provider_class(name).supported_formats <= set(FORMATS)


@pytest.mark.parametrize("name", available_template_providers())
@pytest.mark.parametrize("method", PUBLIC_METHODS)
def test_public_methods_are_always_the_base_implementation(name: str, method: str) -> None:
    """Validation and error wrapping live on `TemplateProvider`; an override would bypass both."""
    provider_cls = get_template_provider_class(name)
    assert getattr(provider_cls, method) is getattr(TemplateProvider, method), f"{name} overrides {method}()"


@pytest.mark.parametrize("name", available_template_providers())
def test_providers_are_concrete(name: str) -> None:
    assert not inspect.isabstract(get_template_provider_class(name))


@pytest.mark.parametrize("name", TRANSLATING)
def test_provider_options_always_reach_the_api(name: str, credentialed: None) -> None:
    """The escape hatch has to work on every provider, including new ones."""
    provider = _build(name)
    try:
        payload = provider._build_payload(  # type: ignore[attr-defined]
            TemplateRequest(template=TEMPLATE, params=PARAMS, provider_options={"x_custom": "sentinel"})
        )
    finally:
        provider.close()

    assert payload["x_custom"] == "sentinel"


@pytest.mark.parametrize("name", TRANSLATING)
def test_no_option_is_ever_sent_as_null(name: str, credentialed: None) -> None:
    """An option left unset must not reach the provider as an explicit null."""
    provider = _build(name)
    try:
        payload = provider._build_payload(TemplateRequest(template=TEMPLATE, params=PARAMS))  # type: ignore[attr-defined]
    finally:
        provider.close()

    assert None not in payload.values()


@pytest.mark.parametrize("name", TRANSLATING)
def test_the_template_and_every_input_reach_the_payload(name: str, credentialed: None) -> None:
    provider = _build(name)
    params = {"url": "https://example.com/page", "page": 2}
    try:
        payload = provider._build_payload(TemplateRequest(template=TEMPLATE, params=params))  # type: ignore[attr-defined]
    finally:
        provider.close()

    sent = repr(payload)
    assert TEMPLATE in sent
    assert all(repr(value) in sent for value in params.values()), f"{name} dropped an input it was handed: {payload}"


@pytest.mark.parametrize("name", TRANSLATING)
def test_every_format_a_provider_supports_is_a_distinct_request(name: str, credentialed: None) -> None:
    """Two formats that build the same call, or a format that builds the bare call, are a mapping bug."""
    provider = _build(name)
    try:
        shapes = {None: repr(provider._build_payload(TemplateRequest(template=TEMPLATE, params=PARAMS)))}  # type: ignore[attr-defined]
        for body_format in sorted(provider.supported_formats):
            request = TemplateRequest(template=TEMPLATE, params=PARAMS, format=body_format)
            provider.validate(request)
            shapes[body_format] = repr(provider._build_payload(request))  # type: ignore[attr-defined]
    finally:
        provider.close()

    assert len(set(shapes.values())) == len(shapes), f"{name} builds the same call for two formats: {shapes}"


@pytest.mark.parametrize("name", available_template_providers())
def test_an_unsupported_format_is_rejected_not_substituted(name: str, credentialed: None) -> None:
    provider = _build(name)
    unsupported = set(FORMATS) - provider.supported_formats
    try:
        for body_format in unsupported:
            with pytest.raises(UnsupportedFormatError, match=body_format):
                provider.run(TemplateRequest(template=TEMPLATE, params=PARAMS, format=body_format))  # type: ignore[arg-type]
    finally:
        provider.close()


@pytest.mark.parametrize("name", available_template_providers())
def test_provider_repr_never_leaks_a_credential(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SEKRIT-0123456789abcdef"
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, secret)

    provider = get_template_provider_class(name)(api_key=secret)
    try:
        assert secret not in repr(provider)
        assert secret not in repr(provider.options)
    finally:
        provider.close()


# -- What TemplateProvider guarantees on every backend's behalf ---------------


def _fake(**overrides: Any) -> TemplateProvider:
    """A fake template backend with one behaviour swapped out."""
    from tests.conftest import FakeTemplateProvider

    return type("Swapped", (FakeTemplateProvider,), overrides)()


def test_a_request_with_no_inputs_is_valid() -> None:
    """Some templates take nothing: the page is fixed, and the record is the whole of it."""
    response = _fake().run(TemplateRequest(template=TEMPLATE))

    assert response.params == {}
    assert response.data["inputs"] == {}


def test_validation_runs_before_anything_is_run() -> None:
    provider = _fake(supported_formats=frozenset({"markdown"}))
    with pytest.raises(UnsupportedFormatError):
        provider.run(TemplateRequest(template=TEMPLATE, params=PARAMS, format="html"))

    assert provider.requests == []  # type: ignore[attr-defined]


def test_an_unmapped_option_is_rejected_before_anything_is_run() -> None:
    provider = _fake(supported_parameters=frozenset())
    with pytest.raises(UnsupportedParameterError, match="format"):
        provider.run(TemplateRequest(template=TEMPLATE, params=PARAMS, format="markdown"))

    assert provider.requests == []  # type: ignore[attr-defined]


def test_no_format_asked_for_means_no_page_beside_the_record() -> None:
    """Unlike an extract, nothing is pinned: the record is the answer and the page is an extra."""
    response = _fake().run(TemplateRequest(template=TEMPLATE, params=PARAMS))

    assert response.document is None
    assert response.data


def test_a_format_asked_for_is_what_the_page_reports() -> None:
    response = _fake().run(TemplateRequest(template=TEMPLATE, params=PARAMS, format="html"))

    assert response.document is not None
    assert response.document.format == "html"


def test_a_backend_failure_on_a_run_is_wrapped_with_the_original() -> None:
    original = RuntimeError("connection reset")

    def _run(self: Any, request: TemplateRequest) -> TemplateResponse:
        raise original

    with pytest.raises(ProviderError, match="connection reset") as caught:
        _fake(_run=_run).run(TemplateRequest(template=TEMPLATE, params=PARAMS))

    assert caught.value.original is original


def test_a_backend_failure_on_a_listing_is_wrapped() -> None:
    def _list(self: Any, *, limit: int | None, offset: int | None) -> Any:
        raise RuntimeError("HTTP 500")

    with pytest.raises(ProviderError, match="listing templates failed: HTTP 500"):
        _fake(_list=_list).templates()


def test_an_unknown_template_name_is_a_provider_error_naming_it() -> None:
    provider = _fake(unknown_names=frozenset({"missing"}))
    with pytest.raises(ProviderError, match="'missing'") as caught:
        provider.template("missing")

    assert "404" in str(caught.value)


def test_a_router_error_from_a_backend_is_not_rewrapped() -> None:
    """An UnsupportedParameterError is a caller bug, not a backend failure."""

    def _run(self: Any, request: TemplateRequest) -> TemplateResponse:
        raise UnsupportedParameterError("fake", ["format"])

    with pytest.raises(UnsupportedParameterError):
        _fake(_run=_run).run(TemplateRequest(template=TEMPLATE, params=PARAMS))


def test_paging_left_unset_reaches_the_backend_unset() -> None:
    """Unset means unset: the backend pages as it would for any client of its own."""
    provider = _fake()
    provider.templates()
    provider.templates(limit=5, offset=10)

    assert provider.listed == [(None, None), (5, 10)]  # type: ignore[attr-defined]


async def test_the_async_path_runs_lists_and_describes_the_same_way() -> None:
    provider = _fake()

    response = await provider.arun(TemplateRequest(template=TEMPLATE, params=PARAMS, format="markdown"))
    page = await provider.atemplates(limit=1)
    template = await provider.atemplate(TEMPLATE)

    assert response.data["inputs"] == PARAMS and response.document is not None
    assert page.names == ["books_pdp", "jobs_listing"] and page.limit == 1
    assert template.name == TEMPLATE and template.published
