"""answer()'s `auto`: the configured answer backends, cheapest first, with a fall-through on failure."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.conftest import FakeAnswerProvider
from web_router import AsyncWebRouter, WebRouter
from web_router.constants import ANSWER_ENV_BY_PROVIDER
from web_router.constants_answer import AUTO_ANSWER_PREFERRED
from web_router.exceptions import NoAnswerProviderError, ProviderError, UnsupportedParameterError
from web_router.registry_answer import get_answer_provider_class, register_answer_provider, unregister_answer_provider
from web_router.types_answer import ANSWER_PARAMETERS, AnswerRequest, AnswerResponse

CHEAPEST, SECOND, THIRD = AUTO_ANSWER_PREFERRED[:3]


def _backend(backend: str, *, supported: frozenset[str] = ANSWER_PARAMETERS) -> type[FakeAnswerProvider]:
    return type(f"Fake_{backend}", (FakeAnswerProvider,), {"name": backend, "supported_parameters": supported})


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every preferred backend configured, each served by a fake under its own name."""
    for name in list(ANSWER_ENV_BY_PROVIDER):
        monkeypatch.delitem(ANSWER_ENV_BY_PROVIDER, name)
    for name in AUTO_ANSWER_PREFERRED:
        monkeypatch.setitem(ANSWER_ENV_BY_PROVIDER, name, (f"WEB_ROUTER_TEST_{name.upper()}_KEY",))
        monkeypatch.setenv(f"WEB_ROUTER_TEST_{name.upper()}_KEY", "k")
        register_answer_provider(name, _backend(name))
    yield
    for name in AUTO_ANSWER_PREFERRED:
        unregister_answer_provider(name)


def test_auto_tries_the_configured_backends_cheapest_first(keys: None) -> None:
    router = WebRouter()

    assert router.get_answer_provider().members == AUTO_ANSWER_PREFERRED  # type: ignore[attr-defined]
    assert router.answer("q").provider == CHEAPEST, "unnamed, the router's default `auto` serves the call"


def test_a_backend_that_fails_is_passed_over_for_the_next(keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_answer_provider_class(CHEAPEST), "raises", ProviderError(CHEAPEST, "HTTP 503"))

    assert WebRouter().answer("q").provider == SECOND


def test_an_empty_answer_falls_through_and_the_first_empty_one_returns_when_all_are(
    keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_answer_provider_class(CHEAPEST), "answer_text", " ")
    assert WebRouter().answer("q").provider == SECOND

    for name in AUTO_ANSWER_PREFERRED:
        monkeypatch.setattr(get_answer_provider_class(name), "answer_text", "")
    assert WebRouter().answer("q").provider == CHEAPEST


def test_a_backend_that_does_not_map_an_option_is_never_built_or_asked(
    keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_answer_provider(CHEAPEST, _backend(CHEAPEST, supported=frozenset()))
    router = WebRouter()

    assert router.answer("q", recency="week").provider == SECOND
    assert 0 not in router.get_answer_provider()._members  # type: ignore[attr-defined]


def test_a_request_no_backend_maps_raises_before_anything_is_asked(keys: None) -> None:
    for name in AUTO_ANSWER_PREFERRED:
        register_answer_provider(name, _backend(name, supported=frozenset()))
    router = WebRouter()

    with pytest.raises(UnsupportedParameterError, match="recency"):
        router.answer("q", recency="week")
    assert router.get_answer_provider()._members == {}  # type: ignore[attr-defined]


def test_a_backend_that_refuses_a_value_before_sending_is_passed_over(keys: None) -> None:
    class OneDomainList(_backend(CHEAPEST)):  # type: ignore[misc]
        def _answer(self, request: AnswerRequest) -> AnswerResponse:
            raise UnsupportedParameterError(self.name, {"include_domains", "exclude_domains"})

    register_answer_provider(CHEAPEST, OneDomainList)

    response = WebRouter().answer("q", include_domains=["a.example"], exclude_domains=["b.example"])

    assert response.provider == SECOND


def test_with_no_answer_key_auto_names_the_variables_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(ANSWER_ENV_BY_PROVIDER):
        monkeypatch.setitem(ANSWER_ENV_BY_PROVIDER, name, (f"WEB_ROUTER_TEST_UNSET_{name.upper()}",))
        monkeypatch.delenv(f"WEB_ROUTER_TEST_UNSET_{name.upper()}", raising=False)

    with pytest.raises(NoAnswerProviderError) as caught:
        WebRouter().answer("q")

    assert caught.value.configured == []
    assert "WEB_ROUTER_TEST_UNSET_PERPLEXITY" in str(caught.value)


def test_a_chain_keyed_by_method_sets_answers_order_alone(keys: None) -> None:
    router = WebRouter(provider_config={"auto": {"chain": {"answer": [THIRD, CHEAPEST]}}})

    assert router.answer("q").provider == THIRD


async def test_aanswer_falls_through_the_same_way(keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_answer_provider_class(CHEAPEST), "raises", ProviderError(CHEAPEST, "HTTP 503"))

    async with AsyncWebRouter() as router:
        assert (await router.answer("q")).provider == SECOND


def test_the_preferred_order_names_every_built_in_answer_backend() -> None:
    from web_router.registry_answer import _BUILTIN_ANSWER_PROVIDERS

    assert set(AUTO_ANSWER_PREFERRED) == set(_BUILTIN_ANSWER_PROVIDERS) - {"auto"}


def test_an_option_only_an_added_backend_maps_reaches_it(keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AUTO_ANSWER_PREFERRED:
        register_answer_provider(name, _backend(name, supported=frozenset()))
    monkeypatch.setitem(ANSWER_ENV_BY_PROVIDER, "custom", ("WEB_ROUTER_TEST_CUSTOM_KEY",))
    monkeypatch.setenv("WEB_ROUTER_TEST_CUSTOM_KEY", "k")
    register_answer_provider("custom", _backend("custom", supported=frozenset({"locale"})))
    try:
        assert WebRouter().answer("q", locale="en").provider == "custom"
    finally:
        unregister_answer_provider("custom")


def test_an_option_no_answer_backend_maps_is_refused_before_any_is_asked(keys: None) -> None:
    for name in AUTO_ANSWER_PREFERRED:  # as every built-in: none maps locale
        register_answer_provider(name, _backend(name, supported=ANSWER_PARAMETERS - {"locale"}))
    router = WebRouter()

    with pytest.raises(UnsupportedParameterError, match="locale"):
        router.answer("q", locale="en")
    assert router.get_answer_provider()._members == {}  # type: ignore[attr-defined]


def test_a_search_only_key_passed_in_code_does_not_join_the_answer_chain(keys: None) -> None:
    router = WebRouter(provider_config={"nimble": {"api_key": "in-code"}})

    assert "nimble" not in router.get_answer_provider().members  # type: ignore[attr-defined]
