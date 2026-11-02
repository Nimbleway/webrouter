"""Guards on the answer contract itself.

The counterpart of test_research_contract.py for the one-call method: a
question goes in, the backend's written answer and its sources come out. No
job, no polling -- so beside the usual drift guards, these pin what the base
class enforces on every backend's behalf: an option it does not map is refused
before anything is sent, and whatever it raises reaches the caller wrapped.
"""

from __future__ import annotations

import inspect
from typing import Any

import httpx
import pytest

from scripts.contracts import SAMPLES
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import ANSWER_ENV_BY_PROVIDER
from web_router.exceptions import ProviderError, UnsupportedParameterError
from web_router.providers._http import _HttpProvider
from web_router.providers.base import AnswerProvider
from web_router.registry import (
    available_answer_providers,
    configured_answer_providers,
    get_answer_provider_class,
)
from web_router.types_answer import ANSWER_ALWAYS_SENT, ANSWER_PARAMETERS, AnswerRequest, AnswerResponse

ROUTER_ONLY_ARGUMENTS = {"self", "query", "provider"}

ENV_VARS = sorted({env_var for env_vars in ANSWER_ENV_BY_PROVIDER.values() for env_var in env_vars})

PUBLIC_METHODS = ("answer", "aanswer", "validate")
"""Everything a caller reaches, all of which enforce something on the backend's behalf."""

PROBE_VALUES: dict[str, Any] = {**SAMPLES, "recency": "week"}
"""One value per option, to probe each backend's refusals with."""

QUERY = "who won the 2022 world cup final"


def _request(**options: Any) -> AnswerRequest:
    return AnswerRequest(query=QUERY, **options)


def _fake(**overrides: Any) -> Any:
    """A fake answer backend with one behaviour swapped out."""
    from tests.conftest import FakeAnswerProvider

    return type("Swapped", (FakeAnswerProvider,), overrides)()


def _refuse_every_call(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a refused option still reached the backend: {request.method} {request.url}")


@pytest.mark.parametrize("method", [WebRouter.answer, AsyncWebRouter.answer], ids=["sync", "async"])
def test_router_answer_exposes_every_shared_option(method: object) -> None:
    """The two answer signatures restate AnswerRequest's fields, so guard the copies."""
    exposed = set(inspect.signature(method).parameters) - ROUTER_ONLY_ARGUMENTS
    assert exposed == ANSWER_PARAMETERS | ANSWER_ALWAYS_SENT - {"query"}


def test_answer_parameters_tracks_the_model() -> None:
    assert set(AnswerRequest.model_fields) - ANSWER_ALWAYS_SENT == ANSWER_PARAMETERS


def test_every_provider_declares_where_its_credentials_come_from() -> None:
    """configured_answer_providers() reads this table, so a gap there hides a provider."""
    assert set(ANSWER_ENV_BY_PROVIDER) == set(available_answer_providers())


def test_no_answer_backend_is_usable_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every answer backend bills the call, and there is no `auto` to list instead."""
    for env_var in ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    assert configured_answer_providers() == []


@pytest.mark.parametrize("name", available_answer_providers())
def test_registered_providers_are_answer_providers(name: str) -> None:
    provider_cls = get_answer_provider_class(name)

    assert issubclass(provider_cls, AnswerProvider)
    assert provider_cls.name == name, f"{name} answers to a name it does not declare"


@pytest.mark.parametrize("name", available_answer_providers())
def test_providers_declare_only_real_options(name: str) -> None:
    """A typo in supported_parameters would silently reject a valid option forever."""
    assert get_answer_provider_class(name).supported_parameters <= ANSWER_PARAMETERS


@pytest.mark.parametrize("name", available_answer_providers())
def test_public_answer_methods_are_always_the_base_implementation(name: str) -> None:
    """Validation and error wrapping live on the base class; an override would skip them."""
    provider_cls = get_answer_provider_class(name)
    for method in PUBLIC_METHODS:
        assert getattr(provider_cls, method) is getattr(AnswerProvider, method), (
            f"{name} overrides {method}() and would skip what the base class enforces"
        )


@pytest.mark.parametrize("name", available_answer_providers())
def test_providers_are_concrete(name: str) -> None:
    assert not inspect.isabstract(get_answer_provider_class(name))


@pytest.mark.parametrize("name", available_answer_providers())
def test_provider_repr_never_leaks_a_credential(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SEKRIT-0123456789abcdef"
    for env_var in ENV_VARS:
        monkeypatch.setenv(env_var, secret)

    provider = get_answer_provider_class(name)(api_key=secret)
    try:
        assert secret not in repr(provider)
        assert secret not in repr(provider.options)
    finally:
        provider.close()


def _unmapped() -> list[tuple[str, str]]:
    return [
        (name, option)
        for name in available_answer_providers()
        for option in sorted(ANSWER_PARAMETERS - get_answer_provider_class(name).supported_parameters)
    ]


@pytest.mark.parametrize(("name", "option"), _unmapped())
def test_every_backend_refuses_every_option_it_does_not_map_before_sending(name: str, option: str) -> None:
    """The acceptance rule, held for every built-in backend rather than per module.

    The backend's HTTP clients fail on any call, so a refusal that came after
    the request went out fails here too.
    """
    provider = get_answer_provider_class(name)(api_key="contract-probe-key")
    if not isinstance(provider, _HttpProvider):
        pytest.fail(f"{name} is not an HTTP backend: give this test a trap for its transport too")
    transport = httpx.MockTransport(_refuse_every_call)
    provider._sync = httpx.Client(**provider._client_kwargs, transport=transport)
    try:
        with pytest.raises(UnsupportedParameterError, match=option):
            provider.answer(_request(**{option: PROBE_VALUES[option]}))
    finally:
        provider.close()


# -- What AnswerProvider guarantees on every backend's behalf ----------------


def test_an_option_the_backend_does_not_map_is_rejected_not_dropped() -> None:
    provider = _fake(supported_parameters=frozenset({"country"}))

    provider.answer(_request(country="US"))  # mapped, so this is fine

    with pytest.raises(UnsupportedParameterError, match="recency"):
        provider.answer(_request(recency="week"))

    assert len(provider.requests) == 1, "the refused call must never reach the backend"


async def test_the_async_path_refuses_the_same_way() -> None:
    provider = _fake(supported_parameters=frozenset())

    with pytest.raises(UnsupportedParameterError, match="include_domains"):
        await provider.aanswer(_request(include_domains=["example.com"]))

    assert provider.requests == []


def test_whatever_a_backend_raises_is_wrapped() -> None:
    provider = _fake(raises=RuntimeError("upstream exploded"))

    with pytest.raises(ProviderError, match="upstream exploded") as caught:
        provider.answer(_request())

    assert isinstance(caught.value.original, RuntimeError)
    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_whatever_a_backend_raises_asynchronously_is_wrapped_too() -> None:
    provider = _fake(raises=RuntimeError("upstream exploded"))

    with pytest.raises(ProviderError, match="upstream exploded") as caught:
        await provider.aanswer(_request())

    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_router_error_passes_through_unwrapped() -> None:
    """A web-router error is the answer already; rewrapping it would bury it."""
    provider = _fake(raises=UnsupportedParameterError("fake", ["country"]))

    with pytest.raises(UnsupportedParameterError):
        provider.answer(_request())


def test_an_empty_answer_is_returned_not_raised() -> None:
    """'The backend had nothing' is an answer a caller can read, raw payload and all."""
    response = _fake(answer_text="").answer(_request())

    assert isinstance(response, AnswerResponse)
    assert response.answer == ""
    assert response.raw == [{"answer": ""}]


def test_an_error_with_no_text_is_named_by_its_type() -> None:
    """httpx's timeouts carry no text; the message must still say what happened."""
    import httpx

    provider = _fake(raises=httpx.ReadTimeout(""))

    with pytest.raises(ProviderError, match=r"answer failed: ReadTimeout$"):
        provider.answer(_request())
