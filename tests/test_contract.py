"""Guards on the shared contract itself.

These catch drift that is invisible at runtime until a user hits it: a router
signature that forgot an option, a provider that skipped validation, a
credential that leaked into a repr.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest
from pydantic import BaseModel

import web_router
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import ENV_BY_PROVIDER
from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers.base import SearchProvider
from web_router.registry import available_providers, configured_providers, get_provider_class
from web_router.types import ALWAYS_SENT, NORMALIZED_PARAMETERS, SearchDepth, SearchRequest, SearchResponse

ROUTER_ONLY_ARGUMENTS = {"self", "query", "provider", "budget"}

DEPTHS: tuple[SearchDepth, ...] = get_args(SearchDepth)


def _maps_depth(name: str) -> bool:
    return "search_depth" in get_provider_class(name).supported_parameters


def _translates(name: str) -> bool:
    """Whether this provider builds a payload of its own.

    `auto` routes to other providers instead of talking to an API, so the
    payload-shaping guarantees below are enforced on its members. Its own
    behaviour is covered in tests/test_auto.py.
    """
    return hasattr(get_provider_class(name), "_build_payload")


TRANSLATING = [name for name in available_providers() if _translates(name)]


SDK_BACKED = {"nimble": ("nimble_python", "Nimble"), "tavily": ("tavily", "TavilyClient")}
"""Providers wrapping a vendor SDK, whose search signature we can check against."""

SAMPLE_VALUES: dict[str, object] = {
    "max_results": 5,
    "search_depth": "deep",
    "include_domains": ["a.com"],
    "exclude_domains": ["b.com"],
    "country": "US",
    "locale": "en",
}

ENV_VARS = (
    "NIMBLE_API_KEY",
    "TAVILY_API_KEY",
    "YOU_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "EXA_API_KEY",
    "PARALLEL_API_KEY",
    "PERPLEXITY_API_KEY",
)


@pytest.mark.parametrize("method", [WebRouter.search, AsyncWebRouter.search], ids=["sync", "async"])
def test_router_search_exposes_every_shared_option(method: object) -> None:
    """The two search signatures restate SearchRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == NORMALIZED_PARAMETERS | ALWAYS_SENT - {"query"}


def test_every_provider_declares_where_its_credential_comes_from() -> None:
    """configured_providers() reads this table, so a gap there silently hides a provider."""
    assert set(ENV_BY_PROVIDER) == set(available_providers())


def test_configured_providers_reports_what_the_environment_supports(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in ENV_BY_PROVIDER.values():
        if env_var:
            monkeypatch.delenv(env_var, raising=False)
    keyless = set(configured_providers())

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")

    assert "brave" not in keyless, "a provider with no key must not be reported as usable"
    assert "brave" in configured_providers()
    assert keyless <= set(configured_providers())
    assert set(configured_providers()) <= set(available_providers())


def test_a_whitespace_only_key_is_not_reported_as_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider would treat it as missing, so listing it would promise a call that cannot be built."""
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", " ")
    assert "brave" not in configured_providers()


def test_a_keyless_provider_needs_no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in ENV_BY_PROVIDER.values():
        if env_var:
            monkeypatch.delenv(env_var, raising=False)

    assert "you-mcp" in configured_providers()


def test_normalized_parameters_tracks_the_model() -> None:
    assert set(SearchRequest.model_fields) - ALWAYS_SENT == NORMALIZED_PARAMETERS


@pytest.mark.parametrize("name", available_providers())
def test_providers_declare_only_real_options(name: str) -> None:
    """A typo in supported_parameters would silently reject a valid option forever."""
    assert get_provider_class(name).supported_parameters <= NORMALIZED_PARAMETERS


@pytest.mark.parametrize("name", available_providers())
def test_public_search_is_always_the_base_implementation(name: str) -> None:
    """Validation and error wrapping live in `SearchProvider.search`.

    A provider that overrode `search` -- directly or through an intermediate
    base like `HttpSearchProvider` -- would bypass both. Comparing the bound
    function catches that however deep the hierarchy gets.
    """
    provider_cls = get_provider_class(name)
    assert provider_cls.search is SearchProvider.search, f"{name} overrides search() and would skip validation"
    assert provider_cls.asearch is SearchProvider.asearch, f"{name} overrides asearch()"


@pytest.mark.parametrize("name", available_providers())
def test_providers_are_concrete(name: str) -> None:
    """Every registered provider must implement the whole contract."""
    assert not inspect.isabstract(get_provider_class(name))


@pytest.mark.parametrize("name", TRANSLATING)
def test_provider_options_always_reach_the_api(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The escape hatch has to work on every provider, including new ones.

    Providers build payloads through `_utils.build_payload`, so this holds
    without each one remembering to merge provider_options itself.
    """
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")

    provider = get_provider_class(name)(api_key="test-key")
    try:
        payload = provider._build_payload(SearchRequest(query="hi", provider_options={"x_custom": "sentinel"}))
    finally:
        provider.close()

    assert payload["x_custom"] == "sentinel"


@pytest.mark.parametrize("name", TRANSLATING)
def test_unset_options_are_never_sent(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An option left as None must not reach the provider as null."""
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")

    provider = get_provider_class(name)(api_key="test-key")
    try:
        payload = provider._build_payload(SearchRequest(query="hi"))
    finally:
        provider.close()

    assert None not in payload.values()
    assert not NORMALIZED_PARAMETERS & set(payload), "an unset normalized name leaked into the payload"


@pytest.mark.parametrize("name", [n for n in TRANSLATING if _maps_depth(n)])
def test_every_depth_level_is_a_distinct_request(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that maps search_depth must build a different request per level.

    Two levels that build the same payload are a mapping bug, not a design
    choice: the caller pays for `deep` and gets `lite`, and nothing fails.
    Nimble was sent our own vocabulary verbatim until it was.
    """
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")

    provider = get_provider_class(name)(api_key="test-key")
    try:
        payloads = {depth: provider._build_payload(SearchRequest(query="hi", search_depth=depth)) for depth in DEPTHS}
    finally:
        provider.close()

    distinct = {depth: repr(sorted(payload.items())) for depth, payload in payloads.items()}
    assert len(set(distinct.values())) == len(DEPTHS), f"{name} builds the same request for two depths: {payloads}"


@pytest.mark.parametrize("name", sorted(SDK_BACKED))
def test_every_mapped_key_is_a_parameter_the_sdk_declares(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A key the SDK does not declare is forwarded and ignored, not rejected.

    Both SDK-backed clients take **kwargs, so an option belonging to a
    different endpoint rides along and is dropped server-side in silence. That
    is how Tavily's `fast` depth came to carry `chunks_per_source`, which is an
    extract and crawl parameter -- it made `fast` an exact duplicate of `lite`
    while still building a payload that *looked* distinct.
    """
    module_name, class_name = SDK_BACKED[name]
    module = pytest.importorskip(module_name, reason=f"{name} extra not installed")
    declared = set(inspect.signature(getattr(module, class_name).search).parameters)

    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, "test-key")

    provider = get_provider_class(name)(api_key="test-key")
    supported = {key: value for key, value in SAMPLE_VALUES.items() if key in provider.supported_parameters}
    try:
        payloads = [
            provider._build_payload(SearchRequest(query="hi", **{**supported, "search_depth": depth}))
            for depth in DEPTHS
        ]
    finally:
        provider.close()

    sent = {key for payload in payloads for key in payload}
    assert sent <= declared, f"{name} sends {sorted(sent - declared)}, which {class_name}.search does not declare"


def test_validation_runs_even_if_a_provider_forgets_it(fake_provider: type) -> None:
    """Validation lives in the base class, so it cannot be skipped by a subclass."""

    class Narrow(fake_provider):  # type: ignore[misc, valid-type]
        name = "narrow"
        supported_parameters = frozenset({"max_results"})

    provider = Narrow()
    with pytest.raises(UnsupportedParameterError):
        provider.search(SearchRequest(query="hi", country="US"))


def test_backend_failures_are_wrapped_without_touching_the_provider(fake_provider: type) -> None:
    """The base class wraps anything a provider raises, so every backend fails alike."""

    class Broken(fake_provider):  # type: ignore[misc, valid-type]
        name = "broken"

        def _search(self, request: SearchRequest) -> SearchResponse:
            raise RuntimeError("upstream exploded")

    with pytest.raises(ProviderError, match="upstream exploded") as caught:
        Broken().search(SearchRequest(query="hi"))

    assert isinstance(caught.value.original, RuntimeError)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_router_errors_are_not_rewrapped(fake_provider: type) -> None:
    """A web-router error passes through intact rather than becoming a ProviderError."""

    class Picky(fake_provider):  # type: ignore[misc, valid-type]
        name = "picky"

        def _search(self, request: SearchRequest) -> SearchResponse:
            raise UnsupportedParameterError(self.name, ["country"])

    with pytest.raises(UnsupportedParameterError):
        Picky().search(SearchRequest(query="hi"))


@pytest.mark.parametrize("name", available_providers())
def test_provider_repr_never_leaks_a_credential(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SEKRIT-0123456789abcdef"
    for env_var in ("NIMBLE_API_KEY", "TAVILY_API_KEY", "YOU_API_KEY"):
        monkeypatch.setenv(env_var, secret)

    provider = get_provider_class(name)(api_key=secret)
    try:
        assert secret not in repr(provider)
        assert secret not in repr(provider.options)
    finally:
        provider.close()


def test_every_public_model_is_importable_from_the_package_root() -> None:
    """A model that types a public field, such as ``Template.samples``, is one a caller has to name."""
    models = {
        name
        for name, value in vars(web_router.types).items()
        if not name.startswith("_")
        and inspect.isclass(value)
        and issubclass(value, BaseModel)
        and value.__module__ == "web_router.types"
    }

    # The shared base of the request models: no caller builds one.
    assert models - {"NormalizedRequest"} <= set(web_router.__all__)
