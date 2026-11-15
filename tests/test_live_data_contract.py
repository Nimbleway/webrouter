"""Guards on the Extract Template contract itself.

The counterpart of test_extract_contract.py for the method that runs a
backend's saved extraction. These catch drift that is invisible at runtime
until a user hits it: a backend that reports a page format it did not return,
a run that comes back with an empty record instead of saying why, a provider
that skipped validation by overriding the public method.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest

from web_router import AsyncWebRouter, WebRouter
from web_router.constants_live_data import LIVE_DATA_ENV_BY_PROVIDER
from web_router.exceptions import UnsupportedFormatError
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
def test_the_provider_is_optional_and_never_the_routers_default(method: str) -> None:
    """Unnamed, it is not the router's `auto` but every live data backend (a listing) or the source's own (a run)."""
    parameter = inspect.signature(getattr(WebRouter, method)).parameters["provider"]
    assert parameter.default is None
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


def test_the_live_data_env_table_names_the_same_variables_as_the_other_methods() -> None:
    """Spelled out in constants_live_data.py to avoid an import loop, so checked against the originals here."""
    from web_router.constants import (
        ENV_BRIGHTDATA_API_KEY,
        ENV_NIMBLE_API_KEY,
    )
    from web_router.constants_live_data_vendors import ENV_OXYLABS_WEB_API_KEY

    assert LIVE_DATA_ENV_BY_PROVIDER["nimble"] == (ENV_NIMBLE_API_KEY,)
    assert LIVE_DATA_ENV_BY_PROVIDER["brightdata"] == (ENV_BRIGHTDATA_API_KEY,)
    assert LIVE_DATA_ENV_BY_PROVIDER["oxylabs"] == (ENV_OXYLABS_WEB_API_KEY,)
