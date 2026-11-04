from __future__ import annotations

import pytest

from web_router import AsyncWebRouter, Provider, WebRouter
from web_router.exceptions import ProviderNotFoundError


def test_unknown_default_provider_fails_fast(fake_provider: type) -> None:
    """Constructing the router validates the name, rather than failing at the first search."""
    with pytest.raises(ProviderNotFoundError, match="Unknown provider"):
        WebRouter("nope")


def test_the_names_a_router_suggests_are_listed_once_each() -> None:
    """The router checks both method registries, and `nimble` is in each of them."""
    with pytest.raises(ProviderNotFoundError) as caught:
        WebRouter("nope")

    assert caught.value.available == sorted(set(caught.value.available))
    assert caught.value.available.count("nimble") == 1


def test_search_normalizes_arguments_into_the_request(fake_provider: type) -> None:
    router = WebRouter("fake")
    response = router.search("hello", max_results=3, search_depth="fast")

    request = router.get_provider().requests[0]
    assert request.query == "hello"
    assert request.max_results == 3
    assert request.search_depth == "fast"
    assert response.provider == "fake"
    assert response.urls == ["https://example.com"]


def test_providers_are_constructed_once_and_reused(fake_provider: type) -> None:
    router = WebRouter("fake")
    assert router.get_provider() is router.get_provider("fake")


def test_api_key_reaches_the_provider(fake_provider: type) -> None:
    router = WebRouter("fake", provider_config={"fake": {"api_key": "secret"}})
    assert router.get_provider().api_key == "secret"


def test_context_manager_closes_instantiated_providers(fake_provider: type) -> None:
    with WebRouter("fake") as router:
        provider = router.get_provider()
    assert provider.closed


async def test_async_router_searches_and_closes(fake_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        response = await router.search("hello")
        provider = router.get_provider()

    assert response.query == "hello"
    assert provider.closed


def test_unsupported_parameter_is_rejected_not_dropped(fake_provider: type) -> None:
    from web_router.exceptions import UnsupportedParameterError
    from web_router.registry import register_provider, unregister_provider

    class Narrow(fake_provider):  # type: ignore[misc, valid-type]
        name = "narrow"
        supported_parameters = frozenset({"max_results"})

    register_provider("narrow", Narrow)
    try:
        router = WebRouter("narrow")
        router.search("hi", max_results=3)  # supported, so this is fine
        with pytest.raises(UnsupportedParameterError, match="country"):
            router.search("hi", country="US")
    finally:
        unregister_provider("narrow")


def test_router_accepts_the_enum_as_well_as_a_string(fake_provider: type) -> None:
    router = WebRouter(Provider.NIMBLE)
    assert router.default_provider == "nimble"


def test_the_default_provider_is_stored_as_a_plain_name() -> None:
    """Downstream code, error messages and reprs should never see an enum member."""
    assert repr(WebRouter(Provider.NIMBLE)) == "<WebRouter default_provider='nimble'>"


def test_provider_config_keyed_by_enum_reaches_the_provider(fake_provider: type) -> None:
    router = WebRouter("fake", provider_config={Provider.NIMBLE: {"api_key": "n"}, "fake": {"api_key": "f"}})
    assert router.get_provider().api_key == "f"


def test_provider_config_key_casing_does_not_have_to_match(fake_provider: type) -> None:
    router = WebRouter("fake", provider_config={"FAKE": {"api_key": "f"}})
    assert router.get_provider().api_key == "f"


async def test_async_router_accepts_the_enum_per_call(fake_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        response = await router.search("hi", provider="fake")
    assert response.provider == "fake"


def test_search_accepts_an_already_constructed_provider(fake_provider: type) -> None:
    """For looping over backends: the settings travel with the provider, not the call."""
    instance = fake_provider()
    router = WebRouter("fake")

    response = router.search("q", provider=instance)

    assert response.provider == "fake"
    assert instance.requests, "the passed-in instance should have served the search"


def test_a_passed_in_provider_is_not_cached_by_the_router(fake_provider: type) -> None:
    instance = fake_provider()
    router = WebRouter("fake")
    router.search("q", provider=instance)

    assert instance not in router._instances.values()


def test_closing_the_router_leaves_a_caller_owned_provider_open(fake_provider: type) -> None:
    """The router never built it, so shutting its client down would be a surprise."""
    instance = fake_provider()
    router = WebRouter("fake")
    router.search("q", provider=instance)

    router.close()

    assert not instance.closed


async def test_async_search_accepts_a_provider_instance(fake_provider: type) -> None:
    instance = fake_provider()
    async with AsyncWebRouter("fake") as router:
        response = await router.search("q", provider=instance)

    assert response.provider == "fake"
    assert not instance.closed


# -- Extract -----------------------------------------------------------------


def test_extract_normalizes_arguments_into_the_request(fake_extract_provider: type) -> None:
    router = WebRouter("fake")
    response = router.extract("https://example.com", format="text")

    provider = router.get_extract_provider()
    assert provider.calls == [["https://example.com"]]
    assert response.provider == "fake"
    assert response.urls == ["https://example.com"]
    assert response.documents[0].format == "text"


def test_a_single_url_and_a_list_of_one_behave_the_same(fake_extract_provider: type) -> None:
    """The convenience of passing one URL must not change the shape of the answer."""
    router = WebRouter("fake")

    assert router.extract("https://example.com").urls == router.extract(["https://example.com"]).urls


def test_extract_providers_are_constructed_once_and_reused(fake_extract_provider: type) -> None:
    router = WebRouter("fake")
    assert router.get_extract_provider() is router.get_extract_provider("fake")


def test_the_two_methods_get_their_own_provider_instance(fake_provider: type, fake_extract_provider: type) -> None:
    """One name resolves to two classes, so caching them together would hand back the wrong one."""
    router = WebRouter("fake")

    assert router.get_provider() is not router.get_extract_provider()
    assert isinstance(router.get_provider(), fake_provider)
    assert isinstance(router.get_extract_provider(), fake_extract_provider)


def test_a_backend_that_only_searches_says_so_when_asked_to_extract(fake_provider: type) -> None:
    """Naming the wrong method's backend must name the ones that method does serve."""
    router = WebRouter("brave")

    with pytest.raises(ProviderNotFoundError, match="jina"):
        router.extract("https://example.com")


def test_a_backend_that_only_extracts_is_still_a_valid_default() -> None:
    """A router defaulted to an extract-only backend still searches through `provider=`."""
    router = WebRouter("oxylabs")

    assert router.default_provider == "oxylabs"
    with pytest.raises(ProviderNotFoundError, match="brave"):
        router.search("hello")


def test_api_key_reaches_the_extract_provider(fake_extract_provider: type) -> None:
    router = WebRouter("fake", provider_config={"fake": {"api_key": "secret"}})
    assert router.get_extract_provider().api_key == "secret"


def test_context_manager_closes_both_methods_providers(fake_provider: type, fake_extract_provider: type) -> None:
    with WebRouter("fake") as router:
        searcher = router.get_provider()
        extractor = router.get_extract_provider()

    assert searcher.closed and extractor.closed


def test_an_already_built_extract_provider_is_not_adopted(fake_extract_provider: type) -> None:
    """Passing an instance keeps it the caller's to close, as on the search side."""
    instance = fake_extract_provider()
    with WebRouter("fake") as router:
        response = router.extract("https://example.com", provider=instance)

    assert response.provider == "fake"
    assert not instance.closed


async def test_async_extract_normalizes_arguments_into_the_request(fake_extract_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        response = await router.extract(["https://example.com", "https://example.org"])
        provider = router.get_extract_provider()

    assert provider.calls == [["https://example.com"], ["https://example.org"]]
    assert len(response) == 2
    assert provider.closed


async def test_async_context_manager_closes_both_methods_providers(
    fake_provider: type, fake_extract_provider: type
) -> None:
    async with AsyncWebRouter("fake") as router:
        searcher = router.get_provider()
        extractor = router.get_extract_provider()

    assert searcher.closed and extractor.closed


# -- Research ----------------------------------------------------------------


def test_run_template_normalizes_arguments_into_the_request(fake_template_provider: type) -> None:
    router = WebRouter("fake")
    response = router.run_template("books_pdp", {"url": "https://example.com/page"}, provider="fake", format="markdown")

    request = router.get_template_provider("fake").requests[0]  # type: ignore[attr-defined]
    assert request.template == "books_pdp"
    assert request.inputs == {"url": "https://example.com/page"}
    assert request.format == "markdown"
    assert response.provider == "fake"
    assert response.data["inputs"] == {"url": "https://example.com/page"}


def test_run_template_with_no_inputs_sends_none(fake_template_provider: type) -> None:
    router = WebRouter("fake")
    router.run_template("fixed_page", provider="fake")

    request = router.get_template_provider("fake").requests[0]  # type: ignore[attr-defined]
    assert request.inputs == {} and request.format is None


def test_templates_and_template_reach_the_same_cached_provider(fake_template_provider: type) -> None:
    router = WebRouter("fake")
    page = router.templates(provider="fake", limit=1, offset=1)
    template = router.template("books_pdp", provider="fake")

    provider = router.get_template_provider("fake")
    assert provider is router.get_template_provider("fake")
    assert provider.listed == [(1, 1)]  # type: ignore[attr-defined]
    assert provider.described == ["books_pdp"]  # type: ignore[attr-defined]
    assert page.names == ["books_pdp", "jobs_listing"]
    assert template.input_schema is not None


def test_run_template_accepts_an_already_constructed_provider(fake_template_provider: type) -> None:
    instance = fake_template_provider()
    router = WebRouter("fake")

    response = router.run_template("books_pdp", provider=instance)

    assert response.provider == "fake"
    assert instance.requests, "the call went to the instance handed in"
    assert instance not in router._template_instances.values()


def test_closing_the_router_closes_the_template_provider_it_built(fake_template_provider: type) -> None:
    with WebRouter("fake") as router:
        router.run_template("books_pdp", provider="fake")
        provider = router.get_template_provider("fake")

    assert provider.closed  # type: ignore[attr-defined]


def test_a_backend_that_only_runs_templates_says_so_when_asked_to_search(fake_template_provider: type) -> None:
    router = WebRouter("fake")

    assert router.run_template("books_pdp", provider="fake").provider == "fake"
    with pytest.raises(ProviderNotFoundError):
        router.search("q")


def test_a_backend_without_templates_is_reported_with_the_ones_that_have_them(fake_template_provider: type) -> None:
    """Tavily extracts; it holds no templates, and the error lists the vendors that do."""
    router = WebRouter("fake")

    with pytest.raises(ProviderNotFoundError, match="fake") as caught:
        router.run_template("books_pdp", provider="tavily")
    assert "tavily" not in caught.value.available


async def test_async_run_template_and_catalogue(fake_template_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        response = await router.run_template("books_pdp", {"url": "https://example.com/page"}, provider="fake")
        page = await router.templates(provider="fake")
        template = await router.template("jobs_listing", provider="fake")
        provider = router.get_template_provider("fake")

    assert response.data["inputs"] == {"url": "https://example.com/page"}
    assert page.names == ["books_pdp", "jobs_listing"]
    assert template.name == "jobs_listing"
    assert provider.closed  # type: ignore[attr-defined]


def test_research_normalizes_arguments_into_the_request(fake_research_provider: type) -> None:
    router = WebRouter("fake")
    response = router.research("hello", effort="high", include_domains=["example.com"])

    request = router.get_research_provider().requests[0]
    assert request.query == "hello"
    assert request.effort == "high"
    assert request.include_domains == ["example.com"]
    assert response.provider == "fake"
    assert response.answer == "the answer"


def test_submit_research_builds_the_same_request_and_hands_back_the_job(fake_research_provider: type) -> None:
    router = WebRouter("fake")
    job = router.submit_research("hello", effort="low")

    assert (job.provider, job.job_id) == ("fake", "job-1")
    assert router.get_research_provider().requests[0].effort == "low"
    assert job.result().answer == "the answer"


def test_a_pydantic_model_describes_the_structured_answer(fake_research_provider: type) -> None:
    """Callers asking for structure usually have a model already; asking them to convert it is noise."""
    from pydantic import BaseModel

    class Winner(BaseModel):
        team: str

    router = WebRouter("fake")
    response = router.research("hello", output_schema=Winner)

    assert router.get_research_provider().requests[0].output_schema == Winner.model_json_schema()
    assert response.structured == {"ok": True}


def test_a_json_schema_is_passed_through_and_no_schema_asks_for_prose(fake_research_provider: type) -> None:
    schema = {"type": "object", "properties": {"team": {"type": "string"}}}
    router = WebRouter("fake")

    router.research("hello", output_schema=schema)
    router.research("hello")

    requests = router.get_research_provider().requests
    assert requests[0].output_schema == schema
    assert requests[1].output_schema is None


def test_research_providers_are_constructed_once_and_reused(fake_research_provider: type) -> None:
    router = WebRouter("fake")
    assert router.get_research_provider() is router.get_research_provider("fake")


def test_a_per_call_provider_overrides_the_routers_default(fake_research_provider: type) -> None:
    router = WebRouter("nimble")
    assert router.research("q", provider="fake").provider == "fake"


def test_the_three_methods_get_their_own_provider_instance(
    fake_provider: type, fake_extract_provider: type, fake_research_provider: type
) -> None:
    """One name resolves to three classes, so caching them together would hand back the wrong one."""
    router = WebRouter("fake")
    instances = [router.get_provider(), router.get_extract_provider(), router.get_research_provider()]

    assert len({id(instance) for instance in instances}) == 3
    assert isinstance(router.get_research_provider(), fake_research_provider)


def test_research_accepts_an_already_constructed_provider(fake_research_provider: type) -> None:
    instance = fake_research_provider()
    router = WebRouter("fake")

    response = router.research("q", provider=instance)

    assert response.provider == "fake"
    assert instance.requests, "the passed-in instance should have served the run"
    assert instance not in router._research_instances.values()


def test_closing_the_router_leaves_a_caller_owned_research_provider_open(fake_research_provider: type) -> None:
    instance = fake_research_provider()
    router = WebRouter("fake")
    router.research("q", provider=instance)

    router.close()

    assert not instance.closed


def test_context_manager_closes_all_three_methods_providers(
    fake_provider: type, fake_extract_provider: type, fake_research_provider: type
) -> None:
    with WebRouter("fake") as router:
        searcher = router.get_provider()
        extractor = router.get_extract_provider()
        researcher = router.get_research_provider()

    assert searcher.closed and extractor.closed and researcher.closed


def test_a_backend_that_only_researches_says_so_when_asked_to_search_or_extract(
    fake_research_provider: type,
) -> None:
    """Naming the wrong method's backend must name the ones that method does serve."""
    router = WebRouter("fake")
    assert router.research("q").provider == "fake"

    with pytest.raises(ProviderNotFoundError, match="brave"):
        router.search("q")
    with pytest.raises(ProviderNotFoundError, match="jina"):
        router.extract("https://example.com")


async def test_async_research_normalizes_arguments_into_the_request(fake_research_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        response = await router.research("hello", effort="low")
        provider = router.get_research_provider()

    assert provider.requests[0].effort == "low"
    assert response.answer == "the answer"
    assert provider.closed


async def test_async_submit_research_hands_back_the_job(fake_research_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        job = await router.submit_research("hello")
        answer = await job.aresult()

    assert (job.provider, job.job_id) == ("fake", "job-1")
    assert answer.answer == "the answer"


async def test_async_research_accepts_an_already_constructed_provider(fake_research_provider: type) -> None:
    instance = fake_research_provider()
    async with AsyncWebRouter("fake") as router:
        response = await router.research("q", provider=instance)

    assert response.provider == "fake"
    assert not instance.closed


async def test_async_context_manager_closes_all_three_methods_providers(
    fake_provider: type, fake_extract_provider: type, fake_research_provider: type
) -> None:
    async with AsyncWebRouter("fake") as router:
        searcher = router.get_provider()
        extractor = router.get_extract_provider()
        researcher = router.get_research_provider()

    assert searcher.closed and extractor.closed and researcher.closed


# -- answer(): one call, and no `auto` to fall back on -----------------------


def _configure(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make exactly ``names`` the configured answer backends, whatever .env holds."""
    from web_router.constants import ANSWER_ENV_BY_PROVIDER

    for name in list(ANSWER_ENV_BY_PROVIDER):
        monkeypatch.delitem(ANSWER_ENV_BY_PROVIDER, name)
    for name in names:
        env_var = f"WEB_ROUTER_TEST_{name.upper()}_KEY"
        monkeypatch.setitem(ANSWER_ENV_BY_PROVIDER, name, (env_var,))
        monkeypatch.setenv(env_var, "test-key")


def test_answer_normalizes_arguments_into_the_request(fake_answer_provider: type) -> None:
    router = WebRouter("fake")
    response = router.answer("q", include_domains=["example.com"], country="US", recency="week")

    request = router.get_answer_provider().requests[0]  # type: ignore[attr-defined]
    assert request.query == "q"
    assert request.include_domains == ["example.com"]
    assert request.country == "US"
    assert request.recency == "week"
    assert response.provider == "fake"
    assert response.answer == "the answer"


def test_a_bare_answer_uses_the_one_configured_backend(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router's default is `auto`, which answer() lacks; one configured backend needs no order."""
    _configure(monkeypatch, "fake")

    assert WebRouter().answer("q").provider == "fake"


def test_a_bare_answer_with_two_configured_backends_goes_to_auto_or_asks_which(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the usual default `auto` tries them in turn; on a default that does not answer, the caller picks."""
    from tests.conftest import FakeAnswerProvider
    from web_router import register_answer_provider, unregister_answer_provider

    register_answer_provider("other", type("Other", (FakeAnswerProvider,), {"name": "other"}))
    try:
        _configure(monkeypatch, "fake", "other")

        assert WebRouter().answer("q").provider == "fake", "added backends follow the preferred ones, by name"
        with pytest.raises(ProviderNotFoundError, match="2 answer backends are configured") as caught:
            WebRouter("nimble").answer("q")
        assert {"fake", "other"} <= set(caught.value.available)
    finally:
        unregister_answer_provider("other")


def test_a_bare_answer_with_nothing_configured_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    with pytest.raises(ProviderNotFoundError, match="no answer backend has a key"):
        WebRouter().answer("q")


def test_a_named_provider_wins_over_the_one_configured(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import FakeAnswerProvider
    from web_router import register_answer_provider, unregister_answer_provider

    register_answer_provider("other", type("Other", (FakeAnswerProvider,), {"name": "other"}))
    try:
        _configure(monkeypatch, "fake")

        assert WebRouter("other").answer("q").provider == "other"
        assert WebRouter().answer("q", provider="other").provider == "other"
    finally:
        unregister_answer_provider("other")


def test_closing_the_router_closes_the_answer_provider_it_built(fake_answer_provider: type) -> None:
    with WebRouter("fake") as router:
        router.answer("q")
        provider = router.get_answer_provider()

    assert provider.closed  # type: ignore[attr-defined]


async def test_async_answer_awaits_the_backend_and_closes_it(fake_answer_provider: type) -> None:
    async with AsyncWebRouter("fake") as router:
        response = await router.answer("q", locale="en")
        provider = router.get_answer_provider()

    assert response.answer == "the answer"
    assert provider.requests[0].locale == "en"  # type: ignore[attr-defined]
    assert provider.closed  # type: ignore[attr-defined]


def test_a_key_passed_in_code_configures_an_answer_backend(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)

    router = WebRouter(provider_config={"fake": {"api_key": "in-code"}})

    assert router.answer("q").provider == "fake"


def test_a_key_set_under_auto_is_not_an_answer_backend(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, "fake")

    router = WebRouter("nimble", provider_config={"auto": {"api_key": "stray"}})

    assert router.answer("q").provider == "fake", "the one real answer backend, not 'two configured'"


def test_the_no_default_refusal_is_its_own_error_and_still_a_provider_not_found(
    fake_answer_provider: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web_router import NoAnswerProviderError
    from web_router.constants import ANSWER_ENV_BY_PROVIDER

    _configure(monkeypatch)
    monkeypatch.setitem(ANSWER_ENV_BY_PROVIDER, "fake", ("WEB_ROUTER_TEST_UNSET_KEY",))
    monkeypatch.delenv("WEB_ROUTER_TEST_UNSET_KEY", raising=False)

    with pytest.raises(NoAnswerProviderError) as caught:
        WebRouter().answer("q")

    assert isinstance(caught.value, ProviderNotFoundError)
    assert caught.value.configured == []
    assert caught.value.env_vars == ["WEB_ROUTER_TEST_UNSET_KEY"], "with nothing configured it names what to set"
    assert "WEB_ROUTER_TEST_UNSET_KEY" in str(caught.value)
