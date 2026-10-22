"""The Extract Template chain of `auto`: the keyed backends, and nothing after them."""

from __future__ import annotations

from typing import Any

import pytest

from web_router.constants import AUTO_TEMPLATE_PREFERRED, ENV_NIMBLE_API_KEY, TEMPLATE_ENV_BY_PROVIDER
from web_router.exceptions import (
    ConfigurationError,
    ProviderError,
    UnsupportedFormatError,
    UnsupportedParameterError,
)
from web_router.providers.auto import AutoTemplateProvider
from web_router.registry import register_template_provider, unregister_template_provider
from web_router.types import TemplateRequest

REQUEST = TemplateRequest(template="books_pdp", params={"url": "https://example.com/page"})


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_vars in TEMPLATE_ENV_BY_PROVIDER.values():
        for env_var in env_vars:
            monkeypatch.delenv(env_var, raising=False)


def _register(name: str, **overrides: Any) -> type:
    from tests.conftest import FakeTemplateProvider

    cls = type(name.title(), (FakeTemplateProvider,), {"name": name, **overrides})
    register_template_provider(name, cls)
    return cls


@pytest.fixture
def two_fakes() -> Any:
    first = _register("first")
    second = _register("second")
    yield first, second
    unregister_template_provider("first")
    unregister_template_provider("second")


def test_every_preferred_member_needs_a_credential() -> None:
    """No keyless tier: a run fetches on the backend's account."""
    for name in AUTO_TEMPLATE_PREFERRED:
        assert TEMPLATE_ENV_BY_PROVIDER[name], f"{name} is in the chain with no credential to be keyed on"


def test_the_default_chain_is_the_backends_you_hold_a_key_for(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert AutoTemplateProvider().members == ()

    monkeypatch.setenv(ENV_NIMBLE_API_KEY, "x")
    assert AutoTemplateProvider().members == ("nimble",)


def test_a_key_in_code_joins_the_chain_as_one_in_the_environment_does(no_keys: None) -> None:
    provider = AutoTemplateProvider(members_config={"nimble": {"api_key": "x"}})

    assert provider.members == ("nimble",)


def test_with_no_backend_configured_a_run_says_which_variable_would_fix_it(no_keys: None) -> None:
    with pytest.raises(ConfigurationError, match=ENV_NIMBLE_API_KEY):
        AutoTemplateProvider().run(REQUEST)


def test_a_listing_and_a_description_go_to_the_first_member_that_answers(two_fakes: Any) -> None:
    provider = AutoTemplateProvider(chain=["first", "second"])

    assert provider.templates().provider == "first"
    assert provider.template("books_pdp").provider == "first"
    assert provider.run(REQUEST).provider == "first"


def test_a_member_that_fails_falls_through_to_the_next(two_fakes: Any) -> None:
    """A refused run bills nothing, so the chain moves on as a search chain does."""

    def _run(self: Any, request: TemplateRequest) -> Any:
        raise ProviderError("first", "HTTP 503")

    two_fakes[0]._run = _run
    provider = AutoTemplateProvider(chain=["first", "second"])

    assert provider.run(REQUEST).provider == "second"


def test_every_member_failing_reports_each_ones_reason(two_fakes: Any) -> None:
    def _run(self: Any, request: TemplateRequest) -> Any:
        raise ProviderError(self.name, f"{self.name} refused")

    two_fakes[0]._run = _run
    two_fakes[1]._run = _run
    with pytest.raises(ProviderError) as caught:
        AutoTemplateProvider(chain=["first", "second"]).run(REQUEST)

    assert "first refused" in str(caught.value) and "second refused" in str(caught.value)


def test_a_member_that_cannot_return_the_format_is_passed_over_before_being_built(two_fakes: Any) -> None:
    two_fakes[0].supported_formats = frozenset({"html"})
    provider = AutoTemplateProvider(chain=["first", "second"])

    response = provider.run(REQUEST.model_copy(update={"format": "markdown"}))

    assert response.provider == "second"
    assert 0 not in provider._members, "a member passed over is never constructed"


def test_with_no_member_returning_the_format_the_call_raises_before_anything_runs(two_fakes: Any) -> None:
    two_fakes[0].supported_formats = frozenset({"html"})
    two_fakes[1].supported_formats = frozenset({"html"})
    provider = AutoTemplateProvider(chain=["first", "second"])

    with pytest.raises(UnsupportedFormatError, match="markdown") as caught:
        provider.run(REQUEST.model_copy(update={"format": "markdown"}))

    assert "first" in str(caught.value) and "second" in str(caught.value)
    assert provider._members == {}


def test_with_no_member_mapping_an_option_the_call_raises_naming_it(two_fakes: Any) -> None:
    two_fakes[0].supported_parameters = frozenset()
    two_fakes[1].supported_parameters = frozenset()
    provider = AutoTemplateProvider(chain=["first", "second"])

    with pytest.raises(UnsupportedParameterError, match="format"):
        provider.run(REQUEST.model_copy(update={"format": "markdown"}))

    assert provider._members == {}


def test_a_chain_keyed_by_method_reads_its_own_entry(two_fakes: Any) -> None:
    provider = AutoTemplateProvider(chain={"template": ["second"], "search": ["first"]})

    assert provider.members == ("second",)


async def test_the_async_path_follows_the_same_chain(two_fakes: Any) -> None:
    async def _arun(self: Any, request: TemplateRequest) -> Any:
        raise ProviderError("first", "HTTP 503")

    two_fakes[0]._arun = _arun
    provider = AutoTemplateProvider(chain=["first", "second"])

    assert (await provider.arun(REQUEST)).provider == "second"
    assert (await provider.atemplates()).provider == "first"
    assert (await provider.atemplate("books_pdp")).provider == "first"


def test_closing_auto_closes_every_member_it_built(two_fakes: Any) -> None:
    provider = AutoTemplateProvider(chain=["first", "second"])
    provider.run(REQUEST)
    provider.close()

    assert provider._members[0].closed  # type: ignore[attr-defined]
